# AGENTS.md

> Canonical guide for AI coding agents working on the **AI Dzeck** codebase.

---

## Project Overview

**AI Dzeck** is an autonomous AI trading analyst platform. Users chat with an agent that analyzes financial markets (Forex, Crypto, Stocks) in real-time using MCP-connected data sources. The agent operates with **full autonomous reasoning** — it reads the market as a professional trader would, decides for itself which tools and parameters to use, and builds its analysis organically from what the data tells it. There are no hardcoded indicator sequences, no prescribed regime rules, and no fixed parameter defaults. Every analysis is unique to the current market state.

| Service | Stack | Port (dev) | Entry Point |
|---|---|---|---|
| **Frontend** | Vue 3 + TypeScript, Vite 4, Tailwind CSS | 5000 | `frontend/src/main.ts` |
| **Backend** | Python 3.12, FastAPI, LangChain, Beanie/Motor | 8000 | `backend/app/main.py` |

Infrastructure: **MongoDB Atlas** (cloud), **Redis Cloud** (Asia Southeast). No local Docker required.

---

## Directory Structure

```
dzeck/
├── frontend/          # Vue 3 SPA (Vite, TypeScript, Tailwind)
│   └── src/
│       ├── api/              # HTTP + SSE clients (agent, auth, config, files)
│       ├── assets/           # Global CSS + theme
│       ├── components/       # UI components
│       │   ├── icons/        # SVG icon components
│       │   ├── settings/     # Settings dialog panels
│       │   ├── toolViews/    # Tool result renderers (SearchToolView, McpToolView)
│       │   └── ui/           # Generic UI primitives (dialog, context menu, etc.)
│       ├── composables/      # Vue composables (theme, i18n, left panel, etc.)
│       ├── constants/        # Tool mappings (tool.ts)
│       ├── pages/            # Route-level page components
│       ├── stores/           # Pinia stores
│       ├── types/            # TypeScript type definitions
│       └── utils/            # Helpers (toast, markdown, etc.)
├── backend/           # FastAPI backend (DDD layout)
│   └── app/
│       ├── domain/           # Models, services, tools, agents, repositories
│       │   └── services/
│       │       ├── agents/   # Planner + Execution agents (LangChain)
│       │       ├── flows/    # Plan-Act orchestration loop
│       │       ├── prompts/  # System, planner, execution prompts ← core agent logic
│       │       └── toolkits/ # Tool registries (MCP, search, message)
│       ├── application/      # Application services (auth, agent, file, token, email)
│       ├── infrastructure/   # External integrations (DB, cache, search, MCP)
│       ├── interfaces/       # API routes, schemas, error handlers, dependencies
│       ├── core/             # Config (config.py)
│       └── main.py
├── mcp-servers/       # Local MCP server implementations
│   ├── deriv/         # Deriv platform — Forex/Gold indicators & analysis
│   ├── tradingview/   # TradingView wrapper — Crypto/Stocks (uses tradingview-mcp package)
│   ├── time/          # Time & forex market session tools
│   ├── economic-calendar/ # Real-time economic calendar (TradingView Calendar API)
│   ├── mongodb/       # MongoDB Atlas query tools
│   └── redis/         # Redis Cloud monitor tools
├── mcp.json           # MCP server definitions
├── .env.example       # Environment variable template
└── replit.md          # Replit project overview and user preferences
```

---

## Agent Architecture

### Orchestration Loop

The agent uses a **Plan → Execute → Update** loop (`backend/app/domain/services/flows/plan_act.py`):

1. **Planner** (`prompts/planner.py`) — decides how many steps are needed and describes the *goal* of each step, not the tools to use
2. **Execution agent** (`prompts/execution.py`) — reads the market, reasons about what it needs to know, chooses tools and parameters autonomously, interprets results in context
3. **Plan updater** — revises remaining steps based on what was actually found (e.g. extreme volatility reshapes the next steps)
4. **Summarizer** — delivers the final decision to the user; streams response as small chunks (~5 chars) for progressive display
5. Results stream to frontend via **SSE**

