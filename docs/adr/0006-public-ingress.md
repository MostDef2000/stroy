# ADR 0006: VPS control plane with outbound home GPU worker

- Status: Accepted
- Date: 2026-09-25

## Context

`stroy.mostdef.ru` already has an A record pointing to the owner's VPS. The application must be reachable from anywhere, while Qwen, FLUX/ComfyUI and Blender should run on the owner's personal workstation.

Exposing the home workstation or model runtime ports directly to the Internet is unnecessary and increases operational risk.

## Decision

Split STROY into two deployment roles.

### VPS control plane

The VPS hosts:

- Caddy HTTPS ingress;
- STROY web/API;
- native single-owner authentication;
- PostgreSQL;
- Redis;
- MinIO/S3-compatible object storage;
- durable jobs and worker leases.

### Home GPU worker

The home workstation runs `stroy-worker` and local:

- Qwen;
- ComfyUI / FLUX;
- Blender.

The worker makes outbound HTTPS requests to the VPS, authenticates with a dedicated service credential, claims leased jobs, transfers required assets, executes them locally and uploads results.

The worker never connects directly to PostgreSQL or Redis and requires no inbound home port.

## Consequences

Positive:

- the existing VPS/domain setup is used directly;
- web/API stay available while the home GPU machine is offline;
- no CGNAT/dynamic-home-IP problem;
- Qwen/ComfyUI remain private;
- durable application data has one always-on home on the VPS.

Trade-offs:

- large assets move between VPS and home workstation;
- GPU work depends on the home worker being online;
- job leasing/heartbeat and worker authentication become first-class product infrastructure.
