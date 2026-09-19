#!/bin/sh
# Container entrypoint for the Vista API image.
#   VISTA_MIGRATE_ON_START=true  run shared + tenant migrations before serving
#                                (safe with several replicas: migrate() takes a Postgres advisory lock)
#   VISTA_ROLE=api|worker|scheduler  which process to run (default api)
#   PORT                         listen port for the API (default 8000)
# Any arguments override the default command entirely (used for one-off admin tasks,
# e.g. `.venv/bin/python -m vista.manage create-workspace ...`).
set -eu

if [ "${VISTA_MIGRATE_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] running migrations"
  .venv/bin/python -m vista.manage migrate
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

case "${VISTA_ROLE:-api}" in
  api)
    # --proxy-headers: trust X-Forwarded-* from the load balancer in front of us.
    exec .venv/bin/uvicorn vista.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
      --proxy-headers --forwarded-allow-ips='*'
    ;;
  worker)
    exec .venv/bin/python -m vista.jobs.worker
    ;;
  scheduler)
    exec .venv/bin/python -m vista.jobs.scheduler
    ;;
  *)
    echo "unknown VISTA_ROLE: ${VISTA_ROLE}" >&2
    exit 64
    ;;
esac