**Resilience mechanisms in `plan_act.py`:**
- `MAX_CONSECUTIVE_FAILURES = 2` — if two steps in a row fail to produce useful data, the loop jumps directly to SUMMARIZING with whatever was gathered, rather than spiralling into a retry storm
- Planner `UPDATE_PLAN_PROMPT` instructs the updater not to re-queue goals that already failed — prevents repeated identical tool calls

### Autonomous Reasoning Model

The agent does **not** follow a fixed protocol or indicator checklist. It operates like a professional trader with full market awareness:

```
1. READ   — look at the current market state (price, session, volatility, direction)
2. THINK  — "what do I still need to understand before I can make a decision?"
3. CHOOSE — select the tool that best answers that question; set parameters based on
            what the current market conditions call for, not defaults
4. INTERPRET — synthesize the result with everything already known
5. REPEAT — until enough conviction exists to decide, or until TUNGGU is clearly right
```

Before calling any tool, the agent states **why** it needs it.
After reading a result, the agent states **what it means** in context — not just the raw numbers.

**Notification protocol (mandatory):** The agent MUST call `message-notify-user` before AND after every tool call. Before: what it is about to check and why. After: what it found and what it means. These narrations appear as live text inside step cards in the frontend — not templates, but the agent's own words as it thinks.

**For pure reasoning steps (no tool calls):** The agent MUST still call `message-notify-user` at least once to narrate what it is synthesizing. A step must never appear empty to the user.

This means:
- No two analyses are identical, even for the same asset
- The agent may call RSI(9) one day and RSI(21) the next — based on current volatility
- The agent may use Ichimoku on a trending day and skip it entirely on a ranging day
- The agent narrates its thinking live at every tool call, making the analysis transparent and conversational
- Final decision / synthesis steps always show at least one notification even if no MCP tools are called

### Active Toolkits

| Toolkit | Tools | Purpose |
|---|---|---|
| **MCP toolkit** | All tools from `mcp.json` | Market data, indicators, DB persistence, time/session |
| **Search toolkit** | `info-search-web` | Economic calendar, news, fundamental events via Tavily |
| **Message toolkit** | `message-notify-user`, `message-ask-user` | Live progress updates + user clarification |

#### Message Toolkit Details

- `message-notify-user(text, attachments?)` — sends a live narration. Rendered as **prose text** inside step cards (no chip). Attachments are sandbox file paths synced to storage and shown as download links.
- `message-ask-user(text, attachments?, suggest_user_takeover?)` — pauses agent execution and waits for user input. Session moves to `WAITING` status. `suggest_user_takeover` enum: `"none"` or `"browser"`.

#### TradingView Tools Filter

The `MCPClientManager` in `domain/services/tools/mcp.py` filters TradingView tools down to **27 allowed tools** from the ~100+ available in the `tradingview-mcp` package. Only these tools are exposed to the agent:

```python
_TRADINGVIEW_ALLOWED = {
    "top_gainers", "top_losers", "bollinger_scan", "rating_filter",
    "coin_analysis", "consecutive_candles_scan", "advanced_candle_pattern",
    "volume_breakout_scanner", "volume_confirmation_analysis",
    "smart_volume_scanner", "multi_agent_analysis", "multi_timeframe_analysis",
    "market_sentiment", "financial_news", "combined_analysis",
    "backtest_strategy", "compare_strategies", "yahoo_price",
    "market_snapshot", "get_trade_levels", "kelly_position_size",
    "risk_based_position_size", "assess_trade_risk_full",
    "get_live_price", "get_multi_price", "get_global_market_overview",
    "save_trade_signal", "list_trade_signals", "recognize_market_pattern",
}
```

To add or remove a TradingView tool from the agent's available set, edit `_TRADINGVIEW_ALLOWED` and restart the **Backend API**.

### MCP Servers (`mcp.json`)

