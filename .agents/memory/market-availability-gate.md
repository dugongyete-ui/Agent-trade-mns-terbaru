---
name: Market availability gate
description: Forex and Gold analysis must stop on live CLOSED/WEEKEND status; crypto remains 24/7.
---

Forex and Gold availability comes from the live forex-market-hours MCP result. A CLOSED/WEEKEND result is terminal: execution stops before price or indicator tools, skips later plan steps and final trade summarization, and tells the user why.

**Why:** A human trader should not fabricate a live setup for a closed market, and prompt-only instructions are not reliable enough when a model emits multiple tool calls.

**How to apply:** Keep the live-status check in the prompt for model behavior and the execution guard for enforcement. Do not replace it with fixed WIB clock ranges in the prompt; crypto is a separate 24/7 path.