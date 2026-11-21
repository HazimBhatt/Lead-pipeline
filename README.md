# LeadPipeline 🚀 — Gemini AI Top-1% Client Acquisition Engine

**Local, AI-powered high-ticket client acquisition. Scrape High-LTV Leads → Audit Live Websites → Score & Personalize with Gemini → Generate Custom Whale Demos → Send Gmail-Primary-Safe Threaded Outreach.**

LeadPipeline runs locally with **zero monthly SaaS subscriptions**:
- 📍 **High-LTV Google Maps Scraper** (`scraper/local_scraper.py`) — Targets high-budget niches locally via Playwright (zero Apify fees).
- 🔍 **Live Website & Decision-Maker Auditor** (`analyzer/site_auditor.py`) — Measures real speed, SSL, mobile readiness, online booking leaks, Schema/SEO gaps, verifies DNS MX records (near-0% bounce rate), and extracts founder/doctor names from `/about` & `/team`.
- 🧠 **Gemini 3.8 Flash Sniper Qualifier & Copywriter** (`analyzer/gemini_engine.py`) — Scores every lead `1–10`, auto-rejects low-budget/trash leads (`<7/10`), dynamically matches your best shipped project from `portfolio.json`, and writes a spam-proof 3-step plain-text sequence enforced by an automated anti-spam guardrail.
- 🐋 **Bespoke Whale Demo Generator** (`analyzer/demo_generator.py`) — Automatically creates a custom standalone 1-page Interactive Conversion Audit & Redesigned Hero Concept (`demos/<company>.html`) for every `9–10/10` Whale lead.
- 📬 **Gmail-Primary-Safe Threaded Outreach** (`outreach/pitch_sender.py`) — Pure plain-text emails, randomized human pacing (`60–150s`), safe daily caps (`25/day` default on `@gmail.com`), true `In-Reply-To` email threading for Day-3 & Day-7 follow-ups, and **automatic IMAP reply/bounce stop** (`outreach/reply_checker.py`).

---

## Complete Setup (From Scratch)

### 1. Clone & Create Virtual Environment
```powershell
git clone https://github.com/HazimBhatt/Lead-pipeline.git
cd lead-pipeline
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Install Dependencies
```powershell
python -m pip install -r requirements.txt
playwright install chromium
```

### 3. Configure High-LTV Targets (`scraper/config.py`)
```powershell
copy scraper\config.py.example scraper\config.py
```
Pre-configured for high-ticket English-speaking markets (`Dubai`, `Austin`, `Miami`, `Scottsdale`, `London`, `Sydney`, `Toronto`) and High-LTV industries (`cosmetic dentist`, `dental implants clinic`, `medspa`, `luxury real estate agency`, `roofing contractor`, `solar installation company`, `immigration law firm`, `boutique hotel`).

### 4. Configure Gemini & Gmail SMTP/IMAP (`outreach/config.py`)
```powershell
copy outreach\config.py.example outreach\config.py
```
Add your:
- **Gmail App Password** (from Google Account Security → 2-Step Verification → App passwords)
- **`GEMINI_API_KEY`** (from [Google AI Studio](https://aistudio.google.com/apikey)) — either in `outreach/config.py` (gitignored) or as an environment variable `$env:GEMINI_API_KEY="your_key"`.

### 5. Customize Your Portfolio Brain (`portfolio.json`)
```powershell
copy portfolio.json.example portfolio.json
```
Edit `portfolio.json` with your real shipped projects, traction, and positioning. Gemini automatically matches the single most relevant project to each prospect's exact problem. (`portfolio.json` is gitignored so your personal profile stays local.)

---

## Daily Top-1% Workflow (`run_pipeline.py`)

```powershell
# 1. Check live pipeline dashboard & Whale leads
python run_pipeline.py --status

# 2. Scrape fresh High-LTV leads from Google Maps
python run_pipeline.py --scrape

# 3. Audit live sites + verify MX + score with Gemini + generate Whale HTML demos
python run_pipeline.py --audit --limit 30

# 4. Preview generated emails safely (sends 0 emails)
python run_pipeline.py --send --preview

# 5. Interactive approval mode (eyeball & approve each Whale before sending)
python run_pipeline.py --review

# 6. Automated Gmail-Primary-safe sending (Email 1 + due Day 3/7 threaded follow-ups)
python run_pipeline.py --send

# Or run the full daily loop end-to-end:
python run_pipeline.py --all --limit 25
```

Press **`Ctrl+C`** at any moment to pause cleanly — every scraped lead, Gemini score, generated Whale demo, and sent email is saved immediately on disk with zero data loss and zero duplicate sends.

---

## Gmail Primary Inbox Survival Rules (Built In)
- **Zero links & zero HTML in Email #1** — Enforced automatically by the anti-spam guardrail. Mention your product brand name naturally and only send your custom Whale demo (`demos/<company>.html`) + live links once they reply *"Sure, send it over."*
- **MX Verification before every send** — Keeps bounce rate near 0%.
- **20–25 high-intent emails/day max on `@gmail.com`** — Randomized 60–150s delays mimic real human typing. Once you close your first $2k+ client, attach a dedicated outreach domain on Google Workspace/Zoho Mail Lite and scale `DAILY_SEND_LIMIT` up safely.
