---
name: Event schema compatibility
description: Persisted agent events need discriminator-based parsing and legacy tool-content normalization.
---

Agent event history is persisted in MongoDB and may outlive changes to the event schema. The event union must dispatch by its `type` discriminator, and storage-facing models should normalize known legacy payload shapes before validation.

**Why:** Plain unions produce a cascade of misleading Pydantic errors when one historical event is missing a field from a newer schema; this can make a valid `tool` event look like a system-wide failure.

**How to apply:** When changing event payloads, preserve readers for existing stored shapes, validate representative old/new JSON fixtures, and restart the backend before testing session restore.