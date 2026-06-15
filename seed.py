"""
seed.py
-------
Manually seed the Veris database with realistic filings.
Run this when Gemini quota is exhausted or for demo purposes.

Usage: python seed.py
"""

import sqlite3
import json
from datetime import datetime
from config import DB_FILE
import ingest

FILINGS = [
    # US filings
    {"ticker":"JPM",  "company_name":"JPMorgan Chase & Co",        "cik":"0000019617","accession_no":"0000019617-26-000301","file_date":"2026-06-12","form_type":"8-K","filing_url":"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000019617&type=8-K","raw_text":"Item 5.02 Departure of Directors or Certain Officers. Mary Erdoes notified the Board of Directors of JPMorgan Chase of her intention to retire as CEO of Asset and Wealth Management, effective December 31, 2026. The Board has commenced a formal succession process.","market":"US"},
    {"ticker":"COF",  "company_name":"Capital One Financial Corp",  "cik":"0000927628","accession_no":"0000927628-26-000200","file_date":"2026-06-11","form_type":"8-K","filing_url":"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000927628&type=8-K","raw_text":"Item 8.01 Other Events. Capital One Financial Corporation disclosed a data security incident in which an unauthorized party obtained personal information of approximately 1.2 million credit card customers through a misconfigured cloud storage bucket. Affected data includes names, addresses, credit scores, and partial card numbers.","market":"US"},
    {"ticker":"GS",   "company_name":"Goldman Sachs Group Inc",     "cik":"0000886982","accession_no":"0000886982-26-000250","file_date":"2026-06-10","form_type":"8-K","filing_url":"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000886982&type=8-K","raw_text":"Item 1.01 Entry into a Material Definitive Agreement. Goldman Sachs Group Inc entered into a definitive agreement to acquire Summit Financial Technologies Inc for approximately $2.8 billion in cash. The acquisition is subject to regulatory approval and is expected to close in Q4 2026.","market":"US"},
    {"ticker":"MS",   "company_name":"Morgan Stanley",              "cik":"0000895421","accession_no":"0000895421-26-000156","file_date":"2026-06-09","form_type":"8-K","filing_url":"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000895421&type=8-K","raw_text":"Item 2.02 Results of Operations and Financial Condition. Morgan Stanley reported Q2 2026 revenues of $15.8 billion, up 12% year-over-year. Net income of $3.4 billion. Diluted EPS of $2.19. ROE of 14.2%, exceeding consensus estimates of 13.1%. Wealth Management AUM crossed $5 trillion.","market":"US"},
    {"ticker":"BAC",  "company_name":"Bank of America Corp",        "cik":"0000070858","accession_no":"0000070858-26-000391","file_date":"2026-06-08","form_type":"8-K","filing_url":"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000070858&type=8-K","raw_text":"Item 8.01 Other Events. Bank of America Corporation reached a settlement with the Consumer Financial Protection Bureau for $250 million regarding alleged violations of the Electronic Fund Transfer Act, including unauthorized account enrollment and undisclosed fee practices affecting approximately 1.4 million retail customers.","market":"US"},
    {"ticker":"NRIX", "company_name":"Nurix Therapeutics Inc",      "cik":"0001549595","accession_no":"0001193125-26-260640","file_date":"2026-06-08","form_type":"8-K","filing_url":"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001549595&type=8-K","raw_text":"Item 1.01 Entry into a Material Definitive Agreement. Nurix Therapeutics Inc entered into a collaboration and license agreement with Roche for bexobrutideg, a BTK protein degrader. Roche will pay $700 million upfront with potential milestone payments totaling $2.3 billion. Nurix retains co-promotion rights in the United States.","market":"US"},
    {"ticker":"WFC",  "company_name":"Wells Fargo & Company",       "cik":"0000072971","accession_no":"0000072971-26-000219","file_date":"2026-06-07","form_type":"8-K","filing_url":"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000072971&type=8-K","raw_text":"Item 8.01 Other Events. Wells Fargo & Company announced that its Board of Directors declared a quarterly common stock dividend of $0.40 per share, payable on September 1, 2026, to stockholders of record on August 7, 2026. The dividend is unchanged from the prior quarter.","market":"US"},
    {"ticker":"GOCO", "company_name":"GoHealth Inc",                "cik":"0001628280","accession_no":"0001628280-26-041369","file_date":"2026-06-07","form_type":"8-K","filing_url":"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001628280&type=8-K","raw_text":"Item 1.03 Bankruptcy or Receivership. GoHealth Inc and certain subsidiaries filed voluntary petitions for relief under Chapter 11 of the United States Bankruptcy Code. The company has entered into a Restructuring Support Agreement with holders of 100% of its first lien term loan. The company expects to continue normal operations during the Chapter 11 process.","market":"US"},
    # Indian filings
    {"ticker":"RELIANCE","company_name":"Reliance Industries Ltd",  "cik":"500325","accession_no":"NSE-RELIANCE-RIL001","file_date":"2026-06-12","form_type":"Outcome of Board Meeting","filing_url":"https://www.nseindia.com/companies-listing/corporate-filings-announcements","raw_text":"Outcome of Board Meeting. Reliance Industries Limited reported Q4 FY2026 consolidated EBITDA of Rs 47,000 crore, up 11% year-on-year. Revenue from operations stood at Rs 2.73 lakh crore. Jio Platforms added 8.2 million subscribers during the quarter. The Board declared a final dividend of Rs 10 per share.","market":"IN","scrip_code":"500325"},
    {"ticker":"TCS",     "company_name":"Tata Consultancy Services","cik":"532540","accession_no":"NSE-TCS-TCS002","file_date":"2026-06-11","form_type":"Change in Directors/Key Managerial Personnel","filing_url":"https://www.nseindia.com/companies-listing/corporate-filings-announcements","raw_text":"TCS announced the appointment of Samir Seksaria as Chief Financial Officer effective July 1, 2026, following the retirement of incumbent CFO Ramakrishnan V. Mr Seksaria has been with TCS for over 25 years and previously served as Global Head of Finance.","market":"IN","scrip_code":"532540"},
    {"ticker":"HDFCBANK","company_name":"HDFC Bank Ltd",            "cik":"500180","accession_no":"NSE-HDFCBANK-HDFC003","file_date":"2026-06-10","form_type":"Financial Results","filing_url":"https://www.nseindia.com/companies-listing/corporate-filings-announcements","raw_text":"HDFC Bank Limited announced Q4 FY2026 financial results. Net profit of Rs 17,620 crore, up 37% year-on-year. Net interest margin stable at 3.6%. Gross NPA ratio improved to 1.24% from 1.42%. The Board recommended a dividend of Rs 22 per share for FY2026.","market":"IN","scrip_code":"500180"},
    {"ticker":"INFY",    "company_name":"Infosys Ltd",              "cik":"500209","accession_no":"NSE-INFY-INF004","file_date":"2026-06-09","form_type":"Outcome of Board Meeting","filing_url":"https://www.nseindia.com/companies-listing/corporate-filings-announcements","raw_text":"Infosys Limited Q4 FY2026 results. Revenue of $4.97 billion, up 6.1% in constant currency. Operating margin of 21.1%, up 0.8 percentage points. The company revised FY2027 revenue growth guidance to 4.5-6.5% in constant currency terms. Large deal wins of $5.4 billion for the quarter.","market":"IN","scrip_code":"500209"},
]

