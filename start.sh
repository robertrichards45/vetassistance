#!/bin/sh
set -eu

PORT_TO_USE="${PORT:-8000}"

# gthread: each worker serves several requests at once (streaming AI answers, uploads),
# and background jobs run as threads. The timeout covers slow uploads, not AI calls,
# which run in the background.
exec gunicorn -k gthread -w "${WEB_CONCURRENCY:-2}" --threads "${GUNICORN_THREADS:-8}" \
  --timeout 120 --graceful-timeout 30 --keep-alive 5 \
  -b "0.0.0.0:${PORT_TO_USE}" "app:create_app()"
