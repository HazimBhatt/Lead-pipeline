import re
import time
import urllib.parse
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import dns.resolver
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SOCIAL_ONLY_DOMAINS = {
    "facebook.com", "fb.com", "instagram.com", "linkedin.com", "twitter.com",
    "x.com", "tiktok.com", "yelp.com", "linktr.ee", "youtube.com",
    "tripadvisor.com", "booking.com", "airbnb.com",
}

JUNK_EMAIL_DOMAINS = {
    "mysite.com", "mystore.com", "example.com", "domain.com", "email.com",
    "test.com", "sample.com", "yoursite.com", "sentry.io", "wixpress.com",
    "wordpress.org", "cloudflare.com", "schema.org", "w3.org",
    "yourhostingaccount.com", "tznvy.pbz", "site123-gal-editor-b.com",
    "godaddy.com", "squarespace.com", "wix.com", "dandb.com", "bbb.org",
    "yelp.com", "yellowpages.com", "mapquest.com", "mailchimp.com",
    "hubspot.com", "weebly.com", "facebook.com", "instagram.com",
}

ROLE_EMAIL_PREFIXES = {
    "info", "contact", "hello", "hi", "office", "admin", "support", "help",
    "sales", "booking", "bookings", "reservations", "reservation", "enquiries",
    "enquiry", "inquiries", "inquiry", "reception", "frontdesk", "mail",
    "general", "team", "service", "services", "marketing", "privacy", "legal",
    "noreply", "no-reply", "donotreply", "webmaster", "postmaster",
    "care", "clientcare", "concierge", "customerservice", "customercare",
}

BAD_EMAIL_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "privacy", "gdpr",
    "abuse", "postmaster", "mailer-daemon", "webmaster", "hostmaster",
    "cgiadmin", "unsubscribe", "bounce", "spam", "jobs", "careers", "hr",
    "recruitment", "billing", "accounts", "invoice", "invoices",
}

BOOKING_SIGNALS = [
    "calendly.com", "acuityscheduling", "janeapp.com", "mindbodyonline",
    "opentable.com", "resy.com", "booksy.com", "fresha.com", "zocdoc.com",
    "simplybook", "setmore.com", "schedulicity", "vagaro.com", "square.site/book",
    "square.site/appointments", "zenoti.com", "boulevard", "blvd.co",
    "nexhealth.com", "localmed.com", "doctolib", "cliniko.com", "halaxy.com",
    "book online", "book an appointment", "book appointment", "schedule consultation",
    "book a consultation", "book consultation", "schedule a call", "book now",
    "online booking", "request an appointment", "request a quote", "free estimate",
]

CHAT_SIGNALS = [
    "intercom", "drift.com", "tidio", "crisp.chat", "livechatinc", "zendesk",
    "tawk.to", "podium.com", "birdeye.com", "hubspot", "manychat", "botpress",
    "voiceflow", "wa.me/", "api.whatsapp.com", "chat-widget", "live-chat",
]

