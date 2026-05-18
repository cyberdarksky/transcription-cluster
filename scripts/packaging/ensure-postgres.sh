#!/usr/bin/env bash
# Postgres.app çalışmıyorsa başlatır (pgwatcher launchd servisi için).
set -euo pipefail

POSTGRES_BIN="${POSTGRES_BIN:-/Applications/Postgres.app/Contents/Versions/latest/bin}"
export PATH="${POSTGRES_BIN}:${PATH}"

if command -v pg_isready &>/dev/null && pg_isready -h localhost -q 2>/dev/null; then
    exit 0
fi

open -a Postgres 2>/dev/null || true

for _ in $(seq 1 30); do
    if pg_isready -h localhost -q 2>/dev/null; then
        exit 0
    fi
    sleep 2
done

exit 1
