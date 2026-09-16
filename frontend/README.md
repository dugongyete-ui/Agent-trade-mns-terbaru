# AI Dzeck — Frontend

Vue 3 + TypeScript + Vite + Tailwind CSS frontend for the AI Dzeck autonomous trading analyst platform.

## Running on Replit

The frontend runs via the **Start application** workflow. Vite dev server starts on port **5000** and proxies all `/api` requests to the backend at `http://localhost:8000`.

No `.env` file needed — the backend URL is handled automatically by the Vite proxy config.

```bash
# Install dependencies (if needed)
cd frontend && pnpm install

# Start dev server (port 5000)
pnpm dev

# Production build
pnpm build

# TypeScript type check
pnpm type-check
```

## Project Structure

```
src/
├── api/             # HTTP + SSE API clients (agent, auth, config, files)
├── assets/          # Global CSS and theme variables
├── components/
│   ├── ChatBox.vue              # Chat input with file attachment support
│   ├── ChatMessage.vue          # Message rendering with markdown support
│   ├── LeftPanel.vue            # Session list sidebar
│   ├── PlanPanel.vue            # Agent plan — step-by-step visualization
│   ├── ToolPanel.vue            # Tool call display (MCP tools, search)
│   ├── ToolUse.vue              # Individual tool call with live narration
│   ├── ToolPanelContent.vue     # Tool result content renderer
│   ├── FilePanel.vue            # Uploaded file management panel
│   ├── UserMenu.vue             # User account menu
│   ├── SessionItem.vue          # Session list item
│   ├── icons/                   # SVG icon components
│   ├── toolViews/               # Tool-specific result renderers
│   │   ├── McpToolView.vue      # MCP tool results (market data, indicators)
│   │   └── SearchToolView.vue   # Web search results
│   ├── settings/                # Settings dialog panels
│   └── ui/                      # Base UI primitives (reka-ui based)
├── composables/     # Vue composables (theme, left panel, i18n, etc.)
├── constants/       # Tool mappings: tool name → icon → view component (tool.ts)
├── pages/
│   ├── ChatPage.vue             # Main analysis workspace
│   ├── LandingPage.vue          # Public landing page
│   ├── LoginPage.vue            # Login / Register
│   └── SharePage.vue            # Shared session view (no auth required)
├── stores/          # Pinia stores
├── types/           # TypeScript type definitions
├── utils/           # Helpers (toast, markdown, etc.)
├── App.vue          # Root component with router-view
└── main.ts          # Entry point
```

## Key Components

### Chat & Analysis

- **`ChatPage.vue`** — main workspace: chat input, message list, plan panel, tool panel
- **`ChatMessage.vue`** — renders agent messages with Markdown (headers, tables, bold, code)
- **`ChatBox.vue`** — textarea with file attachment, send button, session controls
- **`PlanPanel.vue`** — shows the agent's step plan in real-time as steps execute and update

### Tool Display

- **`ToolUse.vue`** — renders a single tool call. `message-notify-user` calls appear as prose text (no chip). All other tool calls appear as clickable chips with status indicators.
- **`McpToolView.vue`** — formats MCP tool results: market data, indicator values, economic calendar events
- **`SearchToolView.vue`** — formats web search results

Tool name → icon → view component mappings are in `constants/tool.ts`.

### Session Sharing

- **`SharePage.vue`** / **`ShareLayout.vue`** — public session view accessible at `/shared/{session_id}` without login

## SSE Event Stream

The `/api/v1/sessions/{id}/chat` endpoint returns an SSE stream. The frontend handles these event types:

| Event | Description |
|---|---|
| `message` | Text from the agent — streamed token by token |
| `title` | Session title update |
| `plan` | Plan created or updated — contains step list |
| `tool` | Tool call started / completed |
| `done` | Analysis finished |

## Tech Stack

| Library | Version | Purpose |
|---|---|---|
| Vue 3 | latest | UI framework (Composition API) |
| TypeScript | latest | Type safety |
| Vite | 4.x | Dev server + build |
| Tailwind CSS | 3.x | Utility-first styling |
| reka-ui | latest | Accessible UI primitives |
| Pinia | latest | State management |
| vue-i18n | latest | Internationalization (EN + ZH) |
| pnpm | latest | Package manager |
