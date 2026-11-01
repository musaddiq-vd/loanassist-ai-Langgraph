import json
import logging
import re
from collections.abc import Iterator
from typing import Any

import boto3
from botocore.config import Config
from langsmith import traceable

from src.app.config import Settings
from src.app.schemas import Source

logger = logging.getLogger(__name__)

DOCUMENT_TYPES = ["salary_slip", "bank_statement", "form_16", "itr", "pan_card", "aadhaar_card", "passport",
                  "utility_bill", "property_document", "other"]

SYSTEM_PROMPT = """You are LoanAssist, a helpful loan-information assistant.
Use the supplied knowledge-base context only for product and policy claims.
Tool results in the context (for example an EMI estimate) are already calculated and correct:
state those numbers directly and do not ask the customer to confirm values that appear there.
Ask for missing details only when the context says something is missing.
Citations: knowledge-base passages are numbered like [1], [2]. Put the matching number in square brackets
right after each policy fact you take from a passage, e.g. "PAN and Aadhaar are required [1]."
Only use numbers that appear in the context. Page numbers and section names may be mentioned only exactly
as written in a passage's header. Never invent document names, section numbers, page numbers
or sources. If the context has no numbered passages, do not add citations.
You never approve, reject or "process" a loan and never talk as if you will: when asked to approve, say clearly
that you cannot approve loans and that a loan officer makes the final decision; you can only give a preliminary view.
Be concise, and never give legal/financial advice."""


# A markdown heading up to the next bullet / quote / numbered item / heading. Bedrock often
# returns chunks with newlines flattened ("## Documents required - PAN card ..."), so don't rely on line ends.
_HEADING_RE = re.compile(r"(?:^|\s)#{2,6}\s+(.+?)(?=\s+(?:[-*>]|\d+\.)\s|\s+#|\n|$)")  # "##"+ = sections (skip the "#" title)


def _document_title(uri: str | None) -> str:
    """'s3://bucket/home-loan.md' -> 'Home loan'."""
    name = (uri or "Document").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return name.replace("-", " ").replace("_", " ").strip().capitalize() or "Document"


def number_passages(results: list[dict[str, Any]]) -> tuple[str, list[Source]]:
    """Group retrieved chunks by document and number the documents [1], [2], ...

    The numbers in the context match the order of the returned sources, so the
    model's inline [n] citations line up with the numbered source list in the UI.
    """
    order: list[str] = []
    grouped: dict[str, list[str]] = {}
    pages: dict[str, set[int]] = {}
    sections: dict[str, list[str]] = {}
    for item in results:
        text = (item.get("content") or {}).get("text", "").strip()
        location = item.get("location") or {}
        uri = (location.get("s3Location") or {}).get("uri") or (location.get("webLocation") or {}).get("url") or "unknown"
        if not text:
            continue
        if uri not in grouped:
            order.append(uri)
            grouped[uri], pages[uri], sections[uri] = [], set(), []
        grouped[uri].append(text)
        # Bedrock adds the PDF page number to each chunk's metadata.
        page = (item.get("metadata") or {}).get("x-amz-bedrock-kb-document-page-number")
        if isinstance(page, (int, float)) and page >= 1:
            pages[uri].add(int(page))
        for heading in _HEADING_RE.findall(text):
            heading = heading.strip("# ").strip()[:60]
            if heading and heading not in sections[uri] and len(sections[uri]) < 4:
                sections[uri].append(heading)
    blocks, sources = [], []
    for number, uri in enumerate(order, start=1):
        passage = "\n".join(grouped[uri])
        where = [uri.rsplit("/", 1)[-1]]
        if pages[uri]:
            where.append("page " + ", ".join(str(p) for p in sorted(pages[uri])))
        if sections[uri]:
            where.append("section: " + "; ".join(sections[uri]))
        blocks.append(f"[{number}] {_document_title(uri)} ({' · '.join(where)}):\n{passage}")
        sources.append(Source(title=_document_title(uri), uri=uri, excerpt=passage[:1500],
                              pages=sorted(pages[uri]), sections=sections[uri]))
    return "\n\n".join(blocks), sources


