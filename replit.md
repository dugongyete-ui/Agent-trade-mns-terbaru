# AI Dzeck

Autonomous AI trading analyst platform built with FastAPI + Vue 3. Users chat with an AI agent that analyzes financial markets (Forex, Crypto, Gold, Stocks) in real-time. The agent operates with **full autonomous reasoning** — like a professional trader given consciousness. It reads the market from scratch, decides for itself which tools and parameters to use, and builds its analysis organically from what the data tells it. No hardcoded indicator sequences. No prescribed rules. Every analysis is a fresh, adaptive response to current market conditions.

## Architecture

| Service | Stack | Port | Entry Point |
|---|---|---|---|
| **Frontend** | Vue 3 + TypeScript + Vite + Tailwind | 5000 | `frontend/src/main.ts` |
| **Backend** | Python 3.12, FastAPI, LangChain (OpenAI), Beanie | 8000 | `backend/app/main.py` |

**Database:** MongoDB Atlas (cloud) + Redis Cloud (Asia Southeast)

## How the Agent Thinks

The agent does not follow a checklist. It reasons like a professional trader:

1. **Macro first** — check active market session (London/NY/Tokyo/Sydney) and economic calendar for HIGH IMPACT events within the next few hours. Without this, any technical signal can be a trap.
2. **Read** — understand market structure from the top down (D1 → H4 → H1): bias, key levels, Order Blocks, Fair Value Gaps, Swing Structure.
3. **Think** — "what do I still need to know before I can make a decision?"
4. **Choose** — pick the tool that answers that question; set parameters based on current conditions — not from a fixed list.
5. **Interpret** — synthesize the result with everything already known. Narrate before and after every tool call.
6. **Repeat** — until there is enough conviction to decide, or until TUNGGU is clearly right.
7. **Devil's advocate** — before any final decision, explicitly state the strongest argument AGAINST the trade.
8. **Decide** — BUY / SELL / TUNGGU with specific entry zone, SL sized to current volatility, TP levels, conviction (HIGH/MEDIUM/LOW), and invalidation conditions.

Before calling any tool, the agent explains **why** it needs it.
After reading a result, the agent explains **what it means** in context.

**Notification protocol (mandatory):** The agent MUST call `message-notify-user` before AND after every tool call. These live narrations appear as text inside step cards — the agent's own words as it thinks aloud, not templates. Do not add example strings or hardcoded phrasing to the notification prompt — the agent must speak in its own voice.

This means the agent may use RSI(9) one session and RSI(21) the next — based on what the market demands. It may use Ichimoku on a trending day and skip it entirely on a ranging day. No two analyses are identical.

## MCP Servers (7 servers — ~85 tools total)

All servers defined in `mcp.json`, launched as stdio subprocesses.

| Server | Tools | Purpose |
|---|---|---|
| **time** | 4 | Session clock, forex market hours (London/NY/Tokyo/Sydney), timezone conversion |
| **mongodb** | 5 | MongoDB Atlas monitoring — find, count, aggregate, stats |
| **redis** | 6 | Redis Cloud monitoring — keys, values, stats, flush |
| **deriv** | 33 | Deriv platform: Gold (frxXAUUSD), Forex pairs — price, candles, RSI, MACD, BB, EMA, ATR, Stoch, Ichimoku, Supertrend, Fibonacci, Pivots, Heikin-Ashi, CCI, Williams%R, Keltner, Donchian, Parabolic SAR, Smart Analysis + ICT/SMC tools: Volume Profile, FVG, Order Blocks, Swing Structure, Liquidity Sweep, Session Levels, Prev Levels, Seasonality, Correlation |
| **tradingview** | 29 | Crypto/Stocks/Indices — screener, multi-timeframe analysis, volume confirmation, backtesting, market sentiment (proxy via `TV_PROXY_BASE`) |
| **economic-calendar** | 4 | Real-time economic calendar: CPI, FOMC, NFP, GDP, PMI, all central bank decisions — with forecast/actual/previous and WIB countdown. Source: TradingView Calendar API (60-min disk cache at `/tmp/ecocal_cache.json`) |
| **sentiment** | 4 | Market sentiment for crypto: Long/Short Ratio, Top Trader Positioning, Open Interest, Fear & Greed Index. Data: Binance Futures API + Alternative.me. Free, no API key, real-time. |