| Server | Key Tools | Instruments |
|---|---|---|
| `time` | `forex-market-hours`, `get-current-time`, `convert-timezone` | All — session/time checks |
| `deriv` | `deriv-smart-analysis`, `deriv-rsi`, `deriv-macd`, `deriv-bbands`, `deriv-ema`, `deriv-atr`, `deriv-stoch`, `deriv-technical-analysis`, `deriv-ichimoku`, `deriv-supertrend`, `deriv-fibonacci`, `deriv-pivot-points`, `deriv-cci`, `deriv-williams-r`, `deriv-heikin-ashi`, `deriv-keltner`, `deriv-donchian`, `deriv-parabolic-sar`, `deriv-volume-profile`, `deriv-fvg`, `deriv-order-blocks`, `deriv-swing-structure`, `deriv-liquidity-sweep`, `deriv-session-levels`, `deriv-prev-levels`, `deriv-seasonality`, `deriv-correlation` | Gold (`frxXAUUSD`), Silver (`frxXAGUSD`), Forex pairs (`frxEURUSD`, `frxGBPUSD`, `frxUSDJPY`, etc.) — **no crypto, no XRP, no stocks/indices** |
| `tradingview` | `coin_analysis`, `multi_timeframe_analysis`, `advanced_candle_pattern`, `volume_confirmation_analysis`, `bollinger_scan`, `backtest_strategy` | All crypto (`BINANCE:BTCUSDT`, etc.), stocks (`NASDAQ:AAPL`), indices (`SP:SPX`) — **not for Forex or Gold** |
| `economic-calendar` | `calendar-today`, `calendar-upcoming`, `calendar-find-event`, `calendar-get-week` | All — fundamental event queries |
| `sentiment` | `sentiment-ls-ratio`, `sentiment-top-traders`, `sentiment-open-interest`, `sentiment-fear-greed` | Crypto only — Binance Futures pairs (BTCUSDT, ETHUSDT, etc.) |
| `mongodb` | find, aggregate, count | Signal storage and history |
| `redis` | get/set, stats | Real-time data cache |

### Tool Routing Rule

This is a **technical constraint**, not a strategy rule — it reflects what each data source actually provides. Deriv and TradingView are **mutually exclusive**: never open both for the same instrument.

```
Deriv MCP   → ONLY for Deriv platform instruments:
              Gold: frxXAUUSD | Silver: frxXAGUSD
              Forex: frxEURUSD, frxGBPUSD, frxUSDJPY, frxAUDUSD, frxUSDCAD, frxUSDCHF, frxNZDUSD, etc.
              Symbol format: always prefix with "frx" — XAUUSD → frxXAUUSD, EURUSD → frxEURUSD
              ❌ Deriv does NOT have crypto (BTC, ETH, XRP, etc.), stocks, or indices.
              ❌ Do NOT also open TradingView for the same Deriv instrument.

TradingView → ONLY for non-Deriv assets:
              Crypto: BINANCE:BTCUSDT, BINANCE:ETHUSDT, KUCOIN:SOLUSDT, etc.
              Stocks: NASDAQ:AAPL, NYSE:TSLA, etc.
              Indices: SP:SPX, FOREXCOM:SPXUSD, etc.
              ❌ Do NOT use TradingView for Forex pairs or Gold/Silver.

Sentiment   → ONLY for crypto Binance Futures pairs: BTCUSDT, ETHUSDT, SOLUSDT, etc.
              ❌ NOT applicable to Forex or Gold.
```

### Session Status Flow

```
PENDING → RUNNING (planner creates plan, executor runs steps)
        → WAITING (message-ask-user called; agent pauses, waits for user reply)
        → RUNNING (user replies, execution resumes)
        → COMPLETED
```

- `WAITING` is triggered when the execution agent calls `message_ask_user`. The session is persisted in this state so a page refresh or reconnect can resume correctly.
- If a session is in `RUNNING` status when a new message arrives, the planner rolls back and replans from the new message.

### Vision Pre-processing Pipeline

When the user attaches an image (e.g. chart screenshot):

