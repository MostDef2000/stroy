# Style analysis

Style analysis consumes the user's text plus 3–5 immutable reference image Assets and produces a validated `StyleProfileProposal`. User overrides are applied deterministically on the VPS after inference and before the final `StyleProfile` is persisted.

## Image-aware paths

The worker supports two interchangeable adapters:

1. `LocalPixelStyleAdapter` is the no-model fallback. It reads the actual image bytes, extracts a deterministic palette and brightness evidence, and combines those measurements with explicit user text. It is deliberately limited for semantic material/form recognition.
2. `OpenAIVisionStyleAdapter` sends the same reference bytes as inline multimodal image content to any configured local OpenAI-compatible vision runtime and validates the returned JSON against the same typed proposal contract.

The default Qwen3-14B text endpoint is not assumed to support images. A separate vision endpoint can be configured with `STROY_STYLE_VISION_BASE_URL`.

## Provenance

The final profile records:

- all source Asset IDs;
- original source text;
- adapter/model provenance;
- structural evidence returned by the analyzer;
- deterministic user overrides.

External Pinterest or web URLs are never retained as the canonical reference. Images must first be imported as project Assets.
