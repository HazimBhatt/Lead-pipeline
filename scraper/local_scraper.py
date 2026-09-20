import json
import os
import re
import sys
import time
import urllib.parse

# Ensure scraper directory is in sys.path
SCRAPER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRAPER_DIR)
if SCRAPER_DIR not in sys.path:
    sys.path.insert(0, SCRAPER_DIR)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from playwright.sync_api import sync_playwright

from config import (
    SEARCH_QUERIES,
    TARGET_CITIES,
    MAX_RESULTS,
    HEADLESS,
    ENRICH_EMAILS,
    EMAIL_TIMEOUT,
    MAX_EMAIL_PAGES,
)
from email_extractor import find_lead_email, extract_emails_from_html

DEFAULT_LEADS_FILE = os.path.join(ROOT_DIR, "leads.json")


def clean_business_name(name: str) -> str:
    """Strip material icons, private-use characters, and collapse extra whitespace."""
    if not name:
        return ""
    # Strip unicode private use area (e.g. Google Material Icons like \ue5d4)
    cleaned = re.sub(r"[\u2000-\u206f\ue000-\uf8ff]+", " ", name)
    return " ".join(cleaned.split()).strip()


def is_invalid_business_name(name: str) -> bool:
    """Filter out Google Maps search headers and ads mistakenly read as place names."""
    if not name:
        return True
    clean = clean_business_name(name).lower()
    if not clean or len(clean) < 2:
        return True
    if clean in ("results", "search results", "sponsored", "ad", "ads", "advertisement"):
        return True
    if clean.startswith("results for") or clean.startswith("search results"):
        return True
    if clean.startswith("sponsored") or clean.startswith("ad ·") or clean.startswith("ads ·"):
        return True
    return False





def handle_consent_dialog(page):
    """Dismiss Google consent / cookie popup if shown."""
    consent_selectors = [
        "button[aria-label*='Accept']",
        "button[aria-label*='Agree']",
        "form[action*='consent'] button",
        "button:has-text('Accept all')",
        "button:has-text('I agree')",
        "button:has-text('Accept')",
    ]
    for sel in consent_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                page.wait_for_timeout(1000)
                break
        except Exception:
            pass


def extract_lead_from_panel(page, context, city: str, expected_name: str = "", enrich_emails: bool = True) -> dict:
    """Extract lead details from the active Google Maps place panel."""
    # Wait for the place details panel elements to load
    try:
        page.wait_for_selector(
            "button[data-item-id='address'], a[data-item-id='authority'], button[data-item-id*='phone'], div.TIH9bg, h1.DUwDvf",
            timeout=5000
        )
    except Exception:
        pass

    # 1. Name - target the place header specifically to avoid ad headers
    name = ""
    try:
        title_selectors = [
            "div[role='main'] h1.DUwDvf",
            "h1.DUwDvf",
            "div.lMbq3e h1",
            "div[role='main'] h1",
        ]
        for sel in title_selectors:
            title_el = page.locator(sel).first
            if title_el.is_visible(timeout=800):
                cand = clean_business_name(title_el.inner_text().strip())
                if cand and not is_invalid_business_name(cand):
                    name = cand
                    break
    except Exception:
        pass

    # Fallback to general h1s if specific title wasn't found
    if not name:
        try:
            for h1 in page.locator("h1").all():
                cand = clean_business_name(h1.inner_text().strip())
                if cand and not is_invalid_business_name(cand):
                    name = cand
                    break
        except Exception:
            pass

    # Fallback to the place card aria-label name from the feed
    if not name and expected_name:
        clean_exp = clean_business_name(expected_name)
        if not is_invalid_business_name(clean_exp):
            name = clean_exp

    # 2. Category
    category = ""
    try:
        cat_btn = page.locator("button[jsaction*='pane.rating.category']").first
        if cat_btn.is_visible(timeout=1000):
            category = cat_btn.inner_text().strip()
    except Exception:
        pass

    # 3. Rating & Reviews
    rating = ""
    reviews = 0
    try:
        rating_el = page.locator("div.F7nice span[aria-hidden='true']").first
        if rating_el.is_visible(timeout=1000):
            r_text = rating_el.inner_text().strip().replace(",", ".")
            rating = float(r_text) if r_text else ""
    except Exception:
        pass

    try:
        rev_el = page.locator("div.F7nice span span span[aria-label], button[jsaction*='pane.reviewChart.moreReviews']").first
        if rev_el.is_visible(timeout=1000):
            rev_text = rev_el.inner_text().strip().replace(",", "").replace(".", "")
            match = re.search(r"\d+", rev_text)
            if match:
                reviews = int(match.group(0))
    except Exception:
        pass

    # 4. Address
    address = ""
    try:
        addr_el = page.locator("button[data-item-id='address']").first
        if addr_el.is_visible(timeout=1000):
            address = addr_el.get_attribute("aria-label") or addr_el.inner_text()
            address = address.replace("Address: ", "").strip()
    except Exception:
        pass

    # 5. Website
    website = ""
    try:
        web_el = page.locator("a[data-item-id='authority']").first
        if web_el.is_visible(timeout=1000):
            website = web_el.get_attribute("href") or ""
            if "google.com/url?q=" in website:
                website = urllib.parse.parse_qs(urllib.parse.urlparse(website).query).get("q", [website])[0]
    except Exception:
        pass

    # 6. Phone
    phone = ""
    try:
        phone_el = page.locator("button[data-item-id*='phone']").first
        if phone_el.is_visible(timeout=1000):
            phone_text = phone_el.get_attribute("aria-label") or phone_el.inner_text()
            phone = phone_text.replace("Phone: ", "").strip()
            if not phone:
                data_id = phone_el.get_attribute("data-item-id") or ""
                if "phone:tel:" in data_id:
                    phone = data_id.replace("phone:tel:", "").strip()
    except Exception:
        pass


    # 7. Maps URL
    maps_link = page.url

    # 8. Multi-Stage High-Probability Email Enrichment
    email = ""
    if enrich_emails:
        # Step 8a: Check if listing panel itself displays an email
        try:
            panel_text = page.locator("div[role='main']").inner_text(timeout=1000)
            panel_emails = extract_emails_from_html(panel_text)
            if panel_emails:
                email = list(panel_emails)[0]
                print(f"      Found direct email on Google Maps: {email}", flush=True)
        except Exception:
            pass

        # Step 8b: Scan website and search engine fallback
        search_name = name or expected_name
        if not email and (website or search_name):
            if website:
                print(f"      Scanning website for email: {website[:55]}...", flush=True)
            email = find_lead_email(
                website=website,
                business_name=search_name,
                city=city,
                timeout=EMAIL_TIMEOUT,
                max_pages=MAX_EMAIL_PAGES,
                browser_context=context,
            ) or ""
            if email:
                print(f"      Found email: {email}", flush=True)

    return {
        "name": name,
        "phone": phone,
        "website": website,
        "email": email,
        "address": address,
        "city": city,
        "category": category,
        "rating": rating,
        "reviews": reviews,
        "maps_link": maps_link,
    }


