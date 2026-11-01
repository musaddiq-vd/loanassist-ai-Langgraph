from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from src.app.loan_math import (
    affordability,
    asks_for_sources,
    calculate_emi as calculate_emi_amount,
    describe_affordability,
    detect_loan_type,
    extract_cibil_score,
    fast_route,
    format_inr,
    format_tenure,
    parse_amount,
    parse_emi_inputs,
    parse_rate,
    parse_tenure_months,
    validate_emi_inputs,
)
from src.app.schemas import Source
from src.app.services.bedrock import BedrockService

HUMAN_REVIEW_ANSWER = (
    "Your CIBIL score needs manual review by a loan officer. No automatic approval or rejection has been made. "
    "A loan officer will review the complete application and advise next steps."
)


class LoanState(TypedDict, total=False):
    question: str
    history: list[str]
    documents: list[dict[str, Any]]  # income facts extracted from files uploaded in this chat
    defer_answer: bool               # streaming: stop before the final LLM answer, the API streams it
    action: Literal[
        "retrieve_loan_documents", "calculate_emi", "check_loan_eligibility",
        "get_customer_details", "document_question", "direct_answer",
    ]
    context: str
    sources: list[Source]
    answer: str
    tool_used: str
    requires_human_review: bool


def latest_income_document(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    for doc in reversed(documents or []):
        if doc.get("eligible_income"):
            return doc
    return None


def describe_documents(documents: list[dict[str, Any]]) -> str:
    """Text the LLM can use to answer questions about uploaded documents (no raw file is kept)."""
    parts = []
    for doc in documents:
        label = doc.get("document_type", "document").replace("_", " ")
        header = f"Uploaded {label} '{doc.get('filename', '')}'"
        if doc.get("employer"):
            header += f" from {doc['employer']}"
        if doc.get("period"):
            header += f" for {doc['period']}"
        figures = [
            f"{name}: {format_inr(round(doc[key]))}"
            for key, name in (("gross_monthly_income", "gross monthly income"), ("net_monthly_income", "net take-home income"),
                              ("eligible_income", "income before loan EMIs"), ("existing_emis_monthly", "existing EMIs"))
            if doc.get(key) is not None
        ]
        parts.append(f"{header}. {'; '.join(figures)}. Key facts: {doc.get('key_facts', '')}")
    return "\n".join(parts)


def build_loan_agent(bedrock: BedrockService):
    """Agent flow: fast route / LLM router -> one tool -> answer (or human review)."""

    def decide_action(state: LoanState) -> LoanState:
        # Clear-cut questions skip the LLM router; everything else asks the model.
        has_docs = bool(state.get("documents"))
        action = fast_route(state["question"], has_docs) or bedrock.select_action(state["question"], state.get("history", []))
        if action == "document_question" and not has_docs:
            action = "check_loan_eligibility"
        return {"action": action}

    def retrieve_documents(state: LoanState) -> LoanState:
        # Narrow the search to the loan type being discussed (this question, else earlier in the chat).
        loan_type = detect_loan_type(state["question"])
        if not loan_type:
            for line in reversed(state.get("history", [])):
                if line.startswith("User:") and (loan_type := detect_loan_type(line)):
                    break
        # "Mention citations" / "what's the source?" -> search for the previous question again,
        # so the answer is rebuilt from real passages with real [n] citations.
        query = state["question"]
        if asks_for_sources(query):
            previous = next((line[5:].strip() for line in reversed(state.get("history", [])) if line.startswith("User:")), None)
            if previous:
                query = f"{previous} {query}"
        context, sources = bedrock.retrieve(query, loan_type)
        if not sources:
            context = "No policy passages matched. Say that this isn't covered in the policy documents; do not add citations."
        elif asks_for_sources(state["question"]):
            context = ("The customer wants citations for the previous answer. Rewrite that answer using ONLY the numbered "
                       "passages below and cite every fact with its [n]. Drop anything the passages do not support.\n\n" + context)
        return {"context": context, "sources": sources, "tool_used": "retrieve_loan_documents"}

    def calculate_emi(state: LoanState) -> LoanState:
        question, history = state["question"], state.get("history", [])
        # 1) LLM tool call (understands "50 lakh", any word order, follow-ups), validated for sane ranges.
        raw = bedrock.extract_emi_params(question, history) or {}
        inputs = validate_emi_inputs(raw.get("principal_inr"), raw.get("annual_rate_percent"), raw.get("tenure_months"))
        # 2) Rule-based parser for anything the LLM missed: this question first, then earlier user messages.
        inputs.fill_from(parse_emi_inputs(question))
        for line in reversed(history):
            if not inputs.missing():
                break
            if line.startswith("User:"):
                inputs.fill_from(parse_emi_inputs(line))

        missing = inputs.missing()
        if missing:
            return {"context": f"EMI calculation needs the {', '.join(missing)}. Ask the customer for it.", "tool_used": "calculate_emi"}

        emi = calculate_emi_amount(inputs.principal, inputs.annual_rate, inputs.tenure_months)
        total_interest = emi * inputs.tenure_months - inputs.principal
        return {
            "context": (
                f"EMI estimate: principal {format_inr(inputs.principal)}, rate {inputs.annual_rate:g}% p.a., "
                f"tenure {format_tenure(inputs.tenure_months)}, monthly EMI {format_inr(round(emi, 2))}, "
                f"total interest {format_inr(round(total_interest))}. This is an estimate; fees and final rates can change it."
            ),
            "tool_used": "calculate_emi",
        }

    def check_eligibility(state: LoanState) -> LoanState:
        question = state["question"]
        # Only a number written next to "CIBIL" / "credit score" counts (not "I earn 600 a day").
        score = extract_cibil_score(question)
        if score is not None and score < 650:
            return {"context": f"CIBIL score {score} is below the 650 review threshold.", "tool_used": "check_loan_eligibility", "requires_human_review": True}

        facts = []
        if score is not None:
            facts.append(f"CIBIL score {score} meets the basic 650 threshold.")
        doc = latest_income_document(state.get("documents", []))
        if doc:
            # Income from the uploaded salary slip / bank statement -> debt-to-income check.
            a = affordability(
                doc["eligible_income"], doc.get("existing_emis_monthly") or 0,
                parse_rate(question), parse_tenure_months(question), parse_amount(question),
            )
            facts.append(f"From the uploaded {doc.get('document_type', 'document').replace('_', ' ')}: {describe_affordability(a)}")
        if not facts:
            return {
                "context": "Ask for the CIBIL score, monthly income, existing EMIs, requested amount and tenure, "
                           "or suggest uploading a salary slip or bank statement with the 📎 button for an instant check.",
                "tool_used": "check_loan_eligibility",
            }
        if score is None:
            facts.append("CIBIL score not shared yet; it is also needed for a full check.")
        return {"context": " ".join(facts), "tool_used": "check_loan_eligibility"}

    def document_question(state: LoanState) -> LoanState:
        documents = state.get("documents", [])
        context = describe_documents(documents)
        doc = latest_income_document(documents)
        if doc:
            a = affordability(doc["eligible_income"], doc.get("existing_emis_monthly") or 0)
            context += f"\nAffordability: {describe_affordability(a)}"
        return {"context": context, "tool_used": "document_question"}

    def get_customer_details(_: LoanState) -> LoanState:
        # TODO: Replace with authenticated CRM/core-banking API; never return raw PII to the LLM.
        return {"context": "Customer profile lookup is not connected in this demo. Connect an authenticated customer API here.", "tool_used": "get_customer_details"}

    def human_review(_: LoanState) -> LoanState:
        return {"answer": HUMAN_REVIEW_ANSWER, "requires_human_review": True}

    def generate_answer(state: LoanState) -> LoanState:
        if state.get("defer_answer"):
            return {}  # the streaming endpoint writes the answer itself
        return {"answer": bedrock.generate(state["question"], state.get("context", ""), state.get("history", []))}

    def route_after_eligibility(state: LoanState) -> str:
        return "human_review" if state.get("requires_human_review") else "generate_answer"

    graph = StateGraph(LoanState)
    graph.add_node("decide_action", decide_action)
    graph.add_node("retrieve_loan_documents", retrieve_documents)
    graph.add_node("calculate_emi", calculate_emi)
    graph.add_node("check_loan_eligibility", check_eligibility)
    graph.add_node("document_question", document_question)
    graph.add_node("get_customer_details", get_customer_details)
    graph.add_node("generate_answer", generate_answer)
    graph.add_node("human_review", human_review)
    graph.add_edge(START, "decide_action")
    graph.add_conditional_edges("decide_action", lambda s: s["action"], {
        "retrieve_loan_documents": "retrieve_loan_documents", "calculate_emi": "calculate_emi",
        "check_loan_eligibility": "check_loan_eligibility", "get_customer_details": "get_customer_details",
        "document_question": "document_question", "direct_answer": "generate_answer",
    })
    for tool in ("retrieve_loan_documents", "calculate_emi", "document_question", "get_customer_details"):
        graph.add_edge(tool, "generate_answer")
    graph.add_conditional_edges("check_loan_eligibility", route_after_eligibility)
    graph.add_edge("generate_answer", END)
    graph.add_edge("human_review", END)
    return graph.compile()
