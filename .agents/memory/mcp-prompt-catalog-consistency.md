---
name: MCP prompt catalog consistency
description: Rule for keeping agent tool documentation aligned with runtime MCP exposure.
---

The agent prompt catalog is documentation and tool-selection guidance, not the runtime access boundary. Public Deriv tools and the TradingView allowlist are exposed by MCP/runtime code, while a local test should ensure every exposed tool is documented in the catalog.

**Why:** A tool can be technically callable but underused or misunderstood when it is absent from the prompt catalog. Drift between the allowlist and prompt creates silent capability gaps.

**How to apply:** When adding, removing, or renaming an MCP tool, update the catalog and the consistency test together. Keep dynamic analysis choices—indicators, timeframes, parameters, and conclusions—out of hardcoded decision logic.