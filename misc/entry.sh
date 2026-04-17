#!/bin/bash
CONFIG="/app/config/config.yml"
GUNICORN_PORT=5000
GUNICORN_WORKERS=2
GUNICORN_THREADS=2

# Load environment variables from .env file if present
ENV_FILE="${PROXYWEB_ENV_FILE:-/app/.env}"
if [ -f "${ENV_FILE}" ]; then
    echo "Loading environment variables from ${ENV_FILE}"
    set -a
    . "${ENV_FILE}"
    set +a
fi

# Generate a unique SECRET_KEY for Flask — only when the shipped placeholder
# is still in place. Restarting the container otherwise would invalidate every
# live session on every restart. The sed pattern is anchored to the specific
# placeholder line so a user accidentally typing 12345678901234567890 in an
# unrelated field (e.g. a comment or port) does not get clobbered.
SECRET_KEY=$(tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 32)
if grep -Eq '^[[:space:]]+SECRET_KEY: 12345678901234567890[[:space:]]*$' "${CONFIG}"; then
    sed -i -E "s|^([[:space:]]+SECRET_KEY:) 12345678901234567890[[:space:]]*$|\1 ${SECRET_KEY}|" "${CONFIG}"
fi
echo
if [ -n "${WEBSERVER_PORT}" ]; then
    GUNICORN_PORT=${WEBSERVER_PORT}
fi

if [ -n "${WEBSERVER_WORKERS}" ]; then
    GUNICORN_WORKERS=${WEBSERVER_WORKERS}
fi

if [ -n "${WEBSERVER_THREADS}" ]; then
    GUNICORN_THREADS=${WEBSERVER_THREADS}
fi

gunicorn --chdir /app wsgi:app -w ${GUNICORN_WORKERS} --threads ${GUNICORN_THREADS} -b 0.0.0.0:${GUNICORN_PORT}

