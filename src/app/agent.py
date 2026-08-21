import re
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

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
        return {"action": bedrock.select_action(state["question"], state.get("history", []))}

    def retrieve_documents(state: LoanState) -> LoanState:
        context, sources = bedrock.retrieve(state["question"])
        return {"context": context, "sources": sources, "tool_used": "retrieve_loan_documents"}

    def calculate_emi(state: LoanState) -> LoanState:
        numbers = [float(value.replace(",", "")) for value in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", state["question"])]
        if len(numbers) < 3:
            return {"context": "EMI calculation needs loan amount, annual interest rate, and tenure in years.", "tool_used": "calculate_emi"}
        principal, annual_rate, years = numbers[:3]
        months = int(years * 12)
        monthly_rate = annual_rate / 1200
        emi = principal / months if monthly_rate == 0 else principal * monthly_rate * (1 + monthly_rate) ** months / ((1 + monthly_rate) ** months - 1)
        return {"context": f"EMI estimate: principal ₹{principal:,.0f}, rate {annual_rate:g}% p.a., tenure {years:g} years, monthly EMI ₹{emi:,.2f}. This is an estimate; fees and final rates can change it.", "tool_used": "calculate_emi"}

    def check_eligibility(state: LoanState) -> LoanState:
        scores = [int(value) for value in re.findall(r"\b\d{3}\b", state["question"]) if 300 <= int(value) <= 900]
        if not scores:
            return {"context": "Please share your CIBIL score, monthly income, existing EMIs, requested amount, and tenure for a preliminary eligibility check.", "tool_used": "check_loan_eligibility"}
        score = scores[0]
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
