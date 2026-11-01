import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.app.agent import build_loan_agent
from src.app.config import get_settings
from src.app.logging_config import configure_logging
from src.app.schemas import CHAT_ID_PATTERN, ChatRequest, ChatResponse, RenameRequest
from src.app.services.bedrock import BedrockService
from src.app.services.chat_store import build_chat_store

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reused across warm Lambda invocations to avoid rebuilding AWS clients/graph.
    app.state.agent = build_loan_agent(BedrockService(settings))
    app.state.store = build_chat_store(settings.chats_table, settings.aws_region)
    yield


app = FastAPI(title="LoanAssist AI", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    logger.exception("Unhandled request error")
    return JSONResponse(status_code=500, content={"detail": "Unable to process your request right now."})


def current_user(request: Request) -> str:
    """The signed-in user's id.

    On AWS, API Gateway's Cognito authorizer has already verified the token and
    passes its claims in the Lambda event. Locally (no Lambda event) everyone is
    a single "local-user".
    """
    event = request.scope.get("aws.event")
    if event is None:
        return "local-user"
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Please sign in.")
    return claims["sub"]


ChatId = Path(pattern=CHAT_ID_PATTERN)


@app.get("/health")
@app.get("/api/health")  # Reachable through CloudFront's /api/* route, no sign-in needed.
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request, user_id: str = Depends(current_user)) -> ChatResponse:
    store = request.app.state.store
    chat_id = payload.session_id or str(uuid.uuid4())
    try:
        history = store.recent_history(user_id, chat_id)
        result = request.app.state.agent.invoke({"question": payload.message, "history": history})
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

    try:
        title = (payload.title or payload.message[:60]).strip() or "New chat"
        store.save_turn(user_id, chat_id, title, payload.message, response.model_dump())
    except Exception:
        # The user still gets their answer; only the history write failed.
        logger.exception("Saving chat history failed", extra={"chat_id": chat_id})
    return response


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
