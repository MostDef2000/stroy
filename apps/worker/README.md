# STROY GPU Worker

The worker runs on the owner's home workstation and executes GPU/renderer tasks for the VPS control plane.

It is **not** a public server.

## Local runtimes

The worker may invoke:

- Qwen through a local OpenAI-compatible endpoint;
- ComfyUI / FLUX through a local ComfyUI endpoint;
- Blender as a local headless process.

These endpoints should bind to loopback/private local networking.

## Network model

The worker initiates outbound HTTPS connections to `https://stroy.mostdef.ru`.

It never needs:

- a public IP;
- router port forwarding;
- inbound firewall rules;
- direct PostgreSQL access;
- direct Redis access;
- public Qwen/ComfyUI endpoints.

## Responsibilities

- authenticate using a dedicated worker credential;
- register capabilities/model/runtime versions;
- heartbeat availability;
- claim leased jobs;
- download job inputs;
- execute locally;
- upload output assets/manifests;
- complete/fail jobs with structured diagnostics;
- renew active leases while work is progressing.

See `docs/worker-protocol.md`.


## Process lifecycle

The worker handles SIGINT/SIGTERM as a graceful shutdown request. If a leased job
is running, its adapter is cancelled when supported and the lease is explicitly
released so another compatible attempt can resume. The worker also polls the
lease for owner cancellation and stops stale attempts.

Transient VPS/network failures do not terminate the process immediately; polling
retries with bounded exponential backoff.
