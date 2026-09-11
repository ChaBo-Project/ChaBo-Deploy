#!/bin/bash

# --- 1. Handle .env.local ---
ENV_LOCAL_PATH=/app/.env.local
if test -z "${DOTENV_LOCAL}" ; then
    if ! test -f "${ENV_LOCAL_PATH}" ; then
        echo "DOTENV_LOCAL was not found in the ENV variables and .env.local is not set using a bind volume. Make sure to set environment variables properly."
    fi;
else
    echo "DOTENV_LOCAL was found in the ENV variables. Creating .env.local file."
    cat <<< "$DOTENV_LOCAL" > ${ENV_LOCAL_PATH}
fi;

# --- 2. Start MongoDB (only when built from the chabo-chatui-db image) ---
# INCLUDE_DB is baked into the image itself (see ChaBo-ChatUI's Dockerfile) — "true" for
# chabo-chatui-db (embedded Mongo), unset/"false" for chabo-chatui (expects MONGODB_URL
# to point at an external Mongo instead, set via chatui.env.local).
if [ "$INCLUDE_DB" = "true" ]; then
    echo "Ensuring MongoDB data directory exists and has correct permissions..."
    mkdir -p /data/db
    chown -R user:user /data/db
    echo "Starting local MongoDB instance..."
    nohup mongod &
    sleep 5
fi

# --- 3. Create Models Directory ---
echo "Ensuring models directory exists and has correct permissions..."
mkdir -p /data/models
chown -R user:user /data/models

# --- 4. Handle PUBLIC_VERSION ---
if [ -z "$PUBLIC_VERSION" ]; then
    export PUBLIC_VERSION="0.0.1"
fi

# --- 5. Disable LLM-based title generation ---
export LLM_SUMMARIZATION=false

# --- 6. Start ChatUI Application ---
echo "Starting ChatUI application..."
exec dotenv -e /app/.env -c -- node /app/build/index.js -- --host 0.0.0.0 --port 3000
