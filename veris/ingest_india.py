"""
ingest_india.py
---------------
Veris — Indian market ingestion layer.

Primary source:  NSE India corporate announcements API
Fallback source: BSE India bulk announcement feed

NSE is preferred — cleaner JSON, more companies, better structured data.
BSE is the automatic fallback if NSE fails for any reason (session expiry,
API changes, rate limits, empty response). Pipeline never breaks.

Session management:
  NSE requires a browser session cookie before API calls work.
  We re-create a fresh session at the start of every run — no stored cookies,
  no expiry issues. Costs 2 seconds, always works.

User-Agent:
  NSE blocks non-browser User-Agents. We send a real Chrome UA string.
  Standard practice for any API that expects browser traffic.

Rate limits:
  One bulk API call per run returns all companies at once.
  No per-company polling = zero rate limit risk.

API changes:
  Full try/except wraps the NSE call. BSE fallback is automatic.
  Pipeline continues regardless of NSE availability.

Usage: python ingest_india.py
Env:   DAYS_BACK=7 (default)
"""

import json
import time
import sqlite3
import os
import requests
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

from config import DB_FILE

DAYS_BACK = int(os.environ.get("DAYS_BACK", 7))

# ── Headers ───────────────────────────────────────────────────────────────────
# NSE requires a real browser User-Agent — Python default gets blocked.
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    "X-Requested-With":"XMLHttpRequest",
    "Connection":      "keep-alive",
}

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Veris/1.0; research use)",
    "Referer":    "https://www.bseindia.com/",
    "Accept":     "application/json",
}

# ── Material category filters ─────────────────────────────────────────────────
# NSE category names
NSE_MATERIAL_CATEGORIES = {
    "Board Meeting",
    "Financial Results",
    "Acquisitions-Mergers-Restructurings",
    "Change in Directors/Key Managerial Personnel",
    "Litigation/Arbitration/Dispute",
    "Credit Rating",
    "Dividend",
    "Buyback",
    "Amalgamation/Merger",
    "Insolvency",
    "Fraud/Default",
    "Regulatory",
    "Outcome of Board Meeting",
}

# BSE category names (fallback)
BSE_MATERIAL_CATEGORIES = {
    "Board Meeting",
    "Outcome of Board Meeting",
    "Financial Results",
    "Mergers/Acquisitions",
    "Change in Directors/ Key Managerial Personnel/ Auditor/ Compliance Officer/ Share Transfer Agent",
    "Insider Trading / SAST",
    "Litigation",
    "Dividend",
    "Buyback",
    "Credit Rating",
    "Regulatory",
    "Insolvency",
}


# ── Retry helper ──────────────────────────────────────────────────────────────
def _get_with_retry(
    session_or_none,
    url: str,
    headers: dict,
    params: dict = None,
    timeout: int = 20,
    retries: int = 3,
) -> requests.Response:
    """
    GET with exponential backoff. Accepts either a requests.Session
    (for NSE which needs cookie continuity) or None (uses requests.get).
    """
    RETRYABLE = {429, 500, 502, 503, 504}
    getter = session_or_none.get if session_or_none else requests.get

    for attempt in range(1, retries + 1):
        try:
            r = getter(url, headers=headers, params=params, timeout=timeout)
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
        except requests.exceptions.Timeout as e:
            if attempt == retries:
                raise
            wait = 2 ** attempt
            print(f"    [RETRY] Timeout — waiting {wait}s: {e}")
            sleep(wait)

    raise requests.exceptions.RetryError(f"Failed after {retries} attempts: {url}")


# ── NSE session management ────────────────────────────────────────────────────
def get_nse_session() -> requests.Session:
    """
    Create a fresh NSE session every run.

    NSE's API checks for a session cookie from a prior homepage visit.
    We re-create this fresh each run — no stored cookies, no expiry issues.
    Fresh session = always works, costs ~2 seconds.
    """
    session = requests.Session()
    try:
        # Step 1: visit homepage — this sets the session cookie NSE expects
        session.get(
            "https://www.nseindia.com",
            headers=NSE_HEADERS,
            timeout=20
        )
        sleep(2)  # let the cookie settle before API calls

        # Step 2: visit the announcements page to warm up the session further
        session.get(
            "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            headers=NSE_HEADERS,
            timeout=20
        )
        sleep(1)

    except Exception as e:
        # Session creation failed — return the session anyway, API call
        # will fail gracefully and trigger BSE fallback
        print(f"  [WARN] NSE session setup issue: {e}")

    return session


