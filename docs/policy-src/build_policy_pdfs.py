"""Builds the SAMPLE policy PDFs in knowledge-base/ (fictional bank, for demos and evals).

    python docs/policy-src/build_policy_pdfs.py

Each document is written as HTML pages and printed to PDF with headless Chrome, so the
Knowledge Base returns real page numbers for citations. Replace these files with your
bank's real policy PDFs (keep a <file>.metadata.json with "loan_type" next to each).
"""

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "knowledge-base"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BANK = "Example Bank Ltd"

CSS = """
@page { size: A4; margin: 18mm 18mm 16mm; }
body { font-family: Georgia, 'Times New Roman', serif; color: #1d1d1b; font-size: 11.5pt; line-height: 1.55; margin: 0; }
.page { page-break-after: always; min-height: 250mm; position: relative; }
.page:last-child { page-break-after: auto; }
.sample { border: 1.5px solid #c2603a; color: #8a3a1a; background: #fdf0ea; padding: 5px 10px; font: bold 9pt Arial, sans-serif; text-align: center; margin-bottom: 14px; }
.head { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #1d1d1b; padding-bottom: 6px; margin-bottom: 14px; font-family: Arial, sans-serif; }
.head b { font-size: 13pt; } .head span { font-size: 9pt; color: #555; }
h1 { font-size: 20pt; margin: 6px 0 4px; } h2 { font-size: 14pt; margin: 18px 0 6px; } h3 { font-size: 12pt; margin: 14px 0 4px; }
.meta { font: 9.5pt Arial, sans-serif; color: #555; margin-bottom: 10px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 10.5pt; }
th, td { border: 1px solid #bbb; padding: 5px 8px; text-align: left; } th { background: #f1efe8; }
.foot { position: absolute; bottom: 0; left: 0; right: 0; border-top: 1px solid #bbb; padding-top: 4px; font: 8.5pt Arial, sans-serif; color: #666; display: flex; justify-content: space-between; }
"""


def page(doc_title: str, ref: str, number: int, total: int, body: str) -> str:
    return f"""<section class="page">
  <div class="sample">SAMPLE POLICY — fictional bank and figures, for testing LoanAssist AI only</div>
  <div class="head"><b>{BANK}</b><span>{doc_title} · {ref}</span></div>
  {body}
  <div class="foot"><span>{BANK} · {doc_title} · Version 3.2 · Effective 1 April 2026</span><span>Page {number} of {total}</span></div>
</section>"""


