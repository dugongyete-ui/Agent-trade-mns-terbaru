---
name: Calibrated trading uncertainty
description: Decision behavior for imperfect but still actionable market evidence.
---

The agent should not treat every contradiction, elevated volatility, event risk, or MEDIUM/LOW conviction as a hard stop. Preserve the best-supported directional bias when a setup remains actionable, lower conviction when appropriate, state the counter-argument, and define observable invalidation conditions. Reserve TUNGGU for unavailable/invalid/unsafe data or genuinely non-actionable situations.

**Why:** Trading decisions are probabilistic and an overly strict critic makes the analyst panic or become paralyzed, while the product still needs hard stops for safety and unusable data.

**How to apply:** Keep this distinction consistent across system and execution prompts, critic behavior, replan logic, and future tests. Do not hardcode indicators, prices, timeframes, or analysis conclusions.