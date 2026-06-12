#!/bin/sh
set -eu

PORT_TO_USE="${PORT:-8000}"

exec gunicorn -w 2 -b "0.0.0.0:${PORT_TO_USE}" "app:create_app()"
