# to enter venv
.\venv\Scripts\Activate.ps1

# Step 1: Scrape leads & auto-discover emails via local browser ($0 cost)
python scraper/local_scraper.py

# Step 2: Send outreach pitches over SMTP
python outreach/pitch_sender.py

# Step 3: Check for incoming replies & warm leads
python outreach/reply_checker.py