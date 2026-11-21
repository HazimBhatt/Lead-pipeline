import argparse
import email
import imaplib
import json
import os
import re
import smtplib
import sys
import urllib.parse
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Dict, List, Optional, Set, Tuple

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

OUTREACH_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(OUTREACH_DIR)
LOGS_DIR = os.path.join(ROOT_DIR, "logs")

if sys.platform == "win32":
    # When launched silently via pythonw.exe, sys.stdout/sys.stderr are None -> redirect cleanly to logs/reply_watcher.log
    if sys.stdout is None or sys.stderr is None:
        os.makedirs(LOGS_DIR, exist_ok=True)
        _log_fh = open(os.path.join(LOGS_DIR, "reply_watcher.log"), "a", encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = _log_fh
        if sys.stderr is None:
            sys.stderr = _log_fh
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ReplyWatcher silent check", flush=True)
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

for p in (ROOT_DIR, OUTREACH_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
from analyzer.demo_generator import save_replied_client_brief

IMAP_HOST = getattr(config, "IMAP_HOST", "imap.gmail.com")
IMAP_USER = getattr(config, "IMAP_USER", config.SMTP_USER)
IMAP_PASSWORD = getattr(config, "IMAP_PASSWORD", config.SMTP_PASSWORD)
SMTP_HOST = config.SMTP_HOST
SMTP_PORT = config.SMTP_PORT
SMTP_USER = config.SMTP_USER
SMTP_PASSWORD = config.SMTP_PASSWORD
FROM_EMAIL = config.FROM_EMAIL
FROM_NAME = config.FROM_NAME
ALERT_EMAIL = (getattr(config, "ALERT_EMAIL", "") or FROM_EMAIL).strip()
DEMO_MODEL = getattr(config, "DEMO_MODEL", "gemini-4-hydrogen")
CHECK_DAYS = getattr(config, "CHECK_DAYS", 14)

STATE_FILE = os.path.join(ROOT_DIR, "outreach_state.json")
PITCHED_FILE = os.path.join(ROOT_DIR, "pitched.json")
QUALIFIED_FILE = os.path.join(ROOT_DIR, "qualified_leads.json")
PORTFOLIO_FILE = os.path.join(ROOT_DIR, "portfolio.json")
NOTIFIED_FILE = os.path.join(ROOT_DIR, "notified_replies.json")

BOUNCE_HINTS = (
    "undelivered", "undeliverable", "mail delivery failed",
    "delivery status notification", "returned mail", "address not found",
    "delivery incomplete", "failure notice",
)
AUTO_REPLY_HINTS = (
    "out of office", "automatic reply", "auto-reply", "autoreply",
    "away from the office", "on vacation", "thank you for contacting",
    "we have received your email", "we received your message",
    "ticket has been created", "do not reply",
)
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


class ReplyStrategyDraft(BaseModel):
    intent: str = Field(description="One of: HOT_INTERESTED, QUESTION, OBJECTION, NOT_INTERESTED")
    urgency: str = Field(description="One of: HIGH, MEDIUM, LOW")
    summary: str = Field(description="1-2 sentence analysis of what the prospect wants and how to close them")
    recommended_reply_subject: str = Field(description="Exact subject line to reply with (Re: ...)")
    recommended_reply_body: str = Field(
        description="Ready-to-copy-paste, confident, human closing reply (50-110 words) that shares proof/demo next step and proposes a quick 10-min call"
    )
    next_actions: List[str] = Field(description="2-3 crisp bullet actions the sender should take right now")


def _get_gemini_key() -> str:
    cfg_k = (getattr(config, "GEMINI_API_KEY", "") or "").strip()
    if cfg_k and cfg_k != "YOUR_GEMINI_API_KEY":
        return cfg_k
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _decode_header_str(val) -> str:
    if not val:
        return ""
    parts = decode_header(val)
    out = []
    for content, enc in parts:
        if isinstance(content, bytes):
            out.append(content.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(content))
    return " ".join(out).strip()


def _extract_body(raw_msg) -> str:
    body = ""
    try:
        if raw_msg.is_multipart():
            for part in raw_msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain" and "attachment" not in str(part.get("Content-Disposition") or "").lower():
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += payload.decode("utf-8", errors="replace") + "\n"
        else:
            payload = raw_msg.get_payload(decode=True)
            if payload:
                body = payload.decode("utf-8", errors="replace")
    except Exception:
        pass
    return body.strip()


def _is_auto_responder(raw_msg, subject: str, body: str) -> bool:
    auto_sub = (raw_msg.get("Auto-Submitted") or "").lower()
    if auto_sub and auto_sub != "no":
        return True
    if raw_msg.get("X-Autoreply") or raw_msg.get("X-Autorespond"):
        return True
    prec = (raw_msg.get("Precedence") or "").lower()
    if prec in ("bulk", "auto_reply", "junk", "list"):
        return True
    blob = (subject + "\n" + body[:400]).lower()
    return any(h in blob for h in AUTO_REPLY_HINTS)


def draft_closing_reply(
    sender_email: str,
    reply_subject: str,
    reply_body: str,
    lead_record: Optional[Dict],
) -> ReplyStrategyDraft:
    """Use gemini-4-hydrogen to classify the prospect's reply and draft a closing response."""
    portfolio = _load_json(PORTFOLIO_FILE, {})
    api_key = _get_gemini_key()

    biz_name = (lead_record or {}).get("name") or sender_email
    demo_path = (lead_record or {}).get("demo_path") or ""
    qual = (lead_record or {}).get("qualification") or {}
    leaks = qual.get("top_revenue_leaks") or []
    matched_proj = qual.get("matched_portfolio_project") or "EverFeatured"

    projects = portfolio.get("projects", [])
    proj_examples = ", ".join(f"{p.get('url')} ({p.get('traction')})" for p in projects[:2] if p.get("url"))
    proof_line = f"You can also see a couple of live platforms I built at {proj_examples}." if proj_examples else ""

    fallback = ReplyStrategyDraft(
        intent="HOT_INTERESTED",
        urgency="HIGH",
        summary=f"{biz_name} replied to your outreach. Respond within 15 minutes with their custom demo and propose a 10-minute call.",
        recommended_reply_subject=reply_subject if reply_subject.lower().startswith("re:") else f"Re: {reply_subject}",
        recommended_reply_body=(
            f"Thanks for getting back to me!\n\n"
            f"I've attached a quick preview of the redesigned concept I put together for {biz_name} "
            f"(plus the 3 quick fixes I spotted). {proof_line}\n\n"
            f"Would you be open to a quick 10-minute call tomorrow or Thursday so I can walk you through "
            f"how we'd ship this in under 2 weeks?\n\n"
            f"Best,\n{FROM_NAME}"
        ),
        next_actions=[
            f"Open {demo_path or 'demos/'} in Chrome and deploy to Vercel/Netlify (or record a 45s Loom).",
            f"Reply directly in your {FROM_EMAIL} thread within 15 minutes.",
            "Offer 2 specific 10-minute call slots in their local timezone.",
        ],
    )

    if not api_key:
        return fallback

    system_prompt = f"""You are an elite B2B sales closer advising {FROM_NAME} (Full-Stack Product & Growth Engineer closing $3k-$10k+ web/AI/SEO engagements).
A real prospect just replied to {FROM_NAME}'s cold email!
Your job:
1. Classify their exact intent (HOT_INTERESTED, QUESTION, OBJECTION, NOT_INTERESTED).
2. Draft a calm, high-status, ready-to-copy-paste reply (60-110 words) that:
   - Directly answers what they just said without sounding needy or overly salesy.
   - If they asked to see the mockup/breakdown, confirms we're sharing the custom redesign preview (leave a clear `[INSERT DEMO/LOOM LINK HERE]` marker only if a link should be pasted) and naturally cites 1-2 relevant live projects from `sender_portfolio`.
   - Ends by proposing a frictionless 10-minute walkthrough call.
   - If NOT_INTERESTED, writes a classy 2-sentence gracious exit."""

    payload = {
        "prospect_email": sender_email,
        "business_name": biz_name,
        "estimated_deal_value": qual.get("estimated_deal_value", "$3,000 - $8,000"),
        "matched_project": matched_proj,
        "has_custom_demo_ready": bool(demo_path),
        "demo_file": os.path.basename(demo_path) if demo_path else "",
        "verified_site_leaks": leaks,
        "prospect_reply_subject": reply_subject,
        "prospect_reply_body": reply_body[:2000],
        "sender_portfolio": projects,
    }

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000))
    for model_name in (DEMO_MODEL, "gemini-4-neon", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=f"Analyze this prospect reply and draft the optimal response:\n\n{json.dumps(payload, indent=2)}",
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=ReplyStrategyDraft,
                    temperature=0.4,
                ),
            )
            if getattr(resp, "parsed", None) is not None:
                return resp.parsed
            return ReplyStrategyDraft.model_validate_json(resp.text)
        except Exception:
            continue

    return fallback


