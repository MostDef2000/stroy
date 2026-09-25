# ADR 0003: AI models use replaceable adapters

- Status: Accepted
- Date: 2026-09-25

## Decision

Qwen is accessed through an internal OpenAI-compatible adapter. Image models are accessed through a STROY generation adapter, with ComfyUI as the initial runtime.

Product/domain code depends on STROY contracts, not provider-specific payloads or ComfyUI node IDs.

Model selection is by STROY model-profile ID. The worker resolves the profile into the upstream runtime model name only inside the adapter/bootstrap boundary and validates the configured use against the profile.

## Adapter boundary

The adapter layer owns:

- provider/runtime HTTP payloads;
- timeout/unavailable/protocol error normalization;
- model/runtime provenance without credentials;
- Qwen/OpenAI tool-call response normalization;
- ComfyUI prompt submission/history/output collection.

The adapter layer must not expose API keys, bearer tokens or other secrets in job results/provenance.

## Tool boundary

The agent sees JSON schemas generated from typed STROY tool argument models. Read tools operate on the project-scoped canonical Scene snapshot. Every mutation is converted into a `DesignCommand` and passes through the command/revision engine and locks.

## Workflow boundary

STROY owns a versioned semantic `WorkflowManifest`. API/domain code supplies semantic names and values. Concrete ComfyUI node IDs/input names are stored only inside the workflow/generation adapter layer.

Generated outputs are uploaded through the worker lease to the VPS asset service. Only resulting Asset IDs enter the finalized `GenerationManifest`.

## Consequences

Models, serving engines and concrete ComfyUI graphs can change without rewriting canonical Scene/domain logic. Adapters and manifests require contract tests. A real production workflow is not accepted merely because its JSON is structurally valid; it must be smoke-tested against the intended runtime/model installation.