# ── NSE bulk announcement fetch ───────────────────────────────────────────────
def fetch_nse_announcements(days_back: int = 7) -> list[dict]:
    """
    Fetch all corporate announcements from NSE for the last `days_back` days.

    One bulk call returns all listed companies — no per-company polling.
    Returns list of normalised announcement dicts on success, raises on failure.

    Raises so caller can trigger BSE fallback.
    """
    end_dt   = datetime.today()
    start_dt = end_dt - timedelta(days=days_back)

    # NSE date format: DD-MM-YYYY
    from_date = start_dt.strftime("%d-%m-%Y")
    to_date   = end_dt.strftime("%d-%m-%Y")

    session = get_nse_session()

    url = "https://www.nseindia.com/api/corporate-announcements"
    params = {
        "index":     "equities",
        "from_date": from_date,
        "to_date":   to_date,
    }

    r = _get_with_retry(session, url, NSE_HEADERS, params=params, timeout=25)

    if r.status_code != 200:
        raise ValueError(f"NSE API returned {r.status_code}")

    data = r.json()

    # NSE returns a list directly or wraps in a key
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get("data", data.get("announcements", []))
    else:
        raise ValueError(f"Unexpected NSE response type: {type(data)}")

    if not raw:
        raise ValueError("NSE returned empty announcement list")

    # Normalise to a consistent schema
    normalised = []
    for ann in raw:
        try:
            # NSE field names vary slightly across API versions
            symbol   = ann.get("symbol", ann.get("sm_symbol", ""))
            company  = ann.get("sm_name", ann.get("sm_isin", symbol))
            category = ann.get("desc", ann.get("subject", ""))
            sub_cat  = ann.get("sm_desc", ann.get("desc", category))
            an_date  = ann.get("an_dt", ann.get("sort_date", ""))[:10]
            news_id  = str(ann.get("an_no", ann.get("anNo", "")))
            headline = ann.get("subject", ann.get("desc", ""))
            body     = ann.get("body", ann.get("desc", ""))
            attch    = ann.get("attchmntFile", ann.get("filename", ""))

            if not symbol or not news_id:
                continue

            # Normalise date to YYYY-MM-DD
            try:
                if "-" in an_date and len(an_date) == 10:
                    # Could be YYYY-MM-DD or DD-MM-YYYY
                    parts = an_date.split("-")
                    if int(parts[0]) > 31:  # YYYY-MM-DD
                        file_date = an_date
                    else:  # DD-MM-YYYY
                        file_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
                else:
                    file_date = datetime.today().strftime("%Y-%m-%d")
            except Exception:
                file_date = datetime.today().strftime("%Y-%m-%d")

            normalised.append({
                "source":   "NSE",
                "ticker":   symbol,
                "company":  company,
                "category": category,
                "sub_cat":  sub_cat,
                "headline": headline,
                "body":     body,
                "file_date":file_date,
                "news_id":  news_id,
                "attachment":attch,
                "acc_prefix":"NSE",
            })
        except Exception:
            continue

    return normalised


