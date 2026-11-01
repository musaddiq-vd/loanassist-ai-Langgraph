"""SQS worker Lambda: reads big uploaded documents queued by the API.

Batch size is 1. A transient error is re-raised so SQS retries the message; after
MAX_RECEIVES attempts SQS moves it to the dead-letter queue. A permanent error
(unreadable file) is marked failed and sent to the DLQ straight away.
"""

import json
import logging
import os

import boto3

from src.app.config import get_settings
from src.app.document_jobs import run_job
from src.app.logging_config import configure_logging
from src.app.services.bedrock import BedrockService
from src.app.services.chat_store import build_chat_store

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

store = build_chat_store(settings.chats_table, settings.aws_region)
bedrock = BedrockService(settings)
s3 = boto3.client("s3", region_name=settings.aws_region)
sqs = boto3.client("sqs", region_name=settings.aws_region)
MAX_RECEIVES = int(os.environ.get("MAX_RECEIVES", "3"))


def handler(event, context):
    for record in event["Records"]:
        job = json.loads(record["body"])
        attempt = int(record["attributes"].get("ApproximateReceiveCount", "1"))
        outcome = run_job(store, bedrock, s3, job, attempt, MAX_RECEIVES)  # raises -> SQS retry / DLQ
        if outcome == "failed_permanent" and settings.document_dlq_url:
            sqs.send_message(QueueUrl=settings.document_dlq_url,
                             MessageBody=json.dumps({**job, "reason": "unreadable file", "attempt": attempt}))
        logger.info("Document %s: %s (attempt %s)", job["doc_id"], outcome, attempt)
    return {"ok": True}
