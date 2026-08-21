from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=128)


class Source(BaseModel):
    title: str | None = None
    uri: str | None = None
    excerpt: str | None = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str | None = None
    sources: list[Source] = []
    tool_used: str | None = None
    requires_human_review: bool = False