def send_reply_alert(
    sender_email: str,
    reply_subject: str,
    reply_body: str,
    lead_record: Optional[Dict],
    draft: ReplyStrategyDraft,
) -> bool:
    """Send a rich, crystal-clear Hot Reply Alert + AI-drafted response to ALERT_EMAIL (from outreach/config.py)."""
    biz_name = (lead_record or {}).get("name") or sender_email
    city = (lead_record or {}).get("city") or ""
    phone = (lead_record or {}).get("phone") or "N/A"
    demo_path = (lead_record or {}).get("demo_path") or "None generated"
    qual = (lead_record or {}).get("qualification") or {}
    deal_val = qual.get("estimated_deal_value") or "High-Ticket"
    score = qual.get("score") or "N/A"

    digits = re.sub(r"\D+", "", phone)
    wa_link = f"https://wa.me/{digits}" if len(digits) >= 9 else "N/A"

    emoji = "🔥" if draft.intent == "HOT_INTERESTED" else ("💬" if draft.intent in ("QUESTION", "OBJECTION") else "📩")
    alert_subject = f"{emoji} {draft.intent}: {biz_name} ({deal_val}) replied!"

    actions_text = "\n".join(f"  {i}. {act}" for i, act in enumerate(draft.next_actions, start=1))

    client_brief_path = save_replied_client_brief(
        lead=lead_record or {"name": biz_name, "email": sender_email},
        reply_subject=reply_subject,
        reply_body=reply_body,
        closing_draft_summary=draft.summary,
        closing_draft_body=draft.recommended_reply_body,
    )
    print(f"📁 Saved full client dossier & $0 redesign prompt -> {client_brief_path}", flush=True)

    alert_body = f"""=================================================================
{emoji} INCOMING CLIENT REPLY ALERT — {biz_name}
=================================================================
INTENT:         {draft.intent} (Urgency: {draft.urgency})
BUSINESS:       {biz_name}{f' ({city})' if city else ''}
PROSPECT EMAIL: {sender_email}
PHONE / WA:     {phone} | {wa_link}
SCORE / EST:    {score}/10 | {deal_val}
CLIENT DOSSIER: {client_brief_path}
                (Contains ALL audit info + $0 copy-paste Redesign Mockup Prompt!)

-----------------------------------------------------------------
WHAT THEY SAID (Subject: {reply_subject}):
-----------------------------------------------------------------
{reply_body[:1500]}

-----------------------------------------------------------------
AI ANALYSIS:
-----------------------------------------------------------------
{draft.summary}

-----------------------------------------------------------------
READY-TO-COPY-PASTE REPLY (Reply from {FROM_EMAIL} in same thread):
-----------------------------------------------------------------
Subject: {draft.recommended_reply_subject}

{draft.recommended_reply_body}

-----------------------------------------------------------------
IMMEDIATE NEXT STEPS:
-----------------------------------------------------------------
{actions_text}
=================================================================
"""

    msg = EmailMessage()
    msg["From"] = f"LeadPipeline Alert <{FROM_EMAIL}>"
    msg["To"] = ALERT_EMAIL
    msg["Subject"] = alert_subject
    msg.set_content(alert_body, subtype="plain", charset="utf-8")

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
        print(f"🚨 Alert sent to {ALERT_EMAIL} for {biz_name} ({draft.intent})!", flush=True)
        return True
    except Exception as e:
        print(f"❌ Failed to send alert email to {ALERT_EMAIL}: {e}", flush=True)
        return False


