from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; set these as Lambda environment variables in AWS."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    # Locally set AWS_REGION in .env. On Lambda, boto3 uses Lambda's managed region.
    aws_region: str | None = None
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
    allowed_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
