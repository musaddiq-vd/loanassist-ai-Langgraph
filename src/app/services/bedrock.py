import json
import logging
from typing import Any

import boto3
from botocore.config import Config

from src.app.config import Settings
from src.app.schemas import Source

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are LoanAssist, a helpful loan-information assistant.
Use the supplied knowledge-base context only for product and policy claims.
Be concise, ask for missing details, and never promise approval or give legal/financial advice."""


class BedrockService:
    def __init__(self, settings: Settings):
        client_config = Config(retries={"max_attempts": 3, "mode": "standard"})
        self.settings = settings
        self.runtime = boto3.client("bedrock-runtime", region_name=settings.aws_region, config=client_config)
        self.agent_runtime = boto3.client("bedrock-agent-runtime", region_name=settings.aws_region, config=client_config)

    def retrieve(self, query: str) -> tuple[str, list[Source]]:
        """Bedrock Knowledge Base retrieval (OpenSearch is normally its managed vector store)."""
        if not self.settings.bedrock_knowledge_base_id:
            logger.warning("Knowledge Base ID is not configured")
            return "", []

        response = self.agent_runtime.retrieve(
            knowledgeBaseId=self.settings.bedrock_knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 4}},
        )
        sources: list[Source] = []
        chunks: list[str] = []
        for item in response.get("retrievalResults", []):
            text = item.get("content", {}).get("text", "")
            location = item.get("location", {})
            uri = location.get("s3Location", {}).get("uri") or location.get("webLocation", {}).get("url")
            chunks.append(text)
            sources.append(Source(uri=uri, excerpt=text[:280]))
        return "\n\n".join(chunks), sources

    def select_action(self, question: str, history: list[str]) -> str:
        """The LLM selects one allowed action; fallback keeps the API resilient."""
        router_prompt = """Choose exactly one action for the customer's loan query.
Actions: retrieve_loan_documents, calculate_emi, check_loan_eligibility,
get_customer_details, direct_answer. Return JSON only: {"action":"..."}.
Use documents for policy/process/documents questions; EMI for EMI calculations;
eligibility for CIBIL/income/eligibility; customer details only when explicitly requested."""
        response = self.runtime.converse(
            modelId=self.settings.bedrock_model_id,
            system=[{"text": router_prompt}],
            messages=[{"role": "user", "content": [{"text": f"Recent chat: {history[-4:]}\nQuestion: {question}"}]}],
            inferenceConfig={"maxTokens": 40, "temperature": 0},
        )
        text = response["output"]["message"]["content"][0]["text"].strip()
        try:
            action = json.loads(text)["action"]
            if action in {"retrieve_loan_documents", "calculate_emi", "check_loan_eligibility", "get_customer_details", "direct_answer"}:
                return action
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Invalid router response; using document retrieval", extra={"response": text})
        return "retrieve_loan_documents"

    def generate(self, question: str, context: str, history: list[str] | None = None) -> str:
        user_text = f"Recent conversation:\n{(history or [])[-4:]}\n\nTool / knowledge context:\n{context or 'No matching context found.'}\n\nCustomer question: {question}"
        request: dict[str, Any] = {
            "modelId": self.settings.bedrock_model_id,
            "system": [{"text": SYSTEM_PROMPT}],
            "messages": [{"role": "user", "content": [{"text": user_text}]}],
            "inferenceConfig": {"maxTokens": 700, "temperature": 0.2},
        }
        if self.settings.bedrock_guardrail_id:
            # Guardrail is optional locally; configure its ID/version in Lambda for production.
            request["guardrailConfig"] = {
                "guardrailIdentifier": self.settings.bedrock_guardrail_id,
                "guardrailVersion": self.settings.bedrock_guardrail_version,
            }
        response = self.runtime.converse(**request)
        return response["output"]["message"]["content"][0]["text"]
