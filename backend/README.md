# AI Dzeck — Backend Service

FastAPI backend for the AI Dzeck autonomous trading analyst platform.

## Stack

| Component | Technology |
|---|---|
| Runtime | Python 3.12 |
| Framework | FastAPI + Uvicorn |
| AI Orchestration | LangChain |
| Database ORM | Beanie (async MongoDB) |
| Cache | Redis (async via `redis.asyncio`) |
| Architecture | Domain-Driven Design (DDD) |

## Directory Structure

```
backend/
├── app/
│   ├── domain/               # Core business logic (no external dependencies)
│   │   ├── models/           # Domain models: Session, Message, Plan, Event
│   │   ├── repositories/     # Repository interfaces
│   │   ├── external/         # External service interfaces (search, etc.)
│   │   └── services/
│   │       ├── agents/       # Planner + ExecutionAgent (LangChain)
│   │       ├── flows/        # Plan-Act orchestration loop (plan_act.py)
│   │       ├── prompts/      # All agent behavior — system.py, planner.py, execution.py
│   │       └── tools/        # MCP, search, and message toolkits
│   ├── application/          # Use cases: auth, agent, file, token, email services
│   ├── infrastructure/       # Technical implementations: MongoDB, Redis, search, MCP
│   ├── interfaces/           # API routes, schemas, error handlers, DI dependencies
│   ├── core/                 # Config (settings, env vars via Pydantic)
│   └── main.py               # App entry — FastAPI setup, lifespan, CORS
├── mcp-servers/              # Local MCP server scripts (NOT in backend/ — at repo root)
├── tests/                    # Integration tests (pytest, requires running backend)
├── pyproject.toml            # Dependencies (managed via uv)
└── README.md
```

## Running

```bash
# Development (Replit: use "Backend API" workflow)
python3 -m uvicorn app.main:app --host localhost --port 8000

# With reload (local dev)
uvicorn app.main:app --host localhost --port 8000 --reload
```

Backend runs on port **8000**. The frontend (port 5000) proxies `/api` → `localhost:8000`.

## Agent Architecture

The agent uses a **Plan → Execute → Update** loop (`services/flows/plan_act.py`):

```
User message
    ↓
PlannerAgent         → creates a step-by-step plan (goal descriptions, no tool prescriptions)
    ↓
ExecutionAgent       → executes each step: reasons about what to check, calls MCP tools,
                       interprets results, narrates live via message-notify-user
    ↓
Plan update          → planner revises remaining steps based on what was found
    ↓
Summarizer           → delivers final decision in user's language
    ↓
SSE stream → frontend
```

### Step count

The planner decides how many steps are needed based on request complexity. There is no fixed step count. `max_steps = 100` is a safety ceiling only.

### Prompt files (`services/prompts/`)

| File | Role |
|---|---|
| `system.py` | Agent identity, full tool catalog, tool routing, decision format |
| `planner.py` | Planning instructions — goal-oriented, step count is free |
| `execution.py` | Execution instructions — reasoning-first, notify before+after every tool |

**After editing any prompt file, restart the Backend API workflow.**

**No-hardcode rule:** Examples in prompts must show STRUCTURE only (use `<placeholder>` syntax). Never embed specific prices, ATR values, indicator readings, or fixed multipliers. See `.agents/skills/no-hardcode/SKILL.md`.

## MCP Servers

7 MCP servers are defined in `mcp.json` (repo root) and launched as stdio subprocesses:

| Server | Tools | Instruments |
|---|---|---|
| `time` | 4 | Session clock, forex market hours, timezone |
| `deriv` | 33 | XAUUSD / Forex: price, RSI, MACD, BB, EMA, ATR, Stoch, Ichimoku, Supertrend, Fibonacci, Pivots, Heikin-Ashi, CCI, Williams%R, Keltner, Donchian, SAR + ICT/SMC: Volume Profile, FVG, Order Blocks, Swing Structure, Liquidity Sweep, Session Levels, Prev Levels, Seasonality, Correlation |
| `tradingview` | 29 | Crypto / Stocks / Indices |
| `economic-calendar` | 4 | CPI, FOMC, NFP, GDP — with WIB countdown |
| `sentiment` | 4 | Crypto Long/Short ratio, top traders, OI, Fear & Greed |
| `mongodb` | 5 | MongoDB Atlas monitoring |
| `redis` | 6 | Redis Cloud monitoring |

TradingView tools are filtered down to 29 relevant tools via `_TRADINGVIEW_ALLOWED` in `services/tools/mcp.py`.

### Adding a new MCP server

1. Create `mcp-servers/<name>/server.py`
2. Register in `mcp.json` with `"enabled": true`
3. Add the server and its tools to `<tool_catalog>` in `system.py` — describe what each tool **measures** and what **question** it answers
4. Add tool routing rules in `system.py` under `<tool_routing>` if needed
5. Restart Backend API