def save_lead_to_json(lead: dict, filename: str = DEFAULT_LEADS_FILE):
    """Immediately append/merge a single scraped lead into the JSON file."""
    name = clean_business_name(lead.get("name", ""))
    lead["name"] = name
    if not name or is_invalid_business_name(name):
        return

    city = (lead.get("city") or "").strip()
    existing = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    # Clean and filter existing items (purge any legacy invalid / ad headers)
    valid_existing = [
        item for item in existing
        if item.get("name") and not is_invalid_business_name(item.get("name"))
    ]

    lead_map = {}
    for item in valid_existing:
        item_name = clean_business_name(item.get("name", ""))
        item_city = (item.get("city") or "").strip()
        item["name"] = item_name
        key = (item_name.lower(), item_city.lower())
        lead_map[key] = item

    current_key = (name.lower(), city.lower())
    if current_key not in lead_map:
        lead_map[current_key] = lead
    else:
        # Keep lead updated (prefer new email if discovered)
        if lead.get("email") and not lead_map[current_key].get("email"):
            lead_map[current_key]["email"] = lead["email"]
        for k, v in lead.items():
            if v and not lead_map[current_key].get(k):
                lead_map[current_key][k] = v

    merged = list(lead_map.values())
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)


def launch_browser(p, headless: bool = False):
    """
    Launch local browser safely.
    Prefers installed Google Chrome or Microsoft Edge to bypass Windows Application
    Control (WDAC/AppLocker) policies that block downloaded AppData binaries.
    """
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]

    # 1. Try real installed Google Chrome
    try:
        return p.chromium.launch(
            channel="chrome",
            headless=headless,
            args=launch_args,
        )
    except Exception:
        pass

    # 2. Try real installed Microsoft Edge
    try:
        return p.chromium.launch(
            channel="msedge",
            headless=headless,
            args=launch_args,
        )
    except Exception:
        pass

    # 3. Fallback to default Playwright Chromium
    return p.chromium.launch(
        headless=headless,
        args=launch_args,
    )


