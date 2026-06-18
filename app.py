 """
app.py
------
Veris — Corporate Filing Intelligence
FastAPI web server.

Routes:
  GET /                → daily digest (top filings last 24h)
  GET /filings         → full weekly filing feed
  GET /radar/executives→ executive movement radar
  GET /digest          → daily digest (same as /)
  GET /disclaimer      → legal disclaimer page
  GET /health          → JSON health check (Render)
  GET /api/filings     → JSON feed
  GET /api/radar       → JSON radar feed
  POST /run            → manual pipeline trigger

Deploy: uvicorn app:app --host 0.0.0.0 --port $PORT
Env:    GEMINI_API_KEY, DATA_DIR (Render persistent disk)
"""

import os
import sqlite3
import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
from apscheduler.schedulers.background import BackgroundScheduler

import ingest
import analyze
import enrich
import memo
import radar

BASE_DIR = Path(__file__).parent
from config import DB_FILE, MEMOS_DIR

app = FastAPI(title="Veris")

# ── Shared HTML primitives ────────────────────────────────────────────────────
BRAND = "veris"

SHARED_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0c10;--surface:#0e1117;--border:#1e2330;--border2:#252b3a;
  --text:#c9d1e0;--text2:#7a8499;--text3:#4a5168;--text4:#2e3347;
  --up:#3fb950;--dn:#f85149;--amber:#d29922;--blue:#58a6ff;--purple:#bc8cff;
  --mono:'IBM Plex Mono','Courier New',monospace;
}
html{font-size:13px;-webkit-font-smoothing:antialiased}
body{background:var(--bg);color:var(--text);font-family:var(--mono);min-height:100vh}
a{color:inherit;text-decoration:none}
nav{
  border-bottom:1px solid var(--border);
  padding:0.75rem 2rem;
  display:flex;align-items:center;gap:2rem;font-size:0.72rem;
  position:sticky;top:0;background:var(--bg);z-index:100;
}
.nav-brand{color:var(--text);font-weight:600;font-size:0.8rem;letter-spacing:0.04em}
.nav-links{display:flex;gap:1.5rem}
.nav-links a{color:var(--text3)}
.nav-links a:hover{color:var(--text)}
.nav-links a.active{color:var(--text);border-bottom:1px solid var(--text3)}
.nav-right{margin-left:auto;color:var(--text3);font-size:0.6rem}
.hero{padding:1rem 2rem 0.75rem;border-bottom:1px solid var(--border);max-width:1100px}
.hero-label{font-size:0.6rem;letter-spacing:0.2em;color:var(--text3);text-transform:uppercase;margin-bottom:0.3rem}
.hero-title{font-size:1.2rem;font-weight:400;color:var(--text);margin-bottom:0.4rem;letter-spacing:-0.01em}
.hero-sub{font-size:0.72rem;color:var(--text2);line-height:1.65;max-width:60ch}
.content{max-width:1100px;padding:0 2rem 1.5rem}
.section-rule{
  font-size:0.6rem;letter-spacing:0.18em;color:var(--text3);text-transform:uppercase;
  padding:0.75rem 0 0.4rem;border-bottom:1px solid var(--border);margin-bottom:0;
  display:flex;justify-content:space-between;align-items:baseline;
}
.section-rule span{color:var(--text3);font-size:0.58rem}
.filing{padding:0.6rem 0;border-bottom:1px solid var(--border)}
.filing-meta{display:flex;align-items:center;gap:0.75rem;margin-bottom:0.3rem;flex-wrap:wrap}
.meta-date{color:var(--text3);font-size:0.65rem}
.meta-ticker{color:var(--blue);font-size:0.8rem;font-weight:500;letter-spacing:0.02em}
.meta-ticker:hover{text-decoration:underline}
.meta-market{font-size:0.6rem;padding:0.1rem 0.35rem;border-radius:2px;background:#0d1a2a;color:var(--blue);border:1px solid #1e2d45}
.meta-evt{font-size:0.62rem;padding:0.1rem 0.4rem;border-radius:3px;letter-spacing:0.04em}
.evt-cybersecurity{background:#1a1040;color:var(--purple)}
.evt-m-a,.evt-ma{background:#0d2818;color:var(--up)}
.evt-leadership-change{background:#1a1200;color:var(--amber)}
.evt-earnings{background:#0d2818;color:var(--up)}
.evt-litigation{background:#1a0d0d;color:var(--dn)}
.evt-debt-financing{background:#1a1040;color:var(--purple)}
.evt-regulatory{background:#1a1200;color:var(--amber)}
.evt-dividend{background:#0d1a2a;color:var(--blue)}
.evt-share-buyback{background:#0d2818;color:var(--up)}
.evt-restatement{background:#1a0d0d;color:var(--dn)}
.evt-other{background:#141820;color:var(--text2)}
.meta-mat{font-size:0.65rem;color:var(--text3)}
.meta-mat strong{color:var(--text2)}
.meta-urg{font-size:0.65rem}
.urg-high{color:var(--dn)}.urg-medium{color:var(--amber)}.urg-low{color:var(--text3)}
.filing-summary{font-size:0.82rem;color:var(--text2);line-height:1.5;margin-bottom:0.35rem;max-width:72ch}
.dir{font-weight:500;margin-right:0.3rem}
.dir.up{color:var(--up)}.dir.dn{color:var(--dn)}.dir.flat{color:var(--text3)}
.filing-detail{display:flex;flex-direction:column;gap:0.15rem;margin-bottom:0.3rem}
.detail-block{display:flex;gap:1rem;font-size:0.7rem;line-height:1.5;align-items:baseline}
.detail-label{color:var(--text3);min-width:6rem;font-size:0.62rem;letter-spacing:0.08em;flex-shrink:0}
.detail-val{color:var(--text2)}
.detail-val.dim{color:var(--text3)}
.returns-block{gap:0.5rem;flex-wrap:wrap}
.ret{font-size:0.72rem;font-weight:500;padding:0.1rem 0.5rem;border-radius:3px}
.ret.up{color:var(--up);background:#0d2818}.ret.dn{color:var(--dn);background:#1a0d0d}
.filing-links{display:flex;align-items:center;gap:1.25rem;font-size:0.65rem}
.filing-links a{color:var(--text3)}.filing-links a:hover{color:var(--blue)}
.acc{color:var(--text3);font-size:0.6rem}
footer{
  padding:1rem 2rem 1.25rem;font-size:0.6rem;color:var(--text3);
  border-top:1px solid var(--border);max-width:1100px;line-height:2.2;
}
footer a{color:var(--text3)}.footer a:hover{color:var(--text)}
.empty{padding:3rem 0;text-align:center;font-size:0.72rem;color:var(--text3)}
"""

def nav(active: str = "") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    def lnk(href, label):
        cls = ' class="active"' if active == label else ""
        return f'<a href="{href}"{cls}>{label}</a>'
    return f"""<nav>
  <span class="nav-brand">{BRAND}</span>
  <div class="nav-links">
    {lnk("/digest","digest")}
    {lnk("/filings","filings")}
    {lnk("/radar/executives","exec radar")}
    {lnk("/events/cyber","events")}
    {lnk("/api/filings","api")}
    {lnk("/disclaimer","disclaimer")}
  </div>
  <div class="nav-right">{now}</div>
</nav>"""

def footer_html() -> str:
    return f"""<footer>
  <div>data: <a href="https://sec.gov">sec.gov</a> · <a href="https://bseindia.com">bseindia.com</a> · <a href="/disclaimer">disclaimer</a></div>
  <div>veris · corporate filing intelligence · not investment advice · {datetime.now().strftime("%Y-%m-%d")}</div>
</footer>"""

def fmt_ret(v):
    if v is None: return "—"
    v = float(v)
    return ("+" if v >= 0 else "") + f"{v:.2f}%"

def ret_cls(v):
    if v is None: return ""
    return "up" if float(v) >= 0 else "dn"

def edgar_url(f):
    cik = f["accession_no"].split("-")[0].lstrip("0")
    acc = f["accession_no"].replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{f['accession_no']}-index.htm"

def source_url(f):
    """Return correct source URL for US (EDGAR) or IN (BSE) filings."""
    mkt = f.get("market", "US")
    if mkt == "IN":
        scrip = f.get("scrip_code", "")
        return f"https://www.bseindia.com/corporates/ann.html?scrip={scrip}" if scrip else "https://bseindia.com"
    return edgar_url(f)

URGENCY_GLYPH = {"high": "●", "medium": "◐", "low": "○"}
EVT_CSS = {
    "cybersecurity": "evt-cybersecurity", "M&A": "evt-ma",
    "leadership_change": "evt-leadership-change", "earnings": "evt-earnings",
    "litigation": "evt-litigation", "debt_financing": "evt-debt-financing",
    "regulatory": "evt-regulatory", "dividend": "evt-dividend",
    "share_buyback": "evt-share-buyback", "restatement": "evt-restatement",
    "other": "evt-other",
}
EVT_LABEL = {
    "cybersecurity": "cyber", "M&A": "m&a", "leadership_change": "leadership",
    "earnings": "earnings", "litigation": "litigation", "debt_financing": "debt",
    "regulatory": "regulatory", "dividend": "dividend",
    "share_buyback": "buyback", "restatement": "restatement", "other": "other",
}

def filing_card(f: dict) -> str:
    risks = json.loads(f["risk_flags"]) if f.get("risk_flags") else []
    impact_raw = (f.get("thesis_impact") or "neutral").split("—", 1)
    direction  = impact_raw[0].strip().lower()
    impact_txt = impact_raw[1].strip() if len(impact_raw) > 1 else ""
    r1c = ret_cls(f.get("return_1d")); r5c = ret_cls(f.get("return_5d"))
    evt = f.get("event_type", "other")
    urg = f.get("urgency", "low")
    mkt = f.get("market", "US")
    url = source_url(f)
    dir_glyph = {"bullish": "▲", "bearish": "▼", "neutral": "—"}.get(direction, "—")
    dir_cls   = {"bullish": "up", "bearish": "dn", "neutral": "flat"}.get(direction, "flat")
    mat_cal   = f.get("calibrated_materiality") or f.get("materiality", 5)
    risk_txt  = " · ".join(risks) if risks else "no material flags"
    val       = f.get("valuation_note") or "no quantitative impact identified"

    market_badge = f'<span class="meta-market">{mkt}</span>'

    return f"""<div class="filing">
  <div class="filing-meta">
    <span class="meta-date">{f.get("file_date","")}</span>
    <a class="meta-ticker" href="/ticker/{f.get('ticker','')}">{f.get("ticker","")}</a>
    {market_badge}
    <span class="meta-evt {EVT_CSS.get(evt,'evt-other')}">{EVT_LABEL.get(evt,evt)}</span>
    <span class="meta-mat">materiality <strong>{mat_cal/10:.2f}</strong></span>
    <span class="meta-urg urg-{urg}">{URGENCY_GLYPH.get(urg,"○")} {urg}</span>
  </div>
  <div class="filing-summary">
    <span class="dir {dir_cls}">{dir_glyph}</span> {f.get("summary","")}
  </div>
  <div class="filing-detail">
    <div class="detail-block">
      <span class="detail-label">thesis</span>
      <span class="detail-val">{impact_txt}</span>
    </div>
    <div class="detail-block">
      <span class="detail-label">valuation</span>
      <span class="detail-val">{val}</span>
    </div>
    <div class="detail-block">
      <span class="detail-label">risk flags</span>
      <span class="detail-val dim">{risk_txt}</span>
    </div>
    <div class="detail-block returns-block">
      <span class="detail-label">price reaction</span>
      <span class="ret {r1c}">1D {fmt_ret(f.get("return_1d"))}</span>
      <span class="ret {r5c}">5D {fmt_ret(f.get("return_5d"))}</span>
    </div>
  </div>
  <div class="filing-links">
    <a href="/filing/{f.get('accession_no','')}">detail ↗</a>
    <a href="{url}" target="_blank">sec source ↗</a>
    <a href="/filing/{f.get('accession_no','')}/json">json</a>
    <a href="/filing/{f.get('accession_no','')}/md">md</a>
    <span class="acc">{f.get("accession_no","")}</span>
  </div>
</div>"""

def fetch_filings(days: int = 7, limit: int = 200) -> list[dict]:
    cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            # Check price_reactions table exists
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            price_join = "LEFT JOIN price_reactions p ON f.id = p.filing_id" \
                         if "price_reactions" in tables else ""
            price_cols = "p.return_1d, p.return_5d," if "price_reactions" in tables else "NULL as return_1d, NULL as return_5d,"
            market_col = "f.market," if "market" in {
                r[1] for r in conn.execute("PRAGMA table_info(filings)")
            } else "'US' as market,"

            rows = conn.execute(f"""
                SELECT f.ticker, f.company_name, f.file_date, f.accession_no,
                       {market_col}
                       a.event_type, a.summary,
                       a.materiality,
                       COALESCE(a.calibrated_materiality, a.materiality) AS calibrated_materiality,
                       a.urgency, a.thesis_impact, a.risk_flags, a.valuation_note,
                       {price_cols}
                       a.analyzed_at
                FROM filings f
                JOIN analyses a ON f.id = a.filing_id
                {price_join}
                WHERE f.file_date >= ? AND a.status = 'success'
                ORDER BY calibrated_materiality DESC, f.file_date DESC
                LIMIT ?
            """, (cutoff, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Pipeline ──────────────────────────────────────────────────────────────────
def run_pipeline():
    print(f"\n[{datetime.now().isoformat()}] Veris pipeline started")
    try:
        ingest.run()
        analyze.run()
        enrich.run()
        memo.run()
        print(f"[{datetime.now().isoformat()}] Pipeline complete")
    except Exception:
        print(f"[{datetime.now().isoformat()}] Pipeline FAILED:")
        traceback.print_exc()

scheduler = BackgroundScheduler()
scheduler.add_job(run_pipeline, trigger="cron", day_of_week="sun", hour=0, minute=0, id="weekly")
scheduler.start()


@app.on_event("startup")
async def startup_event():
    """
    On first boot, if analyses table is empty, run the full pipeline
    in a background thread. No console needed.
    """
    import threading
    try:
        with sqlite3.connect(DB_FILE) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "analyses" not in tables:
                analyzed = 0
            else:
                analyzed = conn.execute(
                    "SELECT COUNT(*) FROM analyses WHERE status='success'"
                ).fetchone()[0]
        if analyzed == 0:
            print("[startup] No analyzed filings found — triggering pipeline...")
            threading.Thread(target=run_pipeline, daemon=True).start()
        else:
            print(f"[startup] {analyzed} analyzed filings found — skipping auto-run.")
    except Exception as e:
        print(f"[startup] Error checking DB: {e} — triggering pipeline anyway...")
        threading.Thread(target=run_pipeline, daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
@app.get("/digest", response_class=HTMLResponse)
def digest():
    """Daily digest — top filings from the last 24 hours by calibrated materiality."""
    filings = fetch_filings(days=1, limit=50)
    high   = [f for f in filings if (f.get("calibrated_materiality") or f["materiality"]) >= 7]
    medium = [f for f in filings if 4 <= (f.get("calibrated_materiality") or f["materiality"]) <= 6]
    low    = [f for f in filings if (f.get("calibrated_materiality") or f["materiality"]) <= 3]

    today = datetime.today().strftime("%B %d, %Y")

    def section(label, items, count_label):
        if not items: return ""
        cards = "".join(filing_card(f) for f in items)
        return f"""<div class="section-rule">{label}<span>{len(items)} filing{"s" if len(items)!=1 else ""}</span></div>
{cards}"""

    body = (
        section("High priority — materiality ≥ 0.70", high, "high") +
        section("Standard review — materiality 0.40–0.69", medium, "medium") +
        section("Routine — materiality < 0.40", low, "low")
    ) or '<div class="empty">No filings in the last 24 hours. Pipeline runs weekly — check back Sunday.</div>'

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veris · Daily Digest · {today}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>{SHARED_CSS}</style>
</head>
<body>
{nav("digest")}
<div class="hero">
  <div class="hero-label">Veris · Daily Digest · {today}</div>
  <h1 class="hero-title">Corporate filing intelligence — last 24 hours</h1>
  <p class="hero-sub">
    High-signal filings selected by calibrated materiality score.
    Machine-generated summaries. Always verify against the primary source document.
  </p>
</div>
<div class="content">
  <div style="padding:1rem 0;font-size:0.65rem;color:var(--text3)">
    {len(filings)} filing{"s" if len(filings)!=1 else ""} today &nbsp;·&nbsp;
    {len(high)} high priority
  </div>
  {body}
</div>
{footer_html()}
</body></html>""")


@app.get("/filings", response_class=HTMLResponse)
def filings_page():
    """Full weekly filing feed — last 7 days."""
    filings = fetch_filings(days=7, limit=200)
    high   = [f for f in filings if (f.get("calibrated_materiality") or f["materiality"]) >= 7]
    medium = [f for f in filings if 4 <= (f.get("calibrated_materiality") or f["materiality"]) <= 6]
    low    = [f for f in filings if (f.get("calibrated_materiality") or f["materiality"]) <= 3]
    week   = datetime.today().strftime("Week of %B %d, %Y")

    def section(label, items):
        if not items: return ""
        cards = "".join(filing_card(f) for f in items)
        return f"""<div class="section-rule">{label}<span>{len(items)} filing{"s" if len(items)!=1 else ""}</span></div>
{cards}"""

    body = (
        section("High priority — materiality ≥ 0.70", high) +
        section("Standard review — materiality 0.40–0.69", medium) +
        section("Routine — materiality < 0.40", low)
    ) or '<div class="empty">No filings this week. Run the pipeline first.</div>'

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veris · Filings · {week}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>{SHARED_CSS}</style>
</head>
<body>
{nav("filings")}
<div class="hero">
  <div class="hero-label">Veris · Filing Feed · {week}</div>
  <h1 class="hero-title">All corporate events — last 7 days</h1>
  <p class="hero-sub">
    {len(filings)} filings tracked across U.S. and Indian markets.
    Sorted by calibrated materiality score.
  </p>
</div>
<div class="content">{body}</div>
{footer_html()}
</body></html>""")


@app.get("/disclaimer", response_class=HTMLResponse)
def disclaimer():
    """Standalone disclaimer page."""
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veris · Disclaimer</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>
{SHARED_CSS}
.disc-body{{max-width:680px;padding:3rem 2rem 5rem}}
.disc-body h2{{font-size:0.72rem;letter-spacing:0.14em;text-transform:uppercase;
               color:var(--text3);margin:2rem 0 0.75rem;font-weight:400}}
.disc-body h2:first-child{{margin-top:0}}
.disc-body p{{font-size:0.8rem;color:var(--text2);line-height:1.8;margin-bottom:0.75rem}}
.disc-body strong{{color:var(--text);font-weight:500}}
.disc-body a{{color:var(--blue)}}
.disc-rule{{height:1px;background:var(--border);margin:2rem 0}}
.notice-box{{
  border:1px solid var(--border2);padding:1.25rem 1.5rem;margin-bottom:2rem;
  background:rgba(248,81,73,0.04);border-left:3px solid var(--dn);
}}
.notice-box p{{color:var(--text);margin:0;font-size:0.78rem;line-height:1.75}}
</style>
</head>
<body>
{nav("disclaimer")}
<div class="hero">
  <div class="hero-label">Veris · Legal Disclaimer &amp; Methodology</div>
  <h1 class="hero-title">What this is — and what it is not</h1>
  <p class="hero-sub">Read this before using Veris for any purpose beyond curiosity.</p>
</div>
<div class="disc-body">

  <div class="notice-box">
    <p>
      <strong>Veris is a research and educational tool only.</strong>
      Nothing on this platform constitutes investment advice, a recommendation
      to buy or sell any security, or a solicitation of any investment decision.
      You proceed entirely at your own risk.
    </p>
  </div>

  <h2>What Veris does</h2>
  <p>
    Veris collects publicly available corporate disclosure filings from
    <a href="https://sec.gov" target="_blank">SEC EDGAR</a> (United States) and
    <a href="https://bseindia.com" target="_blank">BSE India</a> (India).
    It uses a Large Language Model (Google Gemini) to generate summaries,
    classify event types, and assign materiality scores.
    These outputs are machine-generated and uncorrected unless otherwise stated.
  </p>

  <h2>The primary source is always the original filing</h2>
  <p>
    Every filing page on Veris links directly to the source document on EDGAR or BSE.
    <strong>A Veris summary can help you decide what to read first —
    it must not be the final authority for legal, trading, or compliance decisions.</strong>
    The original filing document is the sole source of truth.
  </p>
  <p>
    Extraction quality depends entirely on the text available in the source document.
    Older filings, scanned exhibits, and complex XBRL documents may produce
    incomplete or inaccurate summaries. Always cross-check.
  </p>

  <h2>Materiality scores are estimates</h2>
  <p>
    The materiality score (0.00–1.00) is a raw model estimate, subsequently adjusted
    by deterministic calibration rules that cap known routine events.
    It reflects signal strength for research triage — not legal materiality
    as defined by securities law. Do not use it as a compliance determination.
  </p>

  <h2>Executive Radar — source-linked facts only</h2>
  <p>
    The Executive Movement Radar extracts structured facts from Item 5.02
    (U.S.) filings at temperature zero — no inference, no creative interpretation.
    Each claim is paired with the exact sentence from the filing that supports it.
    Where evidence cannot be located, facts are not published.
  </p>

  <h2>Not real-time. Not comprehensive.</h2>
  <p>
    Veris runs on a weekly pipeline. Data is not live and does not constitute
    real-time market information. Coverage is limited to a defined watchlist
    of U.S. and Indian equities. It does not claim to cover all listed companies
    or all filing types.
  </p>

  <h2>No liability</h2>
  <p>
    The authors of Veris accept no liability for losses, damages, or decisions
    made on the basis of information displayed here. Past filing events and
    their associated price reactions are historical data — they do not predict
    future performance.
  </p>

  <div class="disc-rule"></div>

  <h2>Data sources</h2>
  <p>
    United States: <a href="https://sec.gov" target="_blank">SEC EDGAR</a>
    — public domain, U.S. Securities and Exchange Commission.<br>
    India: <a href="https://bseindia.com" target="_blank">BSE India</a>
    — public corporate announcements feed.<br>
    Market data: Yahoo Finance (via yfinance) — for educational price reaction analysis only.
  </p>

  <h2>Contact</h2>
  <p>
    Questions or corrections? This is a student research project.
    Data inaccuracies should be verified against the primary EDGAR or BSE source document.
  </p>

</div>
{footer_html()}
</body></html>""")


@app.get("/radar/executives", response_class=HTMLResponse)
def exec_radar():
    """Executive Movement Radar — structured Item 5.02 fact table."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            rows = conn.execute("""
                SELECT ticker, company_name, file_date,
                       departures, appointments, succession_note
                FROM executive_radar
                WHERE status = 'success' AND item_5_02_present = 1
                ORDER BY file_date DESC LIMIT 100
            """).fetchall() if "executive_radar" in tables else []
    except Exception:
        rows = []

    events = []
    for row in rows:
        r = dict(row)
        r["departures"]   = json.loads(r["departures"])   if r["departures"]   else []
        r["appointments"] = json.loads(r["appointments"]) if r["appointments"] else []
        events.append(r)

    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    def dep_rows(e):
        out = ""
        for d in e["departures"]:
            vol = "voluntary" if d.get("voluntary") is True else \
                  "involuntary" if d.get("voluntary") is False else "—"
            out += f"""<tr class="dep-row">
  <td class="cell-type dep">↓ departure</td>
  <td class="cell-name">{d.get("name","—")}</td>
  <td class="cell-role">{d.get("role","—")}</td>
  <td class="cell-dtype">{d.get("departure_type","—")}</td>
  <td class="cell-vol">{vol}</td>
  <td class="cell-date">{d.get("effective_date") or "—"}</td>
  <td class="cell-evidence">{d.get("evidence","—")}</td>
</tr>"""
        for a in e["appointments"]:
            promo = "internal" if a.get("internal_promotion") is True else \
                    "external" if a.get("internal_promotion") is False else "—"
            out += f"""<tr class="apt-row">
  <td class="cell-type apt">↑ appointment</td>
  <td class="cell-name">{a.get("name","—")}</td>
  <td class="cell-role">{a.get("role","—")}</td>
  <td class="cell-dtype">{a.get("appointment_type","—")}</td>
  <td class="cell-vol">{promo}</td>
  <td class="cell-date">{a.get("effective_date") or "—"}</td>
  <td class="cell-evidence">{a.get("evidence","—")}</td>
</tr>"""
        return out or '<tr><td colspan="7" class="no-data">No structured facts extracted</td></tr>'

    blocks = ""
    for e in events:
        suc = f'<div class="succession">⟶ {e["succession_note"]}</div>' if e.get("succession_note") else ""
        blocks += f"""<div class="event-block">
  <div class="event-hd">
    <span class="ev-ticker">{e["ticker"]}</span>
    <span class="ev-company">{e["company_name"]}</span>
    <span class="ev-date">{e["file_date"]}</span>
  </div>
  {suc}
  <table class="radar-table"><thead><tr>
    <th>Type</th><th>Name</th><th>Role</th><th>Sub-type</th>
    <th>Voluntary / Origin</th><th>Effective</th><th>Source Evidence</th>
  </tr></thead><tbody>{dep_rows(e)}</tbody></table>
</div>"""

    if not events:
        blocks = '<div class="empty">No executive movement events found. Run the pipeline first.</div>'

    radar_css = """
.event-block{margin-top:2rem;padding-top:1.5rem;border-top:1px solid var(--border)}
.event-hd{display:flex;align-items:center;gap:1rem;margin-bottom:0.6rem}
.ev-ticker{font-size:0.85rem;font-weight:500;color:var(--blue);letter-spacing:0.04em}
.ev-company{font-size:0.8rem;color:var(--text)}
.ev-date{font-size:0.65rem;color:var(--text3);margin-left:auto}
.succession{font-size:0.7rem;color:var(--amber);margin-bottom:0.75rem}
.radar-table{width:100%;border-collapse:collapse;font-size:0.68rem;margin-top:0.5rem}
.radar-table th{text-align:left;padding:0.4rem 0.75rem;font-size:0.58rem;
  letter-spacing:0.12em;text-transform:uppercase;color:var(--text3);
  border-bottom:1px solid var(--border2);font-weight:400}
.radar-table td{padding:0.55rem 0.75rem;border-bottom:1px solid var(--border);vertical-align:top;line-height:1.5}
.dep-row td{background:rgba(248,81,73,0.03)}
.apt-row td{background:rgba(63,185,80,0.03)}
.cell-type{font-size:0.62rem;font-weight:500;white-space:nowrap}
.cell-type.dep{color:var(--dn)}.cell-type.apt{color:var(--up)}
.cell-name{color:var(--text);font-weight:500}.cell-role{color:var(--text2)}
.cell-dtype,.cell-vol{color:var(--text3)}
.cell-date{color:var(--text3);white-space:nowrap;font-size:0.62rem}
.cell-evidence{color:var(--text3);font-style:italic;font-size:0.65rem;max-width:28rem;line-height:1.55}
.no-data{color:var(--text3);font-style:italic;text-align:center;padding:1rem}
"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veris · Executive Movement Radar</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>{SHARED_CSS}{radar_css}</style>
</head>
<body>
{nav("exec radar")}
<div class="hero">
  <div class="hero-label">Veris · Executive Movement Radar · Item 5.02</div>
  <h1 class="hero-title">Executive departure &amp; appointment tracker</h1>
  <p class="hero-sub">
    Structured facts extracted from SEC 8-K Item 5.02 filings at temperature zero.
    Every claim is paired with the exact sentence from the source document.
    Not summaries — evidence.
  </p>
</div>
<div class="content">
  <div style="padding:1rem 0;font-size:0.65rem;color:var(--text3)">
    {len(events)} leadership event{"s" if len(events)!=1 else ""} tracked
    &nbsp;·&nbsp; <a href="/disclaimer" style="color:var(--text3)">disclaimer</a>
  </div>
  {blocks}
</div>
{footer_html()}
</body></html>""")



@app.get("/filing/{accession_no}", response_class=HTMLResponse)
def filing_page(accession_no: str):
    """Individual filing detail page with JSON/MD/Text download links."""
    acc_norm = accession_no

    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT f.*, 
                       a.event_type, a.summary, a.materiality,
                       COALESCE(a.calibrated_materiality, a.materiality) AS calibrated_materiality,
                       a.urgency, a.thesis_impact, a.risk_flags, a.valuation_note,
                       a.analyzed_at,
                       p.return_1d, p.return_5d
                FROM filings f
                LEFT JOIN analyses a ON f.id = a.filing_id
                LEFT JOIN price_reactions p ON f.id = p.filing_id
                WHERE f.accession_no = ?
            """, (acc_norm,)).fetchone()
    except Exception as e:
        return HTMLResponse(f"<pre>DB error: {e}</pre>", status_code=500)

    if not row:
        return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Veris · Not Found</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>{SHARED_CSS}</style></head>
<body>{nav()}
<div class="hero"><h1 class="hero-title">Filing not found</h1>
<p class="hero-sub">{acc_norm}</p></div>
{footer_html()}</body></html>""", status_code=404)

    f = dict(row)
    mkt       = f.get("market", "US")
    url       = source_url(f)
    risks     = json.loads(f["risk_flags"]) if f.get("risk_flags") else []
    mat_cal   = f.get("calibrated_materiality") or f.get("materiality", 5)
    urg       = f.get("urgency", "low")
    evt       = f.get("event_type", "other")
    direction = (f.get("thesis_impact") or "neutral").split("—")[0].strip().lower()
    dir_glyph = {"bullish":"▲","bearish":"▼","neutral":"—"}.get(direction,"—")
    dir_cls   = {"bullish":"up","bearish":"dn","neutral":"flat"}.get(direction,"flat")

    # Machine-readable links
    api_base = f"/filing/{acc_norm}"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veris · {f.get("ticker","")} · {acc_norm}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>{SHARED_CSS}
.filing-detail-grid{{display:grid;grid-template-columns:1fr 260px;gap:2rem;margin-top:1.5rem}}
.detail-main{{}}
.detail-sidebar{{}}
.sidebar-box{{border:1px solid var(--border);padding:1rem;margin-bottom:1rem;border-radius:3px}}
.sidebar-box-title{{font-size:0.58rem;letter-spacing:0.14em;color:var(--text3);
  text-transform:uppercase;margin-bottom:0.75rem;border-bottom:1px solid var(--border);padding-bottom:0.5rem}}
.dl-link{{display:block;font-size:0.68rem;color:var(--text3);padding:0.3rem 0;
  border-bottom:1px solid var(--border);text-decoration:none}}
.dl-link:hover{{color:var(--blue)}}
.dl-link:last-child{{border-bottom:none}}
.fact-row{{display:flex;gap:0.75rem;font-size:0.7rem;padding:0.3rem 0;
  border-bottom:1px solid var(--border);align-items:baseline}}
.fact-key{{color:var(--text3);min-width:7rem;flex-shrink:0;font-size:0.62rem;letter-spacing:0.06em}}
.fact-val{{color:var(--text2)}}
.evidence-block{{margin-top:1.5rem;padding:1rem;border-left:2px solid var(--border2);
  font-size:0.72rem;color:var(--text3);line-height:1.7;font-style:italic}}
@media(max-width:700px){{.filing-detail-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
{nav()}
<div class="hero">
  <div class="hero-label">Veris · Filing Detail · {mkt}</div>
  <h1 class="hero-title">
    <a href="/ticker/{f.get('ticker','')}" style="color:var(--blue)">{f.get("ticker","")}</a>
    <span style="font-weight:300;color:var(--text2);font-size:0.85rem"> {f.get("company_name","")}</span>
  </h1>
  <p class="hero-sub">{acc_norm} &nbsp;·&nbsp; filed {f.get("file_date","")} &nbsp;·&nbsp;
    <span class="meta-evt {EVT_CSS.get(evt,'evt-other')}">{EVT_LABEL.get(evt,evt)}</span>
    &nbsp;·&nbsp; materiality <strong>{mat_cal/10:.2f}</strong>
  </p>
</div>
<div class="content">
  <div class="filing-detail-grid">
    <div class="detail-main">
      <div class="filing-summary">
        <span class="dir {dir_cls}">{dir_glyph}</span>
        {f.get("summary","No summary available.")}
      </div>
      <div class="filing-detail" style="margin-top:1.25rem">
        <div class="detail-block">
          <span class="detail-label">thesis</span>
          <span class="detail-val">{(f.get("thesis_impact") or "").split("—",1)[1].strip() if "—" in (f.get("thesis_impact") or "") else (f.get("thesis_impact") or "—")}</span>
        </div>
        <div class="detail-block">
          <span class="detail-label">valuation</span>
          <span class="detail-val">{f.get("valuation_note") or "No quantitative impact identified."}</span>
        </div>
        <div class="detail-block">
          <span class="detail-label">risk flags</span>
          <span class="detail-val dim">{" · ".join(risks) if risks else "no material flags"}</span>
        </div>
        <div class="detail-block returns-block">
          <span class="detail-label">price reaction</span>
          <span class="ret {("up" if float(f["return_1d"])>0 else "dn") if f.get("return_1d") is not None else ""}">1D {fmt_ret(f.get("return_1d"))}</span>
          <span class="ret {("up" if float(f["return_5d"])>0 else "dn") if f.get("return_5d") is not None else ""}">5D {fmt_ret(f.get("return_5d"))}</span>
        </div>
      </div>
    </div>

    <div class="detail-sidebar">
      <div class="sidebar-box">
        <div class="sidebar-box-title">Filing metadata</div>
        <div class="fact-row"><span class="fact-key">ticker</span><span class="fact-val"><a href="/ticker/{f.get('ticker','')}" style="color:var(--blue)">{f.get("ticker","")}</a></span></div>
        <div class="fact-row"><span class="fact-key">company</span><span class="fact-val">{f.get("company_name","")}</span></div>
        <div class="fact-row"><span class="fact-key">filed</span><span class="fact-val">{f.get("file_date","")}</span></div>
        <div class="fact-row"><span class="fact-key">form</span><span class="fact-val">{f.get("form_type","8-K")}</span></div>
        <div class="fact-row"><span class="fact-key">market</span><span class="fact-val">{mkt}</span></div>
        <div class="fact-row"><span class="fact-key">accession</span><span class="fact-val" style="font-size:0.6rem;word-break:break-all">{acc_norm}</span></div>
        <div class="fact-row"><span class="fact-key">urgency</span><span class="fact-val meta-urg urg-{urg}">{URGENCY_GLYPH.get(urg,"○")} {urg}</span></div>
        <div class="fact-row"><span class="fact-key">analyzed</span><span class="fact-val">{(f.get("analyzed_at") or "")[:16]}</span></div>
      </div>

      <div class="sidebar-box">
        <div class="sidebar-box-title">Machine-readable</div>
        <a class="dl-link" href="{api_base}/json">JSON ↗</a>
        <a class="dl-link" href="{api_base}/md">Markdown ↗</a>
        <a class="dl-link" href="{api_base}/txt">Plain text ↗</a>
        <a class="dl-link" href="{url}" target="_blank">SEC source ↗</a>
      </div>
    </div>
  </div>
</div>
{footer_html()}
</body></html>""")


@app.get("/filing/{accession_no}/json")
def filing_json(accession_no: str):
    """JSON representation of a single filing."""
    acc_norm = accession_no.strip("/")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT f.ticker, f.company_name, f.cik, f.accession_no,
                       f.file_date, f.form_type, f.filing_url,
                       COALESCE(f.market,'US') AS market,
                       a.event_type, a.summary, a.materiality,
                       COALESCE(a.calibrated_materiality, a.materiality) AS calibrated_materiality,
                       a.urgency, a.thesis_impact, a.risk_flags, a.valuation_note,
                       a.analyzed_at, p.return_1d, p.return_5d
                FROM filings f
                LEFT JOIN analyses a ON f.id = a.filing_id
                LEFT JOIN price_reactions p ON f.id = p.filing_id
                WHERE f.accession_no = ?
            """, (acc_norm,)).fetchone()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    f = dict(row)
    f["risk_flags"]  = json.loads(f["risk_flags"]) if f.get("risk_flags") else []
    f["source_url"]  = source_url(f)
    f["filing_page"] = f"/filing/{acc_norm}"
    return JSONResponse(f)


@app.get("/filing/{accession_no}/md")
def filing_markdown(accession_no: str):
    """Markdown representation of a single filing."""
    acc_norm = accession_no.strip("/")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT f.ticker, f.company_name, f.file_date, f.accession_no,
                       COALESCE(f.market,'US') AS market,
                       a.event_type, a.summary, a.materiality,
                       COALESCE(a.calibrated_materiality, a.materiality) AS calibrated_materiality,
                       a.urgency, a.thesis_impact, a.risk_flags, a.valuation_note,
                       p.return_1d, p.return_5d
                FROM filings f
                LEFT JOIN analyses a ON f.id = a.filing_id
                LEFT JOIN price_reactions p ON f.id = p.filing_id
                WHERE f.accession_no = ?
            """, (acc_norm,)).fetchone()
    except Exception as e:
        return Response(f"Error: {e}", media_type="text/plain", status_code=500)
    if not row:
        return Response("Not found", media_type="text/plain", status_code=404)
    f  = dict(row)
    rs = json.loads(f["risk_flags"]) if f.get("risk_flags") else []
    md = f"""# {f["company_name"]} ({f["ticker"]}) — {EVT_LABEL.get(f["event_type"],"other")}

**Accession:** {acc_norm}  
**Filed:** {f["file_date"]}  
**Market:** {f.get("market","US")}  
**Materiality:** {(f.get("calibrated_materiality") or f.get("materiality",5))/10:.2f}  
**Urgency:** {f.get("urgency","").upper()}  

## Summary
{f.get("summary","—")}

## Thesis Impact
{f.get("thesis_impact","—")}

## Valuation Note
{f.get("valuation_note","—")}

## Risk Flags
{chr(10).join(f"- {r}" for r in rs) if rs else "None identified"}

## Price Reaction
1D: {fmt_ret(f.get("return_1d"))} | 5D: {fmt_ret(f.get("return_5d"))}

---
*Source: {source_url(f)}*  
*Generated by Veris · Not investment advice*
"""
    return Response(md, media_type="text/markdown")


@app.get("/filing/{accession_no}/txt")
def filing_text(accession_no: str):
    """Plain text of the raw filing."""
    acc_norm = accession_no.strip("/")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            row = conn.execute(
                "SELECT raw_text, company_name, ticker FROM filings WHERE accession_no = ?",
                (acc_norm,)
            ).fetchone()
    except Exception as e:
        return Response(f"Error: {e}", media_type="text/plain", status_code=500)
    if not row:
        return Response("Not found", media_type="text/plain", status_code=404)
    return Response(
        f"{row[1]} ({row[2]}) — {acc_norm}\n\n{row[0] or '[No text available]'}",
        media_type="text/plain"
    )



@app.get("/events/{event_type}", response_class=HTMLResponse)
def event_page(event_type: str):
    """Event landing page — all recent filings of one event type."""
    # Map URL slug to DB event_type
    slug_map = {
        "cyber": "cybersecurity", "cybersecurity": "cybersecurity",
        "ma": "M&A", "m_and_a": "M&A", "mergers": "M&A",
        "leadership": "leadership_change", "executives": "leadership_change",
        "earnings": "earnings", "debt": "debt_financing", "financing": "debt_financing",
        "litigation": "litigation", "regulatory": "regulatory",
        "dividend": "dividend", "dividends": "dividend",
        "buyback": "share_buyback", "buybacks": "share_buyback",
        "restatement": "restatement", "other": "other",
    }
    db_event = slug_map.get(event_type.lower())
    if not db_event:
        return HTMLResponse("Unknown event type", status_code=404)

    evt_display = EVT_LABEL.get(db_event, db_event)

    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            price_join = "LEFT JOIN price_reactions p ON f.id = p.filing_id" \
                         if "price_reactions" in tables else ""
            price_cols = "p.return_1d, p.return_5d," \
                         if "price_reactions" in tables else "NULL as return_1d, NULL as return_5d,"

            rows = conn.execute(f"""
                SELECT f.ticker, f.company_name, f.file_date, f.accession_no,
                       COALESCE(f.market,'US') AS market, f.scrip_code,
                       a.event_type, a.summary, a.materiality,
                       COALESCE(a.calibrated_materiality, a.materiality) AS calibrated_materiality,
                       a.urgency, a.thesis_impact, a.risk_flags, a.valuation_note,
                       {price_cols}
                       a.analyzed_at
                FROM filings f
                JOIN analyses a ON f.id = a.filing_id
                {price_join}
                WHERE a.event_type = ? AND a.status = 'success'
                ORDER BY f.file_date DESC, calibrated_materiality DESC
                LIMIT 100
            """, (db_event,)).fetchall()
    except Exception as e:
        rows = []

    filings = [dict(r) for r in rows]
    cards   = "".join(filing_card(f) for f in filings)
    count   = len(filings)

    # Event nav links
    event_nav_items = [
        ("cyber","Cyber"),("earnings","Earnings"),("ma","M&A"),
        ("leadership","Leadership"),("litigation","Litigation"),
        ("debt","Debt"),("regulatory","Regulatory"),
        ("dividend","Dividend"),("buyback","Buyback"),("other","Other"),
    ]
    event_nav = " &nbsp;·&nbsp; ".join(
        f'<a href="/events/{slug}" style="color:{'var(--text)' if slug == event_type.lower() else 'var(--text3)'}">{label}</a>'
        for slug, label in event_nav_items
    )

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veris · {evt_display} events</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>{SHARED_CSS}</style>
</head>
<body>
{nav()}
<div class="hero">
  <div class="hero-label">Veris · Event Feed</div>
  <h1 class="hero-title">{evt_display} events</h1>
  <p class="hero-sub" style="margin-bottom:1rem">{count} filing{"s" if count!=1 else ""} on record</p>
  <div style="font-size:0.65rem;color:var(--text3)">{event_nav}</div>
</div>
<div class="content">
  {cards if cards else '<div class="empty">No filings of this type found. Run the pipeline first.</div>'}
</div>
{footer_html()}
</body></html>""")


@app.get("/ticker/{ticker_symbol}", response_class=HTMLResponse)
def ticker_page(ticker_symbol: str):
    """All filings for a single company in reverse chronological order."""
    ticker_upper = ticker_symbol.upper()

    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            # Get company name
            meta = conn.execute(
                "SELECT company_name, market, scrip_code FROM filings WHERE ticker = ? LIMIT 1",
                (ticker_upper,)
            ).fetchone()

            if not meta:
                return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Veris · {ticker_upper}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>{SHARED_CSS}</style></head>
<body>{nav()}
<div class="hero">
  <div class="hero-label">Veris · Ticker</div>
  <h1 class="hero-title">{ticker_upper}</h1>
  <p class="hero-sub">No filings found for this ticker. Run the pipeline first.</p>
</div>
{footer_html()}</body></html>""", status_code=404)

            company_name = meta["company_name"]
            market       = meta["market"] or "US"

            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            price_join = "LEFT JOIN price_reactions p ON f.id = p.filing_id"                          if "price_reactions" in tables else ""
            price_cols = "p.return_1d, p.return_5d,"                          if "price_reactions" in tables else "NULL as return_1d, NULL as return_5d,"

            rows = conn.execute(f"""
                SELECT f.ticker, f.company_name, f.file_date, f.accession_no,
                       COALESCE(f.market,'US') AS market, f.scrip_code,
                       a.event_type, a.summary,
                       a.materiality,
                       COALESCE(a.calibrated_materiality, a.materiality) AS calibrated_materiality,
                       a.urgency, a.thesis_impact, a.risk_flags, a.valuation_note,
                       {price_cols}
                       a.analyzed_at
                FROM filings f
                JOIN analyses a ON f.id = a.filing_id
                {price_join}
                WHERE f.ticker = ? AND a.status = 'success'
                ORDER BY f.file_date DESC
                LIMIT 100
            """, (ticker_upper,)).fetchall()

    except Exception as e:
        return HTMLResponse(f"<pre>Error: {e}</pre>", status_code=500)

    filings = [dict(r) for r in rows]
    cards   = "".join(filing_card(f) for f in filings)

    # Simple stats
    total     = len(filings)
    avg_mat   = sum((f.get("calibrated_materiality") or f["materiality"]) for f in filings) / total if total else 0
    high_ct   = sum(1 for f in filings if (f.get("calibrated_materiality") or f["materiality"]) >= 7)
    event_cts = {}
    for f in filings:
        evt = EVT_LABEL.get(f["event_type"], f["event_type"])
        event_cts[evt] = event_cts.get(evt, 0) + 1
    top_events = sorted(event_cts.items(), key=lambda x: x[1], reverse=True)[:4]

    stats_row = " &nbsp;·&nbsp; ".join(
        f'<span style="color:var(--text)">{n}</span> {e}'
        for e, n in top_events
    )

    market_badge = f'<span style="font-size:0.65rem;padding:0.1rem 0.4rem;border-radius:2px;background:#0d1a2a;color:#58a6ff;border:1px solid #1e2d45;margin-left:0.5rem">{market}</span>'

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veris · {ticker_upper}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>{SHARED_CSS}
.ticker-hero-stats{{display:flex;gap:2.5rem;margin-top:1.25rem;flex-wrap:wrap}}
.t-stat{{font-size:0.68rem;color:var(--text2)}}
.t-stat strong{{color:var(--text);font-size:1rem;display:block;margin-bottom:0.1rem;font-weight:500}}
</style>
</head>
<body>
{nav()}
<div class="hero">
  <div class="hero-label">Veris · Ticker Page</div>
  <h1 class="hero-title">{ticker_upper}{market_badge} <span style="font-weight:300;color:var(--text2);font-size:0.85rem">{company_name}</span></h1>
  <p class="hero-sub">All analyzed filings · {total} event{"s" if total!=1 else ""} on record</p>
  <div class="ticker-hero-stats">
    <div class="t-stat"><strong>{total}</strong>Total filings</div>
    <div class="t-stat"><strong>{high_ct}</strong>High priority</div>
    <div class="t-stat"><strong>{avg_mat/10:.2f}</strong>Avg materiality</div>
    <div class="t-stat" style="flex:1;min-width:200px">
      <strong style="font-size:0.75rem">Event breakdown</strong>
      <span style="color:var(--text3)">{stats_row}</span>
    </div>
  </div>
</div>
<div class="content">
  {cards if cards else '<div class="empty">No analyzed filings found.</div>'}
</div>
{footer_html()}
</body>
</html>""")


@app.get("/health")
def health():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            filing_count = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
            analyzed     = conn.execute(
                "SELECT COUNT(*) FROM analyses WHERE status='success'"
            ).fetchone()[0]
    except Exception:
        filing_count = analyzed = 0
    memos_dir   = Path(MEMOS_DIR)
    latest_memo = max(
        (f.name for f in memos_dir.glob("memo_*.html")),
        default=None
    ) if memos_dir.exists() else None
    return JSONResponse({
        "status":      "ok",
        "product":     "veris",
        "timestamp":   datetime.now().isoformat(),
        "filings":     filing_count,
        "analyzed":    analyzed,
        "latest_memo": latest_memo,
        "next_run":    str(scheduler.get_job("weekly").next_run_time),
    })


@app.get("/api/filings")
def api_filings(days: int = 7, limit: int = 50):
    filings = fetch_filings(days=days, limit=limit)
    for f in filings:
        f["risk_flags"]  = json.loads(f["risk_flags"]) if f.get("risk_flags") else []
        f["source_url"]  = source_url(f)
    return JSONResponse({
        "generated": datetime.now().isoformat(),
        "product":   "veris",
        "days":      days,
        "count":     len(filings),
        "filings":   filings,
    })


@app.get("/api/radar")
def api_radar():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            rows = conn.execute("""
                SELECT ticker, company_name, file_date,
                       departures, appointments, succession_note, extracted_at
                FROM executive_radar
                WHERE status = 'success' AND item_5_02_present = 1
                ORDER BY file_date DESC LIMIT 100
            """).fetchall() if "executive_radar" in tables else []
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    events = []
    for row in rows:
        r = dict(row)
        r["departures"]   = json.loads(r["departures"])   if r["departures"]   else []
        r["appointments"] = json.loads(r["appointments"]) if r["appointments"] else []
        events.append(r)
    return JSONResponse({
        "generated": datetime.now().isoformat(),
        "product":   "veris",
        "count":     len(events),
        "events":    events,
    })


@app.post("/run")
def trigger_pipeline(secret: str = ""):
    expected = os.environ.get("PIPELINE_SECRET", "")
    if expected and secret != expected:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    run_pipeline()
    return JSONResponse({"status": "triggered", "timestamp": datetime.now().isoformat()})


def _loading_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<title>Veris — Starting Up</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0a0c10;color:#c9d1e0;font-family:'Courier New',monospace;
       display:flex;align-items:center;justify-content:center;min-height:100vh;flex-direction:column;gap:1.5rem}
  h1{font-size:1.1rem;font-weight:400;letter-spacing:0.1em}
  p{font-size:0.75rem;color:#4a5168;letter-spacing:0.06em}
  .dot{display:inline-block;animation:blink 1.2s infinite}
  .dot:nth-child(2){animation-delay:0.2s}.dot:nth-child(3){animation-delay:0.4s}
  @keyframes blink{0%,80%,100%{opacity:0}40%{opacity:1}}
</style>
</head>
<body>
  <h1>veris</h1>
  <p>Pipeline initialising<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span></p>
  <p>This page refreshes automatically every 30 seconds.</p>
</body>
</html>"""
