"""
analyze.py
----------
Reads unanalyzed filings from the database, sends each one to Gemini 1.5 Flash,
and stores the structured analysis back into the database.

Gemini 1.5 Flash is FREE — 15 requests/minute, 1M tokens/day, no credit card.
Get your free API key at: https://aistudio.google.com/

Set it as an environment variable before running:
  Mac/Linux:  export GEMINI_API_KEY="AIza..."
  Windows:    set GEMINI_API_KEY=AIza...

Run after ingest.py:
  python analyze.py

To inspect failures:
  sqlite3 database.db "SELECT f.ticker, f.accession_no, a.fail_reason FROM analyses a
  JOIN filings f ON f.id = a.filing_id WHERE a.status = 'failed';"
"""

import json
import sqlite3
import os
import time
from datetime import datetime
from pathlib import Path
from google import genai
from google.genai import types

from config import DB_FILE
import radar as _radar
GEMINI_MODEL = "gemini-2.0-flash"

# ── Prompt ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
You are a buy-side equity research analyst at a hedge fund.
You will be given the text of an SEC 8-K filing.
Your job is to read it and return a structured JSON analysis.

Return ONLY a valid JSON object. No explanation, no preamble, no markdown fences.
Use exactly this structure:

{
  "event_type": "<one of: earnings, M&A, leadership_change, debt_financing, litigation, restatement, cybersecurity, regulatory, dividend, share_buyback, other>",
  "summary": "<2-3 sentence factual description of what happened>",
  "materiality": <integer 1-10, where 10 = company-altering, 1 = boilerplate routine>,
  "urgency": "<one of: high, medium, low>",
  "thesis_impact": "<one of: bullish, bearish, neutral> — <one sentence explaining why>",
  "risk_flags": ["<specific risk 1>", "<specific risk 2>"],
  "valuation_note": "<any impact on multiples, dilution, leverage, or cash flow — or 'none identified' if not applicable>"
}

Materiality scoring guide:
  9-10: Restatement, major M&A, CEO departure, cyber breach, bankruptcy risk
  7-8:  CFO change, significant litigation, large debt issuance, missed earnings
  5-6:  In-line earnings, minor acquisition, dividend change, buyback announcement
  3-4:  Routine governance update, minor amendment, immaterial agreement
  1-2:  Exhibit filing, boilerplate disclosure, administrative update
""".strip()


# ── Calibrated materiality ────────────────────────────────────────────────────
# LLMs tend to overscore routine events. These caps correct known drift.
# Logic mirrors secwatch.observer's calibrated_materiality_score:
# deterministic rules only ever LOWER the raw score, never raise it.
# The raw LLM score is preserved separately for audit purposes.
ROUTINE_CAPS = {
    "dividend":      4,   # routine dividend declarations are rarely high-impact
    "share_buyback": 5,   # buybacks are positive but usually well-telegraphed
    "other":         4,   # uncategorised filings default to low signal
}

# Hard floor: these event types are never routine regardless of LLM score
HIGH_FLOOR_EVENTS = {"cybersecurity", "restatement"}
HIGH_FLOOR_VALUE  = 7   # cyber/restatement always score at least 7

def calibrate_materiality(event_type: str, raw_score: int) -> int:
    """
    Apply deterministic corrections to the raw LLM materiality score.
    - Caps routine event types so identical events score consistently.
    - Floors high-severity event types so cyber/restatements are never buried.
    - Never raises the raw score for uncapped events.
    """
    score = raw_score

    # Apply floor for inherently high-severity events
    if event_type in HIGH_FLOOR_EVENTS:
        score = max(score, HIGH_FLOOR_VALUE)

    # Apply cap for known routine event types
    if event_type in ROUTINE_CAPS:
        score = min(score, ROUTINE_CAPS[event_type])

    return score


# ── Schema migration ──────────────────────────────────────────────────────────
def ensure_schema(conn):
    """
    Add missing columns to analyses table. Safe on existing databases —
    uses ALTER TABLE only when the column is absent.
    """
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
    migrations = {
        "status":                 "TEXT DEFAULT 'success'",
        "fail_reason":            "TEXT",
        "calibrated_materiality": "INTEGER",  # new: deterministic corrected score
    }
    for col, typedef in migrations.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE analyses ADD COLUMN {col} {typedef}")
    conn.commit()


# ── Analysis ──────────────────────────────────────────────────────────────────
def analyze_filing(client: genai.Client, filing: dict) -> tuple[dict | None, str | None]:
    """
    Send one filing to Gemini and parse the JSON response.
    Returns (result_dict, None) on success, (None, reason) on failure.
    """
    raw_text = filing["raw_text"]

    if not raw_text or raw_text.startswith("["):
        return None, f"No usable text: {raw_text[:80]}"

    user_message = f"""Company: {filing['company_name']} ({filing['ticker']})
Filing date: {filing['file_date']}
Form type: {filing['form_type']}

--- FILING TEXT START ---
{raw_text}
--- FILING TEXT END ---

