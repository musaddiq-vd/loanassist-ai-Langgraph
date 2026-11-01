from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from src.app.loan_math import (
    calculate_emi as calculate_emi_amount,
    detect_loan_type,
    extract_cibil_score,
    fast_route,
    format_inr,
    format_tenure,
    parse_emi_inputs,
    validate_emi_inputs,
)
from src.app.schemas import Source
from src.app.services.bedrock import BedrockService


class LoanState(TypedDict, total=False):
    question: str
    history: list[str]
    action: Literal["retrieve_loan_documents", "calculate_emi", "check_loan_eligibility", "get_customer_details", "direct_answer"]
    context: str
    sources: list[Source]
    answer: str
    tool_used: str
    requires_human_review: bool


def build_loan_agent(bedrock: BedrockService):
    """Minimal agent flow: LLM router -> selected tool -> answer or human review."""
    def decide_action(state: LoanState) -> LoanState:
        # Clear-cut EMI / CIBIL questions skip the LLM router; everything else asks the model.
        action = fast_route(state["question"]) or bedrock.select_action(state["question"], state.get("history", []))
        return {"action": action}

    def retrieve_documents(state: LoanState) -> LoanState:
        # Narrow the search to the loan type being discussed (this question, else earlier in the chat).
        loan_type = detect_loan_type(state["question"])
        if not loan_type:
            for line in reversed(state.get("history", [])):
                if line.startswith("User:") and (loan_type := detect_loan_type(line)):
                    break
        context, sources = bedrock.retrieve(state["question"], loan_type)
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
        # Only a number written next to "CIBIL" / "credit score" counts (not "I earn 600 a day").
        score = extract_cibil_score(state["question"])
        if score is None:
            return {"context": "Please share your CIBIL score, monthly income, existing EMIs, requested amount, and tenure for a preliminary eligibility check.", "tool_used": "check_loan_eligibility"}
        if score < 650:
            return {"context": f"CIBIL score {score} is below the 650 review threshold.", "tool_used": "check_loan_eligibility", "requires_human_review": True}
        return {"context": f"CIBIL score {score} meets the basic 650 threshold. This is only a preliminary check; income, debt-to-income ratio, documents, and policy verification are still required.", "tool_used": "check_loan_eligibility"}

    def get_customer_details(_: LoanState) -> LoanState:
        # TODO: Replace with authenticated CRM/core-banking API; never return raw PII to the LLM.
        return {"context": "Customer profile lookup is not connected in this demo. Connect an authenticated customer API here.", "tool_used": "get_customer_details"}

    def human_review(state: LoanState) -> LoanState:
        return {"answer": "Your CIBIL score needs manual review by a loan officer. No automatic approval or rejection has been made. A loan officer will review the complete application and advise next steps.", "requires_human_review": True}

    def generate_answer(state: LoanState) -> LoanState:
        return {"answer": bedrock.generate(state["question"], state.get("context", ""), state.get("history", []))}

    def route_after_agent(state: LoanState) -> str:
        return state["action"]

    def route_after_eligibility(state: LoanState) -> str:
        return "human_review" if state.get("requires_human_review") else "generate_answer"

    graph = StateGraph(LoanState)
    graph.add_node("decide_action", decide_action)
    graph.add_node("retrieve_loan_documents", retrieve_documents)
    graph.add_node("calculate_emi", calculate_emi)
    graph.add_node("check_loan_eligibility", check_eligibility)
    graph.add_node("get_customer_details", get_customer_details)
    graph.add_node("generate_answer", generate_answer)
    graph.add_node("human_review", human_review)
    graph.add_edge(START, "decide_action")
    graph.add_conditional_edges("decide_action", route_after_agent, {
        "retrieve_loan_documents": "retrieve_loan_documents", "calculate_emi": "calculate_emi",
        "check_loan_eligibility": "check_loan_eligibility", "get_customer_details": "get_customer_details",
        "direct_answer": "generate_answer",
    })
    graph.add_edge("retrieve_loan_documents", "generate_answer")
    graph.add_edge("calculate_emi", "generate_answer")
    graph.add_conditional_edges("check_loan_eligibility", route_after_eligibility)
    graph.add_edge("get_customer_details", "generate_answer")
    graph.add_edge("generate_answer", END)
    graph.add_edge("human_review", END)
    return graph.compile()
