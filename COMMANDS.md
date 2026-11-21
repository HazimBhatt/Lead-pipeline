# LeadPipeline — Command Cheat Sheet 🚀

Always run these from inside the repository root folder (PowerShell).

---

## 0. First-Time Activation (Optional)
If you activate the virtual environment first, you can use `python` instead of `venv\Scripts\python.exe`:
```powershell
.\venv\Scripts\Activate.ps1
```
*(Or just copy-paste any `venv\Scripts\python.exe ...` command below directly — no activation needed!)*

---

## 1. Check Live Pipeline Dashboard
See total scraped leads, Whale count (`9-10/10`), Qualified leads (`7-8/10`), active Day-3/7 follow-ups, and replies:
```powershell
venv\Scripts\python.exe run_pipeline.py --status
```

---

## 2. Scrape Fresh High-LTV Leads (Google Maps)
Scrapes high-budget niches configured in `scraper/config.py` (cosmetic dentists, medspas, luxury real estate, roofing, solar, law firms, etc.) and auto-saves every lead instantly to `leads.json`:
```powershell
venv\Scripts\python.exe run_pipeline.py --scrape
```
*(Press `Ctrl+C` anytime to stop — every scraped lead is already saved.)*

---

## 3. Audit Websites + Score with Gemini + Generate Whale Demos
Visits live websites, verifies DNS MX records (near-0% bounce rate), extracts decision-maker names, scores leads `1-10` with Gemini, writes 3-step plain-text sequences, and generates custom HTML mockups (`demos/<company>.html`) for `9-10/10` Whale leads:

```powershell
# Audit & qualify the next 30 uncontacted leads
venv\Scripts\python.exe run_pipeline.py --audit --limit 30

# Audit & qualify ALL uncontacted leads
venv\Scripts\python.exe run_pipeline.py --audit
```
*(Make sure `GEMINI_API_KEY` is set in `outreach/config.py` or via `$env:GEMINI_API_KEY="your_key"`.)*

---

## 4. Preview Generated Emails (Safe Dry-Run — Sends 0 Emails)
Inspect the exact plain-text emails, scores, matched projects, and Whale demo paths before sending anything:
```powershell
venv\Scripts\python.exe run_pipeline.py --send --preview
```

---

## 5. Send Outreach (Gmail Primary Inbox Safe)

### Option A — Interactive Review Mode (Recommended for Whales)
Approve or skip each lead (`y` / `s` / `n` / `q`) before it goes out:
```powershell
venv\Scripts\python.exe run_pipeline.py --review
```

### Option B — Full Hands-Free Auto-Send (Direct Owner Emails Prioritized First!)
Syncs Gmail IMAP replies first (auto-stops follow-ups for anyone who replied), sends due Day-3/Day-7 threaded follow-ups + **Direct Owner Emails first** (then role emails), pauses randomly `60-150s` between emails, and respects `DAILY_SEND_LIMIT = 25`:
```powershell
# Send all qualified leads (Direct owner emails prioritized first)
venv\Scripts\python.exe run_pipeline.py --send

# Send ONLY to direct owner/personal emails (completely skip generic info@/care@/office@!)
venv\Scripts\python.exe run_pipeline.py --send --direct-only

# Or cap a single session to e.g. 10 emails:
venv\Scripts\python.exe run_pipeline.py --send --limit 10
```

---

## 5B. One-Click WhatsApp Whale Launcher (30-50% Reply Rate Cheat Code!)
Prints personalized 1-click `https://wa.me/...` links + matching `demos/*.html` paths for every `9-10/10` Whale (killer for Dubai real estate & clinic owners):
```powershell
venv\Scripts\python.exe run_pipeline.py --whatsapp
```

---

## 6. Full End-to-End Daily Command (Scrape → Audit → Send)
```powershell
venv\Scripts\python.exe run_pipeline.py --all --limit 25
```

---

## 7. Instant Hot-Reply Watcher & AI Closing-Reply Alerter (`ALERT_EMAIL`)
Checks Gmail IMAP for real prospect replies, stops automated follow-ups, drafts a ready-to-send closing reply using `gemini-4-hydrogen`, and emails your configured `ALERT_EMAIL` (from `outreach/config.py`):
```powershell
# Run live reply check & alert
venv\Scripts\python.exe outreach/reply_notifier.py

# Send a realistic simulated test alert to your ALERT_EMAIL
venv\Scripts\python.exe outreach/reply_notifier.py --test-alert
```

---

## 8. Hands-Free Daily Autopilot & Windows Scheduler (3:00 PM + Offline Catch-Up)
Runs automatically every day at **3:00 PM** (and catches up immediately when your PC turns back on / reconnects to Wi-Fi) + checks for hot replies every **30 minutes** silently in the background:
```powershell
# Install / refresh both Windows Scheduled Tasks
powershell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1

# Check scheduled task status, last run & next run
powershell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1 -Status

# Remove scheduled tasks anytime
powershell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1 -Uninstall

# Run today's full autopilot manually right now (or dry-run test)
venv\Scripts\python.exe daily_autopilot.py
venv\Scripts\python.exe daily_autopilot.py --dry-run
```

---

## Quick Config Files
- **Tier-1 Target Niches & Cities (USA/UK/AU/CA/CH only):** `scraper/config.py`
- **Gmail SMTP/IMAP, Gemini API Key, Alert Email, 30/day Cap & Delays:** `outreach/config.py`
- **Your Shipped Projects & Social Proof:** `portfolio.json`
- **Generated `gemini-4-hydrogen` Whale Redesign Demos:** `demos/` folder
- **Daily Autopilot Logs:** `logs/` folder
