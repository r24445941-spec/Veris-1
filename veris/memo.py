"""
memo.py
-------
Veris — memo generator.

Writes the weekly HTML + Markdown memo to the memos/ folder.
HTML uses the same renderer as the web app (app.py) — one design everywhere.
Markdown is a lightweight plain-text version for GitHub.

Run after enrich.py:
  python memo.py
"""

import os
import sqlite3
import json
import pathlib
from datetime import datetime, timedelta

from config import DB_FILE, MEMOS_DIR


# ── Data fetching ─────────────────────────────────────────────────────────────
def fetch_analyzed_filings(conn, days_back: int = 7) -> list[dict]:
    cutoff = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT f.ticker, f.company_name, f.file_date, f.accession_no, f.filing_url,
               COALESCE(f.market, 'US') AS market, f.scrip_code,
               a.event_type, a.summary, a.materiality,
               COALESCE(a.calibrated_materiality, a.materiality) AS calibrated_materiality,
               a.urgency, a.thesis_impact,
               a.risk_flags, a.valuation_note, p.return_1d, p.return_5d
        FROM filings f
        JOIN analyses a ON f.id = a.filing_id
        LEFT JOIN price_reactions p ON f.id = p.filing_id
        WHERE f.file_date >= ? AND a.status = 'success'
        ORDER BY calibrated_materiality DESC, f.file_date DESC
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


# ── Markdown renderer (kept simple — for GitHub portfolio) ───────────────────
EVENT_LABELS = {
    "earnings": "Earnings", "M&A": "M&A", "leadership_change": "Leadership",
    "debt_financing": "Debt", "litigation": "Litigation", "restatement": "Restatement",
    "cybersecurity": "Cyber", "regulatory": "Regulatory", "dividend": "Dividend",
    "share_buyback": "Buyback", "other": "Other",
}

def format_return(v) -> str:
    if v is None: return "—"
    v = float(v)
    return ("+" if v >= 0 else "") + f"{v:.2f}%"

def _edgar_url(f: dict) -> str:
    cik = f["accession_no"].split("-")[0].lstrip("0")
    acc = f["accession_no"].replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{f['accession_no']}-index.htm"

def _source_url(f: dict) -> str:
    if f.get("market") == "IN":
        scrip = f.get("scrip_code", "")
        return f"https://www.bseindia.com/corporates/ann.html?scrip={scrip}"
    return _edgar_url(f)

