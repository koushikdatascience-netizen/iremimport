#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"
export LOCAL_HOST="${LOCAL_HOST:-0.0.0.0}"
export LOCAL_PORT="${LOCAL_PORT:-8091}"

mkdir -p /app/data/browser_profile /app/data/captures /app/data/mappings

Xvfb "$DISPLAY" -screen 0 "${VNC_GEOMETRY:-1366x768x24}" -ac +extension RANDR &

fluxbox >/tmp/fluxbox.log 2>&1 &

x11vnc \
  -display "$DISPLAY" \
  -forever \
  -shared \
  -nopw \
  -listen 127.0.0.1 \
  -rfbport "${VNC_PORT:-5900}" \
  >/tmp/x11vnc.log 2>&1 &

websockify \
  --web=/usr/share/novnc \
  "${NOVNC_PORT:-6080}" \
  "127.0.0.1:${VNC_PORT:-5900}" \
  >/tmp/novnc.log 2>&1 &

exec uvicorn app.main:app --host "$LOCAL_HOST" --port "$LOCAL_PORT"
