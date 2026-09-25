# System Architecture

## Core rule

STROY combines deterministic geometry with probabilistic AI. Canonical state is therefore isolated from rendering and generation runtimes.

```text
Web UI
  |
  v
API / Project Service
  |
  +--> Scene Core ----------> PostgreSQL
  |      revisions
  |      commands
  |
  +--> Qwen Agent Adapter
  |      typed tools only
  |
  +--> Job Orchestrator ----> Redis
         |        |
         v        v
      Blender   ComfyUI
         \        /
          \      /
        S3-compatible storage
```

## Bounded contexts

### Scene Core

Owns canonical apartment state: coordinate system, rooms, surfaces, openings, architectural objects, cameras, technical constraints, geometry locks and semantic IDs.

### Design

Owns style profiles, design variants, commands and revision history. A design variant references a base scene revision rather than duplicating physical geometry.

### Rendering

Converts canonical scene state into deterministic render products: RGB, depth, normals, semantic object-ID masks and material-ID masks. Blender is an execution engine; a .blend file is a derived cache.

### Generation

Executes model-specific image workflows behind a stable internal contract. ComfyUI graphs are versioned implementation details.

### Agent

Interprets user intent and invokes typed domain tools. It may propose changes but does not write arbitrary scene JSON directly.

## Initial runtime choices

- Python 3.12 + FastAPI/Pydantic for API/control plane.
- PostgreSQL for projects, revisions, jobs and provenance.
- S3-compatible storage for uploaded and generated binary assets.
- Redis for queues, transient locks and job state.
- Qwen 14B-class model behind an OpenAI-compatible adapter.
- ComfyUI behind a STROY image-generation adapter.
- Blender headless for geometry-derived render passes.
- React + Three.js/React Three Fiber for the intended web scene editor.

## Failure containment

- failed generation never mutates scene state;
- worker retries are idempotent;
- scene revision creation and command persistence are transactional;
- generation/render jobs record structured failure reasons;
- uploaded assets are addressed by IDs/checksums, never trusted filenames.

## Observability

Every long-running job and agent action should carry a correlation ID and record project ID, revision IDs, model/runtime profile, elapsed time and structured error data.