1. **Pre-processing** (`plan_act.py`) — `_preprocess_images()` is called once upfront using the dedicated **vision model** (`VISION_MODEL_NAME`).
2. The vision model generates a rich text description of the image.
3. The description is **injected into the message** as `[Image Analysis]\n{description}`.
4. Raw image data is cleared — downstream agents (planner + executor) only ever receive plain text.
5. **Fallback** — if no vision model is configured, the raw image is passed directly to the main model. If the main model rejects it (not multimodal), the system retries text-only with a note to the agent.

This design means the main model never needs multimodal capability as long as `VISION_MODEL_NAME` is set.

### Prompt Files (source of agent behavior)

| File | Role |
|---|---|
| `prompts/system.py` | Agent identity + tool catalog (what each tool measures) + tool routing + decision format + security rules |
| `prompts/planner.py` | Goal-oriented planning — describes *what* to understand, not *which tools* to call. Step count varies by complexity. |
| `prompts/execution.py` | Reasoning-first execution — agent MUST call `message-notify-user` before AND after every tool call; explains why before each call and what the result means after; all parameters chosen autonomously |

#### How to modify agent behavior

- To change **what the agent is** and what tools it knows about → edit `system.py`
- To change **how plans are structured** (step granularity, how goals are described) → edit `planner.py`
- To change **how the agent reasons** during execution (notification cadence, parameter logic, decision format) → edit `execution.py`
- **Always restart the Backend API workflow** after any prompt change

#### No-hardcode rule (mandatory before any prompt edit)

Full skill: `.agents/skills/no-hardcode/SKILL.md`

---

##### Yang BOLEH di-hardcode

Semua item di bawah adalah **panduan perilaku dan struktur** — bukan konten yang harus dihasilkan agent. Ini sah dan harus ada di prompt:

| Kategori | Contoh konkret di kode ini |
|---|---|
| **Panduan perilaku** | `"MUST call message_notify_user before AND after every tool call"` |
| **Routing tools** | `"Deriv MCP ONLY for frx* symbols; TradingView for crypto/stocks"` |
| **Format output** | `interface Response { success: boolean; result: string; attachments: [] }` |
| **Fallback behavior** | `"If step has no tool calls, still notify user at least once"` |
| **Resilience rule** | `"If 2 consecutive steps fail → skip to SUMMARIZING"` |
| **Parameter reasoning** | `"Every parameter must have a reason grounded in current market conditions"` |
| **Schema annotation** | `"success: boolean // ALMOST ALWAYS true. Only false if zero data obtained"` |
| **Placeholder examples** | `"result": "<your reasoning log — what you found and what it means>"` |
| **Batasan domain** | `"Do not answer questions outside trading/finance"` |
| **Anti-echo rule** | `"Do NOT echo error messages from conversation history in your summary"` |

---

##### Yang TIDAK BOLEH di-hardcode

| Yang Salah | Kenapa Salah | Gantinya |
|---|---|---|
| Nilai spesifik di contoh: `"RSI 72.3, ADX 44.23"` | LLM anchor ke angka itu meski data nyata berbeda | `"RSI di <nilai>, ADX di <nilai>"` dengan placeholder |
| Default parameter wajib: `"Use RSI(14) always"` | Membatasi otonomi parameter agent | `"Set period based on current volatility"` |
| Formula SL fixed: `"SL = ATR × 1.5"` | Menghilangkan professional judgment | `"Size SL to current market volatility"` |
| Jumlah TP fixed: `"Always give TP1, TP2, TP3"` | Memaksa struktur tanpa melihat setup | `"As many TP levels as the setup genuinely supports"` |
| Kalimat penolakan word-for-word | Agent hanya membaca, tidak berpikir | Panduan gaya: `"Respond honestly in 1–2 sentences"` |
| Daftar instrumen di jawaban | Duplikasi tool catalog | Arahkan agent ke tool catalog di `system.py` |
| `success: boolean` tanpa penjelasan | LLM bebas return `false` saat data parsial | Annotasi inline: kapan `true`, kapan `false` |

---

##### Sebelum & Sesudah — Perubahan Nyata di Proyek Ini

**1. `success` field schema (execution.py)**

