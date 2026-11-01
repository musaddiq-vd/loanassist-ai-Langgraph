"""LoanAssist evaluation runner.

Runs the 50-case golden set (evals/dataset.jsonl) through the real agent and Bedrock,
scores every answer, compares with the saved baseline and exits non-zero on a regression.

    python evals/run_evals.py                  # full run, local report
    python evals/run_evals.py --ids emi-01,pol-01
    python evals/run_evals.py --update-baseline # accept the current results as the new baseline
    python evals/run_evals.py --langsmith       # same run as a LangSmith experiment (needs LANGSMITH_API_KEY)

Two kinds of scoring per case:
  1. Deterministic checks (gate): route, required numbers/words, forbidden text,
     citation validity, human-review flag, expected source files.
  2. LLM judge (Nova Lite, reported): is the answer grounded in the retrieved
     context / tool output, and does it answer the question? Scores 1-5.

The run fails (exit 1) when the pass rate drops below --min-pass, a critical case
fails, or a case that passed in the baseline now fails.
"""

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import boto3  # noqa: E402

from src.app.agent import build_loan_agent  # noqa: E402
from src.app.config import Settings  # noqa: E402
from src.app.observability import flush_traces, setup_tracing  # noqa: E402
from src.app.services.bedrock import BedrockService  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CITE_RE = re.compile(r"\[(\d{1,2})(?:\s*,\s*\d{1,2})*\]")


# ------------------------------------------------------------------ setup
def stack_settings(stack: str, region: str) -> Settings:
    """Use the deployed stack's Knowledge Base and Guardrail unless .env/env already set them."""
    base = Settings()
    kb, guard_id, guard_ver = base.bedrock_knowledge_base_id, base.bedrock_guardrail_id, base.bedrock_guardrail_version
    if not kb:
        outputs = boto3.client("cloudformation", region_name=region).describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
        kb = next(o["OutputValue"] for o in outputs if o["OutputKey"] == "KnowledgeBaseId")
    if not guard_id:
        bedrock = boto3.client("bedrock", region_name=region)
        match = [g for g in bedrock.list_guardrails()["guardrails"] if g["name"] == f"{stack}-guardrail"]
        if match:
            guard_id = match[0]["id"]
            versions = [g["version"] for g in bedrock.list_guardrails(guardrailIdentifier=guard_id)["guardrails"] if g["version"] != "DRAFT"]
            guard_ver = max(versions, key=int) if versions else "DRAFT"
    return Settings(aws_region=region, bedrock_knowledge_base_id=kb, bedrock_guardrail_id=guard_id or "", bedrock_guardrail_version=guard_ver)


# ------------------------------------------------------------------ checks
def check(case: dict[str, Any], out: dict[str, Any]) -> list[str]:
    """Deterministic checks. Returns a list of failure reasons (empty = pass)."""
    fails = []
    answer = out["answer"]
    low = answer.lower()
    if case.get("expected_tool") and out["tool_used"] != case["expected_tool"]:
        fails.append(f"route: expected {case['expected_tool']}, got {out['tool_used']}")
    for text in case.get("must_include", []):
        if text.lower() not in low:
            fails.append(f"missing: {text!r}")
    if case.get("must_include_any") and not any(t.lower() in low for t in case["must_include_any"]):
        fails.append(f"missing any of: {case['must_include_any']}")
    for text in case.get("must_not_include", []):
        if text.lower() in low:
            fails.append(f"forbidden: {text!r}")
    if "review" in case and bool(out["requires_human_review"]) != case["review"]:
        fails.append(f"review flag: expected {case['review']}")

    n_sources = len(out["sources"])
    cited = [int(n) for m in CITE_RE.finditer(answer) for n in re.findall(r"\d+", m.group(0))]
    invalid = [n for n in cited if n < 1 or n > n_sources]
    mode = case.get("citations")
    if mode == "required" and (not n_sources or not cited):
        fails.append(f"citations: expected [n] citations (sources={n_sources}, cited={cited})")
    if mode == "required_if_sources" and n_sources and not cited:
        fails.append("citations: sources returned but no [n] in the answer")
    if mode == "none" and n_sources:
        fails.append(f"citations: expected no sources, got {n_sources}")
    if invalid:
        fails.append(f"citations: invalid numbers {invalid} (only {n_sources} sources)")
    files = {s.uri.rsplit('/', 1)[-1] for s in out["sources"] if s.uri}
    if case.get("pages") and any(not s.pages for s in out["sources"]):
        fails.append("pages: a source has no page numbers")
    for name in case.get("sources_include", []):
        if name not in files:
            fails.append(f"source missing: {name} (got {sorted(files)})")
    return fails


