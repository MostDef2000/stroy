# System Architecture

## Core rule

STROY combines deterministic geometry with probabilistic AI. Canonical state is therefore isolated from rendering and generation runtimes.

```text
Internet / Browser
  |
  v
stroy.mostdef.ru (A -> VPS)
  |
  v
Caddy / HTTPS
  |
  v
STROY Web/API + owner auth
  |
  +--> Scene Core ----------> PostgreSQL (VPS-private)
  |      revisions
  |      commands
  |
  +--> Job Orchestrator ----> Redis (VPS-private)
  |
  +--> Asset Service -------> MinIO (VPS-private)
  |
  +--> Worker API
          ^
          | outbound HTTPS: claim/heartbeat/upload
          |
     Home GPU workstation
          |
          +--> Qwen
          +--> ComfyUI / FLUX
          +--> Blender
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

## VPS ingress and authentication boundary

`stroy.mostdef.ru` already resolves by A record to the owner's VPS. The VPS is the stable public control plane.

- Caddy terminates HTTPS and proxies only the STROY web/API application.
- STROY uses one owner account; no public sign-up, teams or RBAC.
- The password is stored only as a strong Argon2id hash.
- Browser auth uses an HttpOnly, Secure session cookie with CSRF protection for state-changing requests.
- Login attempts are throttled and authenticated sessions expire.
- PostgreSQL, Redis and MinIO are reachable only on the VPS private/container network.
- Qwen, ComfyUI and Blender are not installed/exposed on the VPS unless explicitly desired later.

## Remote GPU worker boundary

The home workstation runs a `stroy-worker` process.

- It creates outbound HTTPS connections to `stroy.mostdef.ru`; no inbound home port is required.
- It authenticates with a dedicated worker service credential separate from the owner's browser session.
- It registers capabilities/runtime versions and sends heartbeats.
- It claims leased jobs from the API.
- It downloads required assets using authenticated or short-lived URLs.
- It invokes local Qwen, ComfyUI/FLUX and Blender over loopback/private local endpoints.
- It uploads output assets/manifests to the VPS and completes/fails the lease.
- If the worker disappears, the lease expires and the durable job returns to a retryable/blocked state.

Redis remains an internal implementation detail; the home worker does **not** connect directly to Redis or PostgreSQL.

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
