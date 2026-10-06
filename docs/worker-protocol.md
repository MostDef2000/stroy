# Remote GPU Worker Protocol

## Purpose

The VPS is the durable control plane; the home workstation supplies local compute.

The protocol should be HTTP/HTTPS-first and independent of Redis so that workers can be changed or added later without exposing infrastructure internals.

## Worker identity

A worker has:

- `worker_id`;
- display name;
- dedicated service credential;
- capabilities;
- runtime/model inventory;
- last heartbeat;
- status.

Example capabilities:

```json
{
  "capabilities": ["llm", "image_generation", "blender_render"],
  "models": ["qwen3-14b", "flux-dev-family"],
  "runtimes": {
    "comfyui": "installed",
    "blender": "installed"
  }
}
```

Do not report sensitive local filesystem paths unless explicitly needed for diagnostics.

## Job lifecycle

```text
queued
  -> waiting_for_worker
  -> leased
  -> running
  -> succeeded
     |-> failed
     |-> cancelled
     |-> lease_expired -> queued/waiting_for_worker
```

Durable truth lives in PostgreSQL. Redis may accelerate dispatch but is not authoritative.

## Lease semantics

A worker claims a compatible job for a bounded lease.

The lease records:

- job ID;
- worker ID;
- lease ID/token;
- leased timestamp;
- expiry;
- attempt number.

Only the worker holding the current lease may complete/fail that attempt.

Long-running jobs periodically renew the lease. If heartbeats/lease renewal stop, the server may return the job to a retryable state after expiry.

## Suggested API surface

Names are provisional until the OpenAPI implementation issue lands.

```text
POST /api/v1/workers/register
POST /api/v1/workers/heartbeat
POST /api/v1/workers/jobs/claim
POST /api/v1/workers/jobs/{job_id}/start
POST /api/v1/workers/jobs/{job_id}/heartbeat
POST /api/v1/workers/jobs/{job_id}/lease-status
POST /api/v1/workers/jobs/{job_id}/release
POST /api/v1/workers/jobs/{job_id}/complete
POST /api/v1/workers/jobs/{job_id}/fail
```

The claim endpoint may use long-polling to avoid frequent empty requests.

## Asset transfer

Job payloads reference Asset IDs, never arbitrary server filesystem paths.

For large binary input/output:

1. worker claims job;
2. API returns short-lived pre-signed GET URLs for inputs;
3. worker downloads inputs;
4. worker requests/receives pre-signed PUT URL(s) for outputs;
5. worker uploads outputs;
6. worker submits completion manifest referencing resulting Asset IDs/objects;
7. server validates/finalizes output metadata.

URLs must be time-limited and scoped to the exact object/action.

## Job types

Initial job families:

- `llm.complete`;
- `style.analyze`;
- `render.blender`;
- `image.generate`;
- `image.edit`;
- `quality.geometry_check`.

Domain-level commands remain on the VPS. The worker computes results; it does not get unrestricted permission to mutate canonical Scene state.

## LLM tool loop

Qwen executes on the worker, while authoritative scene tools execute on the VPS.

A model turn can therefore return tool-call proposals to the API. The API validates/executes the tool and can issue the next LLM turn with the tool result.

For v0.1, this can be implemented as short chained `llm.complete` worker jobs. A later persistent WebSocket/RPC channel is an optimization, not an architectural requirement.

## Worker authentication

Worker auth is separate from browser owner auth.

v0.1:

- high-entropy bearer/service token;
- stored in secret configuration only;
- rotatable/revocable;
- scoped to worker endpoints;
- never accepted as an owner browser session.

Later hardening can use mTLS or signed short-lived worker credentials.

## Offline behavior

If no compatible worker is online:

- projects/history/assets remain usable;
- GPU jobs stay `waiting_for_worker`;
- UI shows worker offline/unavailable;
- queue does not spin/fail repeatedly.

## Idempotency

Completion and failure endpoints must be idempotent per lease/attempt.

A stale worker whose lease expired cannot overwrite a newer attempt result.

## Observability

Record:

- worker ID;
- job/attempt/lease IDs;
- capabilities used;
- model/workflow/runtime profile;
- execution duration;
- upload/download duration;
- peak VRAM/RAM when available;
- structured failure class.


## Compatibility and cancellation

Job payloads may declare `required_models` and `required_runtimes` in addition to
capabilities. Claiming filters all three dimensions. A runtime requirement is
eligible only when the worker reports it as `ready`.

While an executor is running, the worker checks lease status. Owner cancellation
is propagated into cancellable adapters such as ComfyUI. A stale/invalid lease
causes the old local attempt to stop without reporting through that stale lease.

On SIGINT/SIGTERM the worker cancels the active local executor where supported and
explicitly releases the lease back to `waiting_for_worker` instead of waiting for
lease expiry. Transient control-plane failures use bounded exponential reconnect
backoff.

## Credential rotation

Production may configure multiple active SHA-256 worker-token hashes during a
rotation window. The old hash can then be removed after the home worker has moved
to the new token. Browser sessions never accept these credentials.
