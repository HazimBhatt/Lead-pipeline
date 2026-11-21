import html
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

from google import genai
from google.genai import types

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTREACH_DIR = os.path.join(ROOT_DIR, "outreach")
for p in (ROOT_DIR, OUTREACH_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

DEMOS_DIR = os.path.join(ROOT_DIR, "demos")
DEMO_MODEL_FALLBACK_CHAIN = [
    "gemini-4-hydrogen",
    "gemini-4-neon",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "client").lower()).strip("-")
    return slug[:60] or "client-demo"


def _detect_industry_art_direction(lead: Dict, audit: Dict) -> Tuple[str, str]:
    """Choose a tailored, $10k agency-grade visual art direction matched to the business vertical."""
    blob = " ".join([
        str(lead.get("name") or ""),
        str(lead.get("category") or ""),
        str((audit.get("site_audit") or {}).get("title") or ""),
        str((audit.get("site_audit") or {}).get("homepage_snippet") or "")[:500],
    ]).lower()

    if any(k in blob for k in ("dent", "implant", "ortho", "smile", "veneer", "root canal", "endodont")):
        return (
            "Luxury Cosmetic Dentistry & Clinical Aesthetics",
            "Warm alabaster/ivory background (#FAF8F5), crisp white cards (#FFFFFF), deep botanical forest green (#12352C) primary accent, subtle champagne gold (#C5A059) highlights, refined editorial serif headings (Cormorant Garamond or Playfair Display) paired with clean sans-serif body (Plus Jakarta Sans). Bright, serene, Beverly Hills boutique clinic energy — NEVER dark mode or neon.",
        )

    if any(k in blob for k in ("medspa", "med spa", "aesthet", "botox", "laser", "skin", "plastic", "dermat", "injectable")):
        return (
            "Editorial Luxury MedSpa & Aesthetics",
            "Warm porcelain/stone palette (#F9F6F1), soft sand surfaces (#EFE9E0), deep espresso charcoal typography (#1C1917), muted rose-bronze/champagne accent (#B88A5A), Vogue/Aesop editorial serif headings (Cormorant Garamond) + airy sans body (Inter/Plus Jakarta Sans). Minimalist, serene, ultra-premium — NEVER dark mode.",
        )

    if any(k in blob for k in ("real estate", "properties", "broker", "realty", "homes", "estate", "builder", "architect")):
        return (
            "Architectural Luxury Real Estate & Custom Homes",
            "Gallery-grade warm white (#FDFCFB) & soft stone (#F3F1EC) canvas with rich architectural charcoal (#141517) contrast sections and brushed antique brass (#B5945B) accents. Editorial magazine typography (Playfair Display + DM Sans), generous negative space, refined property showcase cards, private viewing drawer.",
        )

    if any(k in blob for k in ("law", "attorney", "legal", "injury", "counsel", "llp", "pllc")):
        return (
            "Prestigious Executive Law Firm",
            "Authoritative crisp ivory (#FAFAF8), deep heritage navy/slate (#0F1D36), subtle warm gold rule lines (#B8964C), classic editorial serif headings (Libre Baskerville or Playfair Display) paired with crisp Inter body text. Dignified, commanding trust, case-result highlights, confidential evaluation CTA.",
        )

    if any(k in blob for k in ("roof", "solar", "hvac", "pool", "remodel", "contractor", "plumb", "construct")):
        return (
            "High-Trust Premium Home Services",
            "Clean bright white (#FFFFFF) & cool slate tint (#F4F7FA), confident deep marine navy (#0B2545), energetic amber/copper CTA accent (#D97706), bold geometric headings (Space Grotesk or Plus Jakarta Sans) + Inter body. Includes an interactive 30-second instant project estimate widget right inside the hero.",
        )

    return (
        "Boutique High-Ticket Modern Brand",
        "Bright editorial warm off-white (#FAF9F6), crisp white elevated cards, deep obsidian ink (#111318), refined warm gold accent (#B88B4A), Playfair Display + Plus Jakarta Sans typography. Ultra-clean agency quality — NEVER generic dark neon SaaS slop.",
    )


def validate_demo_html(html_text: str, biz_name: str) -> List[str]:
    """Ensure Gemini produced complete, valid, non-slop standalone HTML."""
    issues = []
    if not html_text or len(html_text) < 2500:
        issues.append("HTML too short (<2500 chars)")
    lower = (html_text or "").lower()
    if "<!doctype html" not in lower:
        issues.append("Missing <!DOCTYPE html>")
    if "</html>" not in lower or "</body>" not in lower:
        issues.append("Incomplete HTML (missing closing </body> or </html>)")
    if "<style" not in lower and "tailwindcss" not in lower:
        issues.append("Missing styling")
    for bad in ("lorem ipsum", "[insert ", "[company name]", "[your business]", "placeholder text"):
        if bad in lower:
            issues.append(f"Contains placeholder text: {bad}")
    return issues


