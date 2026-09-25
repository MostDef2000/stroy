# Services

Adapters to external/local runtimes and infrastructure.

Planned adapters:

- `llm/` - Qwen/OpenAI-compatible transport;
- `generation/` - ComfyUI/image workflow transport;
- `renderer/` - Blender execution boundary;
- `storage/` - PostgreSQL/S3/Redis implementations.

Adapters translate STROY contracts into runtime-specific payloads. Runtime payloads must not leak into the core domain model.
