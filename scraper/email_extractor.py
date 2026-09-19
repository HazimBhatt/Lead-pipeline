import re
import urllib.parse
from typing import List, Optional, Set
import requests
import urllib3
from bs4 import BeautifulSoup

# Suppress SSL certificate warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Common image/asset extensions that naive regexes mistakenly capture (e.g. icon@2x.png)

IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".js", ".css", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".pdf", ".zip"
}

# Domains and words to ignore (frameworks, analytics, sample emails, CDNs)
IGNORED_DOMAINS = {
    "sentry.io", "wixpress.com", "wordpress.org", "cloudflare.com", "schema.org",
    "googleapis.com", "github.com", "gitlab.com", "medium.com", "gravatar.com",
    "example.com", "domain.com", "email.com", "sample.com", "yoursite.com",
    "sitename.com", "mycompany.com", "myshopify.com", "w3.org"
}

# Prefixes often used for dummy/placeholder emails
IGNORED_PREFIXES = {
    "user", "username", "yourname", "test", "demo", "sample", "example", "firstname", "name",
    "admin123", "placeholder"
}

# High-priority role prefixes for business outreach
PRIORITY_PREFIXES = {
    "info", "contact", "hello", "hi", "enquiry", "enquiries", "sales", "support",
    "help", "booking", "reservations", "reservation", "office", "admin", "marketing",
    "general", "service", "team"
}

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

CONTACT_KEYWORDS = [
    "contact", "contact-us", "contact_us", "contactus", "about", "about-us",
    "reach-us", "reach_us", "get-in-touch", "reservations", "enquiry", "enquiries"
]


def clean_email(raw_email: str) -> Optional[str]:
    """Clean and validate an extracted email address."""
    if not raw_email:
        return None
    
    email = raw_email.strip().lower()
    # Remove URL query parameters if mailto had ?subject=...
    if "?" in email:
        email = email.split("?")[0]
    email = urllib.parse.unquote(email)

    # Basic length & syntax check
    if "@" not in email or len(email) < 6 or len(email) > 80:
        return None

    user, domain = email.split("@", 1)
    
    # Strip trailing punctuation
    user = user.strip(".,;:!'\"")
    domain = domain.strip(".,;:!'\"")

    if not user or not domain or "." not in domain:
        return None

    # Check for invalid file extensions
    for ext in IGNORED_EXTENSIONS:
        if email.endswith(ext):
            return None

    # Check for ignored domains
    for ign in IGNORED_DOMAINS:
        if domain == ign or domain.endswith("." + ign):
            return None

    # Check for ignored prefixes
    if user in IGNORED_PREFIXES:
        return None

    # Basic validation of characters
    if not re.match(r"^[a-z0-9._%+-]+$", user) or not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        return None

    return f"{user}@{domain}"


def extract_emails_from_html(html: str) -> Set[str]:
    """Extract emails from mailto links and regex text search in HTML."""
    emails = set()
    if not html:
        return emails

    # 1. Parse mailto: links (highest accuracy)
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if href.lower().startswith("mailto:"):
                target = href[7:]
                cleaned = clean_email(target)
                if cleaned:
                    emails.add(cleaned)
    except Exception:
        pass

    # 2. Extract with regex across raw HTML & plain text
    matches = EMAIL_REGEX.findall(html)
    for match in matches:
        cleaned = clean_email(match)
        if cleaned:
            emails.add(cleaned)

    # 3. Handle obfuscated emails (e.g. 'info [at] domain.com' or 'info(at)domain.com')
    obfuscated_pattern = re.compile(r"\b([A-Za-z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\s+at\s+)\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", re.IGNORECASE)
    for m_user, m_domain in obfuscated_pattern.findall(html):
        cleaned = clean_email(f"{m_user}@{m_domain}")
        if cleaned:
            emails.add(cleaned)

    return emails


def find_contact_links(base_url: str, html: str, max_links: int = 3) -> List[str]:
    """Find internal links on the homepage likely to contain contact details."""
    contact_urls = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        parsed_base = urllib.parse.urlparse(base_url)
        base_domain = parsed_base.netloc.lower().replace("www.", "")

        seen = {base_url.rstrip("/")}

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            text = (a_tag.get_text() or "").strip().lower()
            href_lower = href.lower()

            # Check if text or href matches contact keywords
            is_candidate = any(kw in href_lower or kw in text for kw in CONTACT_KEYWORDS)
            if not is_candidate:
                continue

            full_url = urllib.parse.urljoin(base_url, href).split("#")[0].rstrip("/")
            parsed_target = urllib.parse.urlparse(full_url)
            target_domain = parsed_target.netloc.lower().replace("www.", "")

            # Ensure link is within the same domain (internal link)
            if target_domain == base_domain and full_url not in seen:
                seen.add(full_url)
                contact_urls.append(full_url)
                if len(contact_urls) >= max_links:
                    break
    except Exception:
        pass

    return contact_urls