def _extract_html_block(raw_text: str) -> str:
    txt = (raw_text or "").strip()
    fence = re.search(r"```(?:html)?\s*(<!DOCTYPE html[\s\S]*?</html>)\s*```", txt, re.I)
    if fence:
        return fence.group(1).strip()
    doc = re.search(r"(<!DOCTYPE html[\s\S]*?</html>)", txt, re.I)
    if doc:
        return doc.group(1).strip()
    return txt


def _build_audit_drawer_snippet(
    biz_name: str,
    leaks: List[str],
    portfolio_project: Dict,
    builder_info: Dict,
    load_time: float,
) -> str:
    """Subtle collapsible bottom-right audit pill & drawer injected cleanly before </body>."""
    safe_biz = html.escape(biz_name)
    safe_builder = html.escape(builder_info.get("name") or "Your Growth Partner")
    safe_role = html.escape(builder_info.get("role") or "Product & Growth Engineer")
    proj_name = html.escape(portfolio_project.get("name") or "Featured Build")
    proj_url = html.escape(portfolio_project.get("url") or "")
    proj_traction = html.escape(portfolio_project.get("traction") or "Live Production Build")

    leak_items = "".join(
        f"<li style='margin:6px 0;color:#334155;font-size:13px;line-height:1.45;'>• {html.escape(str(lk))}</li>"
        for lk in (leaks[:4] or ["Slow mobile experience & missing frictionless online booking"])
    )
    proj_link_html = (
        f' · <a href="https://{proj_url}" target="_blank" style="color:#0f766e;text-decoration:underline;">{proj_url}</a>'
        if proj_url else ""
    )

    return f"""
<!-- Private Redesign Concept & Audit Drawer -->
<div id="lp-audit-root" style="position:fixed;bottom:20px;right:20px;z-index:99999;font-family:Inter,system-ui,sans-serif;">
  <button id="lp-audit-toggle" onclick="var p=document.getElementById('lp-audit-panel');p.style.display=p.style.display==='block'?'none':'block';"
    style="display:flex;align-items:center;gap:8px;background:#111827;color:#ffffff;border:1px solid rgba(255,255,255,0.18);padding:11px 18px;border-radius:999px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 12px 32px rgba(0,0,0,0.22);">
    <span style="width:8px;height:8px;border-radius:50%;background:#10b981;display:inline-block;"></span>
    Concept &amp; Audit Notes for {safe_biz}
  </button>
  <div id="lp-audit-panel" style="display:none;position:absolute;bottom:56px;right:0;width:360px;max-width:calc(100vw - 40px);background:#ffffff;color:#0f172a;border:1px solid #e2e8f0;border-radius:16px;padding:20px;box-shadow:0 24px 60px rgba(15,23,42,0.22);">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
      <span style="font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#0f766e;">Private Teardown · {safe_builder}</span>
      <button onclick="document.getElementById('lp-audit-panel').style.display='none';" style="background:none;border:none;font-size:18px;cursor:pointer;color:#64748b;">&times;</button>
    </div>
    <div style="font-size:15px;font-weight:700;margin-bottom:8px;">What This Redesign Fixes ({f'{load_time}s current load' if load_time else 'Live Audit'})</div>
    <ul style="list-style:none;padding:0;margin:0 0 14px 0;">{leak_items}</ul>
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px;font-size:12px;color:#475569;">
      <strong style="color:#0f172a;display:block;margin-bottom:3px;">Built by {safe_builder} ({safe_role})</strong>
      Shipped work: <strong>{proj_name}</strong> ({proj_traction}){proj_link_html}
    </div>
  </div>
</div>
"""


