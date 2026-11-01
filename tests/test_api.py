"""API tests with a fake Bedrock service (no AWS calls). Runs the real LangGraph agent and in-memory store."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from src.app import main
from src.app.agent import build_loan_agent
from src.app.services.chat_store import MemoryChatStore


class FakeBedrock:
    def __init__(self):
        self.contexts = []

    def select_action(self, question, history):
        return "direct_answer"

    def retrieve(self, query, loan_type=None):
        return "", []

    def extract_emi_params(self, question, history):
        return None

    def generate(self, question, context, history=None):
        self.contexts.append(context)
        return f"answer to: {question}"

    def generate_stream(self, question, context, history=None):
        self.contexts.append(context)
        yield "Hello "
        yield "world"

    def extract_income_document(self, data, file_format):
        return {
            "document_type": "salary_slip", "employer": "Acme Pvt Ltd", "period": "October 2026",
            "gross_monthly_income": 90000, "net_monthly_income": 72000, "existing_emis_monthly": 12000,
            "key_facts": "- Basic 45,000\n- HRA 18,000\n- Car loan EMI deduction 12,000", "confidence": "high",
        }


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        fake = FakeBedrock()
        main.app.state.bedrock = fake
        main.app.state.agent = build_loan_agent(fake)
        main.app.state.store = MemoryChatStore()
        c.fake = fake
        yield c


def test_chat_returns_message_id_and_time(client):
    r = client.post("/api/v1/chat", json={"message": "hello", "session_id": "c1", "title": "T"}).json()
    assert r["answer"] == "answer to: hello"
    assert r["message_id"].endswith("-1") and r["created_at"] > 0
    msgs = client.get("/api/v1/chats/c1").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert all(m["createdAt"] == r["created_at"] for m in msgs)


def test_feedback_roundtrip(client):
    r = client.post("/api/v1/chat", json={"message": "hi", "session_id": "c2"}).json()
    url = f"/api/v1/chats/c2/messages/{r['message_id']}/feedback"
    assert client.put(url, json={"rating": "down"}).status_code == 200
    assert client.get("/api/v1/chats/c2").json()["messages"][1]["feedback"] == "down"
    assert client.put(url, json={"rating": None}).status_code == 200
    assert "feedback" not in client.get("/api/v1/chats/c2").json()["messages"][1]
    assert client.put("/api/v1/chats/c2/messages/0000000000000-9/feedback", json={"rating": "up"}).status_code == 404
    assert client.put(url, json={"rating": "meh"}).status_code == 422


def test_stream_events_and_saved_answer(client):
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "stream please", "session_id": "c3"}) as r:
        assert r.status_code == 200
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert [e["type"] for e in events] == ["meta", "delta", "delta", "done"]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Hello world"
    assert events[-1]["message_id"]
    assert client.get("/api/v1/chats/c3").json()["messages"][1]["text"] == "Hello world"


def test_stream_human_review_is_one_piece(client):
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "My CIBIL score is 600", "session_id": "c4"}) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[0]["requires_human_review"] is True
    assert "loan officer" in events[1]["text"]


def _upload(client, chat_id="d1"):
    body = {"session_id": chat_id, "filename": "slip.pdf", "content_type": "application/pdf",
            "data_base64": base64.b64encode(b"%PDF-1.4 fake").decode()}
    return client.post("/api/v1/documents", json=body)


def test_document_upload_summary_and_follow_up(client):
    r = _upload(client).json()
    assert r["document"]["net_monthly_income"] == 72000
    assert r["document"]["eligible_income"] == 84000  # payroll EMI added back, not counted twice
    summary = r["messages"][1]["text"]
    assert "Salary slip analysed" in summary and "₹72,000" in summary and "₹84,000" in summary and "14%" in summary
    # Follow-up eligibility question uses the uploaded income (debt-to-income check).
    client.post("/api/v1/chat", json={"message": "Can I get a 40 lakh home loan for 20 years?", "session_id": "d1"})
    ctx = client.fake.contexts[-1]
    assert "₹84,000" in ctx and "Requested ₹40,00,000" in ctx
    # Question about the document itself.
    client.post("/api/v1/chat", json={"message": "What deductions are on my salary slip?", "session_id": "d1"})
    assert "Car loan EMI deduction" in client.fake.contexts[-1]


def test_document_validation(client):
    body = {"session_id": "d2", "filename": "x.exe", "content_type": "application/x-msdownload", "data_base64": "AAAA"}
    assert client.post("/api/v1/documents", json=body).status_code == 422
    body.update(content_type="application/pdf", data_base64="not base64!!")
    assert client.post("/api/v1/documents", json=body).status_code == 400


def test_delete_chat_removes_documents(client):
    _upload(client, "d3")
    client.delete("/api/v1/chats/d3")
    assert main.app.state.store.list_documents("local-user", "d3") == []


def test_current_user_reads_verified_token(monkeypatch):
    from fastapi import HTTPException, Request
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "fn")
    claims = base64.urlsafe_b64encode(json.dumps({"sub": "user-123"}).encode()).decode().rstrip("=")
    req = Request({"type": "http", "headers": [(b"authorization", f"h.{claims}.s".encode())]})
    assert main.current_user(req) == "user-123"
    with pytest.raises(HTTPException):
        main.current_user(Request({"type": "http", "headers": []}))
