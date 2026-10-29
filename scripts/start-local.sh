#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Default: CPU-only web/API workbench; model inference is performed by cloud providers.
# No models are downloaded. Local inference is an explicit optional mode.
local_models=false
case "${1:-}" in
  "") ;;
  --with-local-models) local_models=true ;;
  *) echo "Usage: $0 [--with-local-models]" >&2; exit 2 ;;
esac
ollama_pid=""
cleanup() {
  if [ -n "$ollama_pid" ]; then kill "$ollama_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
if [ "$local_models" = true ]; then
  if [ ! -x data/ollama/ollama ]; then
    echo "Bundled Ollama not found. Configure your own OLLAMA_BASE_URL, or use cloud inference." >&2
    exit 1
  fi
  export OLLAMA_HOST=127.0.0.1:11435
  export OLLAMA_MODELS="$PWD/data/ollama/models"
  export OLLAMA_NO_CLOUD=1
  export OLLAMA_NUM_PARALLEL=1
  export OLLAMA_MAX_LOADED_MODELS=1
  export OLLAMA_KEEP_ALIVE=2m
  export OLLAMA_BASE_URL=http://127.0.0.1:11435
  data/ollama/ollama serve > data/ollama/server.log 2>&1 &
  ollama_pid=$!
fi
uv sync --locked
uv run uvicorn photo_separator.app:app --host 127.0.0.1 --port 8765 --no-access-log
