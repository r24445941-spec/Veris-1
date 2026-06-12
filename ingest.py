"""
ingest.py
---------
Veris — US market ingestion layer.

TWO modes:
  1. BROAD MARKET (default): polls EDGAR's live current-events feed for ALL
     8-K filers across the entire US market — same approach as secwatch.observer.
     Uses: https://efts.sec.gov/LATEST/search-index (EDGAR full-text search)

  2. WATCHLIST (fallback): fetches from a fixed list of CIKs in watchlist.json.
     Used when BROAD_MARKET=false in environment.

Broad market mode gives you the full universe — random small caps, biotech,
financials, everything — instead of just 10 pre-defined companies.

Usage: python ingest.py
Env:   BROAD_MARKET=true (default) or BROAD_MARKET=false for watchlist mode
       DAYS_BACK=7 (default)
"""

import json
import time
import sqlite3
import os
import requests
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

from config import DB_FILE, WATCHLIST_FILE

# IMPORTANT: SEC requires a real name + email in User-Agent.
# Set this via environment variable so you don't hardcode personal details in git.
# In Railway dashboard: add EDGAR_UA = "Your Name youremail@gmail.com"
# Locally: export EDGAR_UA="Your Name youremail@gmail.com"
import os as _os
HEADERS = {"User-Agent": _os.environ.get("EDGAR_UA", "Veris Research veris@example.com")}
DAYS_BACK = int(os.environ.get("DAYS_BACK", 7))
BROAD_MARKET = os.environ.get("BROAD_MARKET", "true").lower() != "false"

# Broad market: max filings to ingest per run (keeps costs manageable)
# At ~$0.001/filing for Gemini, 200 filings = ~$0.20/week
MAX_FILINGS = int(os.environ.get("MAX_FILINGS", 200))


# ── Retry helper ──────────────────────────────────────────────────────────────
def _get_with_retry(url: str, headers: dict, params: dict = None,
                    timeout: int = 15, retries: int = 3) -> requests.Response:
    RETRYABLE = {429, 500, 502, 503, 504}
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code not in RETRYABLE:
                r.raise_for_status()
                return r
            wait = 2 ** attempt
            print(f"    [RETRY] HTTP {r.status_code} — waiting {wait}s (attempt {attempt}/{retries})")
            sleep(wait)
        except requests.exceptions.ConnectionError as e:
            if attempt == retries:
                raise
            wait = 2 ** attempt
            print(f"    [RETRY] Connection error — waiting {wait}s: {e}")
            sleep(wait)
    raise requests.exceptions.RetryError(f"Failed after {retries} attempts: {url}")