```python
# SEBELUM — tidak ada guidance, LLM bebas memilih false
"success: boolean;"

# SESUDAH — behavioral guidance yang jelas
"success: boolean;  // ALMOST ALWAYS true. Only false if every tool failed AND zero
                    // data from any source. Partial data = success: true."
```

**2. Notification protocol (execution.py)**

```python
# SEBELUM — hanya untuk tool calls, tidak ada panduan untuk pure-reasoning steps
"MUST call message_notify_user before AND after every tool call"

# SESUDAH — mencakup semua situasi
"MUST call message_notify_user before AND after every tool call.
WHEN THIS STEP REQUIRES NO TOOL CALLS: still call message_notify_user at least once."
```

**3. Contoh result di prompt (execution.py)**

```python
# SEBELUM (contoh salah, tidak ada di proyek ini tapi illustratif)
"result": "ATR = 1.82, RSI = 67, pasar bullish — saya rekomendasikan buy di 4347"

# SESUDAH — hanya struktur dengan placeholder
"result": "<session context — when session opened, liquidity>. I started with
<why you chose this tool>. Found: <actual result>. <synthesis — what it means>."
```

**4. Summarizer prompt (execution.py)**

```python
# SEBELUM — tidak ada instruksi tentang error di history
"Deliver the final analysis result to the user now."

# SESUDAH — anti-echo rule ditambahkan
"Deliver the final analysis result to the user now. ...
Important: the conversation history may contain tool errors. Do NOT echo them.
Begin directly with the analysis."
```

---

Violations cause the AI to anchor to example values instead of reading real market data — the most common source of hardcoded-feeling analysis.

#### Notification rendering (frontend)

`message-notify-user` tool events render as **text prose** inside step cards via `ToolUse.vue`:
- `tool.name === 'message' && tool.args?.text` → rendered as markdown text (no chip)
- All other tools → rendered as a clickable chip with status indicator
- The live narration appears between tool chips as the agent works through each step

**Important — args preservation (`ChatPage.vue` → `handleToolEvent`):** When a tool finishes, the backend emits a `CALLED` event with `args: {}`. The frontend must preserve the original `args` from the earlier `CALLING` event and only merge non-empty args from `CALLED`. Without this, `tool.args?.text` becomes falsy and all `message-notify-user` prose silently disappears, leaving step cards empty.

---

## API Reference

### Auth Endpoints (`/api/v1/auth`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/login` | ❌ | Login with email + password → returns access + refresh token |
| `POST` | `/auth/register` | ❌ | Register new user → returns tokens immediately |
| `GET` | `/auth/status` | ❌ | Returns `auth_provider` setting |
| `GET` | `/auth/me` | ✅ | Get current user info |
| `POST` | `/auth/refresh` | ❌ | Refresh access token using refresh token |
| `POST` | `/auth/logout` | ✅ | Revoke current token (blacklisted in Redis) |
| `POST` | `/auth/change-password` | ✅ | Change password (requires old password) |
| `POST` | `/auth/change-fullname` | ✅ | Update display name |
| `POST` | `/auth/send-verification-code` | ❌ | Send 6-digit code to email (for password reset) |
| `POST` | `/auth/reset-password` | ❌ | Reset password using verification code |
| `GET` | `/auth/user/{user_id}` | ✅ Admin | Get any user by ID |
| `POST` | `/auth/user/{user_id}/deactivate` | ✅ Admin | Deactivate a user account |
| `POST` | `/auth/user/{user_id}/activate` | ✅ Admin | Reactivate a user account |

**Password reset flow:**
1. Call `send-verification-code` with email → 6-digit code sent via SMTP
2. Code TTL: **5 minutes**, max **3 attempts**, **60s cooldown** between requests
3. Call `reset-password` with email + code + new password

**Admin endpoints** require `role = "admin"` on the user document. An admin cannot deactivate their own account.

