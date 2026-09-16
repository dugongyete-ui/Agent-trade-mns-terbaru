#!/bin/bash
# Start Agent-trade stack: backend :8000, frontend :3000 (per .replit, port front disesuaikan utk preview)
cd /home/z/my-project/agent-trade/backend
setsid nohup /home/z/.venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> logs/backend.log 2>&1 < /dev/null &

cd /home/z/my-project/agent-trade/frontend
setsid nohup npx vite --port 3000 --host 0.0.0.0 > ../logs/frontend.log 2>&1 < /dev/null &

echo "started"