### Economic Calendar Tools
- `calendar-today` — all events releasing today with impact level and actual values
- `calendar-upcoming` — next N high-impact events from now with countdown timer
- `calendar-find-event` — find specific event: FOMC, BOJ, CPI, NFP, GDP, PMI, BOE, RBA, etc.
- `calendar-get-week` — full calendar for next 3 weeks grouped by day

### Sentiment Tools (crypto only — Binance Futures pairs, free, no API key)
- `sentiment-ls-ratio` — % Long vs Short all traders right now; high Long% (>65%) = crowded long = potential sell signal
- `sentiment-top-traders` — positioning of institutional / smart money (top account holders on Binance Futures)
- `sentiment-open-interest` — trend of open interest: new money entering or exiting a move
- `sentiment-fear-greed` — overall crypto market sentiment index (0-100): Extreme Fear → contrarian buy; Extreme Greed → reversal risk

> **Limitation:** Sentiment tools only work for Binance Futures pairs (BTCUSDT, ETHUSDT, SOLUSDT, etc.). Not applicable to Forex or Gold.

### Tool Routing (Technical Constraint — Not a Strategy Rule)

Deriv and TradingView are **mutually exclusive** — never use both for the same instrument:

- **Deriv MCP** → ONLY for Deriv platform instruments: Gold (`frxXAUUSD`), Silver (`frxXAGUSD`), and Forex pairs (`frxEURUSD`, `frxGBPUSD`, `frxUSDJPY`, `frxAUDUSD`, `frxUSDCAD`, `frxUSDCHF`, `frxNZDUSD`, etc.). Deriv does NOT have crypto, XRP, stocks, or indices. Symbol format: always prefix with `frx` — EURUSD → `frxEURUSD`, XAUUSD → `frxXAUUSD`.
- **TradingView MCP** → ONLY for non-Deriv assets: all crypto (`BINANCE:BTCUSDT`, `BINANCE:ETHUSDT`, etc.), stocks (`NASDAQ:AAPL`), indices (`SP:SPX`). Do NOT use TradingView for Forex pairs or Gold/Silver.
- **Economic Calendar MCP** → all fundamental queries: "kapan CPI?", "ada event hari ini?", news risk check before entry
- **Sentiment MCP** → crypto Binance Futures pairs only (BTCUSDT, ETHUSDT, SOLUSDT, etc.) — NOT for Forex/Gold. **Mandatory** for any crypto analysis — L/S ratio, Open Interest, Fear & Greed are not optional when analysing crypto.

### Deriv MCP — Server-Side Symbol Guard

`mcp-servers/deriv/server.py` enforces a hard symbol block at `call_tool()` before any request reaches the Deriv WebSocket API:

- Symbols with prefix `cry` or `crypto` → rejected (Deriv crypto pairs like `cryBTCUSD`)
- Exchange-format symbols (`BTCUSDT`, `ETHUSDT`, `BINANCE:xxx`, `NASDAQ:xxx`, etc.) → rejected
- Error message explicitly redirects to TradingView MCP

This is the second layer of protection — the first is the routing rule in `system.py`. If the LLM ignores the routing rule, the server still blocks and returns an actionable error.

## Agent Toolkits

- **MCP toolkit** — 7 servers, ~85 tools (data, indicators, ICT/SMC, sentiment, calendar, DB monitoring)
- **Search toolkit** — Web search via Tavily for real-time news and in-depth research
- **Message toolkit** — `message-notify-user` (live progress), `message-ask-user` (clarification)

## Core Prompt Files

All agent behavior is controlled by three files in `backend/app/domain/services/prompts/`:

| File | Role |
|---|---|
| `system.py` | Agent identity, tool catalog (what each tool measures and what question it answers), tool routing, decision output format, conviction + invalidation mandate, sentiment-mandatory rule for crypto |
| `planner.py` | Goal-oriented planning — describes *what* needs to be understood per step, not which tools to call. Step count determined by request complexity. All examples use `<placeholder>` syntax. |
| `execution.py` | Reasoning-first execution with mandatory pre-signal checks, devil's advocate, cross-step memory reference, calibrated notification depth, and coherence reconciliation before summarizing |

### execution.py — Key Behavioral Rules