**Email config required for password reset** (set in Replit Secrets):
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USERNAME`, `EMAIL_PASSWORD`, `EMAIL_FROM`

---

### Session Endpoints (`/api/v1/sessions`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `PUT` | `/sessions` | ✅ | Create new session |
| `GET` | `/sessions` | ✅ | List all sessions for current user |
| `POST` | `/sessions` | ✅ | SSE stream of session list (live updates every 5s) |
| `GET` | `/sessions/{id}` | ✅ | Get session with full event history |
| `DELETE` | `/sessions` | ✅ | Delete all sessions for user |
| `DELETE` | `/sessions/{id}` | ✅ | Delete a single session |
| `POST` | `/sessions/{id}/stop` | ✅ | Stop a running session |
| `POST` | `/sessions/{id}/chat` | ✅ | Send message → SSE stream of agent events |
| `POST` | `/sessions/{id}/clear_unread_message_count` | ✅ | Mark messages as read |
| `GET` | `/sessions/{id}/files` | ✅ (or public if shared) | List files attached to a session |
| `POST` | `/sessions/{id}/share` | ✅ | Make session publicly viewable |
| `DELETE` | `/sessions/{id}/share` | ✅ | Revoke public access |
| `GET` | `/sessions/shared/{id}` | ❌ | Get a shared session without auth |
| `GET` | `/sessions/{id}/share/files` | ❌ | Get files from a shared session |

---

### File Endpoints (`/api/v1/files`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/files` | ✅ | Upload a file (multipart/form-data) |
| `GET` | `/files/{id}` | Signature | Download file (via signed URL) |
| `GET` | `/files/{id}/download` | ✅ | Download file (authenticated) |
| `GET` | `/files/{id}/info` | ✅ | Get file metadata |
| `DELETE` | `/files/{id}` | ✅ | Delete a file |
| `POST` | `/files/{id}/extract` | ✅ | Extract text from file (server-side, no sandbox) |
| `POST` | `/files/{id}/signed-url` | ✅ | Generate a temporary signed download URL (max 30 min) |

**Server-side text extraction** supports: PDF, PPTX, DOCX, XLSX, CSV, TXT.
Uses: `pdfplumber`, `python-pptx`, `python-docx`, `openpyxl`, `pandas`.
Returns `extracted_text` string + `char_count` — ready to inject into an AI prompt.

**Signed URLs** — time-limited tokens that allow downloading a file without the `Authorization` header. Useful for embedding file links in frontend or sharing specific files.

---

### Config Endpoint (`/api/v1/config`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/config/frontend` | ❌ | Returns `auth_provider` and `google_analytics_id` |

The frontend calls this on startup to determine whether to show the login UI and which analytics ID to use.

---

## Development Environment (Replit)

### Running Services

Two workflows run in parallel:

| Workflow | Command | Port |
|---|---|---|
| **Start application** | `cd frontend && pnpm dev` | 5000 |
| **Backend API** | `cd backend && python3 -m uvicorn app.main:app --host localhost --port 8000` | 8000 |

To restart a workflow, use the Replit workflow UI or the `restart_workflow` agent tool.

> **Important:** After editing any file in `backend/app/domain/services/prompts/`, restart the **Backend API** workflow for changes to take effect.

### Key Environment Variables (configured in Replit)

#### Required

| Variable | Purpose |
|---|---|
| `API_KEY` | LLM API key |
| `API_BASE` | LLM API base URL |
| `MODEL_NAME` | Main agent model — currently `qwen3.7-max` |
| `MODEL_PROVIDER` | LLM provider — `openai` (OpenAI-compat) |
| `MONGODB_URI` | MongoDB Atlas connection string |
| `MONGODB_DATABASE` | Database name — currently `manus` |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | Redis Cloud credentials |
| `JWT_SECRET_KEY` | Secret for signing JWT tokens (must be strong random string) |
| `AUTH_PROVIDER` | `password` (JWT) / `none` (skip login) / `local` (hardcoded single user) |
| `PASSWORD_SALT` | Salt string for password hashing |

#### Vision & Planning (optional — enable extra capabilities)

