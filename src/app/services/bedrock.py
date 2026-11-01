import json
import logging
from collections.abc import Iterator
from typing import Any

import boto3
from botocore.config import Config

from src.app.config import Settings
from src.app.schemas import Source

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are LoanAssist, a helpful loan-information assistant.
Use the supplied knowledge-base context only for product and policy claims.
Tool results in the context (for example an EMI estimate) are already calculated and correct:
state those numbers directly and do not ask the customer to confirm values that appear there.
Ask for missing details only when the context says something is missing.
Be concise, and never promise approval or give legal/financial advice."""


class BedrockService:
    def __init__(self, settings: Settings):
        client_config = Config(retries={"max_attempts": 3, "mode": "standard"})
        self.settings = settings
        self.runtime = boto3.client("bedrock-runtime", region_name=settings.aws_region, config=client_config)
        self.agent_runtime = boto3.client("bedrock-agent-runtime", region_name=settings.aws_region, config=client_config)

    def retrieve(self, query: str, loan_type: str | None = None) -> tuple[str, list[Source]]:
        """Bedrock Knowledge Base retrieval (vectors in S3 Vectors), filtered by loan type."""
        if not self.settings.bedrock_knowledge_base_id:
            logger.warning("Knowledge Base ID is not configured")
            return "", []

        def search(filter_type: str | None) -> dict[str, Any]:
            vector_search: dict[str, Any] = {"numberOfResults": 4}
            if filter_type:
                # Documents carry a loan_type attribute (<file>.metadata.json); "general" applies to all.
                vector_search["filter"] = {"in": {"key": "loan_type", "value": [filter_type, "general"]}}
            return self.agent_runtime.retrieve(
                knowledgeBaseId=self.settings.bedrock_knowledge_base_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={"vectorSearchConfiguration": vector_search},
            )

        response = search(loan_type)
        if loan_type and not response.get("retrievalResults"):
            # Nothing tagged for this loan type: fall back to searching everything.
            response = search(None)
        sources: list[Source] = []
        chunks: list[str] = []
        for item in response.get("retrievalResults", []):
            text = item.get("content", {}).get("text", "")
            location = item.get("location", {})
            uri = location.get("s3Location", {}).get("uri") or location.get("webLocation", {}).get("url")
            chunks.append(text)
            sources.append(Source(uri=uri, excerpt=text[:280]))
        return "\n\n".join(chunks), sources

    def extract_emi_params(self, question: str, history: list[str]) -> dict[str, Any] | None:
        """Ask the model to fill a strict EMI-inputs tool, so '50 lakh', '1.2 crore',
        any word order, and follow-ups like 'and for 15 years?' all work.
        Returns the raw tool input, or None if the model did not call the tool."""
        tool = {
            "toolSpec": {
                "name": "emi_inputs",
                "description": "Record the loan amount, annual interest rate and tenure needed to compute an EMI.",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "principal_inr": {"type": "number", "description": "Loan amount in rupees as a plain number (50 lakh = 5000000, 1 crore = 10000000). Omit if unknown."},
                        "annual_rate_percent": {"type": "number", "description": "Annual interest rate in percent, e.g. 8.5. Omit if unknown."},
                        "tenure_months": {"type": "number", "description": "Loan tenure in months (20 years = 240). Omit if unknown."},
                    },
                }},
            }
        }
        prompt = (
            "Extract the EMI inputs for the customer's latest question. If the latest question only "
            "changes one value (e.g. 'and for 15 years?'), take the other values from the recent chat. "
            "Never guess a value that was not mentioned.\n\n"
            f"Recent chat: {history[-6:]}\nLatest question: {question}"
        )
        try:
            response = self.runtime.converse(
                modelId=self.settings.bedrock_model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                toolConfig={"tools": [tool], "toolChoice": {"tool": {"name": "emi_inputs"}}},
                inferenceConfig={"maxTokens": 200, "temperature": 0},
            )
        except Exception:
            logger.exception("EMI extraction call failed; using the rule-based parser")
            return None
        for block in response["output"]["message"]["content"]:
            if "toolUse" in block:
                return block["toolUse"].get("input") or {}
        return None

    def select_action(self, question: str, history: list[str]) -> str:
        """The LLM selects one allowed action; fallback keeps the API resilient."""
        router_prompt = """Choose exactly one action for the customer's loan query.