Analyze this 8-K filing and return your JSON response."""

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=1000,
            ),
        )

        raw_json = response.text.strip()

        # Strip markdown fences defensively
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]

        result = json.loads(raw_json)

        # Validate required fields
        required = {"event_type", "summary", "materiality", "urgency", "thesis_impact"}
        missing = required - result.keys()
        if missing:
            return None, f"Missing fields in response: {missing}"

        # Coerce and clamp raw materiality
        raw_mat = max(1, min(10, int(result.get("materiality", 5))))
        result["materiality"] = raw_mat

        # Compute calibrated score
        result["calibrated_materiality"] = calibrate_materiality(
            result["event_type"], raw_mat
        )

        return result, None

    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e} | raw: {raw_json[:200]}"
    except Exception as e:
        return None, f"API error: {type(e).__name__}: {e}"


def write_result(conn, filing_id: int, result: dict | None, fail_reason: str | None):
    """Write analysis result (success or failure) into the analyses table."""
    if result:
        conn.execute("""
            INSERT OR REPLACE INTO analyses
              (filing_id, event_type, summary, materiality, calibrated_materiality,
               urgency, thesis_impact, risk_flags, valuation_note,
               analyzed_at, status, fail_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', NULL)
        """, (
            filing_id,
            result.get("event_type", "other"),
            result.get("summary", ""),
            result.get("materiality", 5),
            result.get("calibrated_materiality", result.get("materiality", 5)),
            result.get("urgency", "medium"),
            result.get("thesis_impact", "neutral"),
            json.dumps(result.get("risk_flags", [])),
            result.get("valuation_note", ""),
            datetime.now().isoformat()
        ))
    else:
        conn.execute("""
            INSERT OR REPLACE INTO analyses
              (filing_id, analyzed_at, status, fail_reason)
            VALUES (?, ?, 'failed', ?)
        """, (filing_id, datetime.now().isoformat(), fail_reason))
    conn.commit()


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print("=" * 60)
    print("SEC 8-K Monitor — Analysis (Gemini 1.5 Flash · Free Tier)")
    print("=" * 60)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("\n[ERROR] GEMINI_API_KEY environment variable not set.")
        print("\nGet your FREE key (no credit card) at:")
        print("  https://aistudio.google.com/")
        print("\nThen set it:")
        print("  Mac/Linux:  export GEMINI_API_KEY='AIza...'")
        print("  Windows:    set GEMINI_API_KEY=AIza...")
        return

    client = genai.Client(api_key=api_key)

    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)

        total = conn.execute("""
            SELECT COUNT(*) FROM filings f
            LEFT JOIN analyses a ON f.id = a.filing_id
            WHERE a.id IS NULL OR a.status = 'failed'
        """).fetchone()[0]

        if total == 0:
            print("\nNo new or failed filings to analyze. Run ingest.py first.")
            return

        print(f"\nAnalyzing {total} filings with {GEMINI_MODEL}...\n")
        print("Free tier: 15 req/min. 1-second pause between calls.\n")

        cursor = conn.execute("""
            SELECT f.* FROM filings f
            LEFT JOIN analyses a ON f.id = a.filing_id
            WHERE a.id IS NULL OR a.status = 'failed'
            ORDER BY f.file_date DESC
        """)

        success = failed = 0

        for row in cursor:
            filing = dict(row)
            print(f"[{filing['ticker']}] {filing['accession_no']} ({filing['file_date']})")

            result, fail_reason = analyze_filing(client, filing)
            write_result(conn, filing["id"], result, fail_reason)

            if result:
                raw = result['materiality']
                cal = result['calibrated_materiality']
                adj = f" → calibrated {cal}/10" if cal != raw else ""
                print(f"  ✓ {result['event_type']} | "
                      f"materiality {raw}/10{adj} | "
                      f"{result['urgency']} urgency")
                success += 1

                # Auto-trigger executive radar for leadership_change filings
                if result['event_type'] == 'leadership_change':
                    print(f"  ↳ Running executive radar extraction...")
                    _radar.ensure_schema(conn)
                    radar_result, radar_err = _radar.extract_radar(client, filing)
                    _radar.write_radar(conn, filing, radar_result, radar_err)
                    if radar_result:
                        deps  = len(radar_result.get('departures', []))
                        appts = len(radar_result.get('appointments', []))
                        print(f"  ↳ Radar: {deps} departure(s), {appts} appointment(s)")
                    else:
                        print(f"  ↳ Radar failed: {radar_err}")
                    time.sleep(1)  # extra API call — sleep again for rate limit

            else:
                print(f"  ✗ Failed — {fail_reason}")
                failed += 1

            time.sleep(1)

    print(f"\n{'=' * 60}")
    print(f"Done. {success} succeeded, {failed} failed.")
    if failed:
        print(f"\nTo inspect failures:")
        print(f'  sqlite3 {DB_FILE} "SELECT f.ticker, f.accession_no, a.fail_reason '
              f'FROM analyses a JOIN filings f ON f.id = a.filing_id '
              f'WHERE a.status = \'failed\';"')
    print(f"{'=' * 60}")


if __name__ == "__main__":
    run()
