import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ANALYZER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(ANALYZER_DIR)
OUTREACH_DIR = os.path.join(ROOT_DIR, "outreach")

for p in (ROOT_DIR, OUTREACH_DIR, ANALYZER_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from analyzer.site_auditor import audit_lead

PORTFOLIO_FILE = os.path.join(ROOT_DIR, "portfolio.json")
LEADS_FILE = os.path.join(ROOT_DIR, "leads.json")
QUALIFIED_FILE = os.path.join(ROOT_DIR, "qualified_leads.json")
PITCHED_FILE = os.path.join(ROOT_DIR, "pitched.json")
STATE_FILE = os.path.join(ROOT_DIR, "outreach_state.json")

# Ultra-cost-efficient fallback chain for bulk lead scoring & 50-word emails (deprecated 2.5-flash removed)
MODEL_FALLBACK_CHAIN = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-4-hydrogen",
]

SPAM_TRIGGER_PHRASES = [
    "100% free",
    "act now",
    "limited time",
    "guaranteed",
    "boost your roi",
    "skyrocket",
    "game-changer",
    "game changer",
    "dear sir",
    "dear madam",
    "to whom it may concern",
    "hope this email finds you",
    "hope this finds you well",
    "click here",
    "exclusive deal",
    "no obligation",
    "risk-free",
    "double your revenue",
    "triple your",
]


class LeadQualificationResult(BaseModel):
    score: int = Field(ge=1, le=10, description="High-ticket $2k-$10k+ client score (1=trash, 10=dream whale)")
    tier: str = Field(description="REJECT, QUALIFIED, or WHALE")
    estimated_deal_value: str = Field(description="e.g. '$3,000 - $7,000'")
    score_reasoning: str = Field(description="1-2 concise sentences on budget & fixable pain")
    decision_maker_first_name: str = Field(default="", description="Verified first name or 'Dr. LastName' only, else ''")
    top_revenue_leaks: List[str] = Field(description="2-3 verified audit leaks")
    recommended_service_angle: str = Field(description="Specific specialist angle (never generalist)")
    matched_portfolio_project: str = Field(description="Exact project name from sender_portfolio")
    email_1_subject: str = Field(description="2-5 word casual lowercase subject")
    email_1_body: str = Field(description="45-65 word plain-text Email 1, ZERO URLs/domains, soft mockup permission hook")
    followup_1_body: str = Field(description="Day 3 plain-text bump (<55 words) with 1 extra leak + matched project name")
    followup_2_body: str = Field(description="Day 7 polite breakup (<40 words)")


def _load_outreach_config():
    """Load settings from outreach/config.py cleanly."""
    try:
        import config as outreach_cfg
        return outreach_cfg
    except Exception:
        return None


def get_gemini_api_key() -> str:
    cfg = _load_outreach_config()
    cfg_key = (getattr(cfg, "GEMINI_API_KEY", "") or "").strip() if cfg else ""
    if cfg_key and cfg_key != "YOUR_GEMINI_API_KEY":
        return cfg_key
    env_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    return env_key


def load_portfolio(path: str = PORTFOLIO_FILE) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# Strict Tier-1 High-Paying Markets Guard (blocks Asian & low-budget regions automatically)
NON_TIER1_BLOCKED_MARKERS = {
    "ajman", "dubai", "sharjah", "abu dhabi", "uae", "united arab emirates",
    "kuala lumpur", "malaysia", "jakarta", "indonesia", "singapore", "india",
    "pakistan", "bangladesh", "philippines", "manila", "vietnam", "thailand",
    "bangkok", "hiroshima", "sapporo", "tokyo", "japan", "china", "korea",
    "reykjanesbær", "hafnarfjörður", "keflavik", "iceland",
}


def is_tier1_lead(lead: Dict) -> bool:
    """Ensure only affluent Tier-1 Western/European leads (USA, UK, AU, CA, CH) enter outreach."""
    blob = " ".join([
        str(lead.get("city") or ""),
        str(lead.get("address") or ""),
    ]).lower()
    if any(b in blob for b in NON_TIER1_BLOCKED_MARKERS):
        return False
    return True


