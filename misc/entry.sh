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

#Generate a unique secret key for flask
SECRET_KEY=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 16 | head -n 1)
sed -i "s/12345678901234567890/${SECRET_KEY}/" ${CONFIG}
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

