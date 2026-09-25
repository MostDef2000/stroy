# Remote Deployment

## Goal

The owner opens **https://stroy.mostdef.ru** from anywhere.

The DNS A record already points `stroy.mostdef.ru` to the owner's VPS. The VPS hosts the persistent STROY control plane. GPU-heavy workloads remain on the home workstation.

## Production topology

```text
Browser
  |
  | HTTPS
  v
stroy.mostdef.ru
  |
  | A record -> VPS
  v
Caddy
  |
  v
STROY Web/API
  |
  +--> PostgreSQL
  +--> Redis
  +--> MinIO
  +--> Worker API
          ^
          | outbound HTTPS
          |
    stroy-worker @ home PC
          |
          +--> Qwen
          +--> ComfyUI / FLUX
          +--> Blender
```

## VPS responsibilities

The VPS is always-on and owns:

- HTTPS ingress for `stroy.mostdef.ru`;
- web application;
- API;
- owner authentication/session state;
- PostgreSQL metadata and revision history;
- Redis job coordination;
- MinIO/S3-compatible project assets;
- durable job records;
- worker registration/leases.

Only ports needed for public web traffic should be exposed externally. PostgreSQL, Redis and MinIO remain on a private Docker/network namespace or loopback-only binding.

## Caddy

Caddy is the preferred reverse proxy for v0.1 because the domain already resolves directly to the VPS.

Responsibilities:

- automatic HTTPS certificate issuance/renewal;
- HTTP -> HTTPS redirect;
- reverse proxy to the STROY application;
- standard security headers where appropriate;
- request/body limits coordinated with the application for asset uploads.

The VPS firewall should expose only SSH administration as required and web ports 80/443. Database/storage/model ports are not public.

## Owner authentication

Production uses STROY's native single-user owner authentication:

- no public registration;
- Argon2id password hash;
- HttpOnly + Secure session cookie;
- CSRF protection;
- login throttling/backoff;
- session expiration and logout;
- private asset access.

A future optional TOTP/WebAuthn second factor can be added without changing the single-owner data model.

## Home GPU worker

The home computer runs `stroy-worker`. It does not accept inbound Internet connections.

The worker:

1. authenticates to the VPS with a dedicated worker credential;
2. registers capabilities and runtime/model versions;
3. maintains heartbeat/availability;
4. long-polls or maintains an outbound connection for work;
5. claims a job lease;
6. downloads required input assets;
7. executes Qwen, Blender or ComfyUI locally;
8. uploads result assets and manifests;
9. completes/fails the leased job.

The worker must never connect directly to VPS PostgreSQL or Redis.

## Worker authentication

Browser-owner credentials and worker credentials are separate security domains.

Initial worker auth may use a high-entropy service token stored only on:

- VPS secret configuration;
- the home workstation secret configuration.

The API stores only a safe representation where feasible and supports token rotation/revocation. A later version may use mTLS.

## Asset transfer

Canonical assets live in VPS object storage.

For worker jobs, prefer one of:

- short-lived pre-signed MinIO/S3 GET/PUT URLs;
- authenticated streaming through the API.

Pre-signed URLs are preferred for large image/render assets because they keep binary traffic out of application workers while retaining time-limited access.

## Availability behavior

The VPS remains usable for project browsing/history while the home GPU workstation is offline.

GPU-dependent jobs should show a state such as:

```text
queued -> waiting_for_worker -> leased -> running -> succeeded
                                      \-> lease_expired -> queued/retry
                                      \-> failed
```

The UI should clearly indicate that the local GPU worker is offline rather than treating it as a generic generation failure.

## Backups

At minimum back up:

- PostgreSQL;
- MinIO/project asset storage;
- application secrets required to restore sessions/tokens as appropriate;
- workflow/model profile configuration.

Model weights on the home machine do not need VPS backup if they can be reproduced from documented model profiles.

## Deployment automation

v0.1 should provide:

- VPS Docker Compose profile;
- Caddy configuration;
- environment/secrets example;
- systemd or Docker restart policy;
- database migration command;
- backup/restore notes;
- home-worker install/run instructions.
