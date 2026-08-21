import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.app.agent import build_loan_agent
from src.app.config import get_settings
from src.app.logging_config import configure_logging
from src.app.schemas import ChatRequest, ChatResponse
from src.app.services.bedrock import BedrockService

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
conversation_memory: dict[str, list[str]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reused across warm Lambda invocations to avoid rebuilding AWS clients/graph.
    app.state.agent = build_loan_agent(BedrockService(settings))
    yield


app = FastAPI(title="LoanAssist AI", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)


@app.exception_handler(Exception)
async def unhandled_error(_: Request, exc: Exception):
    logger.exception("Unhandled request error")
    return JSONResponse(status_code=500, content={"detail": "Unable to process your request right now."})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    try:
        session_id = payload.session_id or "anonymous"
        history = conversation_memory.get(session_id, [])[-6:]
        result = request.app.state.agent.invoke({"question": payload.message, "history": history})
        # Demo-only memory. Use DynamoDB/Redis for shared, durable production sessions.
        conversation_memory[session_id] = (history + [f"User: {payload.message}", f"Assistant: {result['answer']}"])[-6:]
        return ChatResponse(answer=result["answer"], session_id=payload.session_id, sources=result.get("sources", []), tool_used=result.get("tool_used"), requires_human_review=result.get("requires_human_review", False))
    except Exception as exc:
        logger.exception("Chat request failed", extra={"session_id": payload.session_id})
        raise HTTPException(status_code=502, detail="LoanAssist service is temporarily unavailable.") from exc