Actions: retrieve_loan_documents, calculate_emi, check_loan_eligibility,
get_customer_details, document_question, direct_answer. Return JSON only: {"action":"..."}.
Use documents for policy/process/documents questions; EMI for EMI calculations;
eligibility for CIBIL/income/eligibility/"how much can I borrow"; document_question for questions about
the salary slip or bank statement the customer uploaded in this chat; customer details only when explicitly requested."""
        response = self.runtime.converse(
            modelId=self.settings.bedrock_model_id,
            system=[{"text": router_prompt}],
            messages=[{"role": "user", "content": [{"text": f"Recent chat: {history[-4:]}\nQuestion: {question}"}]}],
            inferenceConfig={"maxTokens": 40, "temperature": 0},
        )
        text = response["output"]["message"]["content"][0]["text"].strip()
        try:
            action = json.loads(text)["action"]
            if action in {"retrieve_loan_documents", "calculate_emi", "check_loan_eligibility", "get_customer_details", "document_question", "direct_answer"}:
                return action
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Invalid router response; using document retrieval", extra={"response": text})
        return "retrieve_loan_documents"

    def _answer_request(self, question: str, context: str, history: list[str] | None, streaming: bool) -> dict[str, Any]:
        trusted_text = f"Recent conversation:\n{(history or [])[-4:]}\n\nTool / knowledge context:\n{context or 'No matching context found.'}\n\nCustomer question:"
        content: list[dict[str, Any]] = [{"text": f"{trusted_text} {question}"}]
        request: dict[str, Any] = {
            "modelId": self.settings.bedrock_model_id,
            "system": [{"text": SYSTEM_PROMPT}],
            "messages": [{"role": "user", "content": content}],
            "inferenceConfig": {"maxTokens": 700, "temperature": 0.2},
        }
        if self.settings.bedrock_guardrail_id:
            # Only the customer's own words go through the input guardrail (guardContent).
            # Our trusted context (KB text, tool output, history) is skipped, which cuts
            # guardrail text units per request by ~60%. The answer is still fully checked.
            content[:] = [{"text": trusted_text}, {"guardContent": {"text": {"text": question}}}]
            request["guardrailConfig"] = {
                "guardrailIdentifier": self.settings.bedrock_guardrail_id,
                "guardrailVersion": self.settings.bedrock_guardrail_version,
            }
            if streaming:
                # "sync": each chunk is checked before it is sent, so nothing unsafe ever reaches the user.
                request["guardrailConfig"]["streamProcessingMode"] = "sync"
        return request

    def generate(self, question: str, context: str, history: list[str] | None = None) -> str:
        response = self.runtime.converse(**self._answer_request(question, context, history, streaming=False))
        return response["output"]["message"]["content"][0]["text"]

    def generate_stream(self, question: str, context: str, history: list[str] | None = None) -> Iterator[str]:
        """Yield the answer text in small pieces as Nova Lite writes it."""
        response = self.runtime.converse_stream(**self._answer_request(question, context, history, streaming=True))
        for event in response["stream"]:
            delta = event.get("contentBlockDelta", {}).get("delta", {})
            if delta.get("text"):
                yield delta["text"]

    def extract_income_document(self, data: bytes, file_format: str) -> dict[str, Any] | None:
        """Read a salary slip / bank statement (PDF or image) and return the income facts via a strict tool."""
        tool = {
            "toolSpec": {
                "name": "income_document",
                "description": "Record the income facts found in a salary slip or bank statement.",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "document_type": {"type": "string", "enum": ["salary_slip", "bank_statement", "other"]},
                        "employer": {"type": "string", "description": "Employer / company name, if shown."},
                        "period": {"type": "string", "description": "Month or date range covered, e.g. 'October 2026'."},
                        "gross_monthly_income": {"type": "number", "description": "Gross monthly salary in rupees. Omit if not shown."},
                        "net_monthly_income": {"type": "number", "description": "Take-home pay per month in rupees. For a bank statement, the average monthly salary credit."},
                        "existing_emis_monthly": {"type": "number", "description": "Total monthly loan EMIs seen (salary deductions or recurring EMI debits). 0 if none."},
                        "key_facts": {"type": "string", "description": "Up to 12 short bullet points with the document's important figures (allowances, deductions, credits, balances) for answering follow-up questions. Never include account numbers, PAN, Aadhaar, UAN or addresses."},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["document_type", "key_facts", "confidence"],
                }},
            }
        }
        if file_format == "pdf":
            source: dict[str, Any] = {"document": {"format": "pdf", "name": "uploaded-document", "source": {"bytes": data}}}
        else:
            source = {"image": {"format": file_format, "source": {"bytes": data}}}
        prompt = (
            "This is a customer's salary slip or bank statement. Read it carefully and record the income facts "
            "with the income_document tool. Use rupee amounts as plain numbers. If something is not shown, omit it. "
            "If the document is not a salary slip or bank statement, set document_type to 'other'."
        )
        response = self.runtime.converse(
            modelId=self.settings.bedrock_model_id,
            messages=[{"role": "user", "content": [source, {"text": prompt}]}],
            toolConfig={"tools": [tool], "toolChoice": {"tool": {"name": "income_document"}}},
            inferenceConfig={"maxTokens": 900, "temperature": 0},
        )
        for block in response["output"]["message"]["content"]:
            if "toolUse" in block:
                return block["toolUse"].get("input") or {}
        return None