def scrape_leads(query: str, city: str, max_results: int = 20) -> list:
    """Scrape Google Maps locally using Playwright."""
    leads = []
    search_query = f"{query} in {city}"
    search_url = f"https://www.google.com/maps/search/{urllib.parse.quote_plus(search_query)}"

    with sync_playwright() as p:
        browser = launch_browser(p, headless=HEADLESS)


        context = browser.new_context(
            viewport=None,
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        try:
            print(f"Opening Google Maps: {search_url}", flush=True)
            page.goto(search_url, wait_until="domcontentloaded", timeout=40000)
            handle_consent_dialog(page)

            # Wait for results or feed to appear
            try:
                page.wait_for_selector('div[role="feed"], div.Nv2PK, h1.DUwDvf', timeout=12000)
            except Exception:
                print("   No listings container detected.", flush=True)
                return leads

            # If single place match loaded directly
            if page.locator("h1.DUwDvf").is_visible():
                print("   Direct place match found.", flush=True)
                single_lead = extract_lead_from_panel(page, context, city, ENRICH_EMAILS)
                if single_lead["name"]:
                    leads.append(single_lead)
                    save_lead_to_json(single_lead, DEFAULT_LEADS_FILE)
                return leads

            feed = page.locator('div[role="feed"]').first
            if not feed.is_visible(timeout=5000):
                print("   Feed not visible.", flush=True)
                return leads

            # Scroll and collect listings until max_results is reached
            print(f"   Collecting up to {max_results} results...", flush=True)
            seen_keys = set()
            scroll_attempts = 0
            max_scroll_attempts = 35

            while len(leads) < max_results and scroll_attempts < max_scroll_attempts:
                # Find all actual place links in the feed (skip headers/chips)
                raw_links = feed.locator("a.hfpxzc").all()
                place_links = [
                    link for link in raw_links
                    if "/maps/place/" in (link.get_attribute("href") or "")
                ]

                for link in place_links:
                    if len(leads) >= max_results:
                        break

                    name_attr = clean_business_name((link.get_attribute("aria-label") or "").strip())
                    if not name_attr or is_invalid_business_name(name_attr):
                        continue

                    link_key = (name_attr.lower(), city.lower())
                    if link_key in seen_keys:
                        continue

                    # Click to open place detail panel
                    try:
                        link.scroll_into_view_if_needed(timeout=2000)
                        link.click()
                        page.wait_for_timeout(1200)  # Brief pause for details to render

                        lead = extract_lead_from_panel(page, context, city, expected_name=name_attr, enrich_emails=ENRICH_EMAILS)
                        actual_name = clean_business_name(lead.get("name") or name_attr)
                        if is_invalid_business_name(actual_name):
                            actual_name = name_attr
                        if is_invalid_business_name(actual_name):
                            continue

                        lead["name"] = actual_name
                        actual_key = (actual_name.lower(), city.lower())

                        if actual_key not in seen_keys:
                            seen_keys.add(link_key)
                            seen_keys.add(actual_key)
                            leads.append(lead)
                            # Instant auto-save to disk:
                            save_lead_to_json(lead, DEFAULT_LEADS_FILE)
                            print(
                                f"   [{len(leads)}/{max_results}] {actual_name} | "
                                f"Tel: {lead['phone'] or 'N/A'} | "
                                f"Email: {lead['email'] or 'N/A'} (Saved)",
                                flush=True
                            )
                    except KeyboardInterrupt:
                        raise
                    except Exception as err:
                        print(f"   [Notice: skipped '{name_attr}']: {err}", flush=True)
                        continue

                # Check if reached end of list
                end_marker = page.locator("text=You've reached the end of the list").first
                if end_marker.is_visible(timeout=500):
                    print("   Reached the end of Google Maps results.", flush=True)
                    break

                # Scroll down inside the feed
                feed.evaluate("el => el.scrollBy(0, 1000)")
                page.wait_for_timeout(1500)
                scroll_attempts += 1

        except KeyboardInterrupt:
            print("\n   [Search interrupted by user] Preserving all scraped leads...", flush=True)
            raise
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass



    return leads


def save_to_json(leads: list, filename: str = DEFAULT_LEADS_FILE):
    """Save leads to JSON file with UTF-8 encoding."""
    existing = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    valid_existing = [
        item for item in existing
        if item.get("name") and not is_invalid_business_name(item.get("name"))
    ]

    lead_map = {}
    for item in valid_existing:
        item_name = clean_business_name(item.get("name", ""))
        item_city = (item.get("city") or "").strip()
        item["name"] = item_name
        key = (item_name.lower(), item_city.lower())
        lead_map[key] = item

    for lead in leads:
        name = clean_business_name(lead.get("name", ""))
        if not name or is_invalid_business_name(name):
            continue
        lead["name"] = name
        city = (lead.get("city") or "").strip()
        key = (name.lower(), city.lower())
        if key not in lead_map:
            lead_map[key] = lead
        else:
            if lead.get("email") and not lead_map[key].get("email"):
                lead_map[key]["email"] = lead["email"]
            for k, v in lead.items():
                if v and not lead_map[key].get(k):
                    lead_map[key][k] = v

    merged = list(lead_map.values())
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(merged)} total unique leads to {filename}")


if __name__ == "__main__":
    try:
        all_leads = []
        for query in SEARCH_QUERIES:
            for city in TARGET_CITIES:
                print(f"\n Searching: '{query}' in '{city}'", flush=True)
                leads = scrape_leads(query, city, max_results=MAX_RESULTS)
                print(f" Found: {len(leads)} leads for {query} in {city}", flush=True)
                all_leads.extend(leads)

        print(f"\n All searches complete! Total leads in this run: {len(all_leads)}", flush=True)
        print(f" All leads are stored safely in: {DEFAULT_LEADS_FILE}", flush=True)

    except KeyboardInterrupt:
        print("\n\n⚠️ Process stopped by user (Ctrl+C).", flush=True)
        print(f"✅ Don't worry! Every lead scraped up to this point has already been automatically saved to {DEFAULT_LEADS_FILE}.\n", flush=True)
        sys.exit(0)


