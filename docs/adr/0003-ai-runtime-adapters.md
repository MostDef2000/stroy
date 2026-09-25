# ADR 0003: AI models use replaceable adapters

- Status: Accepted
- Date: 2026-09-25

## Decision

Qwen is accessed through an internal OpenAI-compatible adapter. Image models are accessed through a STOY generation adapter, with ComfyUI as the initial runtime.

Product code depends on STOY contracts, not provider-specific payloads or ComfyUI node IDs.

## Consequences

Models and serving engines can change without rewriting domain logic. Adapters require contract tests and versioned workflow manifests.
