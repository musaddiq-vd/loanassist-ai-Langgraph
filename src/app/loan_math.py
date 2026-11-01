"""Deterministic loan helpers: amount/rate/tenure parsing, EMI maths, CIBIL and loan-type detection.

Used as the fallback when the LLM extraction fails, and to validate whatever the
LLM returns. Pure functions, so they are easy to unit test.
"""

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- amounts

_UNIT_MULTIPLIERS = {
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000,
    "million": 1_000_000, "mn": 1_000_000,
    "thousand": 1_000, "k": 1_000,
}
_NUMBER = r"\d+(?:,\d{2,3})*(?:\.\d+)?"
_AMOUNT_RE = re.compile(
    rf"(?:₹|rs\.?|inr)?\s*({_NUMBER})\s*(crores?|cr|lakhs?|lacs?|lac|l|million|mn|thousand|k)?\b(?!\s*%)",
    re.IGNORECASE,
)

MIN_PRINCIPAL, MAX_PRINCIPAL = 1_000, 10_000_000_000  # ₹1,000 .. ₹1,000 crore
MAX_RATE = 50.0
MAX_MONTHS = 480  # 40 years


def _to_float(number: str) -> float:
    return float(number.replace(",", ""))


def parse_amount(text: str) -> float | None:
    """Largest money-like amount in the text: '50 lakh', '₹1.2 crore', '50,00,000', '500k'."""
    candidates = []
    for match in _AMOUNT_RE.finditer(text):
        value = _to_float(match.group(1))
        unit = (match.group(2) or "").lower()
        # Skip numbers that are clearly a rate or a tenure ("8.5%", "20 years").
        after = text[match.end():match.end() + 12].lower()
        if not unit and re.match(r"\s*(%|percent|years?|yrs?|months?|mos?)\b", after):
            continue
        candidates.append(value * _UNIT_MULTIPLIERS.get(unit, 1))
    amounts = [c for c in candidates if MIN_PRINCIPAL <= c <= MAX_PRINCIPAL]
    return max(amounts) if amounts else None


def parse_rate(text: str) -> float | None:
    """Annual interest rate: '8.5%', '8.5 percent', 'rate 9', '9 % p.a.'."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|pc\b)", text, re.IGNORECASE) or re.search(
        r"(?:rate|interest|roi)\D{0,12}(\d+(?:\.\d+)?)", text, re.IGNORECASE
    )
    if not match:
        return None
    rate = float(match.group(1))
    return rate if 0 < rate <= MAX_RATE else None


def parse_tenure_months(text: str) -> int | None:
    """Tenure in months from '20 years', '15 yrs', '240 months', '5 saal'."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(years?|yrs?|yr|saal|months?|mos?)\b", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    months = value if match.group(2).lower().startswith("mo") else value * 12
    months = int(round(months))
    return months if 1 <= months <= MAX_MONTHS else None


# --------------------------------------------------------------------------- EMI


@dataclass
class EmiInputs:
    principal: float | None = None
    annual_rate: float | None = None
    tenure_months: int | None = None

    def missing(self) -> list[str]:
        names = {"principal": "loan amount", "annual_rate": "annual interest rate", "tenure_months": "tenure"}
        return [label for field, label in names.items() if getattr(self, field) is None]

    def fill_from(self, other: "EmiInputs") -> None:
        """Fill missing values (e.g. from earlier messages in the same chat)."""
        self.principal = self.principal if self.principal is not None else other.principal
        self.annual_rate = self.annual_rate if self.annual_rate is not None else other.annual_rate
        self.tenure_months = self.tenure_months if self.tenure_months is not None else other.tenure_months


def validate_emi_inputs(principal, annual_rate, tenure_months) -> EmiInputs:
    """Keep only values that are in a sensible range (guards LLM output too)."""
    try:
        p = float(principal) if principal is not None else None
    except (TypeError, ValueError):
        p = None
    try:
        r = float(annual_rate) if annual_rate is not None else None
    except (TypeError, ValueError):
        r = None
    try:
        m = int(round(float(tenure_months))) if tenure_months is not None else None
    except (TypeError, ValueError):
        m = None
    return EmiInputs(
        principal=p if p is not None and MIN_PRINCIPAL <= p <= MAX_PRINCIPAL else None,
        annual_rate=r if r is not None and 0 < r <= MAX_RATE else None,
        tenure_months=m if m is not None and 1 <= m <= MAX_MONTHS else None,
    )


def parse_emi_inputs(text: str) -> EmiInputs:
    return EmiInputs(parse_amount(text), parse_rate(text), parse_tenure_months(text))


def calculate_emi(principal: float, annual_rate: float, months: int) -> float:
    monthly_rate = annual_rate / 1200
    if monthly_rate == 0:
        return principal / months
    growth = (1 + monthly_rate) ** months
    return principal * monthly_rate * growth / (growth - 1)


def format_inr(amount: float) -> str:
    """Indian digit grouping: 5000000 -> ₹50,00,000."""
    whole, _, fraction = f"{amount:.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"₹{whole}" + ("" if fraction == "00" else f".{fraction}")


def format_tenure(months: int) -> str:
    years, rest = divmod(months, 12)
    if rest == 0:
        return f"{years} year{'s' if years != 1 else ''}"
    if years == 0:
        return f"{months} months"
    return f"{years} years {rest} months"


# --------------------------------------------------------------------------- CIBIL

_SCORE_WORDS = r"(?:cibil|credit\s*score|credit\s*rating|score)"
_CIBIL_AFTER_WORD = re.compile(rf"{_SCORE_WORDS}\D{{0,25}}?(\d{{3}})\b", re.IGNORECASE)
_CIBIL_BEFORE_WORD = re.compile(rf"\b(\d{{3}})\s*(?:is\s+my\s+|as\s+my\s+|my\s+)?{_SCORE_WORDS}", re.IGNORECASE)


def extract_cibil_score(text: str) -> int | None:
    """A 300-900 score only when it sits next to 'CIBIL' / 'credit score' / 'score'.

    'My CIBIL score is 620' -> 620, 'credit score: 745' -> 745, '780 cibil' -> 780,
    'I earn 600 per day' -> None.
    """
    for pattern in (_CIBIL_AFTER_WORD, _CIBIL_BEFORE_WORD):
        for match in pattern.finditer(text):
            score = int(match.group(1))
            if 300 <= score <= 900:
                return score
    return None


# --------------------------------------------------------------------------- fast routing


def fast_route(question: str) -> str | None:
    """Obvious cases that don't need the LLM router (saves one model call + ~1 s).

    - "EMI" plus at least one number      -> calculate_emi
    - a CIBIL / credit score number        -> check_loan_eligibility
    Everything else (incl. "what is EMI?", follow-ups) returns None -> LLM router.
    """
    lower = question.lower()
    if re.search(r"\bemi\b", lower) and re.search(r"\d", lower):
        return "calculate_emi"
    if extract_cibil_score(question) is not None:
        return "check_loan_eligibility"
    return None


# --------------------------------------------------------------------------- loan type

LOAN_TYPES = {
    "home": r"home|housing|house|property|mortgage|flat|apartment",
    "personal": r"personal",
    "car": r"car|auto|vehicle|two[- ]wheeler|bike",
    "education": r"education|student|study",
    "business": r"business|msme|sme",
    "gold": r"gold",
}


def detect_loan_type(text: str) -> str | None:
    lower = text.lower()
    for loan_type, pattern in LOAN_TYPES.items():
        if re.search(rf"\b(?:{pattern})\b", lower):
            return loan_type
    return None
