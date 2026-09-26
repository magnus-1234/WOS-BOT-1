#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=:99
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp -noreset >/home/ubuntu/.wosoracle-xvfb.log 2>&1 &
  for _ in $(seq 1 40); do
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
    sleep 0.25
  done
fi
if ! pgrep -x openbox >/dev/null; then
  openbox --sm-disable >/home/ubuntu/.wosoracle-openbox.log 2>&1 &
  sleep 1
fi

if ! pgrep -x x11vnc >/dev/null; then
  x11vnc -display "$DISPLAY" -localhost -nopw -forever -shared -rfbport 5900 \
    >/home/ubuntu/.wosoracle-vnc.log 2>&1 &
fi
if ! pgrep -x websockify >/dev/null; then
  websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 \
    >/home/ubuntu/.wosoracle-websockify.log 2>&1 &
fi

exec /home/ubuntu/.wosoracle-venv/bin/python /home/ubuntu/bot/wosoracle_browser_service.py