def validate_email_copy(subject: str, body: str, is_first_email: bool = True) -> List[str]:
    """
    Strict Gmail Primary Inbox anti-spam guardrail.
    Enforces ultra-human 45-80 word Email #1 copy with ZERO URLs and ZERO fluff.
    Returns a list of violation descriptions (empty list = PASS).
    """
    violations = []
    subj = (subject or "").strip()
    txt = (body or "").strip()
    lower = txt.lower()

    if not subj or len(subj) > 60:
        violations.append("Subject is empty or too long (>60 chars)")
    if "!" in subj or subj.isupper():
        violations.append("Subject uses exclamation marks or ALL CAPS (spam trigger)")

    words = txt.split()
    max_words = 82 if is_first_email else 70
    if len(words) < 18:
        violations.append("Email body is too short (<18 words)")
    if len(words) > max_words:
        violations.append(f"Email body is too long ({len(words)} words; max {max_words} for punchy human replies)")

    # Unfilled placeholder check
    if re.search(r"\[[^\]]{2,30}\]|\{[^\}]{2,30}\}", txt):
        violations.append("Contains unfilled placeholder brackets like [Name] or {Company}")

    # Zero links in Email 1 (critical for Gmail Primary Inbox)
    if is_first_email:
        if re.search(r"https?://|www\.", lower):
            violations.append("Email 1 contains a URL (strictly forbidden for Gmail Primary Inbox)")
        if re.search(r"\b[a-z0-9-]+\.(com|net|xyz|io|co|org|ae)\b", lower):
            violations.append("Email 1 contains a naked domain link (zero links in Email 1)")

    # Spam trigger phrases
    for phrase in SPAM_TRIGGER_PHRASES:
        if phrase in lower:
            violations.append(f"Contains spam/generic phrase: '{phrase}'")

    if re.search(r"^(hi|hello|hey)\s{2,},", lower):
        violations.append("Broken spacing in greeting")

    return violations


