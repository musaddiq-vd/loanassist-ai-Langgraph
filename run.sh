#!/bin/sh
# Lambda entry point: the Lambda Web Adapter (layer) starts this script and
# forwards API Gateway requests to uvicorn on port 8000.
export PYTHONPATH="/var/task:${PYTHONPATH}"
exec python -m uvicorn src.app.main:app --host 0.0.0.0 --port 8000 --no-access-log