def score_email(email: str, base_domain: str) -> int:
    """
    Score email to prioritize domain-matching addresses and primary business inboxes.
    """
    user, domain = email.split("@", 1)
    clean_base_domain = base_domain.lower().replace("www.", "")
    score = 10

    # 1. Domain match bonus (+50 points)
    if domain == clean_base_domain or domain.endswith("." + clean_base_domain):
        score += 50

    # 2. Priority role prefix bonus (+30 points)
    if user in PRIORITY_PREFIXES:
        score += 30
    elif any(user.startswith(pref) for pref in PRIORITY_PREFIXES):
        score += 20

    # 3. Known business email provider penalty/bonus
    if domain in {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}:
        score += 5  # Small local businesses often use Gmail
    else:
        score += 15  # Custom domains preferred for professional outreach

    return score


def extract_best_email(website: str, timeout: int = 6, max_pages: int = 3, browser_context=None) -> Optional[str]:
    """
    Given a business website URL, crawls the homepage and top contact pages to find the best email.
    Uses fast HTTP requests first, with automatic browser context fallback if available.
    Returns the highest-scoring email or empty string.
    """
    if not website:
        return ""

    if not website.startswith("http://") and not website.startswith("https://"):
        website = "https://" + website

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    discovered_emails: Set[str] = set()
    parsed_base = urllib.parse.urlparse(website)
    base_domain = parsed_base.netloc

    # 1. Fetch homepage via requests
    homepage_html = ""
    try:
        resp = requests.get(website, headers=headers, timeout=(3, timeout), allow_redirects=True, verify=False)
        if resp.status_code == 200:
            homepage_html = resp.text
            discovered_emails.update(extract_emails_from_html(homepage_html))
    except Exception:
        if website.startswith("https://"):
            try:
                http_url = "http://" + website[8:]
                resp = requests.get(http_url, headers=headers, timeout=(3, timeout), allow_redirects=True, verify=False)
                if resp.status_code == 200:
                    homepage_html = resp.text
                    discovered_emails.update(extract_emails_from_html(homepage_html))
            except Exception:
                pass

    # 1b. Fallback to browser context if requests was blocked or empty
    if not homepage_html and browser_context:
        try:
            tab = browser_context.new_page()
            tab.goto(website, wait_until="domcontentloaded", timeout=timeout * 1000)
            homepage_html = tab.content()
            discovered_emails.update(extract_emails_from_html(homepage_html))
            tab.close()
        except Exception:
            pass

    # 2. Check contact/about pages
    contact_pages = find_contact_links(website, homepage_html, max_links=max_pages)
    for page_url in contact_pages:
        page_html = ""
        try:
            resp = requests.get(page_url, headers=headers, timeout=(3, timeout), allow_redirects=True, verify=False)
            if resp.status_code == 200:
                page_html = resp.text
                discovered_emails.update(extract_emails_from_html(page_html))
        except Exception:
            pass

        if not page_html and browser_context:
            try:
                tab = browser_context.new_page()
                tab.goto(page_url, wait_until="domcontentloaded", timeout=timeout * 1000)
                discovered_emails.update(extract_emails_from_html(tab.content()))
                tab.close()
            except Exception:
                pass

    if not discovered_emails:
        return ""

    # 3. Score and pick the best email
    ranked = sorted(
        discovered_emails,
        key=lambda e: score_email(e, base_domain),
        reverse=True
    )
    return ranked[0]


def search_engine_email_fallback(business_name: str, city: str = "") -> str:
    """
    Search public web snippets for contact emails when a business website is blocked,
    unreachable, or does not list an email directly.
    """
    if not business_name or len(business_name) < 2:
        return ""

    # Clean query terms
    clean_name = re.sub(r"[^\w\s]", " ", business_name).strip()
    query = f"{clean_name} {city} email contact".strip()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
            timeout=5,
            verify=False
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            snippets = " ".join([el.get_text() for el in soup.find_all(class_="result__snippet")])
            found = extract_emails_from_html(snippets)
            if not found:
                found = extract_emails_from_html(resp.text)

            if found:
                # Rank emails, favoring ones containing parts of the business name
                name_parts = [p.lower() for p in clean_name.split() if len(p) > 3]
                ranked = sorted(
                    found,
                    key=lambda e: (
                        100 if any(part in e for part in name_parts)
                        else (50 if any(e.startswith(pref) for pref in PRIORITY_PREFIXES) else 10)
                    ),
                    reverse=True
                )
                return ranked[0]
    except Exception:
        pass

    return ""


def find_lead_email(
    website: str = "",
    business_name: str = "",
    city: str = "",
    timeout: int = 6,
    max_pages: int = 3,
    browser_context=None
) -> str:
    """
    Multi-stage high-probability email discovery:
    1. Crawl business website homepage and contact pages (requests + browser fallback).
    2. If no email found, query public web snippets using business name & city.
    """
    email = ""

    # Stage 1: Website deep scan
    if website:
        email = extract_best_email(
            website,
            timeout=timeout,
            max_pages=max_pages,
            browser_context=browser_context
        ) or ""

    # Stage 2: Web search snippet fallback
    if not email and business_name:
        email = search_engine_email_fallback(business_name, city) or ""

    return email


