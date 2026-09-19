
import smtplib
import time
import json
import os
import sys
from email.message import EmailMessage

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


from config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    FROM_EMAIL,
    FROM_NAME,
    BCC,
)

# IMPORTANT: Keep True while testing
PREVIEW_ONLY = False

# Maximum emails per run
MAX_EMAILS_PER_RUN = 5

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEADS_FILE = os.path.join(ROOT_DIR, "leads.json")
PITCHED_FILE = os.path.join(ROOT_DIR, "pitched.json")


def load_leads(filename: str = LEADS_FILE) -> list:
    """Load leads from JSON file."""
    if not os.path.exists(filename):
        print(f"⚠️ {filename} not found. Please run the scraper first:\n   python scraper/local_scraper.py")
        return []
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)




def generate_pitch(lead: dict) -> tuple:
    """Generate a concise, reply-focused pitch."""

    name = lead.get("name", "your business")
    city = lead.get("city", "")
    
    issue = lead.get(
        "issue",
        "a few potential conversion opportunities on your website"
    )

    impact = lead.get(
        "impact",
        "which may be making it harder for visitors to contact you"
    )

    subject = f"Quick idea for {name}"

    body = f"""Hi,

I came across {name}{f" in {city}" if city else ""} while researching businesses in your area.

I noticed {issue}. {impact}.

I work as a growth engineer helping businesses improve their websites, conversion flow, and lead generation.

I noted a few things I would improve first.

Would you like me to send you the short breakdown?

Best,
{FROM_NAME}
{FROM_EMAIL}
"""

    return subject, body


def send_pitch(lead: dict) -> bool:
    """Send personalized email pitch to a lead."""

    if not lead.get("email"):
        return False

    subject, body = generate_pitch(lead)

    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = lead["email"]
    # msg["To"] = "bhathazim230@gmail.com"
    msg["Subject"] = subject

    if BCC:
        msg["Bcc"] = BCC

    msg.set_content(body)

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(
                SMTP_HOST,
                SMTP_PORT,
                timeout=30
            ) as s:
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(
                SMTP_HOST,
                SMTP_PORT,
                timeout=30
            ) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(msg)

        print(f"✅ Sent to: {lead['email']}")
        return True

    except Exception as e:
        print(f"❌ Failed: {lead['email']} — {e}")
        return False


if __name__ == "__main__":

    leads = load_leads(LEADS_FILE)

    email_leads = [
        lead for lead in leads
        if lead.get("email")
    ]

    print(
        f"📧 Leads with email: "
        f"{len(email_leads)}/{len(leads)}"
    )

    if not email_leads:
        print("⚠️ No leads with email found.")
        sys.exit(1)

    pitched_file = PITCHED_FILE


    if os.path.exists(pitched_file):
        with open(pitched_file, "r", encoding="utf-8") as f:
            pitched = set(json.load(f))
    else:
        pitched = set()

    sent = 0
    failed = 0
    processed = 0

    for lead in email_leads:

        if lead["email"] in pitched:
            continue

        if processed >= MAX_EMAILS_PER_RUN:
            break

        subject, body = generate_pitch(lead)

        print("\n" + "=" * 60)
        print(f"BUSINESS: {lead.get('name')}")
        print(f"EMAIL:   {lead['email']}")
        print(f"SUBJECT: {subject}")
        print("-" * 60)
        print(body)
        print("=" * 60)

        if PREVIEW_ONLY:
            print("👀 PREVIEW ONLY — email NOT sent.")
            processed += 1
            continue

        if send_pitch(lead):
            pitched.add(lead["email"])
            sent += 1
        else:
            failed += 1

        processed += 1
        time.sleep(3)

    if not PREVIEW_ONLY:
        with open(pitched_file, "w", encoding="utf-8") as f:
            json.dump(list(pitched), f, indent=2)

    print(
        f"\n✅ Sent: {sent} | "
        f"❌ Failed: {failed} | "
        f"📊 Total Pitched: {len(pitched)}"
    )