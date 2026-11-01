"""Tests for edit/regenerate, follow-up suggestions, the big-file pipeline (S3 + SQS + DLQ) and source preview."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from src.app import document_jobs, main
from src.app.agent import build_loan_agent
from src.app.services.chat_store import MemoryChatStore
from tests.test_api import FakeBedrock


class FeatureBedrock(FakeBedrock):
    def __init__(self, fail_times=0):
        super().__init__()
        self.fail_times = fail_times

    def suggest_followups(self, question, answer, tool=None):
        return ["What is the interest rate?", "How long does approval take?"]

    def extract_income_document(self, data, file_format, text=None):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("Bedrock throttled")
        return {"document_type": "pan_card", "key_facts": "- PAN card", "confidence": "high"}


class FakeS3:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.deleted = []

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://s3.example/{Params['Key']}?sig=1"

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
        data, ctype = self.objects[(Bucket, Key)]
        return {"Body": io.BytesIO(data), "ContentType": ctype}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


class FakeSQS:
    def __init__(self):
        self.sent = []

    def send_message(self, QueueUrl, MessageBody):
        self.sent.append((QueueUrl, json.loads(MessageBody)))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main.settings, "uploads_bucket", "uploads-bkt")
    monkeypatch.setattr(main.settings, "document_queue_url", "https://sqs/queue")
    monkeypatch.setattr(main.settings, "docs_bucket", "docs-bkt")
    with TestClient(main.app) as c:
        fake = FeatureBedrock()
        main.app.state.bedrock = fake
        main.app.state.agent = build_loan_agent(fake)
        main.app.state.store = MemoryChatStore()
        main.app.state.s3 = FakeS3({("docs-bkt", "policies/home.pdf"): (b"%PDF-1.4 policy", "application/pdf")})
        main.app.state.sqs = FakeSQS()
        c.fake = fake
        yield c


def test_chat_returns_suggestions_and_user_message_id(client):
    r = client.post("/api/v1/chat", json={"message": "hello", "session_id": "s1", "suggest": True}).json()
    assert r["suggestions"] == ["What is the interest rate?", "How long does approval take?"]
    assert r["user_message_id"].endswith("-0")


def test_stream_sends_suggestions_after_done(client):
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "hi", "session_id": "s2", "suggest": True}) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert [e["type"] for e in events][-2:] == ["done", "suggestions"]
    assert events[-2]["user_message_id"] and len(events[-1]["items"]) == 2


def test_edit_replaces_question_and_later_answers(client):
    first = client.post("/api/v1/chat", json={"message": "one", "session_id": "e1"}).json()
    client.post("/api/v1/chat", json={"message": "two", "session_id": "e1"})
    # Edit the first question: everything from it onwards is replaced.
    client.post("/api/v1/chat", json={"message": "one edited", "session_id": "e1", "replace_from": first["user_message_id"]})
    msgs = client.get("/api/v1/chats/e1").json()["messages"]
    assert [m["text"] for m in msgs] == ["one edited", "answer to: one edited"]


def test_large_upload_queue_flow(client):
    up = client.post("/api/v1/uploads", json={"session_id": "u1", "filename": "../PAN card.pdf",
                                              "content_type": "application/pdf", "size": 8_000_000}).json()
    assert "/PAN_card.pdf" in up["upload_url"] and ".." not in up["upload_url"]
    docs = client.get("/api/v1/chats/u1/documents").json()
    assert docs[0]["status"] == "uploading"

    queued = client.post(f"/api/v1/chats/u1/documents/{up['doc_id']}/process").json()
    assert queued["status"] == "queued" and queued["message"]["text"] == "📎 PAN card.pdf"
    queue_url, job = main.app.state.sqs.sent[-1]
    assert queue_url == "https://sqs/queue" and job["doc_id"] == up["doc_id"]
    # Processing twice is refused.
    assert client.post(f"/api/v1/chats/u1/documents/{up['doc_id']}/process").status_code == 404


def _queued_job(client, chat="w1"):
    up = client.post("/api/v1/uploads", json={"session_id": chat, "filename": "pan.png",
                                              "content_type": "image/png", "size": 5_000_000}).json()
    client.post(f"/api/v1/chats/{chat}/documents/{up['doc_id']}/process")
    job = main.app.state.sqs.sent[-1][1]
    main.app.state.s3.objects[(job["bucket"], job["key"])] = (b"\x89PNG small", "image/png")
    return job


def test_worker_success_posts_summary_and_deletes_file(client):
    job = _queued_job(client)
    store, s3 = main.app.state.store, main.app.state.s3
    assert document_jobs.run_job(store, client.fake, s3, job, 1, 3) == "done"
    assert client.get("/api/v1/chats/w1/documents").json()[0]["status"] == "done"
    assert "PAN card received" in client.get("/api/v1/chats/w1").json()["messages"][-1]["text"]
    assert s3.deleted == [job["key"]]


def test_worker_transient_error_retries_then_fails(client):
    job = _queued_job(client)
    client.fake.fail_times = 5
    store, s3 = main.app.state.store, main.app.state.s3
    with pytest.raises(RuntimeError):
        document_jobs.run_job(store, client.fake, s3, job, 1, 3)
    assert client.get("/api/v1/chats/w1/documents").json()[0]["status"] == "retrying"
    with pytest.raises(RuntimeError):
        document_jobs.run_job(store, client.fake, s3, job, 3, 3)   # last attempt -> SQS moves it to the DLQ
    assert client.get("/api/v1/chats/w1/documents").json()[0]["status"] == "failed"
    # A failed big upload can be queued again.
    assert client.post(f"/api/v1/chats/w1/documents/{job['doc_id']}/retry").json()["status"] == "queued"


def test_worker_permanent_error_fails_at_once(client):
    job = _queued_job(client)
    main.app.state.s3.objects[(job["bucket"], job["key"])] = (b"x" * 4_000_000, "image/png")   # image too big
    outcome = document_jobs.run_job(main.app.state.store, client.fake, main.app.state.s3, job, 1, 3)
    assert outcome == "failed_permanent"
    doc = client.get("/api/v1/chats/w1/documents").json()[0]
    assert doc["status"] == "failed" and "3.7 MB" in doc["error"]


def test_source_file_only_from_docs_bucket(client):
    r = client.get("/api/v1/sources/file", params={"uri": "s3://docs-bkt/policies/home.pdf"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    for uri in ["s3://other-bkt/policies/home.pdf", "s3://docs-bkt/../secret.pdf", "s3://docs-bkt/missing.pdf", "http://x"]:
        assert client.get("/api/v1/sources/file", params={"uri": uri}).status_code == 404


def test_big_blank_pdf_is_permanent_error():
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(40):
        writer.add_blank_page(width=600, height=800)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue() + b"\n%" + b"0" * 4_500_000   # padded past the inline limit
    with pytest.raises(document_jobs.PermanentDocumentError, match="no readable text"):
        document_jobs.analyse_document(FeatureBedrock(), data, "application/pdf")


def test_placeholder_employer_and_period_are_dropped():
    from src.app.documents import clean_extraction, summary_markdown

    doc = clean_extraction({"document_type": "other", "employer": "N/A", "period": "n/a"}, "x.pdf")
    assert doc["employer"] == "" and doc["period"] == ""
    assert "N/A" not in summary_markdown(doc)


def test_explicit_tenure_in_question_beats_llm_value():
    class WrongTenure(FeatureBedrock):
        def select_action(self, question, history):
            return "calculate_emi"

        def extract_emi_params(self, question, history):
            return {"principal_inr": 3000000, "annual_rate_percent": 9, "tenure_months": 15}

    fake = WrongTenure()
    result = build_loan_agent(fake).invoke({"question": "Calculate EMI for 3000000 at 9% for 15 years", "history": []})
    assert "15 years" in result["answer"] and "1 years 3 months" not in result["answer"]
