#!/bin/bash
# Start backend uvicorn :8000 (script-launch agar selamat antar sesi shell)
cd /home/z/my-project/agent-trade/backend
setsid nohup /home/z/.venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> logs/backend.log 2>&1 < /dev/null &
echo "backend launched pid=$!"
