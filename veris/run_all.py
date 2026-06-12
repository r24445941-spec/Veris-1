"""
run_all.py
----------
Veris — full pipeline execution.

  1. Ingest US:    SEC EDGAR 8-K filings
  2. Ingest India: BSE corporate announcements
  3. Analyze:      Gemini 1.5 Flash classification + executive radar
  4. Enrich:       Yahoo Finance price reactions
  5. Report:       Weekly memo HTML + Markdown

Usage: python run_all.py

Requirements:
  pip install -r requirements.txt
  export GEMINI_API_KEY=AIza...
"""

import sys
import traceback


def main():
    import ingest
    import ingest_india
    import analyze
    import enrich
    import memo

    steps = [
        ("Ingest US:     SEC EDGAR 8-K filings",              ingest.run),
        ("Ingest India:  NSE/BSE announcements (auto-fallback)", ingest_india.run),
        ("Analyze:       Gemini classification + exec radar", analyze.run),
        ("Enrich:        Yahoo Finance price reactions",       enrich.run),
        ("Report:        Generating weekly memo",             memo.run),
    ]

    print("\n" + "=" * 60)
    print("  Veris — Corporate Filing Intelligence")
    print("  Full Pipeline Execution")
    print("=" * 60 + "\n")

    for label, fn in steps:
        print(f"▶  {label}...")
        try:
            fn()
        except Exception:
            print(f"\n[STOPPED] {label} raised an exception:\n")
            traceback.print_exc()
            sys.exit(1)
        print()

    print("=" * 60)
    print("  Pipeline complete.")
    print("  Open memos/ folder or visit http://localhost:8000")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
