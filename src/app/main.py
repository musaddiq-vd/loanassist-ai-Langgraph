import base64
import binascii
import json
import logging
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from src.app.agent import build_loan_agent
from src.app.config import get_settings
from src.app.documents import clean_extraction, summary_markdown
from src.app.logging_config import configure_logging
from src.app.schemas import (
    CHAT_ID_PATTERN,
    MAX_UPLOAD_BYTES,
    MESSAGE_ID_PATTERN,
    ChatRequest,
    ChatResponse,
    DocumentUpload,
    FeedbackRequest,
    RenameRequest,
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
    app.state.bedrock = BedrockService(settings)
    app.state.agent = build_loan_agent(app.state.bedrock)
    app.state.store = build_chat_store(settings.chats_table, settings.aws_region)
    app.state.alerts = FeedbackAlerts(settings.feedback_topic_arn, settings.aws_region)
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


@app.get("/health")
@app.get("/api/health")  # Reachable through CloudFront's /api/* route, no sign-in needed.
def health() -> dict[str, str]:
    return {"status": "ok"}


def _run_agent(request: Request, user_id: str, chat_id: str, question: str, defer_answer: bool) -> dict[str, Any]:
    store = request.app.state.store
    state = {
        "question": question,
        "history": store.recent_history(user_id, chat_id),
        "documents": store.list_documents(user_id, chat_id),
        "defer_answer": defer_answer,
    }
    return request.app.state.agent.invoke(state)


def _save_turn(request: Request, user_id: str, chat_id: str, payload: ChatRequest, reply: dict[str, Any]) -> dict[str, Any] | None:
    try:
        title = (payload.title or payload.message[:60]).strip() or "New chat"
        saved = request.app.state.store.save_turn(user_id, chat_id, title, payload.message, reply)
        return saved[-1]
    except Exception:
        # The user still gets their answer; only the history write failed.
        logger.exception("Saving chat history failed", extra={"chat_id": chat_id})
        return None


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request, user_id: str = Depends(current_user)) -> ChatResponse:
    chat_id = payload.session_id or str(uuid.uuid4())
    try:
        result = _run_agent(request, user_id, chat_id, payload.message, defer_answer=False)
        response = ChatResponse(
            answer=result["answer"],
            session_id=chat_id,
            sources=result.get("sources", []),
            tool_used=result.get("tool_used"),
            requires_human_review=result.get("requires_human_review", False),
        )
    except Exception as exc:
        logger.exception("Chat request failed", extra={"chat_id": chat_id})
        raise HTTPException(status_code=502, detail="LoanAssist service is temporarily unavailable.") from exc

    saved = _save_turn(request, user_id, chat_id, payload, response.model_dump(exclude={"message_id", "created_at"}))
    if saved:
        response.message_id, response.created_at = saved["id"], saved["createdAt"]
    return response


@app.post("/api/v1/chat/stream")
def chat_stream(payload: ChatRequest, request: Request, user_id: str = Depends(current_user)) -> StreamingResponse:
    """Same as /api/v1/chat, but streams newline-delimited JSON events:
    {"type":"meta",...} once, {"type":"delta","text":"..."} many times, then {"type":"done",...}."""
    chat_id = payload.session_id or str(uuid.uuid4())

    def events() -> Iterator[str]:
        def line(obj: dict[str, Any]) -> str:
            return json.dumps(obj, ensure_ascii=False) + "\n"

        try:
            result = _run_agent(request, user_id, chat_id, payload.message, defer_answer=True)
            meta = {
                "session_id": chat_id,
                "sources": [s.model_dump() if hasattr(s, "model_dump") else s for s in result.get("sources", [])],
                "tool_used": result.get("tool_used"),
                "requires_human_review": result.get("requires_human_review", False),
            }
            yield line({"type": "meta", **meta})

            if result.get("answer"):  # fixed answers (human review) are sent in one piece
                answer = result["answer"]
                yield line({"type": "delta", "text": answer})
            else:
                parts = []
                for piece in request.app.state.bedrock.generate_stream(payload.message, result.get("context", ""), result.get("history", [])):
                    parts.append(piece)
                    yield line({"type": "delta", "text": piece})
                answer = "".join(parts)
        except Exception:
            logger.exception("Streaming chat failed", extra={"chat_id": chat_id})
            yield line({"type": "error", "detail": "LoanAssist service is temporarily unavailable."})
            return

        saved = _save_turn(request, user_id, chat_id, payload, {"answer": answer, **meta})
        yield line({"type": "done", "message_id": saved and saved["id"], "created_at": saved and saved["createdAt"]})

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
    file_format = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpeg"}[payload.content_type]
    filename = os.path.basename(payload.filename)[:120]

    try:
        raw = request.app.state.bedrock.extract_income_document(data, file_format)
    except Exception as exc:
        logger.exception("Document extraction failed")
        raise HTTPException(status_code=502, detail="Could not read this document. Please try a clearer PDF or image.") from exc
    document = clean_extraction(raw or {}, filename)

    store = request.app.state.store
    store.save_document(user_id, payload.session_id, document)
    answer = summary_markdown(document)
    title = (payload.title or "Salary slip check").strip()
    saved = store.add_messages(user_id, payload.session_id, title, [
        {"role": "user", "text": f"📎 {filename}", "data": {"attachment": {"filename": filename, "content_type": payload.content_type}}},
        {"role": "assistant", "text": answer, "data": {"answer": answer, "tool_used": "analyze_document", "document": document}},
    ])
    return {"session_id": payload.session_id, "messages": saved, "document": document}


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