class GeminiSniperEngine:
    """Gemini-powered high-ticket lead qualification, portfolio matching, and copy engine."""

    def __init__(self, api_key: Optional[str] = None, preferred_model: Optional[str] = None):
        cfg = _load_outreach_config()
        self.api_key = (api_key or get_gemini_api_key()).strip()
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY not found!\n"
                "Set it in outreach/config.py (GEMINI_API_KEY = '...') OR as an environment variable."
            )
        self.client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(timeout=35_000),
        )
        self.preferred_model = preferred_model or (getattr(cfg, "GEMINI_MODEL", "gemini-3.5-flash-lite") if cfg else "gemini-3.5-flash-lite")
        self.demo_model = getattr(cfg, "DEMO_MODEL", "gemini-4-hydrogen") if cfg else "gemini-4-hydrogen"
        self.min_score = getattr(cfg, "MIN_LEAD_SCORE", 7) if cfg else 7
        self.whale_threshold = getattr(cfg, "WHALE_SCORE_THRESHOLD", 9) if cfg else 9
        self.portfolio = load_portfolio()
        self.active_model = self._resolve_working_model()

    def _resolve_working_model(self) -> str:
        """Build ordered candidate list starting with preferred model; fallback automatically on runtime error."""
        candidates = [self.preferred_model] + [m for m in MODEL_FALLBACK_CHAIN if m != self.preferred_model]
        try:
            live_models = [
                m.name.replace("models/", "")
                for m in self.client.models.list()
                if getattr(m, "name", None)
            ]
            for cand in candidates:
                if cand in live_models:
                    return cand
            # Pick best available flash/hydrogen/neon model dynamically
            for pattern in ("gemini-3.8-flash", "gemini-4-hydrogen", "gemini-3.5-flash", "gemini-2.5-flash", "flash"):
                for lm in live_models:
                    if pattern in lm and "tts" not in lm and "image" not in lm and "audio" not in lm:
                        return lm
        except Exception:
            pass
        return self.preferred_model

    def _call_gemini_structured(self, prompt: str, system_instruction: str) -> LeadQualificationResult:
        """Call Gemini with Pydantic structured output, automatic model fallback, and retry backoff."""
        models_to_try = [self.active_model] + [m for m in MODEL_FALLBACK_CHAIN if m != self.active_model]
        last_err = None

        for model_name in models_to_try:
            for attempt in range(2):
                try:
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            response_mime_type="application/json",
                            response_schema=LeadQualificationResult,
                            temperature=0.45,
                        ),
                    )
                    self.active_model = model_name
                    if getattr(response, "parsed", None) is not None:
                        return response.parsed
                    return LeadQualificationResult.model_validate_json(response.text)
                except Exception as e:
                    last_err = e
                    err_str = str(e).lower()
                    # Fail over immediately to next healthy model on 404/503/504/high-demand/timeout
                    if any(k in err_str for k in ("404", "503", "504", "not found", "not supported", "invalid argument", "unavailable", "deadline", "timeout", "high demand", "overloaded")):
                        break
                    time.sleep(2 ** (attempt + 1))

        raise RuntimeError(f"Gemini API failed across all fallback models: {last_err}")

    def qualify_and_craft(self, lead: Dict, audit: Dict) -> LeadQualificationResult:
        """Analyze audited lead with Gemini, score 1-10, match portfolio project, and write 3-step sequence."""
        builder = self.portfolio.get("builder", {})
        builder_name = builder.get("name") or "Growth Engineer"
        builder_first = builder_name.split()[0]
        projects = self.portfolio.get("projects", [])
        sa = audit.get("site_audit", {})

        # Compact 1-line portfolio summary (cuts ~400 input tokens per lead!)
        compact_portfolio = [
            f"{p.get('name')} ({p.get('traction', '')}): {p.get('one_liner', '')}"
            for p in projects
        ]

        system_instruction = f"""B2B outbound strategist for {builder_name} ({builder.get('role', 'Product & Growth Engineer')}).
Goal: score Tier-1 leads (1-10) & write ultra-short plain-text Gmail Primary emails for $2k-$10k+ deals.

SCORING:
- 9-10 (WHALE): High-LTV business + strong reviews/city + clear fixable site/booking/SEO leaks.
- 7-8 (QUALIFIED): Solid budget + 1-2 real fixable leaks.
- 1-6 (REJECT): Tiny low-margin businesses, chains, or already modern fast sites with booking + schema + analytics.

EMAIL RULES (STRICT):
1. Email 1: 3-4 short sentences (45-65 words, max 75), blank line between paragraphs, ZERO URLs/domains, NO resume/project pitch in Email 1, NO spam words, NO [brackets].
2. Email 1 format:
   - Greeting: verified `Hi Dr. [Last],` or `Hi [First],` or `Hi [Short Company] team,`
   - Observation: 1-2 verified audit facts (load time, SSL, missing booking/H1/schema). Never invent facts.
   - Hook: "I put together a quick redesign mockup showing how this could look on mobile — want me to send it over?"
   - Signoff: `Best,\n{builder_first}`
3. Subject: 2-5 lowercase words, no exclamation marks.
4. Follow-up 1 (Day 3, <55 words): casual bump + 1 extra leak + brief mention of matched project name from sender_portfolio.
5. Follow-up 2 (Day 7, <40 words): polite zero-pressure breakup."""

        payload = {
            "prospect": {
                "name": lead.get("name"),
                "city": lead.get("city"),
                "rating": lead.get("rating"),
                "reviews": lead.get("reviews"),
                "role_email": audit.get("mx", {}).get("is_role_address"),
            },
            "audit": {
                "ok": sa.get("reachable"),
                "load_s": sa.get("load_time_sec"),
                "https": sa.get("uses_https"),
                "ssl": sa.get("ssl_valid"),
                "booking": sa.get("has_online_booking"),
                "chat": sa.get("has_chat_widget"),
                "schema": sa.get("has_schema_org"),
                "dm": sa.get("decision_maker_name"),
                "leaks": (sa.get("detected_leaks") or [])[:4],
                "copy": (sa.get("homepage_snippet") or "")[:500],
            },
            "sender_portfolio": compact_portfolio,
        }

        prompt = f"Qualify & craft 3-step plain-text JSON:\n{json.dumps(payload, ensure_ascii=False)}"

        result = self._call_gemini_structured(prompt, system_instruction)

        if result.score >= self.whale_threshold:
            result.tier = "WHALE"
        elif result.score >= self.min_score:
            result.tier = "QUALIFIED"
        else:
            result.tier = "REJECT"

        # Token-smart repair: ONLY burn a second call on critical Primary-Inbox violations (URL/brackets/spam)
        if result.tier != "REJECT":
            violations = validate_email_copy(result.email_1_subject, result.email_1_body, is_first_email=True)
            critical = [v for v in violations if any(k in v.lower() for k in ("url", "domain", "bracket", "spam", "caps"))]
            if critical:
                repair_prompt = (
                    f"{prompt}\n\nFIX THESE CRITICAL VIOLATIONS ONLY:\n"
                    + "\n".join(f"- {v}" for v in critical)
                )
                result = self._call_gemini_structured(repair_prompt, system_instruction)
                if result.score >= self.whale_threshold:
                    result.tier = "WHALE"
                elif result.score >= self.min_score:
                    result.tier = "QUALIFIED"
                else:
                    result.tier = "REJECT"

        return result


