import argparse
import json
import os
import random
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Dict, List, Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OUTREACH_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(OUTREACH_DIR)
for p in (ROOT_DIR, OUTREACH_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
from analyzer.site_auditor import verify_email_mx
from outreach.reply_checker import get_replied_and_bounced_emails

SMTP_HOST = config.SMTP_HOST
SMTP_PORT = config.SMTP_PORT
SMTP_USER = config.SMTP_USER
SMTP_PASSWORD = config.SMTP_PASSWORD
FROM_EMAIL = config.FROM_EMAIL
FROM_NAME = config.FROM_NAME
BCC = getattr(config, "BCC", "")

# Gmail Primary Inbox Deliverability Settings
MIN_SEND_DELAY = getattr(config, "MIN_SEND_DELAY", 60)
MAX_SEND_DELAY = getattr(config, "MAX_SEND_DELAY", 150)
DAILY_SEND_LIMIT = getattr(config, "DAILY_SEND_LIMIT", 25)
MAX_EMAILS_PER_RUN = getattr(config, "MAX_EMAILS_PER_RUN", None)
FOLLOWUP_1_DAYS = getattr(config, "FOLLOWUP_1_DAYS", 3)
FOLLOWUP_2_DAYS = getattr(config, "FOLLOWUP_2_DAYS", 7)
CHECK_DAYS = getattr(config, "CHECK_DAYS", 14)
PREVIEW_ONLY = getattr(config, "PREVIEW_ONLY", False)

QUALIFIED_FILE = os.path.join(ROOT_DIR, "qualified_leads.json")
PITCHED_FILE = os.path.join(ROOT_DIR, "pitched.json")
STATE_FILE = os.path.join(ROOT_DIR, "outreach_state.json")


def wait_human_delay(min_sec: int = 60, max_sec: int = 150):
    """Wait a randomized human-like duration between sends, instantly responsive to Ctrl+C."""
    low = max(0, int(min_sec))
    high = max(low, int(max_sec))
    seconds = random.randint(low, high)
    if seconds <= 0:
        return
    for remaining in range(seconds, 0, -1):
        print(
            f"\r⏳ Gmail safety pause: {remaining:3d}s before next email (Ctrl+C to stop)...",
            end="",
            flush=True,
        )
        time.sleep(1)
    print("\r" + " " * 72 + "\r", end="", flush=True)


def load_qualified_leads(path: str = QUALIFIED_FILE) -> List[Dict]:
    if not os.path.exists(path):
        print(
            f"⚠️ {path} not found.\n"
            "   Run the Gemini audit first: python run_pipeline.py --audit"
        )
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def load_state() -> Tuple[Dict[str, Dict], set]:
    """
    Load outreach_state.json and merge legacy pitched.json so no previously contacted email
    is ever sent Email 1 twice.
    """
    state: Dict[str, Dict] = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    state = {k.strip().lower(): v for k, v in loaded.items() if k.strip()}
        except Exception:
            state = {}

    legacy_pitched = set()
    if os.path.exists(PITCHED_FILE):
        try:
            with open(PITCHED_FILE, "r", encoding="utf-8") as f:
                for em in json.load(f):
                    if isinstance(em, str) and em.strip():
                        legacy_pitched.add(em.strip().lower())
        except Exception:
            pass

    return state, legacy_pitched


def save_state(state: Dict[str, Dict], legacy_pitched: set):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    all_pitched = sorted(legacy_pitched | set(state.keys()))
    with open(PITCHED_FILE, "w", encoding="utf-8") as f:
        json.dump(all_pitched, f, indent=2)


def count_sent_today(state: Dict[str, Dict]) -> int:
    today_str = datetime.now().strftime("%Y-%m-%d")
    count = 0
    for rec in state.values():
        for ts in rec.get("sent_timestamps", []):
            if isinstance(ts, str) and ts.startswith(today_str):
                count += 1
    return count


def sync_imap_replies(state: Dict[str, Dict], legacy_pitched: set) -> Tuple[int, int]:
    """Check Gmail IMAP for replies & bounces and update state so follow-ups auto-stop."""
    print("📬 Syncing Gmail IMAP for replies & bounces...", flush=True)
    replied_set, bounced_set = get_replied_and_bounced_emails(days=CHECK_DAYS)

    new_replies = 0
    new_bounces = 0
    for em, rec in state.items():
        if em in replied_set and rec.get("status") != "REPLIED":
            rec["status"] = "REPLIED"
            rec["replied_at"] = datetime.now().isoformat(timespec="seconds")
            new_replies += 1
        elif em in bounced_set and rec.get("status") not in ("REPLIED", "BOUNCED"):
            rec["status"] = "BOUNCED"
            rec["bounced_at"] = datetime.now().isoformat(timespec="seconds")
            new_bounces += 1

    if new_replies or new_bounces:
        save_state(state, legacy_pitched)

    print(f"   🟢 New replies detected: {new_replies} | 🔴 New bounces detected: {new_bounces}", flush=True)
    return new_replies, new_bounces


def format_human_email(body: str) -> str:
    """Ensure clean human paragraph breaks (greeting, body paragraphs, signoff) in plain text."""
    txt = (body or "").strip()
    if "\n\n" in txt:
        return txt
    # Convert double-space sentence separators from single-line JSON outputs into clean paragraphs
    txt = re.sub(r"\s{2,}", "\n\n", txt)
    # Ensure greeting starts its own paragraph
    txt = re.sub(r"^(Hi [^,\n]+,)\s+", r"\1\n\n", txt)
    # Ensure signoff starts its own paragraph with name on next line
    txt = re.sub(r"\s+(Best|Thanks|Cheers),\s+", r"\n\n\1,\n", txt)
    return txt.strip()


def send_plain_text_email(
    to_email: str,
    subject: str,
    body: str,
    in_reply_to: Optional[str] = None,
    references: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Send a pure text/plain email via SMTP (Gmail Primary Inbox safe) with proper RFC threading headers.
    Returns (success, message_id).
    """
    domain = FROM_EMAIL.split("@")[-1] if "@" in FROM_EMAIL else "gmail.com"
    msg_id = make_msgid(domain=domain)

    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id

    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        refs = [r for r in (references or [in_reply_to]) if r]
        if refs:
            msg["References"] = " ".join(refs)

    if BCC:
        msg["Bcc"] = BCC

    # Pure plain text (no HTML multipart -> lands in Primary instead of Promotions)
    msg.set_content(format_human_email(body), subtype="plain", charset="utf-8")

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as s:
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.send_message(msg)

        print(f"✅ Sent to: {to_email} (Message-ID: {msg_id})", flush=True)
        return True, msg_id

    except Exception as e:
        print(f"❌ Failed: {to_email} — {e}", flush=True)
        return False, ""


def build_send_queue(
    qualified_leads: List[Dict],
    state: Dict[str, Dict],
    legacy_pitched: set,
    direct_only: bool = False,
) -> List[Dict]:
    """
    Build prioritized outreach queue:
      1. Due Follow-Ups (Stage 2 on Day 3, Stage 3 on Day 7) — highest close rate!
      2. New WHALES & Qualified leads with DIRECT / Personal Decision-Maker emails (5x reply rate!)
      3. New WHALES & Qualified leads with generic role emails (skipped if direct_only=True)
    """
    now = datetime.now()
    due_followups = []
    new_emails = []

    for lead in qualified_leads:
        qual = lead.get("qualification") or {}
        tier = qual.get("tier", "REJECT")
        score = qual.get("score", 0)
        email = (lead.get("email") or "").strip().lower()
        is_role = bool((lead.get("audit") or {}).get("mx", {}).get("is_role_address", False))

        if not email or tier == "REJECT" or score < 7:
            continue

        # Check existing state
        rec = state.get(email)
        if rec:
            status = rec.get("status", "ACTIVE")
            if status in ("REPLIED", "BOUNCED", "COMPLETED", "SKIPPED"):
                continue

            stage = rec.get("stage", 0)
            last_sent_raw = rec.get("last_sent_at", "")
            try:
                last_sent = datetime.fromisoformat(last_sent_raw)
            except Exception:
                continue

            days_since = (now - last_sent).total_seconds() / 86400.0
            orig_subject = rec.get("subject") or qual.get("email_1_subject", "")
            reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"

            if stage == 1 and days_since >= FOLLOWUP_1_DAYS and qual.get("followup_1_body"):
                due_followups.append({
                    "lead": lead,
                    "email": email,
                    "stage": 2,
                    "stage_label": f"FOLLOW-UP #1 (Day {FOLLOWUP_1_DAYS})",
                    "subject": reply_subject,
                    "body": qual["followup_1_body"].strip(),
                    "in_reply_to": rec.get("last_message_id") or rec.get("root_message_id"),
                    "references": rec.get("message_ids", []),
                    "score": score,
                    "tier": tier,
                    "is_role": is_role,
                })
            elif stage == 2 and days_since >= FOLLOWUP_2_DAYS and qual.get("followup_2_body"):
                due_followups.append({
                    "lead": lead,
                    "email": email,
                    "stage": 3,
                    "stage_label": f"FOLLOW-UP #2 BREAKUP (Day {FOLLOWUP_2_DAYS})",
                    "subject": reply_subject,
                    "body": qual["followup_2_body"].strip(),
                    "in_reply_to": rec.get("last_message_id") or rec.get("root_message_id"),
                    "references": rec.get("message_ids", []),
                    "score": score,
                    "tier": tier,
                    "is_role": is_role,
                })
        else:
            # Never double-email someone from legacy pitched.json
            if email in legacy_pitched:
                continue
            if direct_only and is_role:
                continue
            subj = (qual.get("email_1_subject") or "").strip()
            body = (qual.get("email_1_body") or "").strip()
            if not subj or not body:
                continue
            inbox_tag = "DIRECT" if not is_role else "ROLE"
            new_emails.append({
                "lead": lead,
                "email": email,
                "stage": 1,
                "stage_label": f"EMAIL #1 ({tier} · {score}/10 · {inbox_tag})",
                "subject": subj,
                "body": body,
                "in_reply_to": None,
                "references": [],
                "score": score,
                "tier": tier,
                "is_role": is_role,
            })

    # Prioritize: Direct personal emails FIRST (not is_role), then highest score (10 -> 9 -> 8 -> 7)
    due_followups.sort(key=lambda x: (not x["is_role"], x["score"]), reverse=True)
    new_emails.sort(key=lambda x: (not x["is_role"], x["score"]), reverse=True)

    return due_followups + new_emails


def run_outreach(
    preview: bool = False,
    review: bool = False,
    limit: Optional[int] = None,
    skip_imap: bool = False,
    min_delay: Optional[int] = None,
    max_delay: Optional[int] = None,
    direct_only: bool = False,
):
    qualified_leads = load_qualified_leads()
    if not qualified_leads:
        return

    state, legacy_pitched = load_state()

    if not skip_imap and not preview:
        sync_imap_replies(state, legacy_pitched)

    sent_today = count_sent_today(state)
    remaining_daily_quota = max(0, DAILY_SEND_LIMIT - sent_today)

    queue = build_send_queue(qualified_leads, state, legacy_pitched, direct_only=direct_only)

    print("=" * 65)
    print(f"🎯 Qualified / Whale leads loaded: {len(qualified_leads)}")
    mode_tag = "DIRECT OWNER EMAILS ONLY" if direct_only else "Direct Owner Emails FIRST, then Role"
    print(f"📨 Ready to send right now:        {len(queue)} ({mode_tag})")
    print(f"🛡️ Gmail Daily Cap:                {sent_today}/{DAILY_SEND_LIMIT} sent today ({remaining_daily_quota} remaining)")
    delay_lo = MIN_SEND_DELAY if min_delay is None else min_delay
    delay_hi = MAX_SEND_DELAY if max_delay is None else max_delay
    print(f"⏱️ Human Pacing Delay:             {delay_lo}-{delay_hi}s randomized")
    print("=" * 65)

    if not queue:
        print("\n🎉 No pending emails or due follow-ups right now! Run --audit on new leads or check back tomorrow.")
        return

    if not preview and remaining_daily_quota == 0:
        print(
            f"\n🛡️ Daily Gmail safety cap ({DAILY_SEND_LIMIT}/day) reached for today to protect Primary Inbox placement.\n"
            "   Run again tomorrow — or increase DAILY_SEND_LIMIT in outreach/config.py once on a warmed custom domain."
        )
        return

    cap = len(queue)
    if not preview:
        cap = min(cap, remaining_daily_quota)
    if MAX_EMAILS_PER_RUN and MAX_EMAILS_PER_RUN > 0:
        cap = min(cap, MAX_EMAILS_PER_RUN)
    if limit and limit > 0:
        cap = min(cap, limit)

    batch = queue[:cap]
    sent = 0
    failed = 0
    skipped = 0

    try:
        for idx, item in enumerate(batch, start=1):
            lead = item["lead"]
            qual = lead.get("qualification", {})
            email = item["email"]
            demo_path = lead.get("demo_path") or ""

            print("\n" + "=" * 65)
            print(f"[{idx}/{len(batch)}] {item['stage_label']} | {lead.get('name')} ({lead.get('city') or ''})")
            print(f"EMAIL:    {email}")
            print(f"ANGLE:    {qual.get('recommended_service_angle')} | Est: {qual.get('estimated_deal_value')}")
            print(f"PROJECT:  {qual.get('matched_portfolio_project')}")
            if demo_path:
                print(f"🐋 DEMO:   {demo_path}")
            print(f"SUBJECT:  {item['subject']}")
            if item["in_reply_to"]:
                print(f"THREAD:   In-Reply-To {item['in_reply_to']}")
            print("-" * 65)
            print(format_human_email(item["body"]))
            print("=" * 65)

            if review:
                choice = input("👉 Action — [y] Send | [s] Skip permanently | [n] Skip for now | [q] Quit: ").strip().lower()
                if choice == "q":
                    print("🛑 Stopping outreach cleanly.")
                    break
                if choice == "s":
                    state[email] = {
                        **(state.get(email) or {}),
                        "status": "SKIPPED",
                        "business": lead.get("name"),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    save_state(state, legacy_pitched)
                    skipped += 1
                    print("⏭️ Marked skipped permanently.")
                    continue
                if choice != "y":
                    skipped += 1
                    continue

            if preview or PREVIEW_ONLY:
                print("👀 PREVIEW MODE — email NOT sent.")
                continue

            # Final MX safety check before SMTP
            cached_mx_valid = (lead.get("audit") or {}).get("mx", {}).get("valid")
            if cached_mx_valid is False:
                print(f"⏭️ Skipped {email}: failed MX hygiene check.")
                skipped += 1
                continue
            if cached_mx_valid is None:
                mx_check = verify_email_mx(email)
                if not mx_check["valid"]:
                    print(f"⏭️ Skipped {email}: {mx_check['reason']}")
                    skipped += 1
                    continue

            ok, msg_id = send_plain_text_email(
                to_email=email,
                subject=item["subject"],
                body=item["body"],
                in_reply_to=item["in_reply_to"],
                references=item["references"],
            )

            if ok:
                now_iso = datetime.now().isoformat(timespec="seconds")
                rec = state.get(email) or {
                    "business": lead.get("name"),
                    "city": lead.get("city"),
                    "score": item["score"],
                    "tier": item["tier"],
                    "demo_path": demo_path,
                    "subject": item["subject"],
                    "root_message_id": msg_id,
                    "message_ids": [],
                    "sent_timestamps": [],
                }
                rec["stage"] = item["stage"]
                rec["status"] = "COMPLETED" if item["stage"] >= 3 else "ACTIVE"
                rec["last_sent_at"] = now_iso
                rec["last_message_id"] = msg_id
                rec.setdefault("message_ids", []).append(msg_id)
                rec.setdefault("sent_timestamps", []).append(now_iso)
                state[email] = rec
                save_state(state, legacy_pitched)
                sent += 1
            else:
                failed += 1

            if idx < len(batch):
                wait_human_delay(delay_lo, delay_hi)

        print("\n🎉 Batch complete!")

    except KeyboardInterrupt:
        print("\n\n⚠️ Outreach paused by user (Ctrl+C).")
        print(f"✅ All {sent} sent emails are safely saved in outreach_state.json & pitched.json.")

    finally:
        print(
            f"\n📊 Session Summary: Sent: {sent} | Failed: {failed} | Skipped: {skipped} | "
            f"Sent Today: {count_sent_today(state)}/{DAILY_SEND_LIMIT}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gmail-Primary-Safe Threaded Sniper Outreach Sender")
    parser.add_argument("--preview", action="store_true", help="Preview emails without sending")
    parser.add_argument("--review", action="store_true", help="Interactively approve each email before sending")
    parser.add_argument("--limit", type=int, default=None, help="Max emails to send in this run")
    parser.add_argument("--no-imap", action="store_true", help="Skip IMAP reply/bounce sync")
    parser.add_argument("--direct-only", action="store_true", help="ONLY email direct personal/owner addresses (skip info@, care@, office@)")
    parser.add_argument("--min-delay", type=int, default=None, help="Override min delay seconds")
    parser.add_argument("--max-delay", type=int, default=None, help="Override max delay seconds")
    args = parser.parse_args()

    run_outreach(
        preview=args.preview,
        review=args.review,
        limit=args.limit,
        skip_imap=args.no_imap,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        direct_only=args.direct_only,
    )