# ── BSE bulk fallback ─────────────────────────────────────────────────────────
def fetch_bse_bulk(days_back: int = 7) -> list[dict]:
    """
    Fetch all corporate announcements from BSE bulk API.
    No session required — straightforward GET with BSE headers.
    Used as automatic fallback when NSE fails.
    """
    end_dt   = datetime.today()
    start_dt = end_dt - timedelta(days=days_back)

    # BSE date format: YYYYMMDD
    str_date = start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")

    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetAnnouncementsExcel/w"
    params = {
        "strdate":    str_date,
        "enddate":    end_date,
        "category":   "-1",     # all categories
        "subcategory":"-1",     # all subcategories
        "scrip_cd":   "",       # empty = all companies
    }

    r = _get_with_retry(None, url, BSE_HEADERS, params=params, timeout=20)
    data = r.json()
    raw  = data.get("Table", data.get("Table1", []))

    normalised = []
    for ann in raw:
        try:
            scrip_code = str(ann.get("SCRIP_CD", "")).strip()
            ticker     = ann.get("SCRIP_CD", scrip_code)
            company    = ann.get("SLONGNAME", ann.get("NSNAME", ""))
            category   = ann.get("CATEGORYNAME", "")
            sub_cat    = ann.get("SUBCATNAME", category)
            headline   = ann.get("HEADLINE", "")
            body       = ann.get("NEWSSUB", ann.get("DESCRIPTION", ""))
            news_id    = str(ann.get("NEWSID", "")).strip()
            date_raw   = ann.get("NEWS_DT", ann.get("DT_TM", ""))[:10]
            attch      = ann.get("ATTACHMENTNAME", "")

            if not news_id or not scrip_code:
                continue

            # Normalise date
            try:
                if "/" in date_raw:
                    parts = date_raw.split("/")
                    file_date = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
                elif "-" in date_raw:
                    parts = date_raw.split("-")
                    if len(parts[0]) == 4:
                        file_date = date_raw  # already YYYY-MM-DD
                    else:
                        file_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
                else:
                    file_date = datetime.today().strftime("%Y-%m-%d")
            except Exception:
                file_date = datetime.today().strftime("%Y-%m-%d")

            normalised.append({
                "source":    "BSE",
                "ticker":    str(ticker),
                "company":   company,
                "category":  category,
                "sub_cat":   sub_cat,
                "headline":  headline,
                "body":      body,
                "file_date": file_date,
                "news_id":   news_id,
                "attachment":attch,
                "acc_prefix":"BSE",
                "scrip_code":scrip_code,
            })
        except Exception:
            continue

    return normalised


# ── Text fetcher ──────────────────────────────────────────────────────────────
def fetch_announcement_text(ann: dict) -> str:
    """
    Build readable text for an announcement.
    Uses body text if available in the API response (common for NSE).
    Falls back to fetching the attachment document.
    Truncates to 8000 chars for LLM context window.
    """
    headline = ann.get("headline", "")
    category = ann.get("sub_cat", ann.get("category", ""))
    body     = ann.get("body", "")
    attch    = ann.get("attachment", "")

    # If body text is in the API response, use it directly
    if body and len(body) > 10:
        text = f"{headline}\n\n{category}\n\n{body}"
        return text[:8000]

    # Try to fetch attachment
    if attch:
        source = ann.get("source", "BSE")
        if source == "BSE":
            doc_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attch}"
        else:
            doc_url = f"https://nsearchives.nseindia.com/corporate/{attch}"

        try:
            from bs4 import BeautifulSoup
            r = _get_with_retry(
                None,
                doc_url,
                BSE_HEADERS if source == "BSE" else NSE_HEADERS,
                timeout=15
            )
            if doc_url.endswith(".pdf"):
                # PDF — use headline as fallback, note the source
                return f"{headline}\n\n{category}\n\n[Full text available at: {doc_url}]"

            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:8000]
        except Exception:
            pass

    # Final fallback — headline + category only
    return f"{headline}\n\n{category}"[:8000]


