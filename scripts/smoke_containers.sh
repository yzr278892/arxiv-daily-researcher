#!/usr/bin/env bash
set -euo pipefail

# One-off image smoke: both services share disposable, empty mounts. The
# owner's running Compose volumes and credentials are never attached.
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 WORKER_IMAGE WEBUI_IMAGE" >&2
  exit 2
fi
worker_name="adr-smoke-worker-$$"
webui_name="adr-smoke-webui-$$"
smoke_root="$(mktemp -d /tmp/adr-image-smoke-XXXXXX)"
smoke_uid="$(id -u)"
smoke_gid="$(id -g)"
mkdir -p "$smoke_root/data" "$smoke_root/logs" "$smoke_root/configs" "$smoke_root/runtime"
cleanup() {
  docker rm -f "$worker_name" "$webui_name" >/dev/null 2>&1 || true
  rm -r "$smoke_root"
}
trap cleanup EXIT

mounts=(
  --mount "type=bind,src=$smoke_root/data,dst=/app/data"
  --mount "type=bind,src=$smoke_root/logs,dst=/app/logs"
  --mount "type=bind,src=$smoke_root/configs,dst=/app/configs"
  --mount "type=bind,src=$smoke_root/runtime,dst=/app/runtime"
)
docker run -d --name "$worker_name" -e PUID="$smoke_uid" -e PGID="$smoke_gid" -e MODE=manual -e RUN_ON_STARTUP=false -e SETUP_WIZARD=false "${mounts[@]}" "$1" >/dev/null
docker run -d --name "$webui_name" -e PUID="$smoke_uid" -e PGID="$smoke_gid" "${mounts[@]}" "$2" uvicorn src.modern_webui.app:app --host 0.0.0.0 --port 8501 >/dev/null

for _attempt in $(seq 1 30); do
  if docker exec "$worker_name" python /app/src/utils/container_health.py worker >/dev/null 2>&1 \
    && docker exec "$webui_name" python /app/src/utils/container_health.py webui --url http://127.0.0.1:8501/api/health >/dev/null 2>&1; then
    echo "Worker and WebUI entrypoint smoke passed."
    exit 0
  fi
  sleep 2
done

docker logs --tail=80 "$worker_name" >&2 || true
docker logs --tail=80 "$webui_name" >&2 || true
echo "Worker or WebUI did not become healthy." >&2
exit 1
