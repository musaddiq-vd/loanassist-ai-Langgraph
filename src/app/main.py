import base64
import binascii
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

import boto3
from botocore.config import Config as BotoConfig

from src.app.agent import build_loan_agent
from src.app.config import get_settings
from src.app.document_jobs import PermanentDocumentError, analyse_document, finish_document, job_summary, new_doc_id
from src.app.logging_config import configure_logging
from src.app.observability import end_trace, flush_traces, setup_tracing, start_trace, under
from src.app.schemas import (
    CHAT_ID_PATTERN,
    MAX_UPLOAD_BYTES,
    MESSAGE_ID_PATTERN,
    ChatRequest,
    ChatResponse,
    DOC_ID_PATTERN,
    DocumentUpload,
    FeedbackRequest,
    RenameRequest,
    UploadRequest,
)
from src.app.services.bedrock import BedrockService
from src.app.services.alerts import FeedbackAlerts, should_alert
from src.app.services.chat_store import build_chat_store

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Built once per Lambda container and reused across warm invocations.
    setup_tracing(settings.langsmith_api_key, settings.langsmith_api_key_param, settings.langsmith_project, settings.aws_region)
    app.state.bedrock = BedrockService(settings)
    app.state.agent = build_loan_agent(app.state.bedrock)
    app.state.store = build_chat_store(settings.chats_table, settings.aws_region)
    app.state.alerts = FeedbackAlerts(settings.feedback_topic_arn, settings.aws_region)
    # Regional virtual-host endpoint + SigV4, so presigned upload URLs work from the browser.
    app.state.s3 = boto3.client("s3", region_name=settings.aws_region,
                                config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    app.state.sqs = boto3.client("sqs", region_name=settings.aws_region)
    yield


app = FastAPI(title="LoanAssist AI", version="1.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    logger.exception("Unhandled request error")
    return JSONResponse(status_code=500, content={"detail": "Unable to process your request right now."})


def _jwt_claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def current_user(request: Request) -> str:
    """The signed-in user's id (Cognito `sub`).

    On AWS every request first passes API Gateway's Cognito authorizer, which verifies
    the token's signature and expiry; requests without a valid token never reach Lambda.
    So reading the claims of that same, already-verified token here is safe.
    Locally (not on Lambda) everyone is a single "local-user".
    """
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "local-user"
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    try:
        sub = _jwt_claims(token)["sub"]
    except (IndexError, KeyError, ValueError, binascii.Error):
        raise HTTPException(status_code=401, detail="Please sign in.") from None
    return sub


ChatId = Path(pattern=CHAT_ID_PATTERN)
MessageId = Path(pattern=MESSAGE_ID_PATTERN)
DocId = Path(pattern=DOC_ID_PATTERN)


@app.get("/health")
@app.get("/api/health")  # Reachable through CloudFront's /api/* route, no sign-in needed.
def health() -> dict[str, str]:
    return {"status": "ok"}


def _run_agent(request: Request, user_id: str, chat_id: str, payload: ChatRequest, defer_answer: bool) -> dict[str, Any]:
    store = request.app.state.store
    if payload.replace_from:
        # Edit / regenerate: drop that user message and everything after it, then answer again.
        store.delete_from(user_id, chat_id, payload.replace_from)
    state = {
        "question": payload.message,
        "history": store.recent_history(user_id, chat_id),
        # Only finished documents (queued / failed uploads have no facts yet).
        "documents": [d for d in store.list_documents(user_id, chat_id) if d.get("status", "done") == "done"],
        "defer_answer": defer_answer,
    }
    return request.app.state.agent.invoke(state)


def _save_turn(request: Request, user_id: str, chat_id: str, payload: ChatRequest, reply: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns [user message, assistant message] as saved (with ids/times), or [] if saving failed."""
    try:
        title = (payload.title or payload.message[:60]).strip() or "New chat"
        return request.app.state.store.save_turn(user_id, chat_id, title, payload.message, reply)
    except Exception:
        # The user still gets their answer; only the history write failed.
        logger.exception("Saving chat history failed", extra={"chat_id": chat_id})
        return []


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request, user_id: str = Depends(current_user)) -> ChatResponse:
    chat_id = payload.session_id or str(uuid.uuid4())
    run = start_trace("chat", {"question": payload.message}, {"chat_id": chat_id, "streaming": False})
    try:
        with under(run):
            result = _run_agent(request, user_id, chat_id, payload, defer_answer=False)
        response = ChatResponse(
            answer=result["answer"],
            session_id=chat_id,
            sources=result.get("sources", []),
            tool_used=result.get("tool_used"),
            requires_human_review=result.get("requires_human_review", False),
            emi=result.get("emi"),
        )
    except Exception as exc:
        logger.exception("Chat request failed", extra={"chat_id": chat_id})
        end_trace(run, error=repr(exc))
        flush_traces()
        raise HTTPException(status_code=502, detail="LoanAssist service is temporarily unavailable.") from exc
    end_trace(run, {"answer": response.answer}, {"tool_used": response.tool_used, "sources": len(response.sources)})

    saved = _save_turn(request, user_id, chat_id, payload,
                       response.model_dump(exclude={"message_id", "created_at", "user_message_id", "suggestions"}))
    if saved:
        response.user_message_id = saved[0]["id"]
        response.message_id, response.created_at = saved[-1]["id"], saved[-1]["createdAt"]
    if payload.suggest:
        response.suggestions = request.app.state.bedrock.suggest_followups(payload.message, response.answer, response.tool_used)
    flush_traces()
    return response



@app.post("/api/v1/chat/stream")
def chat_stream(payload: ChatRequest, request: Request, user_id: str = Depends(current_user)) -> StreamingResponse:
    """Same as /api/v1/chat, but streams newline-delimited JSON events:
    {"type":"meta",...} once, {"type":"delta","text":"..."} many times, then {"type":"done",...}."""
    chat_id = payload.session_id or str(uuid.uuid4())

    def events() -> Iterator[str]:
        def line(obj: dict[str, Any]) -> str:
            return json.dumps(obj, ensure_ascii=False) + "\n"

        run = start_trace("chat_stream", {"question": payload.message}, {"chat_id": chat_id, "streaming": True})
        try:
            with under(run):
                result = _run_agent(request, user_id, chat_id, payload, defer_answer=True)
            meta = {
                "session_id": chat_id,
                "sources": [s.model_dump() if hasattr(s, "model_dump") else s for s in result.get("sources", [])],
                "tool_used": result.get("tool_used"),
                "requires_human_review": result.get("requires_human_review", False),
                **({"emi": result["emi"]} if result.get("emi") else {}),
            }
            yield line({"type": "meta", **meta})

            if result.get("answer"):  # fixed answers (human review) are sent in one piece
                answer = result["answer"]
                yield line({"type": "delta", "text": answer})
            else:
                parts = []
                extra = {"langsmith_extra": {"parent": run}} if run is not None else {}
                for piece in request.app.state.bedrock.generate_stream(payload.message, result.get("context", ""), result.get("history", []), **extra):
                    parts.append(piece)
                    yield line({"type": "delta", "text": piece})
                answer = "".join(parts)
        except Exception as exc:
            logger.exception("Streaming chat failed", extra={"chat_id": chat_id})
            end_trace(run, error=repr(exc))
            flush_traces()
            yield line({"type": "error", "detail": "LoanAssist service is temporarily unavailable."})
            return
        end_trace(run, {"answer": answer}, {"tool_used": meta["tool_used"], "sources": len(meta["sources"])})

        saved = _save_turn(request, user_id, chat_id, payload, {"answer": answer, **meta})
        yield line({"type": "done", "message_id": saved[-1]["id"] if saved else None,
                    "created_at": saved[-1]["createdAt"] if saved else None,
                    "user_message_id": saved[0]["id"] if saved else None})
        if payload.suggest:
            # Sent after "done": the answer is already complete on screen while these load.
            suggestions = request.app.state.bedrock.suggest_followups(payload.message, answer, meta["tool_used"])
            if suggestions:
                yield line({"type": "suggestions", "items": suggestions})
        flush_traces()  # last, so the user never waits for it

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/documents")
def upload_document(payload: DocumentUpload, request: Request, user_id: str = Depends(current_user)) -> dict:
    """Read a salary slip / bank statement, keep only the extracted facts (not the file) in this chat,
    and post a summary with a debt-to-income eligibility estimate."""
    try:
        data = base64.b64decode(payload.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="The file could not be read.") from None
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Please upload a file smaller than 4 MB.")
    filename = os.path.basename(payload.filename)[:120]
    try:
        raw = analyse_document(request.app.state.bedrock, data, payload.content_type)
    except PermanentDocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        logger.exception("Document extraction failed")
        raise HTTPException(status_code=502, detail="Could not read this document. Please try a clearer PDF or image.") from exc

    store = request.app.state.store
    title = (payload.title or "Document check").strip()
    user_msg = store.add_messages(user_id, payload.session_id, title, [_attachment_message(filename, payload.content_type)])[0]
    reply = finish_document(store, user_id, payload.session_id, new_doc_id(), filename, raw, title)
    return {"session_id": payload.session_id, "messages": [user_msg, reply], "document": reply["data"]["document"]}


def _attachment_message(filename: str, content_type: str) -> dict[str, Any]:
    return {"role": "user", "text": f"📎 {filename}", "data": {"attachment": {"filename": filename, "content_type": content_type}}}


# ---------------------------------------------------------------- big documents (S3 + SQS + DLQ)

@app.post("/api/v1/uploads")
def create_upload(payload: UploadRequest, request: Request, user_id: str = Depends(current_user)) -> dict:
    """Step 1 for big files: a short-lived presigned S3 URL the browser PUTs the file to."""
    if not settings.uploads_bucket:
        raise HTTPException(status_code=400, detail="Large uploads are not enabled here; upload files up to 4 MB.")
    doc_id = new_doc_id()
    filename = os.path.basename(payload.filename)[:120]
    key = f"uploads/{user_id}/{payload.session_id}/{doc_id}/{re.sub(r'[^A-Za-z0-9._-]', '_', filename)}"
    url = request.app.state.s3.generate_presigned_url(
        "put_object", Params={"Bucket": settings.uploads_bucket, "Key": key, "ContentType": payload.content_type}, ExpiresIn=900)
    request.app.state.store.save_document(user_id, payload.session_id, {
        "filename": filename, "content_type": payload.content_type, "status": "uploading", "key": key,
        "title": payload.title, "size": payload.size, "createdAt": int(time.time() * 1000)}, doc_id)
    return {"doc_id": doc_id, "upload_url": url, "content_type": payload.content_type}


def _enqueue(request: Request, user_id: str, chat_id: str, doc: dict[str, Any]) -> None:
    job = {"user_id": user_id, "chat_id": chat_id, "doc_id": doc["doc_id"], "bucket": settings.uploads_bucket,
           "key": doc["key"], "filename": doc["filename"], "content_type": doc["content_type"], "title": doc.get("title")}
    request.app.state.store.save_document(user_id, chat_id, {**doc, "status": "queued", "error": None}, doc["doc_id"])
    request.app.state.sqs.send_message(QueueUrl=settings.document_queue_url, MessageBody=json.dumps(job))


@app.post("/api/v1/chats/{chat_id}/documents/{doc_id}/process")
def process_upload(request: Request, chat_id: str = ChatId, doc_id: str = DocId, user_id: str = Depends(current_user)) -> dict:
    """Step 2: the file is in S3 - post the attachment message and queue it for the worker."""
    store = request.app.state.store
    doc = store.get_document(user_id, chat_id, doc_id)
    if not doc or doc.get("status") != "uploading":
        raise HTTPException(status_code=404, detail="Upload not found.")
    title = (doc.get("title") or "Document check").strip()
    user_msg = store.add_messages(user_id, chat_id, title, [_attachment_message(doc["filename"], doc["content_type"])])[0]
    _enqueue(request, user_id, chat_id, doc)
    return {"doc_id": doc_id, "status": "queued", "message": user_msg}


@app.post("/api/v1/chats/{chat_id}/documents/{doc_id}/retry")
def retry_document(request: Request, chat_id: str = ChatId, doc_id: str = DocId, user_id: str = Depends(current_user)) -> dict:
    doc = request.app.state.store.get_document(user_id, chat_id, doc_id)
    if not doc or doc.get("status") != "failed" or not doc.get("key"):
        raise HTTPException(status_code=409, detail="Only a failed large upload can be retried; upload the file again.")
    _enqueue(request, user_id, chat_id, doc)
    return {"doc_id": doc_id, "status": "queued"}


@app.get("/api/v1/chats/{chat_id}/documents")
def list_documents(request: Request, chat_id: str = ChatId, user_id: str = Depends(current_user)) -> list[dict]:
    """Status of every uploaded document in this chat, for the application checklist."""
    return [job_summary(d) for d in request.app.state.store.list_documents(user_id, chat_id)]


# ---------------------------------------------------------------- source preview

@app.get("/api/v1/sources/file")
def source_file(uri: str, request: Request, user_id: str = Depends(current_user)) -> Response:
    """The policy PDF behind a citation, for the source preview panel. Only files in the docs bucket."""
    match = re.fullmatch(r"s3://([a-z0-9.\-]+)/([A-Za-z0-9._\-/ ]+)", uri)
    if not match or match.group(1) != settings.docs_bucket or ".." in match.group(2):
        raise HTTPException(status_code=404, detail="Source not found.")
    try:
        obj = request.app.state.s3.get_object(Bucket=match.group(1), Key=match.group(2))
    except Exception:
        raise HTTPException(status_code=404, detail="Source not found.") from None
    return Response(content=obj["Body"].read(), media_type=obj.get("ContentType") or "application/pdf",
                    headers={"Cache-Control": "private, max-age=3600"})


@app.put("/api/v1/chats/{chat_id}/messages/{message_id}/feedback")
def set_feedback(payload: FeedbackRequest, request: Request, chat_id: str = ChatId, message_id: str = MessageId,
                 user_id: str = Depends(current_user)) -> dict:
    """Save 👍/👎 and optional written feedback on an answer.

    Stored on the message itself and in a shared FEEDBACK log the team can review;
    a 👎 or a written comment also emails the team (SNS)."""
    store = request.app.state.store
    comment = (payload.comment or "").strip() or None
    if not store.set_feedback(user_id, chat_id, message_id, payload.rating, comment):
        raise HTTPException(status_code=404, detail="Message not found.")

    if payload.rating or comment:
        try:
            question, answer = store.get_exchange(user_id, chat_id, message_id)
            entry = {
                "createdAt": int(time.time() * 1000), "userId": user_id, "chatId": chat_id, "messageId": message_id,
                "rating": payload.rating, "comment": comment, "question": question[:2000], "answer": answer[:4000],
            }
            store.log_feedback({k: v for k, v in entry.items() if v is not None})
            if should_alert(payload.rating, comment):
                request.app.state.alerts.send(entry)
        except Exception:
            logger.exception("Feedback log/alert failed", extra={"chat_id": chat_id})
    return {"message_id": message_id, "rating": payload.rating, "comment": comment}


@app.get("/api/v1/chats")
def list_chats(request: Request, user_id: str = Depends(current_user)) -> list[dict]:
    return request.app.state.store.list_chats(user_id)


@app.get("/api/v1/chats/{chat_id}")
def get_chat(request: Request, chat_id: str = ChatId, user_id: str = Depends(current_user)) -> dict:
    chat = request.app.state.store.get_chat(user_id, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found.")
    return chat


@app.patch("/api/v1/chats/{chat_id}")
def rename_chat(payload: RenameRequest, request: Request, chat_id: str = ChatId, user_id: str = Depends(current_user)) -> dict:
    if not request.app.state.store.rename_chat(user_id, chat_id, payload.title.strip()):
        raise HTTPException(status_code=404, detail="Chat not found.")
    return {"id": chat_id, "title": payload.title.strip()}


@app.delete("/api/v1/chats/{chat_id}", status_code=204)
def delete_chat(request: Request, chat_id: str = ChatId, user_id: str = Depends(current_user)) -> None:
    request.app.state.store.delete_chat(user_id, chat_id)
