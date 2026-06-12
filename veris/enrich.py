"""
enrich.py
---------
Fetches historical market data for analyzed filings to calculate
T_0, T+1, T+5, and T+20 returns. Stores results in price_reactions table.

Run after analyze.py.
Usage: python enrich.py
"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
import yfinance as yf

from config import DB_FILE


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_reactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_id       INTEGER UNIQUE,
            ticker          TEXT,
            price_t0        REAL,
            price_t1        REAL,
            price_t5        REAL,
            price_t20       REAL,
            return_1d       REAL,
            return_5d       REAL,
            return_20d      REAL,
            FOREIGN KEY(filing_id) REFERENCES filings(id)
        )
    """)
    conn.commit()


def calculate_returns(ticker: str, file_date: str) -> dict:
    start_dt = datetime.strptime(file_date, "%Y-%m-%d")
    end_dt   = start_dt + timedelta(days=40)

    try:
        hist = yf.Ticker(ticker).history(
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d")
        )

        if hist.empty:
            return {"error": "No price data found"}

        prices = hist["Close"].values
        p0  = float(prices[0])
        p1  = float(prices[1])  if len(prices) >  1 else None
        p5  = float(prices[5])  if len(prices) >  5 else None
        p20 = float(prices[20]) if len(prices) > 20 else None

        def pct(px, base):
            if px is None or base == 0:
                return None
            return round(((px - base) / base) * 100, 4)

        return {
            "price_t0":  p0,
            "price_t1":  p1,
            "price_t5":  p5,
            "price_t20": p20,
            "return_1d":  pct(p1,  p0),
            "return_5d":  pct(p5,  p0),
            "return_20d": pct(p20, p0),
            "error": None,
        }
    except Exception as e:
        return {"error": str(e)}


def run():
    print("=" * 60)
    print("SEC 8-K Monitor — Market Impact Enrichment")
    print("=" * 60)

    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)

        # Guard: analyses table may not exist if ingest found nothing
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "analyses" not in tables:
            print("\nNo analyses table found. Run ingest.py and analyze.py first.")
            return

        unenriched = conn.execute("""
            SELECT f.id, f.ticker, f.file_date
            FROM filings f
            JOIN analyses a ON f.id = a.filing_id
            LEFT JOIN price_reactions p ON f.id = p.filing_id
            WHERE p.id IS NULL AND a.status = 'success'
            ORDER BY f.file_date DESC
        """).fetchall()

        if not unenriched:
            print("\nNo new filings to enrich.")
            return

        print(f"\nFetching market data for {len(unenriched)} filings...\n")
        success = failed = 0

        for row in unenriched:
            filing_id = row["id"]
            ticker    = row["ticker"]
            file_date = row["file_date"]

            print(f"[{ticker}] Fetching returns from {file_date}...", end=" ", flush=True)
            data = calculate_returns(ticker, file_date)

            if data.get("error"):
                print(f"✗ Failed ({data['error']})")
                failed += 1
            else:
                conn.execute("""
                    INSERT OR IGNORE INTO price_reactions
                      (filing_id, ticker, price_t0, price_t1, price_t5, price_t20,
                       return_1d, return_5d, return_20d)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    filing_id, ticker,
                    data["price_t0"], data["price_t1"],
                    data["price_t5"], data["price_t20"],
                    data["return_1d"], data["return_5d"], data["return_20d"],
                ))
                conn.commit()

                r1 = f"{data['return_1d']:+.2f}%" if data["return_1d"] is not None else "N/A"
                r5 = f"{data['return_5d']:+.2f}%" if data["return_5d"] is not None else "N/A"
                print(f"✓ 1D: {r1} | 5D: {r5}")
                success += 1

            time.sleep(0.5)  # avoid hammering Yahoo Finance

    print(f"\n{'=' * 60}")
    print(f"Done. {success} enriched, {failed} failed.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    run()
