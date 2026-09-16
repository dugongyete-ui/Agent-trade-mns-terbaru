---
name: Retry storm bug fix
description: Intermittent stuck/looping agent state caused by 3 layered bugs; diagnosis and fixes applied.
---

## Bug Pattern

Screenshot shows agent stuck with steps named "Mengulang...", "Mengambil...", "Melakukan upaya terakhir..." — plan updater creating endless retry steps, agent stuck at "Berpikir..."

Happens intermittently ("kadang normal, kadang stuck") because root cause is Deriv API transient latency.

## Root Causes (3 layered)

1. **Deriv WebSocket per-call, no retry** — `deriv_request()` opened a new WS connection per tool call. If Deriv API took >15s, `asyncio.TimeoutError` was raised → tool returned error → step marked failed.

2. **No consecutive failure cap in plan_act.py** — After each failed step, `update_plan()` was called. The plan updater created a new retry step. No limit → 3-4-5 retry steps in a row until MAX_STEPS=100 hit.

3. **No failure handling guidance in prompts** — Execution prompt had no instruction to proceed with partial data when a tool fails. Planner update prompt had no rule against creating repeated retry steps.

## Fixes Applied

### `plan_act.py`
Added `consecutive_failures` counter (cap=2). After 2 consecutive step failures, jumps to SUMMARIZING instead of UPDATING — prevents retry storm regardless of root cause.

### `mcp-servers/deriv/server.py`
`deriv_request()` now retries up to 2 times with exponential backoff (1s, 2s) on any exception. Handles transient Deriv API latency without propagating failure.

### `backend/app/domain/services/prompts/execution.py`
Added "WHEN A TOOL RETURNS AN ERROR OR FAILS" section: proceed with available data, never retry same tool in same step, mark step done with whatever was obtained.

### `backend/app/domain/services/prompts/planner.py` (UPDATE_PLAN_PROMPT)
Added rule: do NOT create retry steps when previous step already contained "retry/ulang/alternative/terakhir". Move forward to next logical goal instead.

**Why:** The 3 bugs amplify each other — Deriv timeout → step fail → planner retry storm → all retry steps also fail → stuck until MAX_STEPS=100. The consecutive_failures cap (Fix 1) is the most critical safety valve.
