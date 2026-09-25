# ADR 0007: Existing Nginx Ingress

- Status: Accepted
- Date: 2026-09-25

## Context

STROY v0.1 deploys to the owner's RU VPS which already runs nginx 1.24 as TLS terminator for multiple `mostdef.ru` vhosts on ports 80 and 443. The VPS is resource-constrained (1 CPU core, 1.8 GiB RAM total / ~0.9 GiB available).

Installing a second TLS terminator (Caddy, as proposed in ADR-0006) would require port arbitration or SNI split, adding unnecessary complexity and memory overhead on a machine with very limited resources.

## Decision

The host nginx will be used to terminate TLS for `stroy.mostdef.ru`.

1. **TLS Termination**: Host nginx manages the certificate for `stroy.mostdef.ru` via certbot.
2. **Static Content**: The built web distribution will be served directly from the host filesystem at `/var/www/stroy.mostdef.ru`.
3. **API Proxying**: Host nginx will proxy backend paths (`/api/*`, `/health`, `/ready`, `/docs*`, `/redoc`, `/openapi.json`) to the API container published on the host loopback interface at `127.0.0.1:8000`.
4. **Simplification**: The `web` service and in-container Caddy are removed from the `deploy/vps/docker-compose.yml`.

## Consequences

- **Certificate Lifecycle**: Certificate issuance and renewal becomes a documented runbook task using `certbot` on the host instead of being automatic within Caddy.
- **Resource Efficiency**: Removing the Caddy container saves RAM and reduces the number of moving parts on the VPS.
- **Deployment Dependency**: Deployment now depends on host nginx configuration conventions (vhost files in `sites-available`).
- **Supersession**: This ADR supersedes ADR-0006 regarding the ingress implementation.
- **Compression**: zstd encoding from the previous Caddy setup is not replicated (Ubuntu nginx ships no zstd module); gzip is used instead.

Reference: `docs/deployment.md` and `deploy/vps/nginx/stroy.mostdef.ru.conf.example`.