ANALYSES = [
    {"event_type":"leadership_change","summary":"JPMorgan Asset and Wealth Management CEO Mary Erdoes announced retirement effective December 31 2026. Board has commenced formal succession process for one of the firm's most senior revenue-generating roles.","materiality":8,"calibrated_materiality":8,"urgency":"high","thesis_impact":"bearish — departure of key revenue leader creates succession uncertainty and potential client relationship disruption in AWM division","risk_flags":json.dumps(["AWM client retention risk during transition","Strategy continuity uncertainty","Investor confidence in franchise"]),"valuation_note":"Modest P/E multiple compression expected near-term. AWM contributes ~18% of total revenue. Watch for analyst target revisions."},
    {"event_type":"cybersecurity","summary":"Capital One disclosed a data security incident affecting 1.2 million credit card customers. Unauthorized access via misconfigured cloud storage bucket exposed names, addresses, credit scores, and partial card numbers.","materiality":9,"calibrated_materiality":9,"urgency":"high","thesis_impact":"bearish — material breach triggers OCC/CFPB enforcement risk, class action litigation, and measurable customer attrition in core card segment","risk_flags":json.dumps(["OCC consent order likely constraining balance sheet growth","Class action litigation near-certain","Customer attrition in prime card segment","Estimated $150-300M remediation cost"]),"valuation_note":"Prior breach analogs (Capital One 2019) suggest 12-18% drawdown sustained 60+ days. P/B discount likely widens near-term."},
    {"event_type":"M&A","summary":"Goldman Sachs entered definitive agreement to acquire Summit Financial Technologies for $2.8 billion cash. Deal targets expansion in financial infrastructure and clearing services, subject to regulatory approval expected Q4 2026.","materiality":8,"calibrated_materiality":8,"urgency":"high","thesis_impact":"bullish — strategic bolt-on deepens fintech infrastructure moat and increases wallet share per institutional client","risk_flags":json.dumps(["Regulatory approval risk (DOJ, FINRA)","Integration execution risk","CET1 ratio compression ~25bps"]),"valuation_note":"At ~11x EV/EBITDA, deal priced at modest premium to clearing asset comps. EPS dilutive year 1 (~3%), accretive by year 3 on synergy estimates."},
    {"event_type":"earnings","summary":"Morgan Stanley reported Q2 2026 revenues of $15.8B (+12% YoY), net income $3.4B, diluted EPS $2.19. ROE of 14.2% beat consensus of 13.1%. Wealth Management AUM crossed $5 trillion milestone.","materiality":7,"calibrated_materiality":7,"urgency":"medium","thesis_impact":"bullish — beat-and-raise quarter with ROE expansion validates wealth management flywheel and diversified revenue model","risk_flags":json.dumps(["Rate sensitivity in fixed income trading","AUM fee compression from passive shift"]),"valuation_note":"EPS beat consensus by 8%. At 14.2x forward P/E vs peer median 16x, stock screens cheap. Probability of price target upgrades elevated."},
    {"event_type":"litigation","summary":"Bank of America settled CFPB enforcement action for $250M over alleged EFTA violations including unauthorized account enrollment and undisclosed fee practices affecting ~1.4M retail customers.","materiality":6,"calibrated_materiality":6,"urgency":"medium","thesis_impact":"bearish — fine is financially immaterial but signals persistent compliance overhang and elevated regulatory risk premium for retail segment","risk_flags":json.dumps(["CFPB enforcement precedent for peer banks","Follow-on private class action litigation","Reputational damage in retail deposit franchise"]),"valuation_note":"$250M ≈ 0.5% of annual net income. Immaterial to earnings but adds ~5bps to regulatory risk spread in cost of equity models."},
    {"event_type":"M&A","summary":"Nurix Therapeutics entered collaboration with Roche for BTK degrader bexobrutideg. Roche pays $700M upfront with $2.3B in potential milestones. Nurix retains US co-promotion rights.","materiality":9,"calibrated_materiality":9,"urgency":"high","thesis_impact":"bullish — transformational deal validates degrader platform, provides non-dilutive capital, and de-risks pipeline through Big Pharma validation","risk_flags":json.dumps(["Clinical trial execution risk","Milestone payment contingency","Co-promotion execution risk"]),"valuation_note":"$700M upfront vs $180M market cap at signing implies significant re-rating. Platform value now validated by Tier-1 pharma partner."},
    {"event_type":"dividend","summary":"Wells Fargo declared quarterly common dividend of $0.40 per share payable September 1 2026. Dividend unchanged from prior quarter, implying annualized yield of approximately 2.8% at current prices.","materiality":3,"calibrated_materiality":3,"urgency":"low","thesis_impact":"neutral — in-line with guidance and prior quarter; no signal for payout ratio change or capital return acceleration","risk_flags":json.dumps([]),"valuation_note":"2.8% yield in line with sector median. No change to capital return model assumptions."},
    {"event_type":"restatement","summary":"GoHealth filed Chapter 11 bankruptcy with prepackaged restructuring plan supported by 100% of first lien lenders. Company expects to continue normal operations during the process.","materiality":9,"calibrated_materiality":9,"urgency":"high","thesis_impact":"bearish — equity value likely zero in restructuring; lender recovery dependent on enterprise value vs debt quantum","risk_flags":json.dumps(["Equity impairment near-certain","Customer and partner relationship risk","Operational disruption risk during restructuring"]),"valuation_note":"Prepackaged plan accelerates emergence but equity holders typically receive negligible recovery. Bonds trading at distressed levels."},
    {"event_type":"earnings","summary":"Reliance Industries Q4 FY2026 consolidated EBITDA Rs 47,000 crore up 11% YoY. Jio added 8.2M subscribers. Final dividend of Rs 10 per share declared. Revenue Rs 2.73 lakh crore.","materiality":6,"calibrated_materiality":6,"urgency":"medium","thesis_impact":"bullish — broad-based beat across O2C, Jio, and Retail segments validates conglomerate growth thesis","risk_flags":json.dumps(["Jio ARPU pressure from competition","Retail margin compression","O2C segment exposed to crude volatility"]),"valuation_note":"Conglomerate discount persists at ~20%. Sum-of-parts valuation suggests 15-20% upside if discount narrows."},
    {"event_type":"leadership_change","summary":"TCS appointed Samir Seksaria as CFO effective July 1 2026 following retirement of incumbent Ramakrishnan V. Seksaria is a 25-year TCS veteran and former Global Head of Finance — internal promotion.","materiality":5,"calibrated_materiality":5,"urgency":"medium","thesis_impact":"neutral — internal promotion from known candidate reduces transition risk; no strategic pivot expected","risk_flags":json.dumps(["CFO transition period execution risk"]),"valuation_note":"Immaterial to valuation. Internal succession signals stability."},
    {"event_type":"earnings","summary":"HDFC Bank Q4 FY2026 net profit Rs 17,620 crore up 37% YoY. NIM stable at 3.6%. Gross NPA improved to 1.24% from 1.42%. Dividend of Rs 22 per share recommended.","materiality":7,"calibrated_materiality":7,"urgency":"medium","thesis_impact":"bullish — NPA improvement and NIM stability confirm post-merger integration is on track; credit quality recovery ahead of consensus","risk_flags":json.dumps(["Rate cut risk compressing NIM","Deposit mobilisation pressure"]),"valuation_note":"At 2.8x book, trades at premium to peers justified by ROA recovery trajectory. Dividend yield 1.3%."},
    {"event_type":"earnings","summary":"Infosys Q4 FY2026 revenue $4.97B up 6.1% constant currency. Operating margin 21.1% up 80bps. Large deal wins $5.4B. FY2027 guidance revised to 4.5-6.5% CC growth.","materiality":6,"calibrated_materiality":6,"urgency":"medium","thesis_impact":"bullish — guidance upgrade and deal pipeline strength signal demand recovery; margin improvement adds to thesis","risk_flags":json.dumps(["Client discretionary spend uncertainty","Visa cost pressure on US margins","AI cannibalisation of legacy services"]),"valuation_note":"At 22x forward earnings, guidance upgrade likely drives 3-5% re-rating. Deal wins provide 12-month revenue visibility."},
]

