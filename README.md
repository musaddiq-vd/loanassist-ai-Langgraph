# LoanAssist AI

Backend for a loan-support RAG assistant. The chat frontend is intentionally deferred.

## Request flow

`CloudFront → S3 frontend → API Gateway → Lambda/FastAPI → LangGraph → Bedrock Knowledge Base (OpenSearch) → Bedrock LLM → response`

## Local setup

1. Copy `.env.example` to `.env` and set `BEDROCK_KNOWLEDGE_BASE_ID` and (optionally) `BEDROCK_GUARDRAIL_ID`.
2. Configure AWS credentials with permission to call Bedrock Runtime and Bedrock Agent Runtime.
3. Install and run:

```bash
pip install -r requirements.txt
uvicorn src.app.main:app --reload
```

Send `POST /api/v1/chat` with `{ "message": "What documents are required?" }`.

## Local frontend

In a second terminal, serve the `frontend` folder (do not open `index.html` directly, because browsers can block API calls from a file origin):

```bash
cd frontend
python -m http.server 5500
```

Open `http://127.0.0.1:5500`. For production, change `frontend/js/config.js` to the API Gateway URL and set `ALLOWED_ORIGINS` to the CloudFront domain.

## Agent flow

The LangGraph agent uses an LLM router to select one small tool: document retrieval, EMI calculation, preliminary eligibility, customer lookup placeholder, or direct answer. A CIBIL score below 650 routes to human review; it never produces an automated approve/reject decision. Chat history is held in memory for local development only.

## AWS deployment notes

- Lambda handler: `src.lambda_handler.handler`; package dependencies in a Lambda layer/container image.
- Deploy the included backend stack with `sam build && sam deploy --guided`. Replace its temporary wildcard Bedrock IAM resources with your exact model, Knowledge Base, and Guardrail ARNs before production.
- API Gateway: proxy `POST /api/v1/chat` and `GET /health` to Lambda; set the API URL in the future frontend.
- S3 + CloudFront: host the static frontend in private S3; use Origin Access Control and allow only CloudFront.
- IAM: least-privilege `bedrock:Converse` for the chosen model and `bedrock:Retrieve` for the Knowledge Base.
- CloudWatch: Lambda logs are structured by the configured log level; create alarms for Lambda errors, throttles, and API Gateway 5XX.
- CORS: set `ALLOWED_ORIGINS` to your CloudFront domain before production.
