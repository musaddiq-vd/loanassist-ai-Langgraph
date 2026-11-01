import pytest

from src.app.loan_math import (
    affordability,
    calculate_emi,
    max_loan_for_emi,
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
    ("What documents are required for a home loan?", "retrieve_loan_documents"),
    ("Explain the loan application process step by step", "retrieve_loan_documents"),
])
def test_fast_route(text, expected):
    assert fast_route(text) == expected


def test_fast_route_with_uploaded_document():
    assert fast_route("What deductions are on my salary slip?", has_documents=True) == "document_question"
    assert fast_route("Am I eligible for a 40 lakh loan?", has_documents=True) == "check_loan_eligibility"
    assert fast_route("What deductions are on my salary slip?", has_documents=False) is None


def test_affordability_foir():
    a = affordability(72_000, 12_000, requested_amount=4_000_000)
    assert round(a.current_dti, 3) == 0.167
    assert a.max_new_emi == 24_000          # 50% of 72,000 minus 12,000
    assert round(a.max_loan, -3) == 2_766_000  # what ₹24,000/month repays at 8.5% over 20 years
    assert a.request_fits is False           # 40 lakh needs ~₹34,700 EMI -> 65% DTI
    assert affordability(20_000).meets_min_income is False
    assert affordability(30_000, 20_000).max_new_emi == 0


def test_max_loan_is_inverse_of_emi():
    emi = calculate_emi(5_000_000, 8.5, 240)
    assert round(max_loan_for_emi(emi, 8.5, 240)) == 5_000_000


@pytest.mark.parametrize("text, expected", [
    ("documents for a home loan", "home"),
    ("housing loan rates", "home"),
    ("personal loan foreclosure", "personal"),
    ("two-wheeler loan", "car"),
    ("what is the application process", None),
])
def test_detect_loan_type(text, expected):
    assert detect_loan_type(text) == expected


def test_citation_questions_go_to_document_search():
    from src.app.loan_math import asks_for_sources
    assert asks_for_sources("could you please mention here citations")
    assert asks_for_sources("Which document says that?")
    assert fast_route("What is the source for this?") == "retrieve_loan_documents"
    assert not asks_for_sources("Calculate EMI for 50 lakh")


def test_number_passages_groups_chunks_by_document():
    from src.app.services.bedrock import number_passages
    results = [
        {"content": {"text": "PAN and Aadhaar"}, "location": {"s3Location": {"uri": "s3://b/home-loan.md"}}},
        {"content": {"text": "Minimum age 21"}, "location": {"s3Location": {"uri": "s3://b/eligibility-and-process.md"}}},
        {"content": {"text": "Tenure up to 30 years"}, "location": {"s3Location": {"uri": "s3://b/home-loan.md"}}},
    ]
    context, sources = number_passages(results)
    assert [s.title for s in sources] == ["Home loan", "Eligibility and process"]
    assert context.startswith("[1] Home loan (home-loan.md):\nPAN and Aadhaar\nTenure up to 30 years")
    assert "[2] Eligibility and process" in context


def test_feedback_alert_text_is_plain_ascii_subject():
    from src.app.services.alerts import feedback_alert_text, should_alert
    subject, body = feedback_alert_text({"createdAt": 1793520000000, "rating": "down", "comment": "wrong rate",
                                         "question": "q", "answer": "a", "chatId": "c", "messageId": "m", "userId": "u"})
    assert subject.isascii() and len(subject) < 100 and "wrong rate" in body
    assert should_alert("down", None) and should_alert(None, "note") and not should_alert("up", "  ")


def test_number_passages_adds_pages_and_sections():
    from src.app.services.bedrock import number_passages
    results = [
        {"content": {"text": "## Documents required\n- PAN card"}, "location": {"s3Location": {"uri": "s3://b/home-loan.pdf"}},
         "metadata": {"x-amz-bedrock-kb-document-page-number": 3.0}},
        {"content": {"text": "## Key terms\n- Tenure up to 30 years"}, "location": {"s3Location": {"uri": "s3://b/home-loan.pdf"}},
         "metadata": {"x-amz-bedrock-kb-document-page-number": 1.0}},
    ]
    context, sources = number_passages(results)
    assert sources[0].pages == [1, 3] and sources[0].sections == ["Documents required", "Key terms"]
    assert context.startswith("[1] Home loan (home-loan.pdf · page 1, 3 · section: Documents required; Key terms):")


def test_threshold_numbers_are_not_the_users_cibil():
    assert extract_cibil_score("What happens if my CIBIL score is below 650?") is None
    assert extract_cibil_score("Is a minimum credit score of 700 needed?") is None
    assert extract_cibil_score("My CIBIL score is 640") == 640


def test_strip_citations_and_policy_routes():
    from src.app.loan_math import strip_citations
    assert strip_citations("PAN is needed [1]. Aadhaar too [1, 2].") == "PAN is needed. Aadhaar too."
    for q in ("What is the loan-to-value ratio for home loans?", "What is the maximum personal loan amount?",
              "What share of my income can go towards EMIs?", "Who makes the final loan decision?",
              "What is the maximum age at loan maturity for self-employed applicants?"):
        assert fast_route(q) == "retrieve_loan_documents", q


def test_sections_from_flattened_markdown_chunks():
    from src.app.services.bedrock import number_passages
    flat = ("# Home Loan Policy (sample) > Sample content. ## Documents required - Identity proof: PAN card "
            "## Key terms - Tenure: up to 30 years - Processing fee: up to 0.5% of the loan amount")
    _, sources = number_passages([{"content": {"text": flat}, "location": {"s3Location": {"uri": "s3://b/home-loan.md"}}}])
    assert sources[0].sections == ["Documents required", "Key terms"]