# ── Schema migration ──────────────────────────────────────────────────────────
def ensure_market_column(conn):
    """Add market/scrip_code columns if missing — safe on existing DBs."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(filings)")}
    if "market" not in cols:
        conn.execute("ALTER TABLE filings ADD COLUMN market TEXT DEFAULT 'US'")
    if "scrip_code" not in cols:
        conn.execute("ALTER TABLE filings ADD COLUMN scrip_code TEXT")
    conn.commit()


# ── Material category check ───────────────────────────────────────────────────
def is_material(ann: dict) -> bool:
    """Return True if this announcement is material enough to ingest."""
    cat     = ann.get("category", "")
    sub_cat = ann.get("sub_cat", "")
    source  = ann.get("source", "BSE")

    material_set = (
        NSE_MATERIAL_CATEGORIES if source == "NSE"
        else BSE_MATERIAL_CATEGORIES
    )

    return (
        cat in material_set or
        sub_cat in material_set or
        any(kw in cat for kw in ["Board", "Result", "Merger", "Director", "Dividend", "Insolvency", "Fraud"]) or
        any(kw in sub_cat for kw in ["Board", "Result", "Merger", "Director", "Dividend", "Insolvency", "Fraud"])
    )


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print("=" * 60)
    print("Veris — Indian Market Ingestion")
    print(f"Days back: {DAYS_BACK}")
    print("=" * 60)

    # ── Step 1: Fetch announcements (NSE primary, BSE fallback) ───────────────
    announcements = []
    source_used   = "none"

    print("\nFetching from NSE (primary)...")
    try:
        announcements = fetch_nse_announcements(days_back=DAYS_BACK)
        if not announcements:
            raise ValueError("Empty response")
        source_used = "NSE"
        print(f"  ✓ NSE: {len(announcements)} announcements fetched")
    except Exception as e:
        print(f"  ✗ NSE failed: {e}")
        print("\nFalling back to BSE bulk feed...")
        try:
            announcements = fetch_bse_bulk(days_back=DAYS_BACK)
            if not announcements:
                raise ValueError("BSE also returned empty")
            source_used = "BSE"
            print(f"  ✓ BSE fallback: {len(announcements)} announcements fetched")
        except Exception as e2:
            print(f"  ✗ BSE fallback also failed: {e2}")
            print("\nNo Indian market data available this run.")
            return

    # ── Step 2: Filter to material events ────────────────────────────────────
    material = [a for a in announcements if is_material(a)]
    print(f"\nMaterial announcements: {len(material)} / {len(announcements)}")

    if not material:
        print("No material Indian market events this period.")
        return

    # ── Step 3: Cutoff filter ─────────────────────────────────────────────────
    cutoff_str = (datetime.today() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    material   = [a for a in material if a.get("file_date", "") >= cutoff_str]
    print(f"Within date window:    {len(material)}")

    # ── Step 4: Store ─────────────────────────────────────────────────────────
    with sqlite3.connect(DB_FILE) as conn:
        ensure_market_column(conn)
        total_new = 0

        for ann in material:
            prefix     = ann.get("acc_prefix", "BSE")
            scrip_code = ann.get("scrip_code", ann.get("ticker", ""))
            news_id    = ann["news_id"]
            acc_no     = f"{prefix}-{scrip_code}-{news_id}"
            ticker     = ann["ticker"]
            company    = ann["company"]
            file_date  = ann["file_date"]
            category   = ann.get("sub_cat", ann.get("category", ""))

            # Dedup
            if conn.execute(
                "SELECT id FROM filings WHERE accession_no = ?", (acc_no,)
            ).fetchone():
                continue

            # Fetch text
            raw_text   = fetch_announcement_text(ann)
            filing_url = (
                f"https://www.nseindia.com/companies-listing/corporate-filings-announcements"
                if prefix == "NSE"
                else f"https://www.bseindia.com/corporates/ann.html?scrip={scrip_code}"
            )

            conn.execute("""
                INSERT OR IGNORE INTO filings
                  (ticker, company_name, cik, accession_no, file_date,
                   form_type, filing_url, raw_text, fetched_at, market, scrip_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN', ?)
            """, (
                ticker, company,
                scrip_code,  # CIK equivalent for Indian filings
                acc_no, file_date, category,
                filing_url, raw_text,
                datetime.now().isoformat(),
                scrip_code,
            ))
            conn.commit()
            total_new += 1

            print(f"  [{ticker}] {company[:35]} | {category[:30]} | {file_date}")
            time.sleep(0.2)

    print(f"\n{'=' * 60}")
    print(f"Done. Source: {source_used} | {total_new} new Indian filings stored.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    run()
