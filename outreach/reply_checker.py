# Reply Checker — Monitor your outreach inbox
# Checks for replies, bounces, and warm leads via IMAP
import email
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parseaddr
from typing import Dict, Set, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OUTREACH_DIR = os.path.dirname(os.path.abspath(__file__))
if OUTREACH_DIR not in sys.path:
    sys.path.insert(0, OUTREACH_DIR)

from config import IMAP_HOST, IMAP_USER, IMAP_PASSWORD, CHECK_DAYS

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
BOUNCE_SUBJECT_HINTS = (
    "undelivered",
    "undeliverable",
    "mail delivery failed",
    "delivery status notification",
    "returned mail",
    "address not found",
    "delivery incomplete",
)


def connect():
    """Connect to IMAP inbox."""
    mail = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    mail.login(IMAP_USER, IMAP_PASSWORD)
    mail.select("INBOX")
    return mail


def decode_subject(subject) -> str:
    """Decode email subject from various encodings."""
    if subject is None:
        return "(No Subject)"
    parts = decode_header(subject)
    result = []
    for content, encoding in parts:
        if isinstance(content, bytes):
            result.append(content.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(str(content))
    return " ".join(result)


def _extract_plain_body(raw_msg) -> str:
    body = ""
    try:
        if raw_msg.is_multipart():
            for part in raw_msg.walk():
                ctype = part.get_content_type()
                if ctype in ("text/plain", "message/delivery-status"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += "\n" + payload.decode("utf-8", errors="replace")
        else:
            payload = raw_msg.get_payload(decode=True)
            if payload:
                body = payload.decode("utf-8", errors="replace")
    except Exception:
        pass
    return body


def get_replied_and_bounced_emails(days: int = 14) -> Tuple[Set[str], Set[str]]:
    """
    Scan IMAP INBOX for the last `days` days and return:
        (replied_emails, bounced_emails)
    Used by pitch_sender.py to automatically halt follow-ups when a lead replies or bounces.
    """
    replied: Set[str] = set()
    bounced: Set[str] = set()
    own_email = (IMAP_USER or "").strip().lower()

    mail = None
    try:
        mail = connect()
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since}")')
        if status != "OK" or not messages or not messages[0]:
            return replied, bounced

        msg_ids = messages[0].split()[-250:]
        for msg_id in msg_ids:
            status, data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue

            raw = email.message_from_bytes(data[0][1])
            subject = decode_subject(raw.get("Subject"))
            subj_lower = subject.lower()
            _, sender_addr = parseaddr(raw.get("From", ""))
            sender_addr = sender_addr.strip().lower()

            is_bounce = (
                "mailer-daemon" in sender_addr
                or "postmaster" in sender_addr
                or any(h in subj_lower for h in BOUNCE_SUBJECT_HINTS)
            )

            if is_bounce:
                body = _extract_plain_body(raw)
                for found in EMAIL_PATTERN.findall(body):
                    cand = found.strip().lower()
                    if cand and cand != own_email and "mailer-daemon" not in cand and "postmaster" not in cand:
                        bounced.add(cand)
            else:
                if sender_addr and sender_addr != own_email:
                    # Any inbound email from a prospect (reply or direct message) stops follow-ups
                    replied.add(sender_addr)
    except Exception as e:
        print(f"⚠️ IMAP reply sync skipped ({e})", flush=True)
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass

    return replied, bounced


def check_replies(mail, days: int = 3):
    """Find replies and bounces from outreach targets in the last N days."""
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")

    status, messages = mail.search(None, f'(SINCE "{since}")')
    if status != "OK":
        print("Search failed")
        return

    msg_ids = messages[0].split()
    print(f"\n📬 Checking {len(msg_ids)} emails from last {days} days...\n")

    replies = []
    bounces = []

    for msg_id in msg_ids[-100:]:
        status, data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        raw = email.message_from_bytes(data[0][1])
        subject = decode_subject(raw["Subject"])
        subj_lower = subject.lower()
        sender = raw["From"] or ""

        if any(h in subj_lower for h in BOUNCE_SUBJECT_HINTS) or "mailer-daemon" in sender.lower():
            bounces.append({"from": sender, "subject": subject})
        elif "re:" in subj_lower or "fwd:" in subj_lower:
            body = _extract_plain_body(raw).strip()[:300]
            replies.append({"from": sender, "subject": subject, "body": body})

    if replies:
        print("🟢 REPLIES FOUND:")
        for r in replies:
            print(f"   From: {r['from']}")
            print(f"   Subject: {r['subject']}")
            print(f"   Preview: {r['body'][:120]}...")
            print()

    if bounces:
        print("🔴 BOUNCES:")
        for b in bounces:
            print(f"   {b['from']} — {b['subject']}")
        print()

    if not replies and not bounces:
        print("😴 No replies or bounces yet.")

    mail.logout()


if __name__ == "__main__":
    try:
        mail = connect()
        check_replies(mail, CHECK_DAYS)
    except Exception as e:
        print(f"❌ Error: {e}")
