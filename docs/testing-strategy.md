# Testing Strategy

## Contract tests

Validate JSON Schemas, API payloads and adapter contracts. Breaking persisted-contract changes require explicit versioning/migration notes.

## Scene-core tests

Test command validation, geometry/transform/material locks, stable IDs, revision conflicts and deterministic serialization.

## Golden-scene renderer tests

Maintain a tiny one-room fixture with fixed camera(s). Renderer tests compare dimensions, pass alignment, ID stability and tolerances rather than photorealistic pixel equality.

## Agent tests

Use fixed user requests and expected typed tool calls. Test invalid target IDs, attempts to mutate locked geometry, ambiguous edits and malformed model responses.

## Generation tests

Record workflow/model profile, seed when supported and input manifests. Validate geometry-preservation diagnostics and masked-edit scope rather than requiring identical diffusion pixels.

## End-to-end v0.1 test

One room -> calibrated camera -> style references -> redesign -> text material edit -> image replacement -> revision compare/undo.
