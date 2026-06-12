# Veris — Corporate Filing Intelligence

Automated pipeline that ingests SEC EDGAR (US) and NSE/BSE (India) corporate filings,
classifies events using Gemini AI, runs executive movement extraction, calculates
post-filing price reactions, and publishes a live research feed.

**Live at:** your-url.up.railway.app

---

## What it does

| Step | Source | What happens |
|------|--------|-------------|
| Ingest US | SEC EDGAR EFTS | All 8-K filings across the entire US market |
| Ingest India | NSE (BSE fallback) | All material corporate announcements |
| Analyze | Gemini 1.5 Flash (free) | Event type, materiality, thesis impact, risk flags |
| Radar | Gemini 1.5 Flash | Executive departure/appointment structured facts |
| Enrich | Yahoo Finance | T+1, T+5 price reactions per filing |
| Publish | FastAPI | Live web feed, 16 routes, JSON API |

Pipeline runs automatically every Sunday midnight UTC.

---

## Deploy to Railway (free, no credit card)

### Step 1 — Get a free Gemini API key
1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Sign in with Google → **Get API key** → **Create API key**
3. Copy the key (starts with `AIza...`)

### Step 2 — Push to GitHub
```bash
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOURUSERNAME/veris.git
git push -u origin main
```

### Step 3 — Deploy on Railway
1. Go to [railway.app](https://railway.app) → sign up with GitHub (free)
2. **New Project** → **Deploy from GitHub repo** → select `veris`
3. Railway reads `railway.toml` and configures build/start commands automatically
4. Go to your service → **Variables** → add these three:

| Variable | Value |
|----------|-------|
| `GEMINI_API_KEY` | `AIza...` |
| `DATA_DIR` | `/data` |
| `EDGAR_UA` | `Your Name your@email.com` |

5. Go to **Settings** → **Volumes** → **Add Volume**:
   - Mount path: `/data`
   - Size: 1 GB (covered by Railway's free $5/month credit)

6. Click **Deploy** — takes ~2 minutes

### Step 4 — Trigger first pipeline run
Your URL is shown in the Railway dashboard.

Open the Railway **Shell** tab and run:
```bash
python run_all.py
```

Or from your terminal:
```bash
curl -X POST https://your-url.up.railway.app/run
```

Pipeline takes 5–15 minutes on first run. Watch logs in Railway dashboard.

### Step 5 — Keep it awake (recommended)
1. Go to [uptimerobot.com](https://uptimerobot.com) → free account
2. **Add Monitor** → HTTP(s) → URL: `https://your-url.up.railway.app/health`
3. Interval: 5 minutes → **Create**

Site stays awake 24/7. No sleep delays for recruiters.

---

## Run locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export GEMINI_API_KEY="AIza..."
export EDGAR_UA="Your Name your@email.com"

# Run full pipeline
python run_all.py

# Start web server
uvicorn app:app --reload
# Open http://localhost:8000
```

---

## Routes

| Route | Description |
|-------|-------------|
| `GET /` | Daily digest — top filings last 24h |
| `GET /filings` | Full weekly feed |
| `GET /ticker/{TICKER}` | All filings for one company |
| `GET /filing/{accession}` | Individual filing detail |
| `GET /filing/{accession}/json` | Machine-readable JSON |
| `GET /filing/{accession}/md` | Machine-readable Markdown |
| `GET /filing/{accession}/txt` | Raw filing text |
| `GET /events/{type}` | Event type feed (cyber/earnings/ma/leadership/...) |
| `GET /radar/executives` | Executive movement radar |
| `GET /disclaimer` | Legal disclaimer |
| `GET /health` | JSON health check |
| `GET /api/filings` | Full JSON API feed |
| `GET /api/radar` | Radar JSON feed |
| `POST /run` | Manually trigger pipeline |

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Free key from aistudio.google.com |
| `DATA_DIR` | Yes (cloud) | Path to persistent volume (`/data`) |
| `EDGAR_UA` | Yes | Your name + email for SEC compliance |
| `DAYS_BACK` | No | Days of filings to fetch (default: 7) |
| `BROAD_MARKET` | No | `true` = all companies, `false` = watchlist only |
| `MAX_FILINGS` | No | Max US filings per run (default: 200) |

---

## Stack

- **Python 3.11+**
- **Ingestion:** `requests`, `BeautifulSoup4` (EDGAR + NSE/BSE)
- **AI:** `google-genai` — Gemini 1.5 Flash (free tier, no credit card)
- **Market data:** `yfinance` (free)
- **Storage:** SQLite3
- **Web:** FastAPI + uvicorn
- **Scheduler:** APScheduler
- **Deploy:** Railway / Render

---

## Disclaimer

Veris summarises public filings for research purposes only.
Not investment advice. Always verify against the primary source document.
See `/disclaimer` for full terms.