| Variable | Purpose |
|---|---|
| `VISION_MODEL_NAME` | Dedicated model for image analysis — `qwen2.5-vl-72b-instruct` |
| `VISION_MODEL_PROVIDER` | Provider for vision model |
| `VISION_API_BASE` | Base URL for vision model API |
| `VISION_API_KEY` | API key for vision model |
| `PLANNER_MODEL_NAME` | Optional separate model for planning step (vs execution) |
| `PLANNER_MODEL_PROVIDER` | Provider for planner model |
| `PLANNER_API_BASE` | Base URL for planner model |
| `PLANNER_API_KEY` | API key for planner model |
| `SUMMARY_MODEL_NAME` | Model used for auto-generating session titles |

#### Search & Proxy

| Variable | Purpose |
|---|---|
| `SEARCH_PROVIDER` | `tavily` / `bing_web` / `baidu_web` / `google` etc. |
| `TAVILY_API_KEY` | Tavily web search API key |
| `TV_PROXY_BASE` | Reverse proxy URL for TradingView scanner (avoids geo-blocking) |
| `SSL_VERIFY` | Set `false` for custom LLM gateways with self-signed TLS certs |

#### Advanced / Optional

| Variable | Purpose |
|---|---|
| `EXTEND_SYSTEM_MESSAGE` | Extra instructions appended to **all** agent system prompts (planner + executor). Use for per-deployment customization without editing prompt files. |
| `CONVERSATION_SAVE_PATH` | If set, saves raw conversation logs to disk at this path (e.g. `/tmp/conversations`). Useful for debugging. |
| `EXTRA_HEADERS` | JSON object — extra HTTP headers sent with every LLM API request (e.g. `{"X-Custom-Header": "value"}`). |
| `MAX_STEPS` | Max total tool calls across entire task before forced summarize (default: `100`). |
| `MAX_CONSECUTIVE_FAILURES` | How many consecutive failed steps before the loop skips to SUMMARIZING (default: `2`). Increase for tasks with many optional tool calls that may legitimately fail. |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_USERNAME` / `EMAIL_PASSWORD` / `EMAIL_FROM` | SMTP config for password reset emails. All five required to enable password reset. |
| `GOOGLE_ANALYTICS_ID` | Google Analytics Measurement ID (e.g. `G-XXXXXXXXXX`) — sent to frontend via `/config/frontend`. |
| `LOCAL_AUTH_EMAIL` / `LOCAL_AUTH_PASSWORD` | Hardcoded credentials when `AUTH_PROVIDER=local` (single-user mode). |

---

## Testing

### Backend Tests (pytest — integration-style)

Tests live in `backend/tests/` and hit a **running** backend at `http://localhost:8000`.

```bash
# Ensure backend is running first (Backend API workflow)
cd backend
python3 -m pytest                               # all tests
python3 -m pytest tests/test_auth_routes.py     # specific file
python3 -m pytest -m file_api                   # by marker
```

Key test files:
- `tests/test_auth_routes.py` — auth endpoints
- `tests/test_api_file.py` — file upload/download

Config: `backend/pytest.ini` (`asyncio_mode = auto`, markers: `file_api`).

### Frontend (No Automated Test Runner)

```bash
cd frontend
npx vue-tsc --noEmit   # type check
pnpm build             # production build (catches TS + template errors)
```

### Validation Workflows (Replit)

| Workflow | What it checks |
|---|---|
| `backend-syntax` | Python AST parse — all `.py` files compile |
| `backend-imports` | All domain modules import without error |
| `backend-pytest` | Integration tests (requires running backend) |
| `frontend-typecheck` | `vue-tsc --noEmit` |

### Full-Stack Integration Test

1. Ensure both workflows are running
2. Open the app preview (port 5000)
3. Register/login (or set `AUTH_PROVIDER=none`)
4. Create a session, send: `"Carikan entry XAUUSD sekarang"`
5. Observe in backend logs that the agent reasons through its tool choices organically
6. The agent should explain *why* it calls each tool before calling it
7. Check backend logs in the **Backend API** workflow console

---

## Code Conventions

### Backend (Python)

