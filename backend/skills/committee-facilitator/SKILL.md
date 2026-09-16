---
name: committee-facilitator
description: When and how to run the multi-agent committee debate (run_committee) and how to synthesize its bull/bear/risk/decision output for the user.
category: workflow
---
# Committee Facilitator

`run_committee` convenes a virtual investment committee: a bull researcher and a bear researcher build opposing cases INDEPENDENTLY and in parallel, a risk officer reviews both and sizes the position, then a portfolio manager makes the final call.

## When it earns its cost
- The user asks for a high-conviction decision on ONE instrument (entry plan, "should I buy X now").
- The picture is genuinely two-sided: strong bull evidence AND strong bear evidence, and a single-chain analysis kept flip-flopping.
- Post-mortem of a contested setup.

When NOT to run it: quick data lookups, simple indicator reads, casual market chatter, or anything where the user wants speed over depth. The committee makes several model calls — it is the most expensive tool you have.

## How to call it
- Pass `query` = the user's actual question in full (the workers only see what you give them).
- Pass `target` = the instrument symbol (e.g. BTCUSDT, XAUUSD) and `market` when known. The committee pre-fetches recent price data itself and hands every worker the SAME ground-truth block, so the debate starts from identical facts.

## Synthesizing the result
- Present the PM's final decision first (direction, entry/SL/TP, conviction, invalidation), then the ONE strongest bull point and the ONE strongest bear point, then the risk officer's sizing/objections if they materially change the picture.
- If the roles disagreed (e.g. PM bullish, risk officer opposed), surface the disagreement explicitly — the value of the debate is the tension, not a fake consensus.
- Cite the ground-truth prices the committee used (they are in the result) and keep your own numbers consistent with them.
- Do not re-litigate the whole debate in your answer; link the conclusion to the evidence.
