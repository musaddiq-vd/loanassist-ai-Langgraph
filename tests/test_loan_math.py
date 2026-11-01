import pytest

from src.app.loan_math import (
    calculate_emi,
    detect_loan_type,
    extract_cibil_score,
    fast_route,
    format_inr,
    parse_amount,
    parse_emi_inputs,
    parse_rate,
    parse_tenure_months,
    validate_emi_inputs,
)


@pytest.mark.parametrize("text, expected", [
    ("Calculate EMI for a 5000000 loan at 8.5% for 20 years", 5_000_000),
    ("EMI on 50 lakh at 8.5% for 20 years", 5_000_000),
    ("₹50,00,000 home loan", 5_000_000),
    ("1.2 crore for 15 years", 12_000_000),
    ("rs 750000 personal loan", 750_000),
    ("I need 500k", 500_000),
    ("loan of 25 lacs", 2_500_000),
    ("at 8.5% for 20 years", None),  # no amount, and rate/tenure numbers are ignored
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("at 8.5% for 20 years", 8.5),
    ("interest rate 9 for 10 years", 9.0),
    ("10.25 percent", 10.25),
    ("no rate here 5000000", None),
    ("at 80% loan to value", None),  # above the 50% sanity limit
])
def test_parse_rate(text, expected):
    assert parse_rate(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("for 20 years", 240),
    ("15 yrs", 180),
    ("240 months", 240),
    ("5 saal", 60),
    ("2.5 years", 30),
    ("for 60 years", None),  # above 40-year limit
])
def test_parse_tenure(text, expected):
    assert parse_tenure_months(text) == expected


def test_parse_emi_any_word_order():
    inputs = parse_emi_inputs("For 20 years at 8.5%, what is the EMI on 50 lakh?")
    assert (inputs.principal, inputs.annual_rate, inputs.tenure_months) == (5_000_000, 8.5, 240)
    assert inputs.missing() == []


def test_follow_up_fills_from_history():
    current = parse_emi_inputs("And if I take it for 15 years instead?")
    assert current.missing() == ["loan amount", "annual interest rate"]
    current.fill_from(parse_emi_inputs("Calculate EMI for 50 lakh at 8.5% for 20 years"))
    assert (current.principal, current.annual_rate, current.tenure_months) == (5_000_000, 8.5, 180)


def test_validate_rejects_bad_llm_values():
    inputs = validate_emi_inputs("5000000", -2, 9999)
    assert inputs.principal == 5_000_000
    assert inputs.annual_rate is None and inputs.tenure_months is None
    assert validate_emi_inputs("abc", None, None).principal is None


def test_calculate_emi_known_value():
    assert round(calculate_emi(5_000_000, 8.5, 240), 2) == 43391.16
    assert calculate_emi(120_000, 0, 12) == 10_000


def test_format_inr_indian_grouping():
    assert format_inr(5_000_000) == "₹50,00,000"
    assert format_inr(43391.16) == "₹43,391.16"
    assert format_inr(999) == "₹999"


@pytest.mark.parametrize("text, expected", [
    ("My CIBIL score is 620. Am I eligible?", 620),
    ("credit score: 745", 745),
    ("780 is my cibil", 780),
    ("my score is 700 and I earn 900 a day", 700),
    ("I earn 600 per day, am I eligible?", None),
    ("loan of 500 thousand", None),
    ("CIBIL 150", None),  # outside 300-900
])
def test_extract_cibil_score(text, expected):
    assert extract_cibil_score(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Calculate EMI for a 5000000 loan at 8.5% for 20 years", "calculate_emi"),
    ("emi on 50 lakh?", "calculate_emi"),
    ("My CIBIL score is 620. Am I eligible?", "check_loan_eligibility"),
    ("What is EMI?", None),  # definition, not a calculation -> LLM router
    ("What CIBIL score do I need for a home loan?", None),  # policy question -> LLM router
    ("And if I take it for 15 years instead?", None),  # follow-up needs history -> LLM router
    ("What documents are required for a home loan?", None),
])
def test_fast_route(text, expected):
    assert fast_route(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("documents for a home loan", "home"),
    ("housing loan rates", "home"),
    ("personal loan foreclosure", "personal"),
    ("two-wheeler loan", "car"),
    ("what is the application process", None),
])
def test_detect_loan_type(text, expected):
    assert detect_loan_type(text) == expected
