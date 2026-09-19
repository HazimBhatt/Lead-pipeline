# LeadPipeline 🚀

**Free, open-source lead generation and outreach automation. Scrape → Enrich → Pitch → Close.**

LeadPipeline is a complete outbound sales system that runs **100% locally on your machine with $0 subscription costs**:
- 📍 **Scrapes Google Maps** locally using Playwright (no paid cloud scrapers or Apify required).
- ✉️ **Auto-Discovers Emails** using a multi-stage engine (homepage scan + contact pages + search fallback).
- 📬 **Sends Personalized Pitches** over SMTP with BCC tracking and duplicate protection.
- 💬 **Monitors Inbound Replies** and delivery bounces via IMAP.
- 📊 **Syncs to Google Sheets CRM** (optional, zero database required).

[![License: MIT](https://img.shields.io/badge/license-MIT-22d3ee?style=flat-square&labelColor=03050a)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-22d3ee?style=flat-square&labelColor=03050a)](https://www.python.org/)
[![Built for](https://img.shields.io/badge/Cost-%240%20Forever-success?style=flat-square&labelColor=03050a)]()

---

## Complete Setup & Walkthrough (From Scratch)

Follow these steps to set up and run the entire pipeline on your computer.

### Step 1: Clone the Repository & Set Up Virtual Environment

Open your terminal (PowerShell on Windows, or bash/zsh on macOS/Linux):

```bash
# Clone repository
git clone 
cd lead-pipeline
```

Create and activate a virtual environment:

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```
*(If you see an execution policy error on PowerShell, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and re-run activate).*

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

---

### Step 2: Install Dependencies & Chromium Browser

Install all Python dependencies and download the local Chromium browser engine used by Playwright:

```bash
pip install -r requirements.txt
playwright install chromium
```

---

### Step 3: Configure Target Leads (`scraper/config.py`)

Copy the example configuration file:

**Windows:**
```powershell
copy scraper\config.py.example scraper\config.py
```
**macOS / Linux:**
```bash
cp scraper/config.py.example scraper/config.py
```

Open `scraper/config.py` in your code editor and customize your target search:

```python
# What industries/businesses to find
SEARCH_QUERIES = [
    "dental clinics",
    "real estate agents",
    "fine dining restaurants"
]

# Cities or areas to target
TARGET_CITIES = [
    "Kuala Lumpur",
    "Singapore"
]

# Number of results per search (e.g. 20 results per city)
MAX_RESULTS = 20

# Set False to watch the browser open on your screen; True to run invisibly
HEADLESS = False

# Automatically crawl business websites & web snippets to discover emails
ENRICH_EMAILS = True
EMAIL_TIMEOUT = 6
MAX_EMAIL_PAGES = 3
```

---

### Step 4: Configure Email Outreach (`outreach/config.py`)

Copy the outreach configuration file:

**Windows:**
```powershell
copy outreach\config.py.example outreach\config.py
```
**macOS / Linux:**
```bash
cp outreach/config.py.example outreach/config.py
```

Open `outreach/config.py` and enter your SMTP credentials. 

> [!TIP]
> **Using Gmail?**
> You need a Gmail **App Password** (not your regular Gmail password):
> 1. Go to [Google Account Security](https://myaccount.google.com/security).
> 2. Ensure **2-Step Verification** is enabled.
> 3. Search for **App passwords**, create one (e.g., name it `LeadPipeline`), and copy the 16-character password.

```python
# SMTP Server Settings (Gmail example)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465                      # 465 for SSL, 587 for STARTTLS
SMTP_USER = "your_email@gmail.com"
SMTP_PASSWORD = "xxxx xxxx xxxx xxxx"  # 16-character App Password

# Sender Details
FROM_EMAIL = "your_email@gmail.com"
FROM_NAME = "Your Name"

# BCC: Automatically sends a copy to yourself to track sent emails
BCC = "your_email@gmail.com"

# Safe sending delay (minimum 45-60 seconds recommended to avoid spam flags)
SEND_DELAY = 45

# Reply Checker Settings (IMAP)
IMAP_HOST = "imap.gmail.com"
IMAP_USER = SMTP_USER
IMAP_PASSWORD = SMTP_PASSWORD
CHECK_DAYS = 3
```

---

## Running the Pipeline

Now that your setup is complete, you can run each stage of the funnel:

### Phase 1: Scrape Leads & Auto-Discover Emails

Run the local browser scraper:

```bash
python scraper/local_scraper.py
```

**What it does:**
1. Opens Chromium directly on your desktop.
2. Searches Google Maps for each query in each city.
3. Clicks each business and extracts name, phone, category, rating, reviews, address, and website.
4. Crawls the business website, contact pages, and search snippets to discover their direct contact email.
5. Deduplicates leads and saves everything cleanly into `leads.json`.

---

### Phase 2: Send Outbound Email Pitches

To pitch the businesses saved in `leads.json`:

```bash
python outreach/pitch_sender.py
```

**Key Features:**
- **Preview Mode:** Set `PREVIEW_ONLY = True` on line 20 of `outreach/pitch_sender.py` to inspect the generated pitches in your terminal without sending anything. Set to `False` when ready to send live.
- **Volume Control:** Adjust `MAX_EMAILS_PER_RUN = 5` (line 23 of `outreach/pitch_sender.py`) to send in safe, controlled daily batches.
- **Duplicate Prevention:** Sent leads are automatically logged in `pitched.json`—the script will **never** email the same business twice.

---

### Phase 3: Monitor Replies & Warm Leads

To scan your inbox for replies, interested clients, or delivery bounces from the last 3 days:

```bash
python outreach/reply_checker.py
```

---

### Phase 4 (Optional): Google Sheets CRM

To sync your scraped leads into a free Google Sheets spreadsheet:

1. Place your Google Cloud service account JSON file at `sheets/credentials.json`.
2. Enter your `SHEET_ID` in `sheets/google_sheets.py`.
3. Run:
   ```bash
   python sheets/google_sheets.py
   ```

---

## Quick Reference Commands

Whenever you want to run the pipeline, activate your environment and execute:

```powershell
# 1. Activate environment
.\venv\Scripts\Activate.ps1

# 2. Scrape leads + emails ($0 local browser)
python scraper/local_scraper.py

# 3. Send personalized email pitches
python outreach/pitch_sender.py

# 4. Check for client replies
python outreach/reply_checker.py
```

---

## Security & Git Safety

All sensitive credentials and data are pre-configured in `.gitignore`:
- `config.py` (your passwords and targeting)
- `leads.json` (your scraped contacts)
- `pitched.json` (your outreach history)
- `credentials.json` (service account keys)

These files will **never** be committed or leaked to your Git repository.

---

## License

MIT — Free to use, modify, and build upon.