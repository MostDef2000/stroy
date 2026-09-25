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
STROY_LLM_BASE_URL
STROY_LLM_API_KEY
STROY_LLM_MODEL
```

The serving engine is deliberately not hard-coded. Tool-calling behavior must be contract-tested against the chosen local runtime.

## ComfyUI

ComfyUI is the initial image runtime. Workflows live under workflows/ and must have STROY-owned manifests defining stable inputs/outputs. Business logic must not depend on ComfyUI node IDs.

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
