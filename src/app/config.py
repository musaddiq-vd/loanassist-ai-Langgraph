from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; set these as Lambda environment variables in AWS."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    # Locally set AWS_REGION in .env. On Lambda, boto3 uses Lambda's managed region.
    aws_region: str = "us-east-1"  # Lambda sets AWS_REGION itself; this default keeps tests/CI working without AWS config
    bedrock_model_id: str = "amazon.nova-lite-v1:0"
    bedrock_knowledge_base_id: str = ""
    # TODO: put your Knowledge Base ID in BEDROCK_KNOWLEDGE_BASE_ID.
    bedrock_guardrail_id: str = ""
    # TODO: put your Guardrail ID in BEDROCK_GUARDRAIL_ID.
    bedrock_guardrail_version: str = "DRAFT"
    # DynamoDB table for chat history. Empty = in-memory store (local development).
    chats_table: str = ""
    # SNS topic for feedback alerts (👎 or written comments). Empty = no alerts.
    feedback_topic_arn: str = ""
    # Big-document pipeline (empty locally = process inline) and the policy PDFs bucket for source preview.
    uploads_bucket: str = ""
    document_queue_url: str = ""
    document_dlq_url: str = ""
    docs_bucket: str = ""
    # Optional LangSmith tracing: a key directly (local .env) or an SSM SecureString name (AWS).
    langsmith_api_key: str = ""
    langsmith_api_key_param: str = ""
    langsmith_project: str = "loanassist-ai"
    allowed_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
