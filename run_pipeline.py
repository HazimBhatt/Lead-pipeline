import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _load_json(name, default):
    path = os.path.join(ROOT_DIR, name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build_whatsapp_link(lead: dict) -> tuple:
    """
    Build a 1-click WhatsApp opener (https://wa.me/...) + personalized message
    for high-ticket Whales (especially Dubai / international brokers & clinics).
    Returns (wa_url, message_text) or ("", "").
    """
    raw_phone = (lead.get("phone") or "").strip()
    digits = re.sub(r"\D+", "", raw_phone)
    if len(digits) < 9:
        return "", ""

    qual = lead.get("qualification") or {}
    biz = (lead.get("name") or "your business").strip()
    short_biz = biz.split("|")[0].split("-")[0].strip()
    dm = (qual.get("decision_maker_first_name") or "").strip()
    leaks = qual.get("top_revenue_leaks") or (lead.get("audit") or {}).get("site_audit", {}).get("detected_leaks") or []
    top_leak = (leaks[0] if leaks else "your site's conversion & booking flow").rstrip(".")
    # Keep leak concise for WhatsApp
    if len(top_leak) > 110:
        top_leak = top_leak[:107].rsplit(" ", 1)[0] + "..."

    greeting = f"Hi {dm}" if dm else f"Hi {short_biz} team"
    msg = (
        f"{greeting} — just sent you an email too. "
        f"I noticed {top_leak.lower()} on {short_biz}'s site, so I mocked up a quick cleaner homepage concept "
        f"and 3-point fix for you. Happy to drop a screenshot or the live preview here if you'd like to see it!"
    )
    wa_url = f"https://wa.me/{digits}?text={urllib.parse.quote(msg)}"
    return wa_url, msg


def show_whatsapp_whales(limit: int = 15):
    """Print ready-to-click 1-click WhatsApp links + demo paths for your top Whales."""
    qualified = _load_json("qualified_leads.json", [])
    state = _load_json("outreach_state.json", {})
    whales = [q for q in qualified if (q.get("qualification") or {}).get("tier") == "WHALE"]

    print("\n" + "=" * 70)
    print("💬 ONE-CLICK WHATSAPP WHALE LAUNCHER (Email + WhatsApp = 30-50% Replies)")
    print("=" * 70)
    if not whales:
        print("⚠️ No Whales yet. Run: venv\\Scripts\\python.exe run_pipeline.py --audit")
        return

    shown = 0
    for w in whales:
        if shown >= limit:
            break
        wa_url, msg = build_whatsapp_link(w)
        if not wa_url:
            continue
        shown += 1
        q = w.get("qualification", {})
        em = (w.get("email") or "").lower()
        st = (state.get(em) or {}).get("status", "NEW")
        is_role = (w.get("audit") or {}).get("mx", {}).get("is_role_address", False)
        inbox_tag = "DIRECT EMAIL" if not is_role else "ROLE EMAIL"

        print(f"\n[{shown}] 🐋 {w.get('name')} ({w.get('city')}) — {q.get('score')}/10 | {q.get('estimated_deal_value')} | {st} ({inbox_tag})")
        print(f"    📞 Phone:    {w.get('phone')}")
        if w.get("demo_path"):
            print(f"    🎨 Demo:     {w.get('demo_path')}")
        print(f"    💬 Message:  {msg}")
        print(f"    🚀 Open WA:  {wa_url}")

    print("\n💡 Tip: Open the Demo HTML in Chrome, snap 1 screenshot of the redesigned hero, click the 'Open WA' link, attach the screenshot, and hit Send!")


def show_status():
    """Print an instant executive dashboard of pipeline health, direct emails, and Whale demos."""
    leads = _load_json("leads.json", [])
    qualified = _load_json("qualified_leads.json", [])
    state = _load_json("outreach_state.json", {})
    pitched = _load_json("pitched.json", [])

    whales = [q for q in qualified if (q.get("qualification") or {}).get("tier") == "WHALE"]
    direct_whales = [w for w in whales if not (w.get("audit") or {}).get("mx", {}).get("is_role_address", False)]
    good = [q for q in qualified if (q.get("qualification") or {}).get("tier") == "QUALIFIED"]
    rejected = [q for q in qualified if (q.get("qualification") or {}).get("tier") == "REJECT"]
    replied = [s for s in state.values() if s.get("status") == "REPLIED"]
    active = [s for s in state.values() if s.get("status") == "ACTIVE"]

    print("\n" + "=" * 68)
    print("🚀 GEMINI TOP-1% LEAD PIPELINE — LIVE DASHBOARD")
    print("=" * 68)
    print(f"📍 Raw Leads Scraped (leads.json):             {len(leads)}")
    print(f"🐋 WHALE Leads (9-10/10 + Custom Demo):        {len(whales)} ({len(direct_whales)} direct owner inboxes)")
    print(f"✅ QUALIFIED Leads (7-8/10):                   {len(good)}")
    print(f"⏭️ Auto-Rejected Trash Leads (<7/10):          {len(rejected)}")
    print("-" * 68)
    print(f"📨 Active Threaded Sequences (Day 3/7):        {len(active)}")
    print(f"🟢 Replied Prospects (Follow-ups Stopped):     {len(replied)}")
    print(f"📬 Total Unique Emails Pitched (All-Time):     {len(pitched)}")
    print("=" * 68)

    if whales:
        print("\n🐋 TOP WHALE PROSPECTS (Direct Emails First + 1-Click WhatsApp):")
        ordered_whales = sorted(
            whales,
            key=lambda w: (
                not (w.get("audit") or {}).get("mx", {}).get("is_role_address", False),
                (w.get("qualification") or {}).get("score", 0),
            ),
            reverse=True,
        )
        for w in ordered_whales[:8]:
            q = w.get("qualification", {})
            em = (w.get("email") or "").lower()
            st = (state.get(em) or {}).get("status", "NEW")
            is_role = (w.get("audit") or {}).get("mx", {}).get("is_role_address", False)
            tag = "DIRECT" if not is_role else "ROLE"
            wa_url, _ = build_whatsapp_link(w)
            print(
                f"   [{q.get('score')}/10 | {tag} | {st}] {w.get('name')} ({w.get('city')}) "
                f"-> {q.get('estimated_deal_value')} | {w.get('email')}"
            )
            if w.get("demo_path"):
                print(f"      Demo: {w.get('demo_path')}")
            if wa_url:
                print(f"      WA:   {wa_url}")
        print("\n💡 Run `venv\\Scripts\\python.exe run_pipeline.py --whatsapp` to see full WhatsApp messages for all Whales.")


def run_scraper():
    print("\n🔍 STAGE 1: Scraping High-LTV Google Maps Leads...")
    scraper_script = os.path.join(ROOT_DIR, "scraper", "local_scraper.py")
    subprocess.run([sys.executable, scraper_script], cwd=ROOT_DIR, check=False)


def run_audit(limit=None, reaudit=False):
    print("\n🧠 STAGE 2: Live Site Audit + MX Check + Gemini Qualification + Whale Demos...")
    from analyzer.gemini_engine import run_audit_and_qualification
    run_audit_and_qualification(limit=limit, reaudit=reaudit)


def run_send(preview=False, review=False, limit=None, skip_imap=False, direct_only=False):
    print("\n📨 STAGE 3: Gmail-Primary-Safe Threaded Outreach...")
    from outreach.pitch_sender import run_outreach
    run_outreach(
        preview=preview,
        review=review,
        limit=limit,
        skip_imap=skip_imap,
        direct_only=direct_only,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Gemini-Powered Top-1% High-Ticket Client Acquisition Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py --status                  # View dashboard, Direct Whales & WhatsApp links
  python run_pipeline.py --whatsapp                # Open 1-click WhatsApp Whale launcher + demos
  python run_pipeline.py --scrape                  # Scrape High-LTV niches from Google Maps
  python run_pipeline.py --audit --limit 25        # Audit sites + Gemini qualify + build Whale demos
  python run_pipeline.py --send --direct-only      # Send ONLY to direct owner emails (skip info@)
  python run_pipeline.py --send --review           # Approve each Whale email interactively
  python run_pipeline.py --all --limit 25          # Full daily cycle: scrape -> audit -> send
        """,
    )
    parser.add_argument("--status", action="store_true", help="Show live pipeline metrics and Whale leads")
    parser.add_argument("--whatsapp", action="store_true", help="Generate 1-click WhatsApp links + messages for top Whales")
    parser.add_argument("--scrape", action="store_true", help="Run Google Maps scraper (High-LTV niches)")
    parser.add_argument("--audit", action="store_true", help="Audit websites, verify MX, qualify with Gemini & build Whale demos")
    parser.add_argument("--send", action="store_true", help="Send threaded plain-text outreach (Email 1 + due follow-ups)")
    parser.add_argument("--review", action="store_true", help="Run outreach in interactive approval mode (implies --send)")
    parser.add_argument("--all", action="store_true", help="Run scrape -> audit -> send end-to-end")
    parser.add_argument("--preview", action="store_true", help="Dry-run preview mode for --send (sends 0 emails)")
    parser.add_argument("--direct-only", action="store_true", help="ONLY email direct personal/owner addresses (skip generic info@/care@)")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of leads audited, sent, or shown")
    parser.add_argument("--reaudit", action="store_true", help="Force re-audit of already scored leads")
    parser.add_argument("--no-imap", action="store_true", help="Skip IMAP reply sync before sending")

    args = parser.parse_args()

    if not any([args.status, args.whatsapp, args.scrape, args.audit, args.send, args.review, args.all]):
        parser.print_help()
        show_status()
        return

    try:
        if args.status:
            show_status()

        if args.whatsapp:
            show_whatsapp_whales(limit=args.limit or 15)

        if args.all or args.scrape:
            run_scraper()

        if args.all or args.audit:
            run_audit(limit=args.limit, reaudit=args.reaudit)

        if args.all or args.send or args.review:
            run_send(
                preview=args.preview,
                review=args.review,
                limit=args.limit,
                skip_imap=args.no_imap,
                direct_only=args.direct_only,
            )
    except KeyboardInterrupt:
        print("\n\n⚠️ Pipeline cleanly stopped by user (Ctrl+C). All completed work is saved on disk.")
        sys.exit(0)


if __name__ == "__main__":
    main()