- **Notification depth** — calibrated to significance: routine findings = 1 sentence, significant or contradictory findings = 2-3 sentences. Never compress a major finding.
- **Pre-signal checks** — before any BUY/SELL, agent MUST have checked: (1) current session quality and liquidity, (2) economic calendar for high-impact events within 4 hours. TUNGGU is always a valid conclusion.
- **Devil's advocate** — mandatory before any final decision: agent must state the strongest argument AGAINST the trade and why it is proceeding despite it.
- **Cross-step memory** — agent explicitly references findings from earlier steps when executing later steps. No step is treated in isolation.
- **Coherence reconciliation** — before summarizing, agent internally checks whether findings from all steps tell a consistent story; contradictions must be resolved in the output.
- **Conviction level** — every trading output must state HIGH / MEDIUM / LOW with a specific reason.
- **Invalidation conditions** — every BUY/SELL output must name one or two specific, observable conditions that signal the setup has failed.

After editing any prompt file, restart the **Backend API** workflow.

## Memory Compaction (`backend/app/domain/models/memory.py`)

The `compact()` method runs after each step to keep LLM context lean. Three passes:

- **Pass 1** — Strip base64 image data from HumanMessages. Chart images (~150-300 KB each) are stripped once the LLM has processed them.
- **Pass 2** — Truncate large MCP ToolMessage results to last 3000 characters.
- **Pass 3** — Remove intermediate tool call/result pairs from completed steps. Keeps only SystemMessage, HumanMessage, and AIMessage with real text content (narration/summaries). Removes pure tool-dispatch AIMessages (empty content) and all ToolMessages. This prevents "ghost success" where the model loses track of available tools and fabricates a completion response instead of actually calling tools.

---

## No-Hardcode Rule — Wajib Dibaca Sebelum Edit Prompt

Skill lengkap: `.agents/skills/no-hardcode/SKILL.md`

### Yang BOLEH di-hardcode (panduan perilaku & struktur)

| Kategori | Contoh yang benar |
|---|---|
| Panduan perilaku | `"Before each tool call, notify the user what you are about to check and why"` |
| Aturan routing tools | `"Use Deriv MCP for frxXAUUSD, TradingView MCP for BINANCE:BTCUSDT"` |
| Protocol wajib | `"MUST call message_notify_user before AND after every tool call"` |
| Format output JSON | `{"success": boolean, "result": string, "attachments": []}` |
| Fallback rule | `"If step has no tool calls, still call message_notify_user at least once"` |
| Contoh struktur | `"result": "<analisis dalam kata-katamu sendiri>"` dengan placeholder |
| Resilience rule | `"If 2 consecutive steps fail, skip to SUMMARIZING"` |
| Batasan domain | `"Do not answer questions outside trading/finance"` |

### Yang TIDAK BOLEH di-hardcode (konten yang harus dihasilkan AI)

| Yang salah | Kenapa salah | Yang benar |
|---|---|---|
| `"RSI di 72 menunjukkan overbought"` di contoh prompt | LLM akan anchor ke angka 72 meski data nyata berbeda | `"RSI di <nilai yang kamu baca> menunjukkan <interpretasimu>"` |
| `"Set RSI period = 14"` sebagai default wajib | Membatasi otonomi parameter | `"Set period berdasarkan kondisi pasar saat ini"` |
| `"SL = ATR × 1.5"` sebagai aturan fixed | Menghilangkan judgment agent | `"Size SL berdasarkan volatilitas pasar saat ini"` |
| Kalimat penolakan word-for-word di prompt | Agent hanya membaca, tidak berpikir | Panduan gaya: `"Respond honestly in 1-2 sentences"` |
| Daftar instrumen di isi jawaban | Duplikasi tool catalog | Biarkan agent jawab dari tool catalog di `system.py` |
| TP levels fixed (misal: TP1, TP2, TP3 selalu) | Memaksa jumlah TP | `"Set as many TP levels as the setup genuinely supports"` |

### Sebelum & Sesudah — Contoh Nyata dari Proyek Ini

**Bug yang pernah terjadi (sebelum fix):**
```python
# SALAH — contoh dengan nilai spesifik di prompt
"result": "RSI 72.3, ADX 44.23 — setup bullish dengan SL di 4310"
```

