"""Reading uploaded documents, shared by the API (small files, inline) and the SQS worker (big files).

Big-file flow:
  browser --PUT--> S3 uploads bucket (presigned URL)
  API  /documents/{id}/process --> SQS DocumentQueue --> worker Lambda (src/worker.py)
  worker: read file -> Nova -> facts + summary saved in DynamoDB -> file deleted from S3
  failures: transient errors are retried by SQS (3 receives) and then land in the DLQ;
            permanent errors (unreadable file) are marked failed and sent to the DLQ at once.
"""

import io
import re
import logging
import time
from typing import Any

from src.app.documents import DOC_LABELS, clean_extraction, summary_markdown

logger = logging.getLogger(__name__)

FORMATS = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpeg"}
MAX_INLINE_PDF_BYTES = 4_400_000   # Bedrock Converse document limit is 4.5 MB; bigger PDFs are sent as text
MAX_IMAGE_BYTES = 3_700_000        # Converse image limit 3.75 MB
MAX_PDF_PAGES_AS_TEXT = 40


class PermanentDocumentError(Exception):
    """The file itself can't be read (retrying won't help)."""


def read_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:MAX_PDF_PAGES_AS_TEXT]
        return "\n\n".join(f"--- page {i + 1} ---\n{(page.extract_text() or '').strip()}" for i, page in enumerate(pages))
    except Exception as exc:
        raise PermanentDocumentError("This PDF could not be opened (it may be damaged or password-protected).") from exc


def analyse_document(bedrock: Any, data: bytes, content_type: str) -> dict[str, Any]:
    """Raw facts from Nova. Big PDFs go in as extracted text; scanned big PDFs are a permanent error."""
    file_format = FORMATS.get(content_type)
    if not file_format:
        raise PermanentDocumentError("Only PDF, PNG or JPG files are supported.")
    if file_format == "pdf" and len(data) > MAX_INLINE_PDF_BYTES:
        text = read_pdf_text(data)
        if len(re.sub(r"--- page \d+ ---", "", text).strip()) < 40:
            raise PermanentDocumentError("This large PDF has no readable text (it looks scanned). "
                                         "Please upload a smaller PDF or a clear photo of the page.")
        return bedrock.extract_income_document(b"", "pdf", text=text) or {}
    if file_format != "pdf" and len(data) > MAX_IMAGE_BYTES:
        raise PermanentDocumentError("Images must be smaller than 3.7 MB. Please upload a smaller photo or a PDF.")
    return bedrock.extract_income_document(data, file_format) or {}


def finish_document(store: Any, user_id: str, chat_id: str, doc_id: str, filename: str, raw: dict[str, Any],
                    title: str) -> dict[str, Any]:
    """Save the facts (status done) and post the summary message. Returns the saved assistant message."""
    document = {**clean_extraction(raw, filename), "status": "done", "doc_id": doc_id}
    store.save_document(user_id, chat_id, document, doc_id)
    answer = summary_markdown(document)
    return store.add_messages(user_id, chat_id, title, [
        {"role": "assistant", "text": answer, "data": {"answer": answer, "tool_used": "analyze_document", "document": document}},
    ])[-1]


def fail_document(store: Any, user_id: str, chat_id: str, doc_id: str, filename: str, error: str, title: str) -> None:
    existing = store.get_document(user_id, chat_id, doc_id) or {}
    store.save_document(user_id, chat_id, {**existing, "filename": filename, "status": "failed", "error": error[:300]}, doc_id)
    answer = (f"⚠️ I couldn't read **{filename}**: {error}\n\n"
              "It has been set aside for review. Use **Retry** in the Checklist, or upload a clearer copy.")
    store.add_messages(user_id, chat_id, title, [
        {"role": "assistant", "text": answer, "data": {"answer": answer, "tool_used": "analyze_document", "error": True}},
    ])


def job_summary(document: dict[str, Any]) -> dict[str, Any]:
    """Only what the UI needs for the checklist (no extracted figures, no IDs)."""
    return {k: document.get(k) for k in ("doc_id", "filename", "document_type", "status", "error", "attempts", "createdAt")} | {
        "label": DOC_LABELS.get(document.get("document_type") or "", None)}


def run_job(store: Any, bedrock: Any, s3: Any, job: dict[str, Any], attempt: int, max_attempts: int) -> str:
    """Process one queued document. Returns 'done' or 'failed_permanent'; raises on a transient error."""
    user_id, chat_id, doc_id = job["user_id"], job["chat_id"], job["doc_id"]
    filename, title = job["filename"], job.get("title") or "Document check"
    existing = store.get_document(user_id, chat_id, doc_id) or {}
    store.save_document(user_id, chat_id, {**existing, "status": "processing", "attempts": attempt}, doc_id)
    try:
        data = s3.get_object(Bucket=job["bucket"], Key=job["key"])["Body"].read()
        raw = analyse_document(bedrock, data, job["content_type"])
    except PermanentDocumentError as exc:
        fail_document(store, user_id, chat_id, doc_id, filename, str(exc), title)
        return "failed_permanent"
    except Exception as exc:
        logger.exception("Document job failed (attempt %s/%s)", attempt, max_attempts)
        if attempt >= max_attempts:
            fail_document(store, user_id, chat_id, doc_id, filename, "the service kept failing after several tries.", title)
        else:
            store.save_document(user_id, chat_id, {**existing, "status": "retrying", "attempts": attempt}, doc_id)
        raise exc
    finish_document(store, user_id, chat_id, doc_id, filename, raw, title)
    try:
        s3.delete_object(Bucket=job["bucket"], Key=job["key"])  # the file itself is never kept
    except Exception:
        logger.warning("Could not delete processed upload %s", job["key"])
    return "done"


def new_doc_id() -> str:
    return f"{int(time.time() * 1000):013d}"
