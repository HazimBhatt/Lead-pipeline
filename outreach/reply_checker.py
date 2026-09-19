# Reply Checker — Monitor your outreach inbox
# Checks for replies, bounces, and warm leads via IMAP
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import os
import sys

OUTREACH_DIR = os.path.dirname(os.path.abspath(__file__))
if OUTREACH_DIR not in sys.path:
    sys.path.insert(0, OUTREACH_DIR)

from config import IMAP_HOST, IMAP_USER, IMAP_PASSWORD, CHECK_DAYS


def connect():
    """Connect to IMAP inbox."""
    mail = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    mail.login(IMAP_USER, IMAP_PASSWORD)
    return mail

def decode_subject(subject):
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

def check_replies(mail, days: int = 3):
    """Find replies from outreach targets in the last N days."""
    
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    
    # Search INBOX for recent emails
    status, messages = mail.search(None, f'(SINCE "{since}")')
    if status != "OK":
        print("Search failed")
        return
    
    msg_ids = messages[0].split()
    print(f"\n📬 Checking {len(msg_ids)} emails from last {days} days...\n")
    
    replies = []
    bounces = []
    
    for msg_id in msg_ids[-100:]:  # Last 100 max
        status, data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue
        
        raw = email.message_from_bytes(data[0][1])
        subject = decode_subject(raw["Subject"])
        sender = raw["From"]
        
        # Classify
        if "Undelivered" in subject or "Mail delivery failed" in subject or "Returned mail" in subject:
            bounces.append({"from": sender, "subject": subject})
        elif "Re:" in subject or "Fwd:" in subject:
            body = ""
            if raw.is_multipart():
                for part in raw.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")[:300]
                        break
            replies.append({"from": sender, "subject": subject, "body": body})
    
    # Report
    if replies:
        print("🟢 REPLIES FOUND:")
        for r in replies:
            print(f"   From: {r['from']}")
            print(f"   Subject: {r['subject']}")
            print(f"   Preview: {r['body'][:100]}...")
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