# ── Database ──────────────────────────────────────────────────────────────────
def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT,
            company_name    TEXT,
            cik             TEXT,
            accession_no    TEXT UNIQUE,
            file_date       TEXT,
            form_type       TEXT,
            filing_url      TEXT,
            raw_text        TEXT,
            fetched_at      TEXT,
            market          TEXT DEFAULT 'US',
            scrip_code      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_id               INTEGER UNIQUE,
            event_type              TEXT,
            summary                 TEXT,
            materiality             INTEGER,
            calibrated_materiality  INTEGER,
            urgency                 TEXT,
            thesis_impact           TEXT,
            risk_flags              TEXT,
            valuation_note          TEXT,
            analyzed_at             TEXT,
            status                  TEXT DEFAULT 'success',
            fail_reason             TEXT,
            FOREIGN KEY(filing_id) REFERENCES filings(id)
        )
    """)
    conn.commit()


# ── EDGAR EFTS broad market feed ──────────────────────────────────────────────
def fetch_broad_market_8ks(days_back: int = 7, max_results: int = 200) -> list[dict]:
    """
    Fetch recent 8-K filings from the entire US market using EDGAR's
    full-text search API (EFTS). This is how secwatch.observer ingests —
    watching the live feed rather than polling individual company CIKs.

    Returns list of filing metadata dicts.
    """
    end_date   = datetime.today()
    start_date = end_date - timedelta(days=days_back)

    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q":         "",
        "forms":     "8-K",
        "dateRange": "custom",
        "startdt":   start_date.strftime("%Y-%m-%d"),
        "enddt":     end_date.strftime("%Y-%m-%d"),
        "hits.hits._source": "file_date,entity_name,file_num,period_of_report,form_type,biz_location",
        "hits.hits.total":   "true",
        "_source":           "file_date,entity_name",
    }

    all_hits = []
    from_offset = 0
    page_size = 40  # EDGAR EFTS max per request

    while len(all_hits) < max_results:
        params["from"] = from_offset
        params["hits.hits.total"] = page_size

        try:
            r = _get_with_retry(url, HEADERS, params=params)
            data = r.json()
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                break
            all_hits.extend(hits)
            from_offset += len(hits)
            if len(hits) < page_size:
                break
            time.sleep(0.15)
        except Exception as e:
            print(f"  [ERROR] EFTS query failed: {e}")
            break

    return all_hits[:max_results]


def parse_efts_hit(hit: dict) -> dict | None:
    """Parse a raw EFTS search hit into a structured filing dict."""
    try:
        src          = hit.get("_source", {})
        accession_no = hit.get("_id", "").split(":")[0]  # strip filename suffix
        if not accession_no:
            return None

        # Normalise accession number format: 0001193125-26-260640
        acc_clean = accession_no.replace("-", "")
        if len(acc_clean) == 18:
            accession_no = f"{acc_clean[:10]}-{acc_clean[10:12]}-{acc_clean[12:]}"

        entity_name = src.get("entity_name", src.get("display_names", ["Unknown"])[0] if isinstance(src.get("display_names"), list) else "Unknown")
        file_date   = src.get("file_date", "")[:10]
        form_type   = src.get("form_type", "8-K")

        # Extract CIK from accession number (first 10 digits)
        cik = accession_no.split("-")[0].lstrip("0") or "0"

        return {
            "accession_no": accession_no,
            "company_name": entity_name,
            "cik":          cik,
            "ticker":       "",   # EFTS doesn't reliably return ticker; resolved separately
            "file_date":    file_date,
            "form_type":    form_type,
        }
    except Exception:
        return None


def resolve_ticker(cik: str) -> str:
    """
    Look up ticker symbol for a CIK from EDGAR company submissions API.
    Returns empty string on failure (CIK is shown instead).
    """
    try:
        cik_padded = cik.zfill(10)
        r = _get_with_retry(
            f"https://data.sec.gov/submissions/CIK{cik_padded}.json", HEADERS
        )
        data   = r.json()
        tickers = data.get("tickers", [])
        return tickers[0] if tickers else cik  # fallback to CIK if no ticker
    except Exception:
        return cik


# ── Watchlist mode (fallback) ─────────────────────────────────────────────────
def get_recent_8ks_for_cik(cik: str, days_back: int = 7) -> list[dict]:
    """Fetch 8-Ks for a single CIK from EDGAR submissions API."""
    cutoff     = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    cik_padded = cik.zfill(10)

    try:
        r    = _get_with_retry(f"https://data.sec.gov/submissions/CIK{cik_padded}.json", HEADERS)
        data = r.json()
        recent     = data.get("filings", {}).get("recent", {})
        forms      = recent.get("form", [])
        dates      = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        tickers    = data.get("tickers", [""])

        hits = []
        for form, date, acc in zip(forms, dates, accessions):
            if form in ("8-K", "8-K/A") and date >= cutoff:
                hits.append({
                    "_id":     acc,
                    "_source": {"file_date": date, "form_type": form},
                    "_ticker": tickers[0] if tickers else cik,
                })
        return hits
    except Exception as e:
        print(f"  [ERROR] Submissions API failed for CIK {cik}: {e}")
        return []


# ── Filing text fetcher (shared) ──────────────────────────────────────────────
def build_filing_url(cik: str, accession_no: str) -> str:
    cik_stripped = str(int(cik)) if cik.isdigit() else cik
    acc_clean    = accession_no.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_stripped}/{acc_clean}/{accession_no}-index.htm"
    )


def fetch_filing_text(cik: str, accession_no: str) -> str:
    """Download and clean 8-K text. Returns operative text starting at first Item heading."""
    try:
        from bs4 import BeautifulSoup
        import re

        cik_stripped = str(int(cik)) if cik.isdigit() else cik
        acc_clean    = accession_no.replace("-", "")

        index_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik_stripped}/{acc_clean}/{accession_no}-index.htm"
        )

        r    = _get_with_retry(index_url, HEADERS)
        soup = BeautifulSoup(r.text, "html.parser")

        primary_doc = None
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 4:
                doc_type = cells[3].get_text(strip=True)
                if doc_type in ("8-K", "8-K/A"):
                    link = cells[2].find("a")
                    if link:
                        primary_doc = link.get("href")
                        break

        if not primary_doc:
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if (href.endswith(".htm") or href.endswith(".html")) \
                        and "index" not in href.lower():
                    primary_doc = href
                    break

        if not primary_doc:
            return "[Could not locate primary document]"

        doc_url = f"https://www.sec.gov{primary_doc}" \
                  if primary_doc.startswith("/") else primary_doc

        time.sleep(0.15)
        doc_r    = _get_with_retry(doc_url, HEADERS)
        doc_soup = BeautifulSoup(doc_r.text, "html.parser")

        for tag in doc_soup(["script", "style"]):
            tag.decompose()

        text = doc_soup.get_text(separator="\n", strip=True)

        # Start from first Item heading — skip SEC cover page boilerplate
        item_match = re.search(r'(?m)^\s*Item\s+\d+\.\d+', text)
        if item_match:
            text = text[item_match.start():].lstrip()

        return text[:8000]

    except Exception as e:
        return f"[Text fetch error: {e}]"


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print("=" * 60)
    mode = "BROAD MARKET" if BROAD_MARKET else "WATCHLIST"
    print(f"Veris — US Ingestion ({mode} mode)")
    print(f"Days back: {DAYS_BACK} | Max filings: {MAX_FILINGS if BROAD_MARKET else 'all'}")
    print("=" * 60)

    with sqlite3.connect(DB_FILE) as conn:
        init_db(conn)
        total_new = 0

        if BROAD_MARKET:
            # ── Mode 1: Full market via EFTS ──────────────────────────────
            print(f"\nFetching up to {MAX_FILINGS} recent 8-Ks from all US companies...")
            hits = fetch_broad_market_8ks(days_back=DAYS_BACK, max_results=MAX_FILINGS)
            print(f"Found {len(hits)} filings from EDGAR.\n")

            for hit in hits:
                filing = parse_efts_hit(hit)
                if not filing:
                    continue

                acc_no = filing["accession_no"]

                # Dedup
                if conn.execute(
                    "SELECT id FROM filings WHERE accession_no = ?", (acc_no,)
                ).fetchone():
                    continue

                # Resolve ticker (one extra API call per new filing)
                ticker = resolve_ticker(filing["cik"])
                filing["ticker"] = ticker

                print(f"  [{ticker}] {filing['company_name'][:40]} | {acc_no} ({filing['file_date']})")

                filing_url = build_filing_url(filing["cik"], acc_no)
                raw_text   = fetch_filing_text(filing["cik"], acc_no)

                conn.execute("""
                    INSERT OR IGNORE INTO filings
                      (ticker, company_name, cik, accession_no, file_date,
                       form_type, filing_url, raw_text, fetched_at, market)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'US')
                """, (
                    ticker, filing["company_name"], filing["cik"],
                    acc_no, filing["file_date"], filing["form_type"],
                    filing_url, raw_text, datetime.now().isoformat()
                ))
                conn.commit()
                total_new += 1
                time.sleep(0.2)

        else:
            # ── Mode 2: Watchlist ─────────────────────────────────────────
            with open(WATCHLIST_FILE) as f:
                watchlist = json.load(f)

            for company in watchlist:
                ticker = company["ticker"]
                name   = company["name"]
                cik    = company["cik"]
                print(f"\n[{ticker}] {name}")

                for hit in get_recent_8ks_for_cik(cik, DAYS_BACK):
                    acc_no    = hit.get("_id", "")
                    file_date = hit["_source"]["file_date"]
                    form_type = hit["_source"]["form_type"]
                    resolved_ticker = hit.get("_ticker", ticker)

                    if conn.execute(
                        "SELECT id FROM filings WHERE accession_no = ?", (acc_no,)
                    ).fetchone():
                        print(f"  Already stored: {acc_no}")
                        continue

                    print(f"  New: {acc_no} ({file_date})")
                    raw_text   = fetch_filing_text(cik, acc_no)
                    filing_url = build_filing_url(cik, acc_no)

                    conn.execute("""
                        INSERT OR IGNORE INTO filings
                          (ticker, company_name, cik, accession_no, file_date,
                           form_type, filing_url, raw_text, fetched_at, market)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'US')
                    """, (
                        resolved_ticker, name, cik, acc_no, file_date,
                        form_type, filing_url, raw_text, datetime.now().isoformat()
                    ))
                    conn.commit()
                    total_new += 1
                    time.sleep(0.2)

    print(f"\n{'=' * 60}")
    print(f"Done. {total_new} new US filings stored.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    run()