def check_and_notify_replies(days: int = CHECK_DAYS) -> Dict:
    """
    Check IMAP INBOX for real prospect replies & bounces.
    - Marks REPLIED / BOUNCED in outreach_state.json (auto-stops follow-ups).
    - For every NEW genuine prospect reply, drafts a closing response with gemini-4-hydrogen
      and emails ALERT_EMAIL (configured in outreach/config.py) immediately.
    """
    state: Dict[str, Dict] = _load_json(STATE_FILE, {})
    pitched_list = _load_json(PITCHED_FILE, [])
    qualified_list = _load_json(QUALIFIED_FILE, [])
    notified_ids: Set[str] = set(_load_json(NOTIFIED_FILE, []))

    known_prospects: Set[str] = {k.strip().lower() for k in state.keys()} | {
        e.strip().lower() for e in pitched_list if isinstance(e, str) and e.strip()
    }
    qualified_by_email: Dict[str, Dict] = {
        (q.get("email") or "").strip().lower(): q for q in qualified_list if q.get("email")
    }

    own_emails = {
        (IMAP_USER or "").strip().lower(),
        (FROM_EMAIL or "").strip().lower(),
        (ALERT_EMAIL or "").strip().lower(),
    }

    stats = {"checked": 0, "new_replies": 0, "alerts_sent": 0, "bounces": 0, "auto_replies_ignored": 0}
    if not known_prospects:
        print("📭 No contacted prospects yet — skipping reply watcher.")
        return stats

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, 993)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        mail.select("INBOX")

        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since}")')
        if status != "OK" or not messages or not messages[0]:
            print("📭 No recent inbox messages found.")
            return stats

        msg_ids = messages[0].split()[-250:]
        stats["checked"] = len(msg_ids)
        state_dirty = False

        for num in msg_ids:
            status, data = mail.fetch(num, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue

            raw = email.message_from_bytes(data[0][1])
            msg_uid = (raw.get("Message-ID") or f"imap-{num.decode(errors='ignore')}").strip()
            subject = _decode_header_str(raw.get("Subject"))
            subj_lower = subject.lower()
            _, sender_addr = parseaddr(raw.get("From", ""))
            sender_addr = sender_addr.strip().lower()

            # 1. Detect bounces
            is_bounce = (
                "mailer-daemon" in sender_addr
                or "postmaster" in sender_addr
                or any(h in subj_lower for h in BOUNCE_HINTS)
            )
            if is_bounce:
                body = _extract_body(raw)
                for found in EMAIL_PATTERN.findall(body):
                    em = found.strip().lower()
                    if em in state and state[em].get("status") not in ("REPLIED", "BOUNCED"):
                        state[em]["status"] = "BOUNCED"
                        state[em]["bounced_at"] = datetime.now().isoformat(timespec="seconds")
                        state_dirty = True
                        stats["bounces"] += 1
                continue

            if not sender_addr or sender_addr in own_emails:
                continue

            # Match sender against our contacted prospects (exact email OR same company domain on a Re: thread)
            matched_email = sender_addr if sender_addr in known_prospects else ""
            if not matched_email and "re:" in subj_lower and "@" in sender_addr:
                s_dom = sender_addr.split("@", 1)[1]
                if s_dom not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "live.com"):
                    for cand_em in known_prospects:
                        if cand_em.endswith("@" + s_dom):
                            matched_email = cand_em
                            break

            if not matched_email:
                continue

            body = _extract_body(raw)
            if _is_auto_responder(raw, subject, body):
                stats["auto_replies_ignored"] += 1
                continue

            # Mark prospect REPLIED so follow-ups halt immediately
            if matched_email in state and state[matched_email].get("status") != "REPLIED":
                state[matched_email]["status"] = "REPLIED"
                state[matched_email]["replied_at"] = datetime.now().isoformat(timespec="seconds")
                state_dirty = True

            if msg_uid in notified_ids:
                continue

            stats["new_replies"] += 1
            lead_rec = qualified_by_email.get(matched_email) or (
                {"name": state[matched_email].get("business"), "email": matched_email}
                if matched_email in state
                else {"name": sender_addr, "email": sender_addr}
            )
            print(f"🔥 NEW PROSPECT REPLY from {sender_addr} ({subject})!", flush=True)
            draft = draft_closing_reply(sender_addr, subject, body, lead_rec)
            if send_reply_alert(sender_addr, subject, body, lead_rec, draft):
                stats["alerts_sent"] += 1
                notified_ids.add(msg_uid)
                _save_json(NOTIFIED_FILE, sorted(list(notified_ids)))

        if state_dirty:
            _save_json(STATE_FILE, state)

    except Exception as e:
        print(f"⚠️ Reply watcher error: {e}", flush=True)
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass

    print(
        f"📬 Reply Watcher Done | Checked: {stats['checked']} | "
        f"🔥 New Replies: {stats['new_replies']} | 🚨 Alerts Sent: {stats['alerts_sent']} | "
        f"🔴 Bounces: {stats['bounces']} | 🤖 Auto-Replies Ignored: {stats['auto_replies_ignored']}",
        flush=True,
    )
    return stats


