#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

readonly ollama_image=ollama/ollama:0.40.16
readonly ollama_container=bahaybulsa-preview-ollama
readonly ollama_url=http://127.0.0.1:11434
readonly model="${BUDGETAI_MODEL:-qwen3.5:4b-q4_K_M}"
readonly preview_health=http://127.0.0.1:8080/api/health
readonly log_dir="${TMPDIR:-/tmp}/bahaybulsa-preview-${UID}"
umask 077
mkdir -p "$log_dir"
exec 9>"$log_dir/start.lock"
flock 9

wait_for_http() {
    local url=$1
    for _ in {1..30}; do
        if curl --fail --silent --max-time 2 "$url" >/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

if ! timeout 30s bash -c 'until docker info >/dev/null 2>&1; do sleep 1; done'; then
    echo "Docker did not start. Restart the Codespace and retry the preview." >&2
    exit 1
fi

if running=$(docker container inspect --format '{{.State.Running}}' "$ollama_container" 2>/dev/null); then
    if [[ "$running" != true ]]; then
        docker start "$ollama_container" >/dev/null
    fi
else
    docker run --detach --name "$ollama_container" \
        --publish 127.0.0.1:11434:11434 --volume bahaybulsa-preview-model:/root/.ollama \
        --env OLLAMA_HOST=0.0.0.0:11434 --env OLLAMA_NO_CLOUD=1 "$ollama_image" >/dev/null
fi

if ! wait_for_http "$ollama_url/api/version"; then
    docker logs --tail 30 "$ollama_container" >&2
    echo "Ollama did not become ready." >&2
    exit 1
fi

if ! docker exec --env "OLLAMA_HOST=$ollama_url" "$ollama_container" ollama show "$model" >/dev/null 2>&1; then
    echo "Downloading the BudgetAI model. This can take a few minutes on the first start."
    docker exec --env "OLLAMA_HOST=$ollama_url" "$ollama_container" ollama pull "$model"
fi

if ! curl --fail --silent --max-time 2 "$preview_health" >/dev/null; then
    PYTHONDONTWRITEBYTECODE=1 OLLAMA_HOST="$ollama_url" BUDGETAI_MODEL="$model" BUDGETAI_TIMEOUT_SECONDS=300 \
        nohup budgetai/.venv/bin/python -m uvicorn --app-dir budgetai browser_preview:app \
        --host 127.0.0.1 --port 8080 --no-access-log \
        9>&- </dev/null >"$log_dir/preview.log" 2>&1 &
    preview_pid=$!
    if ! wait_for_http "$preview_health"; then
        tail -n 30 "$log_dir/preview.log" >&2
        kill "$preview_pid" 2>/dev/null || true
        echo "The BahayBulsa preview did not become ready." >&2
        exit 1
    fi
fi

echo "BahayBulsa is ready. Open the forwarded port 8080 to use the dashboard and chat."