def render_markdown(filings: list[dict], week_str: str) -> str:
    high   = [f for f in filings if (f.get("calibrated_materiality") or f["materiality"]) >= 7]
    medium = [f for f in filings if 4 <= (f.get("calibrated_materiality") or f["materiality"]) <= 6]
    low    = [f for f in filings if (f.get("calibrated_materiality") or f["materiality"]) <= 3]

    lines = [
        f"# Veris — Corporate Filing Intelligence",
        f"## {week_str}",
        f"**Markets:** US (SEC EDGAR) + India (BSE) | **Events:** {len(filings)}\n",
        "---\n",
    ]

    def section(label, items):
        if not items: return
        lines.append(f"## {label}\n")
        for f in items:
            risks = json.loads(f["risk_flags"]) if f["risk_flags"] else []
            url   = _source_url(f)
            mat   = f.get("calibrated_materiality") or f["materiality"]
            lines.append(f"### {f['company_name']} ({f['ticker']}) [{f.get('market','US')}] — {EVENT_LABELS.get(f['event_type'], f['event_type'])}")
            lines.append(f"**Date:** {f['file_date']} | **Materiality:** {mat/10:.2f} | **Urgency:** {f['urgency'].upper()}\n")
            lines.append(f"> 1D: `{format_return(f['return_1d'])}` | 5D: `{format_return(f['return_5d'])}`\n")
            lines.append(f"**Summary:** {f['summary']}\n")
            lines.append(f"**Impact:** {f['thesis_impact']}\n")
            if risks:
                lines.append("**Risk flags:** " + " · ".join(risks) + "\n")
            lines.append(f"**Valuation:** {f['valuation_note'] or 'None identified'}\n")
            lines.append(f"**Source:** {url}\n\n---\n")

    section("🔴 High Priority (materiality ≥ 0.70)", high)
    section("🟡 Standard Review (materiality 0.40–0.69)", medium)
    section("🟢 Routine (materiality < 0.40)", low)
    lines.append(f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · Veris · Not investment advice*")
    return "\n".join(lines)


# ── HTML renderer — delegates to app.py (one design everywhere) ───────────────
def render_html(filings: list[dict], week_str: str) -> str:
    """
    Render the weekly memo as HTML using the same terminal design as the web app.
    Imports app.py's filing_card, SHARED_CSS, nav, and footer_html so there is
    exactly one renderer in the codebase.
    """
    from app import (
        filing_card, SHARED_CSS, nav as _nav, footer_html,
        URGENCY_GLYPH, EVT_CSS, EVT_LABEL,
    )

    high   = [f for f in filings if (f.get("calibrated_materiality") or f["materiality"]) >= 7]
    medium = [f for f in filings if 4 <= (f.get("calibrated_materiality") or f["materiality"]) <= 6]
    low    = [f for f in filings if (f.get("calibrated_materiality") or f["materiality"]) <= 3]
    total  = len(filings)

    def section(label, items):
        if not items: return ""
        cards = "".join(filing_card(f) for f in items)
        return f"""<div class="section-rule">{label}<span>{len(items)} filing{"s" if len(items)!=1 else ""}</span></div>
{cards}"""

    body = (
        section("High priority — materiality ≥ 0.70", high) +
        section("Standard review — materiality 0.40–0.69", medium) +
        section("Routine — materiality < 0.40", low)
    ) or '<div class="empty">No filings this week.</div>'

    generated = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veris · {week_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>{SHARED_CSS}</style>
</head>
<body>
<nav>
  <span class="nav-brand">veris</span>
  <div class="nav-links">
    <a href="/digest">digest</a>
    <a href="/filings" class="active">filings</a>
    <a href="/radar/executives">exec radar</a>
    <a href="/api/filings">api</a>
    <a href="/disclaimer">disclaimer</a>
  </div>
  <div class="nav-right">generated {generated}</div>
</nav>
<div class="hero">
  <div class="hero-label">Veris · Weekly Filing Report · {week_str}</div>
  <h1 class="hero-title">Corporate event intelligence — weekly digest</h1>
  <p class="hero-sub">
    {total} filing{"s" if total != 1 else ""} tracked across U.S. (SEC EDGAR) and Indian (BSE) markets.
    Machine-generated. Verify all claims against primary source documents.
    <a href="/disclaimer" style="color:var(--text3)">disclaimer ↗</a>
  </p>
</div>
<div class="content">
  <div style="padding:1rem 0;font-size:0.65rem;color:var(--text3)">
    {len(high)} high priority &nbsp;·&nbsp;
    {len(medium)} standard &nbsp;·&nbsp;
    {len(low)} routine
  </div>
  {body}
</div>
{footer_html()}
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print("=" * 60)
    print("Veris — Memo Generator")
    print("=" * 60)

    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        filings = fetch_analyzed_filings(conn, days_back=7)

    week_str = f"Week of {datetime.today().strftime('%B %d, %Y')}"
    date_str = datetime.today().strftime("%Y-%m-%d")

    print(f"\nBuilding memo for: {week_str}")
    print(f"Filings included:  {len(filings)}")

    os.makedirs(MEMOS_DIR, exist_ok=True)

    html_path = str(pathlib.Path(MEMOS_DIR) / f"memo_{date_str}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(filings, week_str))
    print(f"\n✓ HTML memo:     {html_path}")

    md_path = str(pathlib.Path(MEMOS_DIR) / f"memo_{date_str}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(filings, week_str))
    print(f"✓ Markdown memo: {md_path}")
    print(f"\nOpen {html_path} in your browser.")


if __name__ == "__main__":
    run()
