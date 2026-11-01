"""Optional LangSmith tracing.

Turned on only when an API key is available, either
  - LANGSMITH_API_KEY in the environment / .env (local), or
  - a SecureString in SSM Parameter Store named by LANGSMITH_API_KEY_PARAM (on AWS).
Without a key everything runs exactly the same, with no tracing and no extra calls.

When on, each chat request becomes one trace: the LangGraph nodes (router, tool,
answer) and every Bedrock call inside them, each with its own timing.

The request span is a RunTree passed around explicitly (not a context manager),
because a streamed response is produced across several threads.
"""

import contextlib
import logging
import os
import threading
from typing import Any

import boto3

logger = logging.getLogger(__name__)
_enabled = False


def setup_tracing(api_key: str = "", api_key_param: str = "", project: str = "loanassist-ai", region: str | None = None) -> bool:
    global _enabled
    key = api_key or os.environ.get("LANGSMITH_API_KEY", "")
    if not key and api_key_param:
        try:
            key = boto3.client("ssm", region_name=region).get_parameter(Name=api_key_param, WithDecryption=True)["Parameter"]["Value"]
        except Exception as exc:  # parameter not created -> tracing stays off
            logger.info("LangSmith tracing off (%s)", type(exc).__name__)
    if key:
        os.environ["LANGSMITH_API_KEY"] = key
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", project)
        _enabled = True
        logger.info("LangSmith tracing on (project %s)", os.environ["LANGSMITH_PROJECT"])
    return _enabled


def tracing_enabled() -> bool:
    return _enabled


def start_trace(name: str, inputs: dict[str, Any], metadata: dict[str, Any]) -> Any:
    """Open the parent span for one API request. Returns None when tracing is off."""
    if not _enabled:
        return None
    try:
        from langsmith.run_trees import RunTree

        run = RunTree(name=name, run_type="chain", inputs=inputs, extra={"metadata": metadata}, tags=["loanassist"])
        run.post()
        return run
    except Exception:
        logger.debug("Could not start LangSmith trace", exc_info=True)
        return None


def under(run: Any):
    """Context manager: spans created inside (LangGraph nodes, Bedrock calls) nest under `run`."""
    if run is None:
        return contextlib.nullcontext()
    from langsmith import tracing_context

    return tracing_context(parent=run)


def end_trace(run: Any, outputs: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None, error: str | None = None) -> None:
    if run is None:
        return
    try:
        if metadata:
            run.add_metadata(metadata)
        run.end(outputs=outputs or {}, error=error)
        run.patch()
    except Exception:
        logger.debug("Could not end LangSmith trace", exc_info=True)


def _flush() -> None:
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception:
        pass
    try:
        from langsmith.run_trees import get_cached_client

        get_cached_client().flush()
    except Exception:
        logger.debug("LangSmith flush failed", exc_info=True)


def flush_traces(timeout: float = 2.0) -> None:
    """Send buffered spans now (Lambda may freeze the process right after the response),
    but never wait more than `timeout` seconds, even if LangSmith is slow or down.
    Anything not sent yet is sent on the next request."""
    if not _enabled:
        return
    worker = threading.Thread(target=_flush, daemon=True)
    worker.start()
    worker.join(timeout)