JUDGE_TOOL = {"toolSpec": {"name": "grade", "description": "Grade the assistant answer.", "inputSchema": {"json": {
    "type": "object",
    "properties": {
        "grounded": {"type": "integer", "minimum": 1, "maximum": 5, "description": "5 = every fact and number is supported by the context; 1 = invents facts."},
        "helpful": {"type": "integer", "minimum": 1, "maximum": 5, "description": "5 = directly and correctly answers the question."},
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["grounded", "helpful", "reason"],
}}}}


def judge(bedrock: BedrockService, question: str, context: str, answer: str) -> dict[str, Any]:
    prompt = (
        "You grade a loan assistant. Judge ONLY against the context it was given (policy passages and tool results).\n"
        "Refusing an out-of-scope or unsafe request is helpful. Asking for missing details is grounded.\n\n"
        f"Question:\n{question}\n\nContext given to the assistant:\n{context or '(none)'}\n\nAssistant answer:\n{answer}"
    )
    try:
        response = bedrock.runtime.converse(
            modelId=bedrock.settings.bedrock_model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig={"tools": [JUDGE_TOOL], "toolChoice": {"tool": {"name": "grade"}}},
            inferenceConfig={"maxTokens": 200, "temperature": 0},
        )
        for block in response["output"]["message"]["content"]:
            if "toolUse" in block:
                return block["toolUse"]["input"]
    except Exception as exc:
        return {"grounded": None, "helpful": None, "reason": f"judge error: {type(exc).__name__}"}
    return {"grounded": None, "helpful": None, "reason": "judge gave no grade"}


# ------------------------------------------------------------------ run one case
def run_case(agent, bedrock: BedrockService, case: dict[str, Any], use_judge: bool) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = agent.invoke({"question": case["question"], "history": case.get("history", []), "documents": case.get("documents", [])})
        error = None
    except Exception as exc:
        result, error = {}, f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - started
    out = {
        "answer": result.get("answer", ""),
        "tool_used": result.get("tool_used"),
        "sources": result.get("sources", []),
        "requires_human_review": result.get("requires_human_review", False),
    }
    failures = [f"error: {error}"] if error else check(case, out)
    grade = judge(bedrock, case["question"], result.get("context", ""), out["answer"]) if use_judge and not error else {}
    return {
        "id": case["id"], "category": case["category"], "critical": bool(case.get("critical")),
        "question": case["question"], "answer": out["answer"], "tool_used": out["tool_used"],
        "sources": [s.uri.rsplit("/", 1)[-1] + (f" p.{','.join(map(str, s.pages))}" if s.pages else "") for s in out["sources"] if s.uri],
        "requires_human_review": out["requires_human_review"],
        "passed": not failures, "failures": failures, "latency_s": round(latency, 2),
        "grounded": grade.get("grounded"), "helpful": grade.get("helpful"), "judge_reason": grade.get("reason"),
    }


# ------------------------------------------------------------------ reporting
def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(values):
        values = [v for v in values if isinstance(v, (int, float))]
        return round(statistics.mean(values), 2) if values else None
    latencies = sorted(r["latency_s"] for r in rows)
    by_cat = {}
    for r in rows:
        c = by_cat.setdefault(r["category"], {"total": 0, "passed": 0})
        c["total"] += 1
        c["passed"] += r["passed"]
    return {
        "cases": len(rows), "passed": sum(r["passed"] for r in rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 3) if rows else 0,
        "grounded_avg": mean(r["grounded"] for r in rows), "helpful_avg": mean(r["helpful"] for r in rows),
        "latency_p50_s": latencies[len(latencies) // 2] if latencies else None,
        "latency_p95_s": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None,
        "by_category": by_cat,
    }


def write_reports(rows: list[dict[str, Any]], summary: dict[str, Any], regressions: list[str], meta: dict[str, Any]) -> Path:
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps({"meta": meta, "summary": summary, "regressions": regressions, "cases": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        f"# LoanAssist eval — {meta['timestamp']} (commit {meta['commit']})", "",
        f"**{summary['passed']}/{summary['cases']} passed ({summary['pass_rate']:.0%})** · grounded {summary['grounded_avg']}/5 · "
        f"helpful {summary['helpful_avg']}/5 · latency p50 {summary['latency_p50_s']}s, p95 {summary['latency_p95_s']}s", "",
        "| Category | Passed |", "|---|---:|",
        *[f"| {k} | {v['passed']}/{v['total']} |" for k, v in sorted(summary["by_category"].items())], "",
    ]
    if regressions:
        lines += ["## ❌ Regressions (passed in baseline, failing now)", "", *[f"- `{r}`" for r in regressions], ""]
    failed = [r for r in rows if not r["passed"]]
    if failed:
        lines += ["## Failing cases", "", "| Case | Question | Why |", "|---|---|---|"]
        lines += [f"| `{r['id']}`{' ⚠️' if r['critical'] else ''} | {r['question'][:60]} | {'; '.join(r['failures'])[:160]} |" for r in failed]
        lines.append("")
    low = [r for r in rows if isinstance(r["grounded"], int) and r["grounded"] <= 2]
    if low:
        lines += ["## Low groundedness (judge ≤ 2/5)", "", *[f"- `{r['id']}`: {r['judge_reason']}" for r in low], ""]
    report = RESULTS / "latest.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    history = RESULTS / "history.csv"
    new = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "commit", "cases", "passed", "pass_rate", "grounded_avg", "helpful_avg", "latency_p50_s", "latency_p95_s"])
        w.writerow([meta["timestamp"], meta["commit"], summary["cases"], summary["passed"], summary["pass_rate"],
                    summary["grounded_avg"], summary["helpful_avg"], summary["latency_p50_s"], summary["latency_p95_s"]])
    return report


# ------------------------------------------------------------------ LangSmith experiment
def run_in_langsmith(agent, bedrock: BedrockService, cases: list[dict[str, Any]], use_judge: bool, prefix: str) -> list[dict[str, Any]]:
    """Run the same cases through langsmith.evaluate so every case shows up as a traced
    experiment row (inputs, answer, scores, per-step timings) in the LangSmith UI."""
    from langsmith import Client, evaluate

    client = Client()
    name = "loanassist-golden-set"
    if not client.has_dataset(dataset_name=name):
        ds = client.create_dataset(name, description="LoanAssist 50-case golden set (evals/dataset.jsonl)")
        client.create_examples(dataset_id=ds.id, examples=[{"inputs": {"case": c}, "outputs": {"id": c["id"]}} for c in cases])
    rows: list[dict[str, Any]] = []
    wanted = {c["id"] for c in cases}

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        row = run_case(agent, bedrock, inputs["case"], use_judge)
        rows.append(row)
        return row

    def checks(run, example) -> dict[str, Any]:
        o = run.outputs or {}
        return {"key": "checks_pass", "score": int(bool(o.get("passed"))), "comment": "; ".join(o.get("failures", []))}

    def grounded(run, example) -> dict[str, Any]:
        g = (run.outputs or {}).get("grounded")
        return {"key": "grounded", "score": (g / 5) if isinstance(g, int) else None}

    data = [e for e in client.list_examples(dataset_name=name) if e.inputs.get("case", {}).get("id") in wanted]
    evaluate(target, data=data, evaluators=[checks, grounded], experiment_prefix=prefix, max_concurrency=4)
    return rows


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(HERE / "dataset.jsonl"))
    ap.add_argument("--ids", help="comma-separated case ids to run")
    ap.add_argument("--stack", default=os.environ.get("STACK_NAME", "loanassist-ai"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--min-pass", type=float, default=0.85, help="minimum pass rate (0-1)")
    ap.add_argument("--no-judge", action="store_true", help="skip the LLM judge (faster, cheaper)")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--baseline", default=str(HERE / "baseline.json"))
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--langsmith", action="store_true", help="run as a LangSmith experiment (needs LANGSMITH_API_KEY)")
    args = ap.parse_args()

    cases = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    settings = stack_settings(args.stack, args.region)
    tracing = setup_tracing(settings.langsmith_api_key, "", f"{args.stack}-evals", args.region)
    bedrock = BedrockService(settings)
    agent = build_loan_agent(bedrock)
    use_judge = not args.no_judge
    meta = {"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "commit": git_sha(),
            "model": settings.bedrock_model_id, "judge": use_judge, "langsmith": bool(args.langsmith and tracing)}
    print(f"Running {len(cases)} cases (judge={'on' if use_judge else 'off'}, langsmith={'on' if meta['langsmith'] else 'off'}) ...")

    if args.langsmith:
        if not tracing:
            print("LANGSMITH_API_KEY is not set: put it in .env or the environment.", file=sys.stderr)
            return 2
        rows = run_in_langsmith(agent, bedrock, cases, use_judge, f"loanassist-{meta['commit']}")
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            rows = list(pool.map(lambda c: run_case(agent, bedrock, c, use_judge), cases))
    flush_traces(timeout=10)
    rows.sort(key=lambda r: r["id"])

    summary = summarize(rows)
    baseline_path = Path(args.baseline)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
    regressions = [r["id"] for r in rows if baseline.get(r["id"]) is True and not r["passed"]]
    report = write_reports(rows, summary, regressions, meta)

    for r in rows:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  {mark}  {r['id']:<8} {r['latency_s']:>5.1f}s  grounded={r['grounded']}  {'; '.join(r['failures'])[:110]}")
    print(f"\n{summary['passed']}/{summary['cases']} passed ({summary['pass_rate']:.0%}), grounded avg {summary['grounded_avg']}, "
          f"p50 {summary['latency_p50_s']}s · report: {report.relative_to(ROOT)}")

    if args.update_baseline:
        baseline_path.write_text(json.dumps({r["id"]: r["passed"] for r in rows}, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline updated: {baseline_path.relative_to(ROOT)}")
        return 0

    critical_fail = [r["id"] for r in rows if r["critical"] and not r["passed"]]
    problems = []
    if summary["pass_rate"] < args.min_pass:
        problems.append(f"pass rate {summary['pass_rate']:.0%} < {args.min_pass:.0%}")
    if critical_fail:
        problems.append(f"critical cases failing: {critical_fail}")
    if regressions:
        problems.append(f"regressions vs baseline: {regressions}")
    if problems:
        print("EVAL GATE FAILED: " + " | ".join(problems), file=sys.stderr)
        return 1
    print("EVAL GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
