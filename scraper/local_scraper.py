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


def is_invalid_business_name(name: str) -> bool:
    """Filter out Google Maps search headers mistakenly read as place names."""
    if not name:
        return True
    nl = name.strip().lower()
    return nl == "results" or nl.startswith("results for") or nl == "search results"





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


def extract_lead_from_panel(page, context, city: str, enrich_emails: bool = True) -> dict:
    """Extract lead details from the active Google Maps place panel."""
    # Wait for the place details panel elements to load
    try:
        page.wait_for_selector(
            "button[data-item-id='address'], a[data-item-id='authority'], button[data-item-id*='phone'], div.TIH9bg",
            timeout=6000
        )
    except Exception:
        pass

    # 1. Name
    name = ""
    try:
        for h1 in page.locator("h1").all():
            cand = h1.inner_text().strip()
            if cand and not is_invalid_business_name(cand):
                name = cand
                break
    except Exception:
        pass

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
        if not email and (website or name):
            if website:
                print(f"      Scanning website for email: {website[:55]}...", flush=True)
            email = find_lead_email(
                website=website,
                business_name=name,
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


def scrape_leads(query: str, city: str, max_results: int = 20) -> list:
    """Scrape Google Maps locally using Playwright."""
    leads = []
    search_query = f"{query} in {city}"
    search_url = f"https://www.google.com/maps/search/{urllib.parse.quote_plus(search_query)}"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )

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
                return leads

            feed = page.locator('div[role="feed"]').first
            if not feed.is_visible(timeout=5000):
                print("   Feed not visible.", flush=True)
                return leads

            # Scroll and collect listings until max_results is reached
            print(f"   Collecting up to {max_results} results...", flush=True)
            seen_names = set()
            scroll_attempts = 0
            max_scroll_attempts = 30

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

                    name_attr = (link.get_attribute("aria-label") or "").strip()
                    if not name_attr or is_invalid_business_name(name_attr) or name_attr in seen_names:
                        continue

                    # Click to open place detail panel
                    try:
                        link.scroll_into_view_if_needed(timeout=2000)
                        link.click()
                        page.wait_for_timeout(1000)  # Brief pause for details to render

                        lead = extract_lead_from_panel(page, context, city, ENRICH_EMAILS)
                        actual_name = lead["name"] or name_attr
                        if is_invalid_business_name(actual_name):
                            actual_name = name_attr
                        if is_invalid_business_name(actual_name):
                            continue

                        if actual_name and actual_name not in seen_names:
                            lead["name"] = actual_name
                            seen_names.add(actual_name)
                            leads.append(lead)
                            print(
                                f"   [{len(leads)}/{max_results}] {actual_name} | "
                                f"Tel: {lead['phone'] or 'N/A'} | "
                                f"Email: {lead['email'] or 'N/A'}",
                                flush=True
                            )
                    except Exception:
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

        finally:
            context.close()
            browser.close()


    return leads


def save_to_json(leads: list, filename: str = DEFAULT_LEADS_FILE):
    """Save leads to JSON file with UTF-8 encoding."""
    # If leads file exists, load existing to merge/update
    existing = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    # Deduplicate by name, preferring new records with emails
    lead_map = {item.get("name", ""): item for item in existing if item.get("name")}
    for lead in leads:
        name = lead.get("name", "")
        if not name:
            continue
        if name not in lead_map:
            lead_map[name] = lead
        else:
            # If new lead has an email and existing doesn't, update it
            if lead.get("email") and not lead_map[name].get("email"):
                lead_map[name]["email"] = lead["email"]

    merged = list(lead_map.values())
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(merged)} total unique leads to {filename}")


if __name__ == "__main__":
    all_leads = []
    for query in SEARCH_QUERIES:
        for city in TARGET_CITIES:
            print(f"\n Searching: '{query}' in '{city}'")
            leads = scrape_leads(query, city, max_results=MAX_RESULTS)
            print(f" Found: {len(leads)} leads for {query} in {city}")
            all_leads.extend(leads)

    # Deduplicate current run
    seen = set()
    unique = []
    for lead in all_leads:
        name = lead.get("name")
        if name and name not in seen:
            seen.add(name)
            unique.append(lead)

    print(f"\n Total scraped in this run: {len(unique)}")
    save_to_json(unique, DEFAULT_LEADS_FILE)

