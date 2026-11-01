"""Turn facts extracted from an uploaded salary slip / bank statement into a saved summary message."""

from typing import Any

from src.app.loan_math import FOIR_LIMIT, MIN_NET_INCOME, affordability, format_inr, format_tenure

DOC_LABELS = {
    "salary_slip": "Salary slip", "bank_statement": "Bank statement", "form_16": "Form 16", "itr": "Income tax return",
    "pan_card": "PAN card", "aadhaar_card": "Aadhaar card", "passport": "Passport", "utility_bill": "Utility bill",
    "property_document": "Property document", "other": "Document",
}
INCOME_TYPES = {"salary_slip", "bank_statement"}


def clean_extraction(raw: dict[str, Any], filename: str) -> dict[str, Any]:
    """Keep only expected fields with sane values (the model output is untrusted)."""
    def money(key: str) -> float | None:
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            return None
        return value if 0 <= value <= 100_000_000 else None

    def text(key: str, limit: int) -> str:
        value = str(raw.get(key) or "").strip()[:limit]
        return "" if value.lower() in {"n/a", "na", "none", "null", "unknown", "-", "not available"} else value

    doc_type = raw.get("document_type") if raw.get("document_type") in DOC_LABELS else "other"
    net = money("net_monthly_income") or None
    emis = money("existing_emis_monthly") or 0.0
    # On a salary slip the loan EMIs are payroll deductions, so take-home pay is already net of them.
    # Add them back so the debt-to-income check doesn't count the same EMI twice.
    # (On a bank statement the salary credit is before EMI debits, so no add-back.)
    eligible_income = (net + emis if doc_type == "salary_slip" else net) if net else None
    return {
        "filename": filename,
        "document_type": doc_type,
        "employer": text("employer", 120),
        "period": text("period", 60),
        "gross_monthly_income": money("gross_monthly_income"),
        "net_monthly_income": net,
        "existing_emis_monthly": emis,
        "eligible_income": eligible_income,
        "key_facts": str(raw.get("key_facts") or "")[:2500],
        "confidence": raw.get("confidence") if raw.get("confidence") in ("high", "medium", "low") else "low",
    }


def summary_markdown(doc: dict[str, Any]) -> str:
    """Markdown answer shown in the chat right after an upload."""
    label = DOC_LABELS[doc["document_type"]]
    where = " · ".join(x for x in (doc["employer"], doc["period"]) if x)
    lines = [f"**{label} analysed**" + (f" — {where}" if where else "")]

    if doc["document_type"] not in INCOME_TYPES and doc["document_type"] != "other":
        # Identity / address / property / tax documents: only tick them off, never repeat their contents.
        return "\n".join([
            f"**{label} received** ✅",
            "",
            "Added to your application checklist. Open **Checklist** (top bar) to see what's still missing.",
        ])

    if doc["document_type"] == "other" or not doc["net_monthly_income"]:
        lines += [
            "",
            "I couldn't find a monthly income in this file. Please upload a **salary slip** or a **bank statement** "
            "that shows salary credits (PDF, PNG or JPG).",
        ]
        if doc["key_facts"]:
            lines += ["", "What I could read:", "", doc["key_facts"]]
        return "\n".join(lines)

    a = affordability(doc["eligible_income"], doc["existing_emis_monthly"])
    rows = []
    if doc["gross_monthly_income"]:
        rows.append(("Gross income", format_inr(round(doc["gross_monthly_income"]))))
    rows.append(("Net take-home income", format_inr(round(doc["net_monthly_income"]))))
    if doc["eligible_income"] != doc["net_monthly_income"]:
        rows.append(("Income before loan EMIs", format_inr(round(doc["eligible_income"]))))
    rows += [
        ("Existing EMIs", format_inr(round(a.existing_emis))),
        ("Debt-to-income today", f"{a.current_dti:.0%}"),
        (f"Room for a new EMI (≤ {FOIR_LIMIT:.0%} of income)", format_inr(round(a.max_new_emi))),
        (f"Estimated max loan · {a.annual_rate:g}% · {format_tenure(a.months)}", format_inr(round(a.max_loan, -3))),
    ]
    lines += ["", "| | Monthly |", "|---|---:|"] + [f"| {name} | {value} |" for name, value in rows]
    lines += [
        "",
        f"- Minimum income {format_inr(MIN_NET_INCOME)}: {'✅ met' if a.meets_min_income else '❌ not met'}",
        f"- Debt-to-income: {'✅ within' if a.current_dti <= FOIR_LIMIT else '❌ above'} the {FOIR_LIMIT:.0%} limit",
        "- Share your **CIBIL score** for a fuller check.",
    ]
    if doc["confidence"] == "low":
        lines.append("- ⚠️ The file was hard to read; please double-check these numbers.")
    lines += [
        "",
        "Ask me anything about this document, e.g. *“Can I get a 40 lakh home loan for 20 years?”*",
        "",
        "_Preliminary estimate only. A loan officer makes the final decision._",
    ]
    return "\n".join(lines)