Do NOT add prescriptive rules about when to use the tools — the catalog description is enough.

## Key Environment Variables

Set in Replit Secrets. See `replit.md` for the full list.

| Variable | Required | Purpose |
|---|---|---|
| `API_KEY` / `API_BASE` / `MODEL_NAME` / `MODEL_PROVIDER` | ✅ | LLM credentials |
| `MONGODB_URI` / `MONGODB_DATABASE` | ✅ | MongoDB Atlas |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | ✅ | Redis Cloud |
| `JWT_SECRET_KEY` / `PASSWORD_SALT` | ✅ | Auth security |
| `AUTH_PROVIDER` | ✅ | `password` / `none` / `local` |
| `VISION_MODEL_NAME` / `VISION_API_BASE` / `VISION_API_KEY` | Optional | Chart image analysis |
| `PLANNER_MODEL_NAME` / `PLANNER_API_BASE` / `PLANNER_API_KEY` | Optional | Separate planning model |
| `TAVILY_API_KEY` | Optional | Web search |
| `TV_PROXY_BASE` | Optional | TradingView proxy (avoids geo-blocking) |
| `EXTEND_SYSTEM_MESSAGE` | Optional | Extra instructions appended to all agent prompts at runtime |
| `MAX_STEPS` | Optional | Max steps per task (default: 100) |
| `SSL_VERIFY` | Optional | `true` (default) or `false` — set to `false` when using a custom LLM gateway |

### SSL_VERIFY

Python's `httpx` (used by the OpenAI client) verifies SSL certificates against its own CA bundle. Some custom gateways — including Replit-hosted proxies — may use certificate chains not recognized by this bundle, causing `APIConnectionError` even when `curl` works fine.

**Rule:** If `API_BASE` points to a custom gateway (not `api.openai.com`, `api.anthropic.com`, or a major cloud provider), set `SSL_VERIFY=false`.

```
SSL_VERIFY=false   # for custom/self-hosted gateways
SSL_VERIFY=true    # for official provider APIs (default)
```

This setting applies to all three LLM clients: main model, vision model, and planner model.

## API Reference

### Auth (`/api/v1/auth`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/login` | ❌ | Login → access + refresh tokens |
| `POST` | `/auth/register` | ❌ | Register → tokens |
| `GET` | `/auth/me` | ✅ | Current user |
| `POST` | `/auth/refresh` | ❌ | Refresh token |
| `POST` | `/auth/logout` | ✅ | Revoke token |
| `POST` | `/auth/change-password` | ✅ | Change password |
| `POST` | `/auth/send-verification-code` | ❌ | Send reset code via email |
| `POST` | `/auth/reset-password` | ❌ | Reset password with code |
| `GET` | `/auth/user/{id}` | ✅ Admin | Get user |
| `POST` | `/auth/user/{id}/deactivate` | ✅ Admin | Deactivate user |
| `POST` | `/auth/user/{id}/activate` | ✅ Admin | Activate user |

### Sessions (`/api/v1/sessions`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `PUT` | `/sessions` | ✅ | Create session |
| `GET` | `/sessions` | ✅ | List sessions |
| `GET` | `/sessions/{id}` | ✅ | Get session with event history |
| `DELETE` | `/sessions/{id}` | ✅ | Delete session |
| `POST` | `/sessions/{id}/chat` | ✅ | Send message → **SSE stream** |
| `POST` | `/sessions/{id}/stop` | ✅ | Stop running session |
| `POST` | `/sessions/{id}/share` | ✅ | Make session public |
| `DELETE` | `/sessions/{id}/share` | ✅ | Revoke public access |
| `GET` | `/sessions/shared/{id}` | ❌ | View shared session |

The `/chat` endpoint returns an **SSE stream** with event types: `message`, `title`, `plan`, `tool`, `done`.

### Files (`/api/v1/files`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/files` | ✅ | Upload file |
| `GET` | `/files/{id}/download` | ✅ | Download file |
| `POST` | `/files/{id}/extract` | ✅ | Extract text (PDF, DOCX, PPTX, XLSX, CSV, TXT) |
| `POST` | `/files/{id}/signed-url` | ✅ | Temporary download URL (max 30 min) |
| `DELETE` | `/files/{id}` | ✅ | Delete file |

## Tests

Tests in `backend/tests/` are integration tests that hit a **running** backend at `http://localhost:8000`.

```bash
# Backend must be running first
cd backend
python3 -m pytest                           # all tests
python3 -m pytest tests/test_auth_routes.py # specific file
```

## Validation Workflows (Replit)

| Workflow | Command | What it checks |
|---|---|---|
| `backend-syntax` | `python3 -m ast ...` | All `.py` files parse without syntax errors |
| `backend-imports` | `python3 -c "import app..."` | All domain modules import cleanly |
| `backend-pytest` | `pytest` | Integration tests |
