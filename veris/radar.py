"""
radar.py
--------
Executive Movement Radar — focused extraction layer for Item 5.02 filings.

Runs automatically after standard analysis whenever event_type == 'leadership_change'.
Produces structured, source-linked facts about executive departures and appointments.

This is a separate extraction pass — not a summary, not opinion.
Every claim is paired with the exact sentence from the filing that proves it.

Storage: executive_radar table in database.db
Surface: GET /radar/executives on the web server

Mirrors the "Executive Movement Radar" feature on secwatch.observer.
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

GEMINI_MODEL = "gemini-1.5-flash"

# ── Radar prompt ──────────────────────────────────────────────────────────────
# Focused purely on Item 5.02 — departure/appointment facts only.
# Every claim MUST cite the exact sentence that proves it.
RADAR_PROMPT = """
You are extracting structured facts from an SEC 8-K filing Item 5.02 disclosure.
Item 5.02 covers departure and appointment of directors and certain officers.

Return ONLY a valid JSON object. No explanation, no preamble, no markdown fences.
Use exactly this structure:

{
  "departures": [
    {
      "name": "<full name>",
      "role": "<exact title as stated in filing>",
      "departure_type": "<one of: resignation, retirement, termination, death, other>",
      "effective_date": "<YYYY-MM-DD or null if not stated>",
      "voluntary": <true if resignation/retirement, false if termination, null if unclear>,
      "evidence": "<exact sentence or phrase from the filing that proves this departure>"
    }
  ],
  "appointments": [
    {
      "name": "<full name>",
      "role": "<exact title as stated in filing>",
      "appointment_type": "<one of: permanent, interim, acting, board_election, other>",
      "effective_date": "<YYYY-MM-DD or null if not stated>",
      "internal_promotion": <true if promoted from within, false if external hire, null if unclear>,
      "evidence": "<exact sentence or phrase from the filing that proves this appointment>"
    }
  ],
  "succession_note": "<one sentence: is there a named successor? search ongoing? board restructuring? — or null>",
  "item_5_02_present": <true if Item 5.02 text was found, false if this is a different event type>
}

Rules:
- Only extract facts explicitly stated in the filing. Do not infer or assume.
- evidence must be a direct quote or close paraphrase from the filing text, not your interpretation.
- If no Item 5.02 content is present, return item_5_02_present: false and empty arrays.
- If a person both departed and was replaced, create separate departure and appointment entries.
- effective_date format: YYYY-MM-DD. If only month/year given, use the 1st of that month.
""".strip()


# ── Schema ────────────────────────────────────────────────────────────────────
def ensure_schema(conn):
    """Create executive_radar table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS executive_radar (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_id       INTEGER UNIQUE,
            ticker          TEXT,
            company_name    TEXT,
            file_date       TEXT,
            departures      TEXT,   -- JSON array
            appointments    TEXT,   -- JSON array
            succession_note TEXT,
            item_5_02_present INTEGER DEFAULT 1,
            extracted_at    TEXT,
            status          TEXT DEFAULT 'success',
            fail_reason     TEXT,
            FOREIGN KEY(filing_id) REFERENCES filings(id)
        )
    """)
    conn.commit()


# ── Extraction ────────────────────────────────────────────────────────────────
def extract_radar(client: genai.Client, filing: dict) -> tuple[dict | None, str | None]:
    """
    Run focused Item 5.02 extraction on a filing.
    Returns (result_dict, None) on success, (None, reason) on failure.
    """
    raw_text = filing["raw_text"]
    if not raw_text or raw_text.startswith("["):
        return None, f"No usable text: {raw_text[:80]}"

    user_message = f"""Company: {filing['company_name']} ({filing['ticker']})
Filing date: {filing['file_date']}

--- FILING TEXT ---
{raw_text}
--- END ---

Extract all executive departure and appointment facts from this Item 5.02 filing."""

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=RADAR_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,        # zero temperature — facts only, no creativity
                max_output_tokens=1500,
            ),
        )

        raw_json = response.text.strip()
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]

        result = json.loads(raw_json)

        # Validate structure
        if "departures" not in result or "appointments" not in result:
            return None, "Missing departures or appointments fields"

        return result, None

    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    except Exception as e:
        return None, f"API error: {type(e).__name__}: {e}"


def write_radar(conn, filing: dict, result: dict | None, fail_reason: str | None):
    """Write radar extraction result into executive_radar table."""
    if result:
        conn.execute("""
            INSERT OR REPLACE INTO executive_radar
              (filing_id, ticker, company_name, file_date,
               departures, appointments, succession_note,
               item_5_02_present, extracted_at, status, fail_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', NULL)
        """, (
            filing["id"],
            filing["ticker"],
            filing["company_name"],
            filing["file_date"],
            json.dumps(result.get("departures", [])),
            json.dumps(result.get("appointments", [])),
            result.get("succession_note"),
            1 if result.get("item_5_02_present", True) else 0,
            datetime.now().isoformat(),
        ))
    else:
        conn.execute("""
            INSERT OR REPLACE INTO executive_radar
              (filing_id, ticker, company_name, file_date,
               extracted_at, status, fail_reason)
            VALUES (?, ?, ?, ?, ?, 'failed', ?)
        """, (
            filing["id"],
            filing["ticker"],
            filing["company_name"],
            filing["file_date"],
            datetime.now().isoformat(),
            fail_reason,
        ))
    conn.commit()


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    """
    Process all leadership_change filings that haven't been through radar yet.
    Called automatically from analyze.py after each leadership_change classification,
    and can be run standalone: python radar.py
    """
    print("=" * 60)
    print("Executive Movement Radar — Focused Item 5.02 Extraction")
    print("=" * 60)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("\n[ERROR] GEMINI_API_KEY not set. See analyze.py for setup instructions.")
        return

    client = genai.Client(api_key=api_key)

    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)

        # Only process leadership_change filings not yet in radar table
        pending = conn.execute("""
            SELECT f.*
            FROM filings f
            JOIN analyses a ON f.id = a.filing_id
            LEFT JOIN executive_radar r ON f.id = r.filing_id
            WHERE a.event_type = 'leadership_change'
              AND a.status = 'success'
              AND r.id IS NULL
            ORDER BY f.file_date DESC
        """).fetchall()

        if not pending:
            print("\nNo new leadership_change filings to process.")
            return

        print(f"\nProcessing {len(pending)} leadership filing(s)...\n")
        success = failed = 0

        for row in pending:
            filing = dict(row)
            print(f"[{filing['ticker']}] {filing['accession_no']} ({filing['file_date']})")

            result, fail_reason = extract_radar(client, filing)
            write_radar(conn, filing, result, fail_reason)

            if result:
                deps  = len(result.get("departures", []))
                appts = len(result.get("appointments", []))
                print(f"  ✓ {deps} departure(s), {appts} appointment(s)")
                if result.get("departures"):
                    for d in result["departures"]:
                        print(f"    ↓ {d.get('name','?')} — {d.get('role','?')} [{d.get('departure_type','?')}]")
                if result.get("appointments"):
                    for a in result["appointments"]:
                        print(f"    ↑ {a.get('name','?')} — {a.get('role','?')} [{a.get('appointment_type','?')}]")
                success += 1
            else:
                print(f"  ✗ Failed — {fail_reason}")
                failed += 1

            time.sleep(1)  # free tier rate limit

    print(f"\n{'=' * 60}")
    print(f"Done. {success} succeeded, {failed} failed.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    run()
