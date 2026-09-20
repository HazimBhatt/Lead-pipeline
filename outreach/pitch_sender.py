
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


OUTREACH_DIR = os.path.dirname(os.path.abspath(__file__))
if OUTREACH_DIR not in sys.path:
    sys.path.insert(0, OUTREACH_DIR)

import config

SMTP_HOST = config.SMTP_HOST
SMTP_PORT = config.SMTP_PORT
SMTP_USER = config.SMTP_USER
SMTP_PASSWORD = config.SMTP_PASSWORD
FROM_EMAIL = config.FROM_EMAIL
FROM_NAME = config.FROM_NAME
BCC = getattr(config, "BCC", "")

# Rate limiting (seconds between consecutive sends) - Defaults to 30s
SEND_DELAY = getattr(config, "SEND_DELAY", 30)

# Optional cap per run (None or 0 = runs continuously until all leads are contacted)
MAX_EMAILS_PER_RUN = getattr(config, "MAX_EMAILS_PER_RUN", None)

# IMPORTANT: Set to True for testing without sending real emails
PREVIEW_ONLY = getattr(config, "PREVIEW_ONLY", False)

ROOT_DIR = os.path.dirname(OUTREACH_DIR)
LEADS_FILE = os.path.join(ROOT_DIR, "leads.json")
PITCHED_FILE = os.path.join(ROOT_DIR, "pitched.json")


def wait_between_emails(seconds: int = 30):
    """Wait with a responsive live countdown between email sends (press Ctrl+C to stop)."""
    if seconds <= 0:
        return
    for remaining in range(seconds, 0, -1):
        print(f"\r⏳ Waiting {remaining:2d}s before next email (press Ctrl+C to stop)...", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 65 + "\r", end="", flush=True)


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
        if lead.get("email") and lead.get("email").strip()
    ]

    pitched_file = PITCHED_FILE

    if os.path.exists(pitched_file):
        try:
            with open(pitched_file, "r", encoding="utf-8") as f:
                pitched = {e.strip().lower() for e in json.load(f) if isinstance(e, str) and e.strip()}
        except Exception:
            pitched = set()
    else:
        pitched = set()

    # Filter uncontacted leads & deduplicate within this run
    uncontacted_leads = []
    seen_in_this_run = set()

    for lead in email_leads:
        email = lead.get("email", "").strip().lower()
        if not email or email in pitched or email in seen_in_this_run:
            continue
        seen_in_this_run.add(email)
        uncontacted_leads.append(lead)

    total_pending = len(uncontacted_leads)

    print("=" * 60)
    print(f"📧 Total leads in database:   {len(leads)}")
    print(f"📬 Already pitched:          {len(pitched)}")
    print(f"🎯 Ready to pitch:           {total_pending} uncontacted leads")
    print(f"⏱️ Delay between emails:     {SEND_DELAY} seconds")
    print("=" * 60)

    if total_pending == 0:
        print("\n🎉 All leads with emails have already been contacted! No emails left to send.")
        sys.exit(0)

    total_to_process = total_pending
    if MAX_EMAILS_PER_RUN and MAX_EMAILS_PER_RUN > 0:
        total_to_process = min(total_pending, MAX_EMAILS_PER_RUN)
        print(f"⚙️ Cap active: sending up to {total_to_process} emails this run.\n")
    else:
        print(f"🚀 Running continuously until all {total_to_process} uncontacted leads are emailed (or until Ctrl+C).\n")

    sent = 0
    failed = 0

    try:
        for idx, lead in enumerate(uncontacted_leads[:total_to_process], start=1):
            email = lead.get("email", "").strip().lower()

            subject, body = generate_pitch(lead)

            print("\n" + "=" * 60)
            print(f"[{idx}/{total_to_process}] BUSINESS: {lead.get('name')}")
            print(f"EMAIL:   {email}")
            print(f"SUBJECT: {subject}")
            print("-" * 60)
            print(body)
            print("=" * 60)

            if PREVIEW_ONLY:
                print("👀 PREVIEW ONLY — email NOT sent.")
            else:
                if send_pitch(lead):
                    pitched.add(email)
                    sent += 1
                    # Save pitched email immediately so it's never emailed again even if interrupted
                    with open(pitched_file, "w", encoding="utf-8") as f:
                        json.dump(sorted(list(pitched)), f, indent=2)
                else:
                    failed += 1

            # Only delay if there are more emails left to send in this run
            if idx < total_to_process:
                wait_between_emails(SEND_DELAY)

        print("\n🎉 All available leads have been contacted! Outreach completed.")

    except KeyboardInterrupt:
        print("\n\n⚠️ Process stopped by user (Ctrl+C).")
        print(f"✅ Every email sent up to this point ({sent} sent) is already recorded in {pitched_file}.")
        print("   You can run pitch_sender.py again anytime to pick up with the remaining leads.")

    finally:
        remaining_now = total_pending - (sent + failed)
        print(
            f"\n📊 Session Summary: Sent: {sent} | "
            f"Failed: {failed} | "
            f"Remaining uncontacted: {max(0, remaining_now)} | "
            f"Total Pitched (all-time): {len(pitched)}"
        )