def _load_json_list(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _load_already_pitched_emails() -> set:
    pitched = set()
    if os.path.exists(PITCHED_FILE):
        try:
            with open(PITCHED_FILE, "r", encoding="utf-8") as f:
                for e in json.load(f):
                    if isinstance(e, str) and e.strip():
                        pitched.add(e.strip().lower())
        except Exception:
            pass
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                if isinstance(state, dict):
                    for em in state.keys():
                        pitched.add(em.strip().lower())
        except Exception:
            pass
    return pitched


def _save_qualified_records(records_by_email: Dict[str, Dict], path: str = QUALIFIED_FILE):
    # Sort Whales and highest scores to the very top!
    ordered = sorted(
        records_by_email.values(),
        key=lambda r: (
            r.get("qualification", {}).get("score", 0),
            r.get("reviews") or 0,
        ),
        reverse=True,
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)


def run_audit_and_qualification(limit: Optional[int] = None, reaudit: bool = False) -> Dict:
    """
    Audit uncontacted leads from leads.json, verify MX records, qualify with Gemini,
    generate custom 3-step sequences + Whale HTML demos, and save incrementally to qualified_leads.json.
    """
    leads = _load_json_list(LEADS_FILE)
    if not leads:
        print("⚠️ No leads found in leads.json. Run the scraper first.")
        return {"processed": 0, "whales": 0, "qualified": 0, "rejected": 0, "bad_mx": 0}

    existing_records = _load_json_list(QUALIFIED_FILE)
    records_by_email: Dict[str, Dict] = {}
    for rec in existing_records:
        em = (rec.get("email") or "").strip().lower()
        if em:
            records_by_email[em] = rec

    already_pitched = _load_already_pitched_emails()
    engine = GeminiSniperEngine()
    portfolio_projects = {p["name"].lower(): p for p in engine.portfolio.get("projects", [])}
    builder_info = engine.portfolio.get("builder", {})

    # Collect pending Tier-1 leads with emails (newest scraped leads FIRST; strictly skip non-Tier-1/Asian regions)
    pending = []
    seen = set()
    for lead in reversed(leads):
        em = (lead.get("email") or "").strip().lower()
        if not em or em in seen or em in already_pitched:
            continue
        if not is_tier1_lead(lead):
            continue
        if not reaudit and em in records_by_email:
            continue
        seen.add(em)
        pending.append(lead)

    if limit and limit > 0:
        pending = pending[:limit]

    print("=" * 65)
    print(f"🧠 Gemini Sniper Engine Active | Model: {engine.active_model}")
    print(f"📋 Pending leads to audit & score: {len(pending)}")
    print(f"🎯 Min Score: {engine.min_score}/10 | 🐋 Whale Demo Threshold: {engine.whale_threshold}/10")
    print("=" * 65)

    if not pending:
        print("✅ All uncontacted leads are already audited & scored in qualified_leads.json!")
        return {"processed": 0, "whales": 0, "qualified": 0, "rejected": 0, "bad_mx": 0}

    stats = {"processed": 0, "whales": 0, "qualified": 0, "rejected": 0, "bad_mx": 0}

    try:
        for idx, lead in enumerate(pending, start=1):
            name = lead.get("name") or "Unknown"
            email = lead.get("email", "").strip().lower()
            print(f"\n[{idx}/{len(pending)}] 🔍 Auditing: {name} ({email})...", flush=True)

            audit = audit_lead(lead)
            mx = audit["mx"]
            sa = audit["site_audit"]

            if not mx["valid"]:
                print(f"   ❌ Skipped (Bad Email/MX): {mx['reason']}", flush=True)
                stats["bad_mx"] += 1
                stats["processed"] += 1
                records_by_email[email] = {
                    **lead,
                    "email": email,
                    "audit": audit,
                    "qualification": {
                        "score": 0,
                        "tier": "REJECT",
                        "score_reasoning": f"Email failed MX/hygiene check: {mx['reason']}",
                    },
                    "demo_path": "",
                    "processed_at": datetime.now().isoformat(timespec="seconds"),
                }
                _save_qualified_records(records_by_email)
                continue

            print(
                f"   🌐 Site: {'OK' if sa['reachable'] else 'DOWN/SOCIAL'} | "
                f"{sa['load_time_sec']}s | Leaks: {len(sa['detected_leaks'])} | "
                f"DM: {sa['decision_maker_name'] or 'N/A'}",
                flush=True,
            )

            try:
                qual = engine.qualify_and_craft(lead, audit)
            except Exception as err:
                print(f"   ⚠️ Gemini error for {name}: {err}", flush=True)
                continue

            qual_dict = qual.model_dump()
            demo_path = ""

            if qual.tier == "WHALE":
                stats["whales"] += 1
                # ZERO-WASTE MODE: Demos & briefs are ONLY created once the prospect actually replies!
                print(
                    f"   🐋 WHALE ({qual.score}/10 | {qual.estimated_deal_value}) | "
                    f"Project: {qual.matched_portfolio_project} | Subj: '{qual.email_1_subject}' (Demo deferred until reply)",
                    flush=True,
                )
            elif qual.tier == "QUALIFIED":
                stats["qualified"] += 1
                print(
                    f"   ✅ QUALIFIED ({qual.score}/10 | {qual.estimated_deal_value}) | "
                    f"Project: {qual.matched_portfolio_project} | Subj: '{qual.email_1_subject}'",
                    flush=True,
                )
            else:
                stats["rejected"] += 1
                print(f"   ⏭️ REJECTED ({qual.score}/10): {qual.score_reasoning[:100]}", flush=True)

            records_by_email[email] = {
                **lead,
                "email": email,
                "audit": audit,
                "qualification": qual_dict,
                "demo_path": demo_path,
                "processed_at": datetime.now().isoformat(timespec="seconds"),
            }
            stats["processed"] += 1
            _save_qualified_records(records_by_email)

    except KeyboardInterrupt:
        print("\n\n⚠️ Audit paused by user (Ctrl+C). Every lead scored so far is saved in qualified_leads.json!")

    print("\n" + "=" * 65)
    print(
        f"📊 Audit Summary: Processed: {stats['processed']} | "
        f"🐋 Whales: {stats['whales']} | "
        f"✅ Qualified: {stats['qualified']} | "
        f"⏭️ Rejected: {stats['rejected']} | "
        f"❌ Bad MX: {stats['bad_mx']}"
    )
    print(f"💾 Saved to: {QUALIFIED_FILE}")
    print("=" * 65)
    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Audit leads & qualify with Gemini AI")
    parser.add_argument("--limit", type=int, default=None, help="Max leads to audit in this run")
    parser.add_argument("--reaudit", action="store_true", help="Re-audit already scored leads")
    args = parser.parse_args()
    run_audit_and_qualification(limit=args.limit, reaudit=args.reaudit)