DOCS = {
    "home-loan": ("Home Loan Policy", "POL-HL-2026", [
        """<h1>Home Loan Policy</h1>
<div class="meta">Applies to: salaried and self-employed individuals · Owner: Retail Lending</div>
<h2>1. Purpose and scope</h2>
<p>This policy sets out how the bank offers home loans for buying a ready or under-construction house or flat,
building a house on owned land, and extending or renovating an existing home. It applies to all branches and
digital channels.</p>
<h2>2. Eligible properties</h2>
<ul>
  <li>Residential flats and houses with a clear and marketable title.</li>
  <li>Under-construction projects approved by the bank's technical team.</li>
  <li>Plots only when construction starts within 24 months of the first disbursement.</li>
</ul>
<h2>3. Who can apply</h2>
<p>Applicants must meet the bank's general eligibility rules in the <i>Eligibility and Application Process</i>
policy (age, income, existing EMIs and credit score). Up to two co-applicants may be added; a co-owner of the
property must be a co-applicant.</p>""",
        """<h2>4. Documents required</h2>
<table>
  <tr><th>Category</th><th>Documents</th></tr>
  <tr><td>Identity proof</td><td>PAN card and Aadhaar card</td></tr>
  <tr><td>Address proof</td><td>Aadhaar, passport, or utility bill</td></tr>
  <tr><td>Income proof (salaried)</td><td>Last 3 months' salary slips and Form 16</td></tr>
  <tr><td>Income proof (self-employed)</td><td>Last 2 years' ITR and audited financial statements</td></tr>
  <tr><td>Bank statements</td><td>Last 6 months' bank statements</td></tr>
  <tr><td>Property documents</td><td>Sale agreement, title deed, and approved building plan</td></tr>
</table>
<h2>5. Property valuation and legal check</h2>
<p>Every property is valued by an empanelled valuer and its title is checked by the bank's legal panel before
the loan is sanctioned. The loan amount is based on the lower of the valuation and the agreement value.</p>""",
        """<h2>6. Key terms</h2>
<table>
  <tr><th>Term</th><th>Policy</th></tr>
  <tr><td>Tenure</td><td>Up to 30 years</td></tr>
  <tr><td>Loan-to-value (LTV)</td><td>Up to 80% of the property value</td></tr>
  <tr><td>Processing fee</td><td>Up to 0.5% of the loan amount</td></tr>
  <tr><td>Prepayment</td><td>No charges on floating-rate home loans for individuals</td></tr>
  <tr><td>Interest type</td><td>Floating, linked to the bank's external benchmark rate</td></tr>
</table>
<h2>7. Insurance</h2>
<p>Property insurance against fire and natural calamities is mandatory for the loan tenure. Life cover for the
borrower is recommended but optional.</p>
<h2>8. Disbursement</h2>
<p>Ready properties are paid in full at registration. Under-construction properties are paid in stages linked to
construction progress, as certified by the bank's technical team.</p>""",
    ]),
    "personal-loan": ("Personal Loan Policy", "POL-PL-2026", [
        """<h1>Personal Loan Policy</h1>
<div class="meta">Applies to: salaried individuals · Owner: Retail Lending</div>
<h2>1. Purpose</h2>
<p>Personal loans are unsecured loans for personal needs such as medical expenses, education, travel, weddings or
consolidating other debts. They may not be used for speculation or for buying shares.</p>
<h2>2. Documents required</h2>
<ul>
  <li>PAN card and Aadhaar card</li>
  <li>Last 3 months' salary slips</li>
  <li>Last 3 months' bank statements</li>
</ul>
<h2>3. Key terms</h2>
<table>
  <tr><th>Term</th><th>Policy</th></tr>
  <tr><td>Loan amount</td><td>Up to INR 25 lakh</td></tr>
  <tr><td>Tenure</td><td>12 to 60 months</td></tr>
  <tr><td>Collateral</td><td>No collateral required</td></tr>
</table>""",
        """<h2>4. Foreclosure and part-payment</h2>
<p>Foreclosure is allowed after 12 EMIs have been paid, with a charge of up to 4% of the outstanding principal.
Part-payments are allowed once a year, up to 25% of the outstanding principal.</p>
<h2>5. Disbursement</h2>
<p>The approved amount is credited to the applicant's salary account after the loan agreement is signed
digitally. Disbursement is usually completed within two working days of approval.</p>
<h2>6. Repayment</h2>
<p>EMIs are collected by auto-debit (NACH) from the salary account on a fixed date each month. A bounced EMI
attracts a charge as per the bank's schedule of charges.</p>""",
    ]),
    "eligibility-and-process": ("Eligibility and Application Process", "POL-EL-2026", [
        """<h1>Eligibility and Application Process</h1>
<div class="meta">Applies to: all retail loan products · Owner: Credit Risk</div>
<h2>1. Eligibility guidelines</h2>
<ul>
  <li>Minimum age 21, maximum age 60 at loan maturity (65 for self-employed)</li>
  <li>Minimum monthly income: INR 25,000 for salaried applicants</li>
  <li>Total EMIs, including the new loan, should not exceed 50% of monthly income</li>
  <li>A CIBIL score of 750 or above is preferred</li>
  <li>A CIBIL score below 650 is sent for manual review by a loan officer</li>
</ul>
<h2>2. Income assessment</h2>
<p>For salaried applicants, income is taken from salary slips and bank statements. For self-employed applicants,
the average of the last two years' income in the ITR is used.</p>""",
        """<h2>3. Application process</h2>
<ol>
  <li>Submit the application with the required documents</li>
  <li>Document verification and credit check</li>
  <li>Property valuation (home loans only)</li>
  <li>Sanction letter issued</li>
  <li>Loan agreement signed and amount disbursed</li>
</ol>
<h2>4. Decision</h2>
<p>The final loan decision is always made by a loan officer. The digital assistant gives information and
preliminary estimates only; it does not approve or reject loans.</p>
<h2>5. Grievances</h2>
<p>Customers can raise a complaint at any branch or through the bank's website. Complaints are acknowledged within
two working days.</p>""",
    ]),
}


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for name, (title, ref, pages) in DOCS.items():
            html = "<!doctype html><html><head><meta charset='utf-8'><style>" + CSS + "</style></head><body>"
            html += "".join(page(title, ref, i + 1, len(pages), body) for i, body in enumerate(pages)) + "</body></html>"
            src = Path(tmp) / f"{name}.html"
            src.write_text(html, encoding="utf-8")
            pdf = OUT / f"{name}.pdf"
            subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--user-data-dir={tmp}/profile-{name}", f"--print-to-pdf={pdf}", src.as_uri()],
                           check=True, capture_output=True)
            print(f"{pdf.relative_to(ROOT)}  ({len(pages)} pages)")


if __name__ == "__main__":
    main()