def run_simulated_test_alert():
    """Send a realistic test Hot Reply Alert + AI closing draft to ALERT_EMAIL so you can verify it live."""
    sample_lead = {
        "name": "Apex Cosmetic Dentistry (Example)",
        "city": "Scottsdale AZ",
        "phone": "+1 555-019-9000",
        "email": "dr.smith@example-dental.com",
        "demo_path": os.path.join(ROOT_DIR, "demos", "apex-cosmetic-dentistry.html"),
        "qualification": {
            "score": 10,
            "estimated_deal_value": "$4,000 - $8,000",
            "matched_portfolio_project": "Flagship Demo",
            "top_revenue_leaks": [
                "5.6s mobile load time",
                "SSL certificate security warning",
                "Missing instant online implant consultation scheduler",
            ],
        },
    }
    first_name = (FROM_NAME or "there").split()[0]
    sample_reply = (
        f"Hi {first_name},\n\n"
        "Didn't realize our SSL was throwing a warning on mobile — appreciate you flagging that. "
        "Sure, send over the redesign concept you put together and what something like this typically costs.\n\n"
        "Thanks,\nDr. Smith"
    )
    print("🧪 Generating simulated closing reply with gemini-4-hydrogen & sending test alert...", flush=True)
    draft = draft_closing_reply(
        "dr.smith@example-dental.com",
        "Re: quick question about apex cosmetic dentistry",
        sample_reply,
        sample_lead,
    )
    ok = send_reply_alert(
        "dr.smith@example-dental.com",
        "Re: quick question about apex cosmetic dentistry",
        sample_reply,
        sample_lead,
        draft,
    )
    print(f"🧪 Test alert sent to {ALERT_EMAIL}: {ok}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Instant Hot-Reply Watcher & AI Closing-Reply Alerter")
    parser.add_argument("--days", type=int, default=CHECK_DAYS, help="Days of IMAP history to check")
    parser.add_argument("--test-alert", action="store_true", help="Send a simulated Hot Reply Alert to ALERT_EMAIL")
    args = parser.parse_args()

    if args.test_alert:
        run_simulated_test_alert()
    else:
        check_and_notify_replies(days=args.days)