def run():
    print("=" * 55)
    print("Veris — Manual Data Seed")
    print(f"Seeding {len(FILINGS)} filings...")
    print("=" * 55)

    with sqlite3.connect(DB_FILE) as conn:
        ingest.init_db(conn)

        # ensure market + scrip_code columns
        cols = {r[1] for r in conn.execute("PRAGMA table_info(filings)")}
        if "market" not in cols:
            conn.execute("ALTER TABLE filings ADD COLUMN market TEXT DEFAULT 'US'")
        if "scrip_code" not in cols:
            conn.execute("ALTER TABLE filings ADD COLUMN scrip_code TEXT")

        # ensure analyses schema
        cols_a = {r[1] for r in conn.execute("PRAGMA table_info(analyses)")}
        for col, typedef in [
            ("status","TEXT DEFAULT 'success'"),
            ("fail_reason","TEXT"),
            ("calibrated_materiality","INTEGER"),
        ]:
            if col not in cols_a:
                conn.execute(f"ALTER TABLE analyses ADD COLUMN {col} {typedef}")
        conn.commit()

        inserted = 0
        for f, a in zip(FILINGS, ANALYSES):
            # Insert filing
            existing = conn.execute(
                "SELECT id FROM filings WHERE accession_no=?", (f["accession_no"],)
            ).fetchone()

            if not existing:
                conn.execute("""INSERT INTO filings
                    (ticker,company_name,cik,accession_no,file_date,form_type,
                     filing_url,raw_text,fetched_at,market,scrip_code)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (f["ticker"],f["company_name"],f["cik"],f["accession_no"],
                     f["file_date"],f["form_type"],f["filing_url"],f["raw_text"],
                     datetime.now().isoformat(),f.get("market","US"),
                     f.get("scrip_code","")))

            fid = conn.execute(
                "SELECT id FROM filings WHERE accession_no=?", (f["accession_no"],)
            ).fetchone()[0]

            # Insert analysis
            conn.execute("""INSERT OR REPLACE INTO analyses
                (filing_id,event_type,summary,materiality,calibrated_materiality,
                 urgency,thesis_impact,risk_flags,valuation_note,analyzed_at,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,'success')""",
                (fid,a["event_type"],a["summary"],a["materiality"],
                 a["calibrated_materiality"],a["urgency"],a["thesis_impact"],
                 a["risk_flags"],a["valuation_note"],datetime.now().isoformat()))

            conn.commit()
            inserted += 1
            print(f"  ✓ [{f['ticker']}] {f['company_name'][:35]} | {a['event_type']} | mat {a['materiality']}/10")

    print(f"\n{'=' * 55}")
    print(f"Done. {inserted} filings seeded.")
    print(f"Visit /filings or /digest to see them live.")
    print(f"{'=' * 55}")

if __name__ == "__main__":
    run()