ANALYTICS_SIGNALS = [
    "googletagmanager.com", "google-analytics.com", "gtag(", "fbevents.js",
    "fbq(", "plausible.io", "posthog", "clarity.ms", "hotjar.com", "segment.com",
]

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def verify_email_mx(email: str, timeout: float = 5.0) -> Dict:
    """
    Validate email syntax, reject known junk/no-reply/placeholder domains,
    and verify DNS MX (or A) records so Gmail bounce rate stays near 0%.
    """
    if not email or not isinstance(email, str):
        return {"valid": False, "reason": "Empty email", "mx_host": "", "is_role_address": False}

    cleaned = email.strip().lower()
    if "@" not in cleaned or " " in cleaned or len(cleaned) < 6 or len(cleaned) > 100:
        return {"valid": False, "reason": "Invalid syntax", "mx_host": "", "is_role_address": False}

    if not re.match(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", cleaned):
        return {"valid": False, "reason": "Malformed email characters", "mx_host": "", "is_role_address": False}

    local_part, domain = cleaned.split("@", 1)

    if local_part in BAD_EMAIL_PREFIXES or any(local_part.startswith(p + ".") for p in ("privacy", "noreply", "no-reply")):
        return {"valid": False, "reason": f"Non-outreach address ({local_part}@)", "mx_host": "", "is_role_address": True}

    if domain in JUNK_EMAIL_DOMAINS or any(domain.endswith("." + d) for d in JUNK_EMAIL_DOMAINS):
        return {"valid": False, "reason": f"Placeholder/junk domain ({domain})", "mx_host": "", "is_role_address": False}

    # Catch broken scraped domains like restaurant@www.domain.com
    if domain.startswith("www."):
        return {"valid": False, "reason": f"Invalid www. domain ({domain})", "mx_host": "", "is_role_address": False}

    is_role = local_part in ROLE_EMAIL_PREFIXES

    # DNS MX verification
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout

    try:
        mx_records = resolver.resolve(domain, "MX")
        mx_hosts = sorted([(r.preference, str(r.exchange).rstrip(".")) for r in mx_records], key=lambda x: x[0])
        if mx_hosts and mx_hosts[0][1]:
            return {
                "valid": True,
                "reason": "MX verified",
                "mx_host": mx_hosts[0][1],
                "is_role_address": is_role,
            }
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.resolver.LifetimeTimeout) as e:
        # Fallback to A record check only on NoAnswer (RFC 5321 allows A record fallback when MX absent)
        if isinstance(e, dns.resolver.NoAnswer):
            try:
                a_records = resolver.resolve(domain, "A")
                if a_records:
                    return {
                        "valid": True,
                        "reason": "A-record fallback verified",
                        "mx_host": domain,
                        "is_role_address": is_role,
                    }
            except Exception:
                pass
        return {
            "valid": False,
            "reason": f"DNS/MX failed ({type(e).__name__})",
            "mx_host": "",
            "is_role_address": is_role,
        }
    except Exception as e:
        return {
            "valid": False,
            "reason": f"MX check error ({e})",
            "mx_host": "",
            "is_role_address": is_role,
        }

    return {"valid": False, "reason": "No MX hosts found", "mx_host": "", "is_role_address": is_role}


def _fetch_html(url: str, timeout: int = 10) -> Tuple[Optional[requests.Response], float, bool, str]:
    """Fetch a URL, measuring response time and tracking SSL validity."""
    start = time.perf_counter()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True, verify=True)
        elapsed = round(time.perf_counter() - start, 2)
        return resp, elapsed, True, ""
    except requests.exceptions.SSLError:
        try:
            start = time.perf_counter()
            resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True, verify=False)
            elapsed = round(time.perf_counter() - start, 2)
            return resp, elapsed, False, "Invalid or expired SSL certificate"
        except Exception as e:
            return None, round(time.perf_counter() - start, 2), False, str(e)
    except Exception as e:
        return None, round(time.perf_counter() - start, 2), True, str(e)


def _detect_tech_stack(html_lower: str) -> List[str]:
    stack = []
    if "wp-content" in html_lower or "wp-includes" in html_lower:
        stack.append("WordPress")
    if "wixstatic.com" in html_lower or "wix.com" in html_lower:
        stack.append("Wix")
    if "squarespace.com" in html_lower or "static1.squarespace" in html_lower:
        stack.append("Squarespace")
    if "webflow.com" in html_lower or "website-files.com" in html_lower:
        stack.append("Webflow")
    if "cdn.shopify.com" in html_lower:
        stack.append("Shopify")
    if "godaddy" in html_lower or "wsimg.com" in html_lower:
        stack.append("GoDaddy Builder")
    if "__next_data__" in html_lower or "/_next/" in html_lower:
        stack.append("Next.js")
    if "elementor" in html_lower:
        stack.append("Elementor")
    return stack or ["Custom/Unknown"]


