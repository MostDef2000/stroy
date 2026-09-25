# VPS Deployment

Target hostname: `stroy.mostdef.ru`.

The DNS A record already points to this VPS.

## Intended services

- Caddy: public 80/443;
- STROY web/API: private app network;
- PostgreSQL: private;
- Redis: private;
- MinIO: private.

Only Caddy should be Internet-facing.

## Firewall

Expected public ingress:

- TCP 80: HTTP/ACME/redirect;
- TCP 443: HTTPS;
- SSH only according to the owner's administration policy.

Do not expose PostgreSQL 5432, Redis 6379, MinIO 9000/9001, Qwen or ComfyUI.

## Caddy

See `Caddyfile.example`. The final upstream port will be set when `apps/api`/web runtime is implemented.

## Secrets

Production secrets must not be committed. Use an environment file outside the repository, Docker secrets, systemd credentials, or another local VPS secret mechanism.

## Backups

At minimum back up PostgreSQL and the MinIO data volume.