**Sesudah fix:**
```python
# BENAR — hanya placeholder
"result": "<session context>. I started with <why you chose this first tool>. Result: <actual tool output>. This tells me <interpretation>."
```

**Contoh lain — notification protocol:**
```python
# SALAH — hardcode kalimat notifikasi
"Saya sedang memeriksa RSI untuk melihat apakah pasar overbought"

# BENAR — panduan perilaku, agent pilih kata sendiri
"Before each tool: tell the user what you are about to check and why, in your own words."
```

**Contoh lain — success field:**
```python
# SALAH — tidak ada penjelasan → LLM bebas return false
"success: boolean"

# BENAR — behavioral guidance yang jelas
"success: boolean  // ALMOST ALWAYS true. Only false if zero tools returned any data."
```

## What Autonomous Means Here

The agent decides everything based on what it finds — no hardcoded rules:

| Aspect | How it works |
|---|---|
| **Which indicators to use** | Agent chooses based on what it needs to understand at that moment |
| **Which parameters to set** | Agent sets based on current volatility, timeframe, and market character |
| **How many steps to plan** | Planner decides — 1 step for simple questions, many steps for deep analysis |
| **How many tools to call** | Agent calls as many or as few as needed to reach conviction |
| **How to interpret results** | Agent synthesizes in context of everything it already knows |
| **Stop loss sizing** | Agent sizes SL to current market volatility — no fixed ATR multiplier |
| **Take profit levels** | Agent sets as many TP levels as the setup genuinely supports — no minimum count |
| **When to say TUNGGU** | Agent judges honestly — conflicting signals, extreme volatility, imminent news |
| **Decision delivery** | Agent writes in its own voice — no fixed template, no regime labels |

## Frontend Pages & Components