- **DDD architecture**: `domain/` → `application/` → `infrastructure/` → `interfaces/`
- **FastAPI** with **Pydantic v2** models and settings
- **Beanie** ODM for MongoDB documents (`infrastructure/models/documents.py`)
- **Redis** for caching and message queues
- Dependency management: **uv** + `pyproject.toml` (PEP 621)
- No enforced linter/formatter (no Ruff, Black, or Flake8 configured)
- Async-first: use `async def` for route handlers and service methods

### Adding a New MCP Server

1. Add the server script to `mcp-servers/<name>/server.py`
2. Register it in `mcp.json` with `"enabled": true`
3. Add the server and its tools to the `<tool_catalog>` in `system.py` — describe what each tool **measures** and what **question** it answers
4. Add tool routing rules in `system.py` under `<tool_routing>` if the server has platform-specific instruments
5. Restart **Backend API**

> Do NOT add prescriptive rules about when to use the new tools. The catalog description is enough — the agent will decide when to use them based on its own reasoning.

### Frontend (TypeScript / Vue)

- **Vue 3 Composition API** with `<script setup lang="ts">`
- **TypeScript** throughout
- **Tailwind CSS** for styling, **reka-ui** component library
- Path alias: `@/` → `src/`
- **vue-i18n** for internationalization (Chinese + English)
- Dependency management: **pnpm** + `package.json`
- No ESLint or Prettier configured

### Tool Views

Tool results rendered in the chat use two components:
- `SearchToolView.vue` — displays web search results
- `McpToolView.vue` — displays MCP tool call results (market data, signals, etc.)

Mappings from tool name → icon → component live in `frontend/src/constants/tool.ts`.

---

## Debugging

### Backend Logs
Check the **Backend API** workflow console in Replit, or read `/tmp/logs/Backend_API_*.log`.

Look for these log patterns to trace agent execution:
```
"Agent started processing message"   → planner triggered
"created plan with N steps"          → plan created (N varies by complexity — 1 to many)
"executing step 1"                   → first step running
"executing step 2"                   → deeper analysis running
"executing step N"                   → later steps — count decided by planner, not preset
"state changed ... to COMPLETED"     → analysis finished
```

In the execution output, healthy autonomous reasoning looks like:
```
"Saya ingin tahu seberapa kuat tren ini — saya panggil deriv-atr dulu untuk ukur volatilitas"
"ATR = 1.82, rata-rata sekitar 1.45 — volatilitas sedikit di atas normal tapi masih aman"
"Karena ada arah yang jelas, saya perlu check struktur besar — pilih Ichimoku H4"
```

### Frontend Logs
Check the **Start application** workflow console, or the browser DevTools console.

### Common Issues

| Issue | Likely Cause | Fix |
|---|---|---|
| Agent calls tools without explaining why | Old prompt cache | Restart Backend API workflow |
| MCP tool returns empty / error | MCP server not running or wrong symbol format | Check `mcp.json`, verify symbol routing (Deriv vs TradingView) |
| Agent always says TUNGGU | Genuine market uncertainty or conflicting signals — this is correct behavior | Check ATR/volatility in logs; may be a real market condition |
| TradingView tools fail | `TV_PROXY_BASE` misconfigured or scanner.tradingview.com blocked | Set/check `TV_PROXY_BASE` env var |
| Step cards appear empty (no chips, no text) | `CALLED` event with empty `args` overwrites notification text | Fixed in `ChatPage.vue` `handleToolEvent` — args from CALLING event are preserved |
| Summarize phase stuck on "Berpikir…" for 30s+ then text pops in all at once | `summarize()` buffered full LLM response before emitting | Fixed in `execution.py` `summarize()` — emits 5-char chunks progressively |
| Pure reasoning steps (no tools) show no content | Notification protocol only triggered by tool calls | Fixed in `prompts/execution.py` — added rule requiring at least one `message-notify-user` call even in tool-free steps |
| Deriv WebSocket disconnects mid-session | Network hiccup with no retry logic | Fixed in `mcp-servers/deriv/server.py` — up to 2 retries with exponential backoff |

### Resetting State

- MongoDB data is in Atlas cloud — wipe via Atlas console if needed.
- Redis data is in Redis Cloud — flush via Redis Cloud console if needed.
