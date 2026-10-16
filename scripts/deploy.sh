#!/bin/bash
# Copy the code to the EC2 server and run the tests there.
# Uses the "overnight-bot" host from ~/.ssh/config (SSH tunneled through SSM).
# Never sends .env, logs, state or the trade journal: the server keeps its own.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${HOST:-overnight-bot}"

rsync -av --delete \
  --exclude '.venv' --exclude 'data' --exclude 'logs' --exclude 'state' \
  --exclude 'journal' --exclude '.git' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.DS_Store' --exclude '.env' \
  "$DIR/" "$HOST:~/overnight-bot/"

ssh "$HOST" 'cd ~/overnight-bot && .venv/bin/pip install -q -r requirements.txt && .venv/bin/pytest -q'