class BedrockService:
    def __init__(self, settings: Settings):
        client_config = Config(retries={"max_attempts": 3, "mode": "standard"})
        self.settings = settings
        self.runtime = boto3.client("bedrock-runtime", region_name=settings.aws_region, config=client_config)
        self.agent_runtime = boto3.client("bedrock-agent-runtime", region_name=settings.aws_region, config=client_config)

    @traceable(name="kb_retrieve", run_type="retriever")
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
        return number_passages(response.get("retrievalResults", []))

    @traceable(name="nova_emi_tool", run_type="llm")
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

    @traceable(name="nova_router", run_type="llm")
    @traceable(name="nova_followups", run_type="llm")
    def suggest_followups(self, question: str, answer: str, tool: str | None) -> list[str]:
        """Up to 3 short next questions the customer might ask, in the customer's language."""
        tool_spec = {"toolSpec": {"name": "followups", "description": "Suggest next questions.", "inputSchema": {"json": {
            "type": "object",
            "properties": {"questions": {"type": "array", "items": {"type": "string"}, "maxItems": 3,
                                         "description": "2-3 short follow-up questions (max 8 words each) the customer could ask next."}},
            "required": ["questions"],
        }}}}
        prompt = (
            "A customer is chatting with a bank's loan assistant. Suggest 2-3 short, useful next questions the CUSTOMER "
            "could ask, written from the customer's point of view. Language: match the customer's question exactly - "
            "if it is in English, write in English; use Hinglish only if the customer wrote in Hinglish. Only loan "
            "topics: EMIs, eligibility, documents, fees, process, their uploaded documents."
            f"\n\nCustomer: {question}\nAssistant ({tool or 'answer'}): {answer[:1200]}"
        )
        try:
            response = self.runtime.converse(
                modelId=self.settings.bedrock_model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                toolConfig={"tools": [tool_spec], "toolChoice": {"tool": {"name": "followups"}}},
                inferenceConfig={"maxTokens": 150, "temperature": 0.3},
            )
            for block in response["output"]["message"]["content"]:
                if "toolUse" in block:
                    items = block["toolUse"].get("input", {}).get("questions", [])
                    return [q.strip()[:80] for q in items if isinstance(q, str) and q.strip()][:3]
        except Exception:
            logger.exception("Follow-up suggestions failed")
        return []

    def select_action(self, question: str, history: list[str]) -> str:
        """The LLM selects one allowed action; fallback keeps the API resilient."""
        router_prompt = """Choose exactly one action for the customer's loan query.
Actions: retrieve_loan_documents, calculate_emi, check_loan_eligibility,
get_customer_details, document_question, direct_answer. Return JSON only: {"action":"..."}.
- retrieve_loan_documents: any policy fact or rule (documents, limits, ages, fees, tenure, LTV, minimum income,
  what happens below a CIBIL threshold, process, who decides), even when it mentions CIBIL or income in general.
- calculate_emi: the customer wants an EMI computed (amount/rate/tenure), including follow-ups that change one value.
- check_loan_eligibility: the customer asks about THEIR OWN eligibility using their own numbers ("my CIBIL is 720").
- document_question: about the salary slip / bank statement uploaded in this chat.
- get_customer_details: only when explicitly asked for their customer profile.
- direct_answer: greetings, thanks, or anything not about loans."""
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

    @traceable(name="nova_answer", run_type="llm")
    def generate(self, question: str, context: str, history: list[str] | None = None) -> str:
        response = self.runtime.converse(**self._answer_request(question, context, history, streaming=False))
        return response["output"]["message"]["content"][0]["text"]

    @traceable(name="nova_answer_stream", run_type="llm")
    def generate_stream(self, question: str, context: str, history: list[str] | None = None) -> Iterator[str]:
        """Yield the answer text in small pieces as Nova Lite writes it."""
        response = self.runtime.converse_stream(**self._answer_request(question, context, history, streaming=True))
        for event in response["stream"]:
            delta = event.get("contentBlockDelta", {}).get("delta", {})
            if delta.get("text"):
                yield delta["text"]

    # The file itself is never sent to LangSmith, only its format and size.
    @traceable(name="nova_read_document", run_type="llm",
               process_inputs=lambda i: {"file_format": i.get("file_format"), "size_bytes": len(i.get("data") or b""),
                                         "text_chars": len(i.get("text") or "")})
    def extract_income_document(self, data: bytes, file_format: str, text: str | None = None) -> dict[str, Any] | None:
        """Read an applicant's document (PDF or image) via a strict tool: its type, and for salary slips /
        bank statements the income facts. Large PDFs are passed as extracted `text` instead of bytes."""
        tool = {
            "toolSpec": {
                "name": "income_document",
                "description": "Record the income facts found in a salary slip or bank statement.",
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "document_type": {"type": "string", "enum": DOCUMENT_TYPES},
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
        if text is not None:
            source: dict[str, Any] = {"text": f"Text extracted from the uploaded PDF:\n{text[:60000]}"}
        elif file_format == "pdf":
            source = {"document": {"format": "pdf", "name": "uploaded-document", "source": {"bytes": data}}}
        else:
            source = {"image": {"format": file_format, "source": {"bytes": data}}}
        prompt = (
            "This is a document a loan applicant uploaded. First decide its document_type. For a salary slip or bank "
            "statement, also record the income facts. Use rupee amounts as plain numbers. If something is not shown, "
            "omit it. For identity, address or property documents only set document_type, a one-line key_facts "
            "(e.g. 'PAN card of the applicant') and confidence - never copy ID numbers, names of other people or addresses."
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
