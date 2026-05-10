#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
LOG_LEVEL="${TV_LOG_LEVEL:-INFO}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -x "${ROOT_DIR}/venv/bin/python" ]; then
  echo "Brak venv w ${ROOT_DIR}/venv."
  echo "Zrób: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 2
fi

existing_pids="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)"
if [ -n "${existing_pids}" ]; then
  echo "Port ${PORT} zajęty przez PID(y): ${existing_pids} — kończę je przed startem…"
  echo "${existing_pids}" | xargs -r kill -TERM 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.3
    still="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)"
    [ -z "${still}" ] && break
  done
  still="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "${still}" ]; then
    echo "Wymuszam SIGKILL na PID(y): ${still}"
    echo "${still}" | xargs -r kill -KILL 2>/dev/null || true
    sleep 0.5
  fi
fi

echo "Startuję FastAPI (uvicorn) na http://${HOST}:${PORT}"
echo "Log level: ${LOG_LEVEL}"

cd "${ROOT_DIR}"
TV_LOG_LEVEL="${LOG_LEVEL}" exec "${ROOT_DIR}/venv/bin/uvicorn" app:app --host "${HOST}" --port "${PORT}"