def _extract_clean_text(soup: BeautifulSoup, max_chars: int = 2500) -> str:
    clone = BeautifulSoup(str(soup), "html.parser")
    for tag in clone(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    text = " ".join(clone.get_text(" ", strip=True).split())
    return text[:max_chars]


UI_TRAILING_WORDS = {
    "meet", "about", "home", "contact", "team", "read", "learn", "more", "view",
    "book", "schedule", "call", "our", "the", "welcome", "click", "menu",
    "office", "clinic", "dental", "practice", "spa", "medspa", "services",
    "reviews", "testimonials", "blog", "faq", "hours", "location", "locations",
    "is", "has", "was", "and", "at", "in", "of", "for", "with",
}


def _clean_person_name(raw: str) -> str:
    """Strip trailing UI/navigation words mistakenly glued onto a doctor/founder name."""
    parts = (raw or "").strip().split()
    while len(parts) > 2 and parts[-1].lower().strip(".,:-") in UI_TRAILING_WORDS:
        parts.pop()
    if len(parts) == 2 and parts[0].lower().startswith("dr") and parts[1].lower() in UI_TRAILING_WORDS:
        return ""
    return " ".join(parts).strip()


def _extract_decision_maker_from_text(text: str) -> Tuple[str, str]:
    """
    Extract a likely Founder / Owner / Managing Partner / Doctor name from page copy.
    Returns (full_name, role) or ("", "").
    """
    if not text:
        return "", ""

    # Pattern 1: "Dr. First Last" (high value for cosmetic dentists, medspas, clinics)
    dr_match = re.search(
        r"\b(Dr\.?\s+[A-Z][a-z]{1,18}(?:\s+[A-Z][a-z]{1,18}){1,2})\b",
        text,
    )
    if dr_match:
        cand = _clean_person_name(dr_match.group(1))
        if cand and not any(bad in cand.lower() for bad in ("drive", "street", "road", "suite", "avenue")):
            return cand, "Doctor / Clinical Lead"

    # Pattern 2: "First Last, Founder / CEO / Owner / Managing Partner / Principal / Director"
    role_after = re.search(
        r"\b([A-Z][a-z]{1,15}\s+[A-Z][a-z]{1,18})\s*[,\-–|]\s*(Founder|Co-Founder|Owner|CEO|Managing Partner|Principal|Broker|Director|President|MD|DDS|DMD)\b",
        text,
    )
    if role_after:
        cand = _clean_person_name(role_after.group(1))
        if cand and cand.split()[0].lower() not in UI_TRAILING_WORDS:
            return cand, role_after.group(2).strip()

    # Pattern 3: "Founded by First Last" / "Meet First Last, ..."
    founded_by = re.search(
        r"\b(?:Founded by|Led by|Owner|CEO|Founder|Managing Partner)[:\s]+([A-Z][a-z]{1,15}\s+[A-Z][a-z]{1,18})\b",
        text,
    )
    if founded_by:
        cand = _clean_person_name(founded_by.group(1))
        if cand and cand.split()[0].lower() not in UI_TRAILING_WORDS:
            return cand, "Founder / Owner"

    return "", ""


def _find_about_team_urls(soup: BeautifulSoup, base_url: str) -> List[str]:
    base_domain = urllib.parse.urlparse(base_url).netloc.lower()
    keywords = ("about", "team", "doctor", "leadership", "founder", "attorney", "staff", "who-we-are", "our-story")
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(full)
        if parsed.netloc.lower() != base_domain:
            continue
        path_and_text = (parsed.path + " " + a.get_text(" ", strip=True)).lower()
        if any(k in path_and_text for k in keywords) and full not in seen:
            seen.add(full)
            found.append(full)
        if len(found) >= 2:
            break
    return found


def audit_website(url: str, timeout: int = 10) -> Dict:
    """
    Run a real technical, SEO, conversion, and decision-maker audit on a live website.
    """
    result = {
        "url": url or "",
        "reachable": False,
        "social_only": False,
        "http_status": 0,
        "load_time_sec": 0.0,
        "html_size_kb": 0.0,
        "uses_https": False,
        "ssl_valid": False,
        "title": "",
        "meta_description": "",
        "h1_tags": [],
        "has_mobile_viewport": False,
        "has_schema_org": False,
        "has_og_tags": False,
        "copyright_year": None,
        "has_online_booking": False,
        "has_lead_form": False,
        "has_chat_widget": False,
        "has_analytics": False,
        "tech_stack": [],
        "decision_maker_name": "",
        "decision_maker_role": "",
        "homepage_snippet": "",
        "detected_leaks": [],
        "error": "",
    }

    if not url or not url.strip():
        result["detected_leaks"].append("No website listed on Google Maps (relying purely on Maps profile)")
        return result

    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url
    result["url"] = clean_url

    parsed = urllib.parse.urlparse(clean_url)
    host = parsed.netloc.lower().replace("www.", "")
    if any(host == d or host.endswith("." + d) for d in SOCIAL_ONLY_DOMAINS):
        result["social_only"] = True
        result["detected_leaks"].append(
            f"Website link points to {host} instead of a dedicated conversion-optimized website"
        )
        return result

    resp, elapsed, ssl_valid, err = _fetch_html(clean_url, timeout=timeout)
    result["load_time_sec"] = elapsed
    result["ssl_valid"] = ssl_valid

    if resp is None:
        result["error"] = err
        result["detected_leaks"].append(f"Website failed to load or timed out ({err[:80]})")
        return result

    result["reachable"] = resp.status_code < 400
    result["http_status"] = resp.status_code
    result["uses_https"] = resp.url.lower().startswith("https://")
    result["html_size_kb"] = round(len(resp.content) / 1024.0, 1)

    if resp.status_code >= 400:
        result["detected_leaks"].append(f"Website returned HTTP {resp.status_code} error")
        return result

    html = resp.text or ""
    html_lower = html.lower()
    soup = BeautifulSoup(html, "html.parser")

    # Core SEO & Technical signals
    if soup.title and soup.title.string:
        result["title"] = soup.title.string.strip()[:160]

    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta_desc and meta_desc.get("content"):
        result["meta_description"] = meta_desc["content"].strip()[:240]

    result["h1_tags"] = [h.get_text(" ", strip=True)[:120] for h in soup.find_all("h1") if h.get_text(strip=True)][:3]
    result["has_mobile_viewport"] = bool(soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}))
    result["has_schema_org"] = (
        "application/ld+json" in html_lower
        or "schema.org" in html_lower
    )
    result["has_og_tags"] = bool(soup.find("meta", attrs={"property": re.compile(r"^og:", re.I)}))
    result["tech_stack"] = _detect_tech_stack(html_lower)

    # Copyright year detection
    current_year = datetime.now().year
    years = re.findall(r"(?:©|&copy;|copyright)\s*(?:20\d{2}\s*[-–]\s*)?(20\d{2})", html_lower)
    if years:
        try:
            max_year = max(int(y) for y in years if 2005 <= int(y) <= current_year + 1)
            result["copyright_year"] = max_year
        except ValueError:
            pass

    # Conversion signals
    result["has_online_booking"] = any(sig in html_lower for sig in BOOKING_SIGNALS)
    forms = soup.find_all("form")
    result["has_lead_form"] = any(
        f.find("input", attrs={"type": re.compile(r"^(email|tel|text)$", re.I)}) or f.find("textarea")
        for f in forms
    )
    result["has_chat_widget"] = any(sig in html_lower for sig in CHAT_SIGNALS)
    result["has_analytics"] = any(sig in html_lower for sig in ANALYTICS_SIGNALS)

    # Clean homepage text
    homepage_text = _extract_clean_text(soup, max_chars=2500)
    result["homepage_snippet"] = homepage_text

    # Decision-maker extraction (homepage first, then /about or /team)
    dm_name, dm_role = _extract_decision_maker_from_text(homepage_text)
    if not dm_name:
        for sub_url in _find_about_team_urls(soup, resp.url):
            sub_resp, _, _, _ = _fetch_html(sub_url, timeout=6)
            if sub_resp and sub_resp.status_code < 400:
                sub_soup = BeautifulSoup(sub_resp.text or "", "html.parser")
                sub_text = _extract_clean_text(sub_soup, max_chars=3000)
                dm_name, dm_role = _extract_decision_maker_from_text(sub_text)
                if dm_name:
                    break

    result["decision_maker_name"] = dm_name
    result["decision_maker_role"] = dm_role

    # Synthesize concrete, undeniable revenue/technical leaks
    leaks = []
    if not result["uses_https"]:
        leaks.append("Site is running on insecure HTTP (browsers flag it as 'Not Secure')")
    elif not result["ssl_valid"]:
        leaks.append("SSL certificate error detected when loading the site")

    if elapsed >= 3.0:
        leaks.append(f"Slow initial server response ({elapsed}s just for HTML, causing high mobile bounce)")

    if not result["has_mobile_viewport"]:
        leaks.append("Missing mobile viewport meta tag (broken mobile layout on iPhone/Android)")

    if not result["has_online_booking"]:
        leaks.append("No instant online booking or consultation scheduler found on the homepage")

    if not result["has_lead_form"] and not result["has_online_booking"]:
        leaks.append("No lead capture form or booking flow above/below the fold")

    if not result["has_chat_widget"]:
        leaks.append("No instant chat or 24/7 AI lead-qualification assistant to capture after-hours visitors")

    if not result["has_schema_org"]:
        leaks.append("Missing Schema.org structured data (hurting Google rich results & AI search visibility)")

    if not result["meta_description"]:
        leaks.append("Missing meta description tag (weak Google search snippet click-through rate)")

    if not result["h1_tags"]:
        leaks.append("Missing primary H1 headline (unclear positioning & weak on-page SEO)")

    if not result["has_analytics"]:
        leaks.append("No Google Analytics / Tag Manager / conversion tracking pixel detected")

    if result["copyright_year"] and result["copyright_year"] <= current_year - 2:
        leaks.append(f"Footer copyright still reads {result['copyright_year']} (signals an outdated site to visitors)")

    if any(t in result["tech_stack"] for t in ("Wix", "GoDaddy Builder")):
        leaks.append(f"Built on {result['tech_stack'][0]}, limiting speed, custom funnels, and technical SEO")

    result["detected_leaks"] = leaks
    return result


def audit_lead(lead: Dict, timeout: int = 10) -> Dict:
    """Run MX verification + live website audit on a single lead record."""
    email = (lead.get("email") or "").strip()
    website = (lead.get("website") or "").strip()

    mx_info = verify_email_mx(email)
    site_audit = audit_website(website, timeout=timeout)

    return {
        "mx": mx_info,
        "site_audit": site_audit,
        "audited_at": datetime.now().isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    import json
    import os
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    leads_path = os.path.join(root, "leads.json")
    if not os.path.exists(leads_path):
        print("leads.json not found.")
        sys.exit(1)

    with open(leads_path, "r", encoding="utf-8") as f:
        leads = [l for l in json.load(f) if l.get("email")][:5]

    for lead in leads:
        print(f"\n=== Auditing: {lead.get('name')} ({lead.get('email')}) ===")
        res = audit_lead(lead)
        print("  MX:", res["mx"])
        sa = res["site_audit"]
        print(f"  Site: reachable={sa['reachable']} | {sa['load_time_sec']}s | stack={sa['tech_stack']} | DM='{sa['decision_maker_name']}'")
        print("  Leaks:", sa["detected_leaks"][:4])
