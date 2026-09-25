# Local Development

## Prerequisites

- Git
- Docker + Docker Compose
- Python 3.12+

GPU components are installed separately because CUDA/ROCm/Apple Silicon setup is machine-specific.

## Infrastructure

```bash
cp .env.example .env
make infra-up
make check
```

Local services:

- PostgreSQL: localhost:5432
- Redis: localhost:6379
- MinIO S3 API: localhost:9000
- MinIO console: localhost:9001

## Qwen

STROY talks to Qwen via an OpenAI-compatible endpoint configured by:

```text
STROY_MODEL_PROFILES_PATH
STROY_MODEL_USE
STROY_LLM_MODEL_PROFILE
STROY_LLM_BASE_URL
STROY_LLM_API_KEY
```

The serving engine is deliberately not hard-coded. The selected profile resolves the upstream model name inside the home worker; domain/API code sees only the STROY profile ID. Tool-calling behavior must be contract-tested against the chosen local runtime.

## ComfyUI

ComfyUI is the initial image runtime. STROY-owned workflow manifests define semantic inputs/outputs and map them to concrete graph nodes only inside the generation layer. Business logic must not depend on ComfyUI node IDs.

The repository intentionally contains only a test fixture until a real graph has been exported and smoke-tested against the intended ComfyUI/model installation. Generated Comfy images are collected through the adapter, uploaded as project Assets by the leased worker, and then referenced by the finalized GenerationManifest.

## Blender

The renderer invokes Blender headlessly via STROY_BLENDER_BIN. Blender scripts belong under blender/.

## Hardware profiles

Do not hard-code VRAM assumptions before measurement. Define explicit profiles:

- cpu-dev: contracts/API only;
- gpu-low: quantized LLM and reduced image workflows;
- gpu-standard: intended local workstation;
- gpu-high: higher resolution/batching.

Actual limits belong in benchmark documentation.

## Data hygiene

Never commit model weights, generated renders, uploaded apartment data, API tokens or private reference images.

## Production split

The root local-development setup may run infrastructure and model runtimes on one workstation for convenience.

Production is intentionally split:

- VPS: existing nginx vhost, API, PostgreSQL, Redis, MinIO (web static served by nginx);
- home workstation: `stroy-worker`, Qwen, ComfyUI/FLUX, Blender.

Use `deploy/vps/env.example` for VPS settings and `apps/worker/env.example` for the home worker. The worker communicates only with the VPS application API over outbound HTTPS; it does not connect directly to PostgreSQL or Redis.


## Run the mocked vertical slice

The application can be exercised without Qwen, FLUX, Blender or a VPS.

```bash
make install
cp .env.example .env
make password-hash
```

Put the generated Argon2id value into `STROY_AUTH_PASSWORD_HASH` in `.env`, then:

```bash
make infra-up
make api
```

In separate terminals:

```bash
make web
make worker-fake
```

Open `http://localhost:5173`.

The fake worker uses the same remote-worker protocol as the future home GPU worker. It can claim jobs and execute deterministic fake LLM/image/render operations so auth, revisions, queues and UI can be tested before real model integration.

Useful flow:

1. sign in as the configured owner;
2. create a project;
3. click **Golden room**;
4. type `Сделай диван бежевым и убери стол`;
5. the instruction becomes an `llm.complete` job;
6. the fake worker returns typed tool calls;
7. the VPS/API-side command engine validates locks and creates revisions;
8. the scene viewer refreshes to the resulting canonical state.

For a future real home-model integration, set `STROY_WORKER_EXECUTOR_MODE=local` and configure the local Qwen OpenAI-compatible and ComfyUI endpoints.