def _render_editorial_fallback_html(
    lead: Dict,
    audit: Dict,
    qualification: Dict,
    portfolio_project: Dict,
    builder_info: Dict,
    gemini_demo_copy: Optional[Dict] = None,
) -> str:
    """Clean, bright editorial luxury fallback HTML (never dark slop) used only if Gemini fails."""
    biz_name = html.escape(lead.get("name") or "Your Practice")
    city = html.escape(lead.get("city") or "")
    rating = lead.get("rating") or 4.9
    reviews = lead.get("reviews") or 120
    phone = html.escape(lead.get("phone") or "")
    site_audit = audit.get("site_audit", {})
    leaks = qualification.get("top_revenue_leaks") or site_audit.get("detected_leaks") or []
    load_time = site_audit.get("load_time_sec") or 0.0

    demo_copy = gemini_demo_copy or {}
    eyebrow = html.escape(demo_copy.get("hero_eyebrow") or (f"{city} · Private Practice" if city else "Boutique Client Care"))
    headline = html.escape(demo_copy.get("hero_headline") or f"Elevated Care & Effortless Booking at {lead.get('name', 'Our Practice')}")
    subhead = html.escape(
        demo_copy.get("hero_subheadline")
        or f"Rated {rating}★ across {reviews}+ verified client reviews{f' in {city}' if city else ''}. Experience bespoke service with instant 30-second online scheduling."
    )
    primary_cta = html.escape(demo_copy.get("primary_cta") or "Book Private Consultation")
    secondary_cta = html.escape(demo_copy.get("secondary_cta") or "Explore Services")
    wins = demo_copy.get("three_quick_wins") or [
        "Instant 30-second mobile consultation & appointment booking",
        "Sub-second page speed designed for high-intent local search visitors",
        "Clear service architecture and transparent next steps above the fold",
    ]

    wins_cards = "\n".join(
        f"""
        <div class="card">
          <div class="card-num">0{i}</div>
          <h3>Key Upgrade</h3>
          <p>{html.escape(str(w))}</p>
        </div>"""
        for i, w in enumerate(wins[:3], start=1)
    )

    drawer = _build_audit_drawer_snippet(
        lead.get("name") or "Your Business", leaks, portfolio_project, builder_info, load_time
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{biz_name} — Bespoke Redesign Concept</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
      background: #FAF8F5; color: #1A1A1A; line-height: 1.6;
    }}
    nav {{
      max-width: 1160px; margin: 0 auto; padding: 24px 24px;
      display: flex; justify-content: space-between; align-items: center;
      border-bottom: 1px solid rgba(26,26,26,0.08);
    }}
    .brand {{ font-family: 'Cormorant Garamond', serif; font-size: 26px; font-weight: 700; letter-spacing: -0.01em; color: #12352C; }}
    .nav-cta {{
      background: #12352C; color: #FFFFFF; padding: 11px 22px; border-radius: 999px;
      text-decoration: none; font-size: 14px; font-weight: 600; border: none; cursor: pointer;
    }}
    .hero {{
      max-width: 1160px; margin: 0 auto; padding: 84px 24px 72px;
      display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 48px; align-items: center;
    }}
    @media (max-width: 860px) {{ .hero {{ grid-template-columns: 1fr; padding: 48px 20px; }} }}
    .eyebrow {{
      font-size: 12px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
      color: #A67C46; margin-bottom: 16px;
    }}
    h1 {{
      font-family: 'Cormorant Garamond', serif; font-size: clamp(38px, 5.2vw, 62px);
      line-height: 1.06; font-weight: 600; color: #12352C; margin-bottom: 20px;
    }}
    .sub {{ font-size: 17px; color: #4B5563; max-width: 560px; margin-bottom: 32px; }}
    .cta-row {{ display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 32px; }}
    .btn-primary {{
      background: #12352C; color: #fff; padding: 15px 28px; border-radius: 999px;
      font-weight: 600; font-size: 15px; border: none; cursor: pointer;
    }}
    .btn-outline {{
      background: transparent; color: #12352C; padding: 15px 26px; border-radius: 999px;
      font-weight: 600; font-size: 15px; border: 1px solid rgba(18,53,44,0.3); cursor: pointer;
    }}
    .trust {{ display: flex; flex-wrap: wrap; gap: 24px; font-size: 13px; color: #374151; font-weight: 500; }}
    .booking-card {{
      background: #FFFFFF; border: 1px solid rgba(26,26,26,0.08); border-radius: 22px;
      padding: 32px; box-shadow: 0 24px 60px rgba(18,53,44,0.07);
    }}
    .booking-card h2 {{ font-family: 'Cormorant Garamond', serif; font-size: 28px; color: #12352C; margin-bottom: 6px; }}
    .booking-card p {{ font-size: 14px; color: #6B7280; margin-bottom: 20px; }}
    .field {{ width: 100%; padding: 12px 14px; border: 1px solid #E5E7EB; border-radius: 10px; margin-bottom: 12px; font-size: 14px; }}
    .section {{ max-width: 1160px; margin: 0 auto; padding: 48px 24px 96px; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 22px; }}
    .card {{
      background: #FFFFFF; border: 1px solid rgba(26,26,26,0.07); border-radius: 18px; padding: 28px;
    }}
    .card-num {{ font-size: 12px; font-weight: 700; color: #A67C46; margin-bottom: 10px; }}
    .card h3 {{ font-family: 'Cormorant Garamond', serif; font-size: 24px; color: #12352C; margin-bottom: 8px; }}
    .card p {{ font-size: 14px; color: #4B5563; }}
  </style>
</head>
<body>
  <nav>
    <div class="brand">{biz_name}</div>
    <button class="nav-cta" onclick="document.getElementById('book-form').scrollIntoView({{behavior:'smooth'}})">{primary_cta}</button>
  </nav>

  <section class="hero">
    <div>
      <div class="eyebrow">{eyebrow}</div>
      <h1>{headline}</h1>
      <p class="sub">{subhead}</p>
      <div class="cta-row">
        <button class="btn-primary" onclick="document.getElementById('book-form').scrollIntoView({{behavior:'smooth'}})">{primary_cta} →</button>
        <button class="btn-outline" onclick="document.getElementById('highlights').scrollIntoView({{behavior:'smooth'}})">{secondary_cta}</button>
      </div>
      <div class="trust">
        <span>★ {rating} Google Rating ({reviews}+ Reviews)</span>
        {f'<span>📍 {city}</span>' if city else ''}
        {f'<span>📞 {phone}</span>' if phone else ''}
      </div>
    </div>

    <div class="booking-card" id="book-form">
      <h2>Request Your Consultation</h2>
      <p>Instant scheduling — our team confirms within minutes.</p>
      <input class="field" type="text" placeholder="Full Name" />
      <input class="field" type="tel" placeholder="Mobile Number" />
      <input class="field" type="text" placeholder="Preferred Day / Service" />
      <button class="btn-primary" style="width:100%;" onclick="alert('Concept preview: in production this books directly into your calendar + sends instant SMS confirmation!')">Confirm Priority Slot</button>
    </div>
  </section>

  <section class="section" id="highlights">
    <div class="grid-3">{wins_cards}</div>
  </section>
  {drawer}
</body>
</html>"""


def _generate_html_with_gemini_hydrogen(
    lead: Dict,
    audit: Dict,
    qualification: Dict,
    portfolio_project: Dict,
    builder_info: Dict,
    api_key: str,
    preferred_model: str = "gemini-4-hydrogen",
) -> Optional[str]:
    """Use gemini-4-hydrogen to craft a genuinely bespoke, $10k agency-grade HTML landing page redesign."""
    if not api_key:
        return None

    industry_label, art_direction = _detect_industry_art_direction(lead, audit)
    sa = audit.get("site_audit", {})
    biz_name = lead.get("name") or "Client Business"
    city = lead.get("city") or ""
    rating = lead.get("rating") or 4.8
    reviews = lead.get("reviews") or 50
    phone = lead.get("phone") or ""
    address = lead.get("address") or ""
    leaks = qualification.get("top_revenue_leaks") or sa.get("detected_leaks") or []
    homepage_copy = (sa.get("homepage_snippet") or "")[:2000]
    dm_name = sa.get("decision_maker_name") or qualification.get("decision_maker_first_name") or ""

    prompt = f"""You are an Awwwards-winning Creative Director & Senior Frontend Engineer building a jaw-dropping, $10,000-caliber website homepage redesign concept for a REAL high-ticket business.

TARGET BUSINESS (USE ONLY REAL DETAILS BELOW — NEVER INVENT FAKE DOCTORS OR FAKE ADDRESSES):
- Business Name: {biz_name}
- Industry: {industry_label}
- City: {city}
- Address: {address or city}
- Phone: {phone}
- Google Reputation: {rating} stars across {reviews}+ verified reviews
- Decision Maker / Lead (if known): {dm_name or "Not specified"}
- Their Current Homepage Content (extract their REAL services, specialties, and tone from this):
{homepage_copy}

VERIFIED PROBLEMS ON THEIR CURRENT SITE THAT THIS REDESIGN SOLVES:
{chr(10).join(f"- {lk}" for lk in leaks[:4])}

MANDATORY ART DIRECTION (CRITICAL — NO GENERIC DARK AI SLOP!):
{art_direction}

DESIGN & ENGINEERING REQUIREMENTS:
1. Return ONE complete, self-contained HTML5 document (`<!DOCTYPE html>...</html>`) using Google Fonts (`<link>`) and clean hand-crafted CSS inside `<style>` (plus lightweight vanilla JS for interactive features). Do NOT use broken external image URLs (use refined CSS architectural surfaces, subtle geometric SVG accents, clean editorial typography, and crisp UI cards — zero broken `<img>` tags!).
2. Include these high-converting sections:
   - **Refined Top Navigation Bar** with business name wordmark, 3-4 real section links, phone number, and primary pill CTA.
   - **Above-the-Fold Editorial Hero** with prestige eyebrow, magnetic headline tailored to their exact niche & city, crisp subheadline, primary & secondary CTAs, verified Google rating trust strip (`{rating}★ · {reviews}+ Reviews`), AND an **interactive Instant Consultation / Priority Quote Booking Card** right in the hero (with working JavaScript confirmation state!).
   - **Signature Services Grid (3–6 real services extracted from their homepage copy)** with refined hover elevation, concise benefit descriptions, and individual "Inquire →" triggers that open the booking modal pre-filled with that service.
   - **Why Clients Choose {biz_name} (Authority & Process Strip)** — 3 crisp pillars + realistic testimonial style block referencing their real `{rating}★` reputation (do NOT fabricate fake person quotes; frame as verified Google review highlights).
   - **Interactive Booking / Quote Modal** triggered by any CTA button, allowing visitors to pick a service, preferred time slot, and see an instant interactive confirmation state.
   - **Clean Editorial Footer** with real city/address/phone and current year copyright.
3. Make the typography, spacing, visual hierarchy, and mobile responsiveness genuinely look like a **$10,000 boutique studio build** (generous whitespace, `clamp()` fluid typography, subtle borders, refined color contrast).
4. Zero `Lorem ipsum`, zero `[Placeholder]` brackets, zero mentions of the developer/agency inside the main business design (the private audit drawer is injected separately!).

Output ONLY the raw complete HTML document starting with `<!DOCTYPE html>` and ending with `</html>`."""

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=90_000))
    models_to_try = [preferred_model] + [m for m in DEMO_MODEL_FALLBACK_CHAIN if m != preferred_model]

    for model_name in models_to_try:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.65),
            )
            html_out = _extract_html_block(resp.text or "")
            issues = validate_demo_html(html_out, biz_name)
            if not issues:
                # Inject subtle collapsible Audit Notes floating pill right before </body>
                drawer = _build_audit_drawer_snippet(
                    biz_name,
                    leaks,
                    portfolio_project,
                    builder_info,
                    sa.get("load_time_sec") or 0.0,
                )
                html_out = re.sub(r"</body>", drawer + "\n</body>", html_out, count=1, flags=re.I)
                print(f"      🎨 Bespoke agency redesign crafted with {model_name}!", flush=True)
                return html_out
        except Exception as e:
            err_str = str(e).lower()
            if any(k in err_str for k in ("404", "not found", "not supported", "invalid argument")):
                continue
            time.sleep(2)

    return None


def generate_whale_demo_html(
    lead: Dict,
    audit: Dict,
    qualification: Dict,
    portfolio_project: Dict,
    builder_info: Dict,
    gemini_demo_copy: Optional[Dict] = None,
    api_key: Optional[str] = None,
    demo_model: Optional[str] = None,
) -> str:
    """
    Generate a bespoke, agency-grade 1-page redesign concept HTML powered by gemini-4-hydrogen
    for 9-10/10 Whale leads. Falls back to a clean bright editorial layout if API is unavailable.
    """
    os.makedirs(DEMOS_DIR, exist_ok=True)

    resolved_key = (api_key or "").strip()
    resolved_model = (demo_model or "").strip()
    try:
        import config as outreach_cfg
        if not resolved_key:
            cfg_k = (getattr(outreach_cfg, "GEMINI_API_KEY", "") or "").strip()
            if cfg_k and cfg_k != "YOUR_GEMINI_API_KEY":
                resolved_key = cfg_k
        if not resolved_model:
            resolved_model = getattr(outreach_cfg, "DEMO_MODEL", "gemini-4-hydrogen")
    except Exception:
        pass
    if not resolved_key:
        resolved_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if not resolved_model:
        resolved_model = "gemini-4-hydrogen"

    html_doc = _generate_html_with_gemini_hydrogen(
        lead=lead,
        audit=audit,
        qualification=qualification,
        portfolio_project=portfolio_project,
        builder_info=builder_info,
        api_key=resolved_key,
        preferred_model=resolved_model,
    )

    if not html_doc:
        html_doc = _render_editorial_fallback_html(
            lead=lead,
            audit=audit,
            qualification=qualification,
            portfolio_project=portfolio_project,
            builder_info=builder_info,
            gemini_demo_copy=gemini_demo_copy,
        )

    filename = f"{_slugify(lead.get('name', 'whale'))}.html"
    out_path = os.path.join(DEMOS_DIR, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return out_path
