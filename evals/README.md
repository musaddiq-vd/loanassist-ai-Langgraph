# 🧪 Evaluation & observability

This folder answers one question on every change: **did the AI get better or worse?**

```
evals/
├── build_dataset.py   # the 50 golden cases (edit here, then re-run it)
├── dataset.jsonl      # generated golden set
├── run_evals.py       # runs the cases through the real agent + Bedrock, scores, gates
├── baseline.json      # which cases passed last time we accepted the results
├── ci-role.yaml       # one-time IAM role so GitHub Actions can run evals (OIDC, no keys)
└── results/
    ├── latest.md      # human-readable report of the last run
    └── history.csv    # one line per run: pass rate, groundedness, latency → trend over time
```

## Where does what happen?

| Question | Where | Why there |
|---|---|---|
| **"Did my change break or improve answers?"** | **`run_evals.py`**, on your machine or in **GitHub Actions** | It is the *gate*: deterministic checks + a regression comparison against `baseline.json`, and a non-zero exit code that can block a merge. It works without any third-party account. |
| **"Where did the time go in this request?"** (router → tool → KB → Nova, per step) | **LangSmith traces** | LangSmith shows every request as a tree of spans with timings, inputs and outputs — for real production traffic and for eval runs. |
| **"How do two experiments compare, case by case?"** | **LangSmith experiments** (`--langsmith`) | The same 50 cases are uploaded as a LangSmith dataset; each run becomes an experiment you can diff side by side in the UI. |

In short: **evaluate and gate locally/CI, observe and compare in LangSmith.** LangSmith is optional — everything works without it.

## What each case checks

Every case in `dataset.jsonl` is a question (optionally with chat history or an uploaded salary slip) plus checks:

| Check | Example |
|---|---|
| `expected_tool` – the route the agent took | EMI questions must use `calculate_emi` |
| `must_include` / `must_include_any` | `43,391` for 50 lakh · 8.5% · 20 yrs |
| `must_not_include` | the system prompt must never leak |
| `citations` | `[n]` must exist and point to a real source; no stray `[n]` without sources |
| `sources_include` | a home-loan question must cite `home-loan.md` |
| `review` | CIBIL < 650 must set the human-review flag |
| `critical` | 11 must-never-break cases (EMI maths, CIBIL review, citations, safety) |

Then an **LLM judge** (Nova Lite) scores each answer 1–5 for **grounded** (is every fact backed by the context?) and **helpful**. The judge is reported, not a gate, because LLM grades are noisier than the checks.

The run **fails** if the pass rate drops below 85 %, any critical case fails, or a case that passed in `baseline.json` now fails.

## Run it

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python evals/run_evals.py                        # all 50, ~2–3 min, ≈ $0.05 of Bedrock
python evals/run_evals.py --ids emi-01,pol-01    # a few cases
python evals/run_evals.py --no-judge             # skip the LLM judge
python evals/run_evals.py --update-baseline      # accept today's results as the new baseline
```

It reads the Knowledge Base and Guardrail ids from the deployed stack (`loanassist-ai`), so no setup beyond AWS credentials. Change the questions in `build_dataset.py`, then `python evals/build_dataset.py`.

## Every change, automatically (GitHub Actions)

`.github/workflows/ci.yml` runs on every push to `main` and every pull request:

1. **Unit + API tests** — always, no AWS needed.
2. **Golden-set evals** — after you give GitHub a way into AWS, once:

```powershell
aws cloudformation deploy --template-file evals/ci-role.yaml --stack-name loanassist-ci-evals-role `
  --capabilities CAPABILITY_NAMED_IAM --parameter-overrides GitHubRepo=musaddiq-vd/loanassist-ai-Langgraph
aws cloudformation describe-stacks --stack-name loanassist-ci-evals-role --query "Stacks[0].Outputs[0].OutputValue" --output text
```

Put that role ARN in **GitHub → Settings → Secrets and variables → Actions → Variables** as `AWS_EVAL_ROLE_ARN`. The role uses OIDC (no AWS keys stored in GitHub) and can only read the stack and call Bedrock. The report is added to each run's summary page and uploaded as an artifact.

## LangSmith (traces + experiments)

1. Create a free account at <https://smith.langchain.com> and an API key (Settings → API Keys).
2. **App traces on AWS** — store the key in SSM; the Lambda picks it up on its next cold start (or after `.\deploy.ps1`):

   ```powershell
   aws ssm put-parameter --name /loanassist/langsmith-api-key --type SecureString --value "<your key>"
   ```

   Every chat request then appears in project **`loanassist-ai`** as a trace: `chat_stream` → LangGraph nodes (`decide_action`, `retrieve_loan_documents`, …) → `kb_retrieve`, `nova_router`, `nova_answer_stream`, each with its duration. Uploaded files are never sent — only their format and size.
3. **Local / CI** — set `LANGSMITH_API_KEY` in `.env` (or the `LANGSMITH_API_KEY` GitHub secret). Eval runs are traced into project `loanassist-ai-evals`, and `--langsmith` runs them as a LangSmith **experiment** on the dataset `loanassist-golden-set`.

To turn tracing off again: `aws ssm delete-parameter --name /loanassist/langsmith-api-key`.

> **Privacy:** traces contain the user's questions and the answers (including figures read from salary slips). Use LangSmith only if that is acceptable for your data, or keep it for evals and testing.
