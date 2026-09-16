---
name: Notification event transport
description: User-facing message-tool narration must be emitted as MessageEvent, including text-only calls.
---

`message_notify_user` is a presentation event as well as a tool call. Text-only notifications must be converted to persisted `MessageEvent` objects; forwarding only the tool chip makes the chat appear silent between executions.

**Why:** The frontend renders assistant narration from message events, while tool chips are primarily execution details and can be hidden or grouped inside a step.

**How to apply:** Preserve notification conversion for every call, with or without attachments, and apply the same rule to planner-created or plan-update events before they are buffered or streamed.