**Pages** (`frontend/src/pages/`):
- `LandingPage.vue` — product landing (6-step Cara Kerja: macro check → structure → tools → narration → devil's advocate → decision)
- `LoginPage.vue` — JWT auth
- `ChatPage.vue` — main analysis workspace
- `SharePage.vue` / `ShareLayout.vue` — view shared sessions

**Key Components** (`frontend/src/components/`):
- `ChatBox.vue` / `ChatMessage.vue` — conversation interface
- `ChatBoxFiles.vue` — image upload (restricted to `image/*` only)
- `PlanPanel.vue` — real-time step-by-step plan visualization
- `ToolPanel.vue` / `ToolUse.vue` / `ToolPanelContent.vue` — tool call display with formatted output
- `LeftPanel.vue` — session navigation
- `FilePanel.vue` / `FilePanelContent.vue` — uploaded image management

## Features

### Image Upload
Users dapat upload gambar (JPG, PNG, WEBP, GIF, HEIC, dll) langsung dari chat. Backend menggunakan **vision model** (`qwen2.5-vl-72b-instruct`) untuk membaca chart image — hasilnya dikonversi ke deskripsi teks dan diinjeksi ke konteks agent. File input dibatasi ke `image/*` saja — dokumen (PDF, DOCX, XLSX, dll) tidak didukung.

### Session Sharing
Setiap session bisa dibagikan publik via tombol "Share" di header chat. Session yang dibagikan bisa dilihat siapa saja lewat `/shared/{session_id}` tanpa login. Sharing bisa dicabut kapan saja.

### Admin User Management
User dengan `role = "admin"` di MongoDB bisa melihat, deactivate, dan activate user lain via API (`/auth/user/{id}/deactivate`, `/auth/user/{id}/activate`). Admin tidak bisa deactivate dirinya sendiri.

### Password Reset via Email
Jika SMTP dikonfigurasi (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USERNAME`, `EMAIL_PASSWORD`, `EMAIL_FROM`), user bisa reset password via 6-digit verification code yang dikirim ke email (TTL 5 menit, maks 3 percobaan).

### TradingView Tools Filter
Backend memfilter tools TradingView dari ~100+ ke **29 tools spesifik** yang relevan untuk trading signal. Filter ada di `backend/app/domain/services/tools/mcp.py` (`_TRADINGVIEW_ALLOWED`).

## Running on Replit

Two primary workflows:
- **Start application** — Vite dev server on port 5000, proxies `/api` → backend
- **Backend API** — FastAPI + Uvicorn on port 8000

Validation workflows (run on-demand):
- `backend-syntax` — AST parse all Python files
- `backend-imports` — import all key modules
- `backend-pytest` — run test suite
- `frontend-typecheck` — `vue-tsc --noEmit`

## Python Dependencies (Active)

Only packages actually used — cleaned of all general-purpose agent era dead weight:

| Package | Purpose |
|---|---|
| `fastapi` + `uvicorn` | HTTP server |
| `beanie` + `motor` | MongoDB ODM (async) |
| `langchain-openai` | LLM client (OpenAI-compatible — used for Qwen) |
| `langchain-core` + `langchain` | Agent orchestration |
| `redis` | Redis client (JWT blacklist, task registry) |
| `pydantic` + `pydantic-settings` | Data validation & config |
| `python-jose` + `passlib` | JWT auth + password hashing |
| `tavily-python` | Web search |
| `aiofiles` | Async file I/O |
| `httpx` | Async HTTP client |
| `python-multipart` | File upload parsing |
| `email-validator` | Email validation |

## Nix System Dependencies (Active)

Defined in `replit.nix` and `[nix] packages` in `.replit`:

| Package | Purpose |
|---|---|
| `gitFull` | Git version control |
| `glibcLocales` | Locale support for Python |
| `libiconv` | Text encoding conversion |
| `libxcrypt` | Cryptography (password hashing) |
| `openssl` | TLS/HTTPS connections |
| `pkg-config` | Build tool for native packages |
| `procps` | Process utilities (`replit.nix`) |

Removed (were dead weight from general-purpose agent era): `cargo`, `gdb`, `rustc`, `playwright-driver`, `xvfb-run`, `chromium`, `x11vnc`, `xorg.xorgserver`

## Key Environment Variables

All configured in Replit Secrets / userenv:

**Required:**
- `API_KEY` / `API_BASE` / `MODEL_NAME` / `MODEL_PROVIDER` — LLM provider credentials
- `MONGODB_URI` / `MONGODB_DATABASE` — MongoDB Atlas
- `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` — Redis Cloud (Asia Southeast)
- `JWT_SECRET_KEY` / `PASSWORD_SALT` — auth security
- `AUTH_PROVIDER` — `password` (JWT-based auth)

**Vision & planning (optional):**
- `VISION_MODEL_NAME` / `VISION_API_BASE` / `VISION_API_KEY` — dedicated model for chart image analysis (`qwen2.5-vl-72b-instruct`)
- `PLANNER_MODEL_NAME` / `PLANNER_API_BASE` / `PLANNER_API_KEY` — separate model for the planning step (optional, uses main model if not set)
- `SUMMARY_MODEL_NAME` — model for auto-generating session titles

**Search & proxy:**
- `TAVILY_API_KEY` — web search
- `SEARCH_PROVIDER` — `tavily`
- `TV_PROXY_BASE` — TradingView screener proxy URL (avoids geo-blocking); used by TradingView MCP server
- `SSL_VERIFY` — set `false` for custom LLM gateways with self-signed TLS certs

**Advanced / optional:**
- `EXTEND_SYSTEM_MESSAGE` — extra instructions appended to all agent system prompts at runtime (no prompt file edit needed)
- `CONVERSATION_SAVE_PATH` — directory to save raw conversation logs to disk (e.g. `/tmp/conversations`)
- `EXTRA_HEADERS` — JSON object of extra HTTP headers for every LLM request
- `MAX_STEPS` — max total tool calls per task before forced summarize (default: `100`)
- `MAX_CONSECUTIVE_FAILURES` — how many consecutive failed steps before loop skips to SUMMARIZING (default: `2`; increase if tasks have many optional tool calls that may legitimately fail)
- `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_USERNAME` / `EMAIL_PASSWORD` / `EMAIL_FROM` — SMTP config for password reset emails
- `GOOGLE_ANALYTICS_ID` — Google Analytics ID (sent to frontend at startup)

## User Preferences

- LLM API credentials are configured as shared environment variables when the provider requires a non-Secret env var; never commit or display credential values in source or logs
- No Docker — all services run directly in the Replit container
- MongoDB Atlas + Redis Cloud for persistence (no local DB)
- Agent is reasoning-first and fully autonomous — do not add hardcoded indicator rules back to prompts
- Upload dibatasi gambar saja — tidak ada server-side text extraction untuk dokumen
