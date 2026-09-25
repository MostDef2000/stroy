# VPS Operational Runbook

Target hostname: `stroy.mostdef.ru`.

## Topology

```text
Browser
  |
  | HTTPS (443)
  v
Host Nginx (Ubuntu 24.04)
  |
  +--> Static Web Dist (/var/www/stroy.mostdef.ru)
  |
  +--> Proxy (127.0.0.1:8000) --> API Container
                                     |
                                     +--> PostgreSQL (Private Net)
                                     +--> Redis (Private Net)
                                     +--> MinIO (Private Net)
```

## One-time VPS Setup

1. **DNS**: Ensure `stroy.mostdef.ru` A record points to the VPS IP.
2. **Web Root**: Create the static directory:
   ```bash
   sudo mkdir -p /var/www/stroy.mostdef.ru
   sudo chown -R $USER:$USER /var/www/stroy.mostdef.ru
   ```
3. **Temporary HTTP vhost**: Install a temporary HTTP-only server block to allow certbot ACME challenge:
   ```nginx
   server {
       listen 80;
       server_name stroy.mostdef.ru;
       root /var/www/stroy.mostdef.ru;
   }
   ```
   Enable this block, then run `sudo nginx -t && sudo systemctl reload nginx`.
4. **TLS Certificate**: Use certbot in webroot mode to obtain the certificate:
   ```bash
   sudo certbot certonly --webroot -w /var/www/stroy.mostdef.ru -d stroy.mostdef.ru
   ```
   The certificate will be placed in `/etc/letsencrypt/live/stroy.mostdef.ru/`.
5. **Final vhost**: Copy `deploy/vps/nginx/stroy.mostdef.ru.conf.example` to `/etc/nginx/sites-available/stroy.mostdef.ru`, create a symbolic link to `sites-enabled`, and verify. `/etc/nginx/nginx.conf` must include `sites-enabled/*` (standard Ubuntu layout — the host already serves `mostdef.ru`, `auth.mostdef.ru` and `3d.mostdef.ru` through it):
   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```
6. **Renewal**: Certbot's systemd timer renews the certificate automatically. Ensure the renewal triggers an nginx reload:
   ```bash
   sudo certbot renew --dry-run
   # Add --deploy-hook "systemctl reload nginx" to certbot configuration if needed.
   ```

## Web Distribution Deploy

Build the web assets locally or in CI and sync them to the VPS:
```bash
make web-dist
rsync -avz apps/web/dist/ user@stroy.mostdef.ru:/var/www/stroy.mostdef.ru/
```

## Application Deploy

1. **Environment**: Create `.env` in `deploy/vps/` based on `env.example` with production secrets.
2. **Launch**:
   ```bash
   git pull
   docker compose -f deploy/vps/docker-compose.yml up -d --build
   ```
   *Note: Database migrations are run automatically via `alembic upgrade head` in the API container's entrypoint.*

## Health Checks

- **Ingress**: Verify `https://stroy.mostdef.ru/health` and `https://stroy.mostdef.ru/ready` return 200 OK.
- **Containers**: Run `docker compose -f deploy/vps/docker-compose.yml ps` to verify all services are healthy.

## Backup and Restore

### Database (PostgreSQL)
- **Backup**: `docker compose exec postgres pg_dump -U stroy stroy > stroy_backup.sql`
- **Restore**: `cat stroy_backup.sql | docker compose exec -T postgres psql -U stroy stroy`

### Storage (MinIO)
- **Backup**: Create a tar archive of the `stroy_minio` volume.
- **Restore**: Extract the archive into the MinIO data directory.

## Resource-Limit Rationale

The VPS is resource-constrained (1 CPU core, ~0.9 GiB available RAM). To prevent OOM kills and ensure stability:

| Service  | Mem Limit | Mem Reservation | Note |
|----------|-----------|-----------------|------|
| API      | 256M      | 192M            | Main logic |
| Postgres | 224M      | 128M            | Metadata |
| Redis    | 96M       | 64M             | Coordination (maxmemory 48mb) |
| MinIO    | 320M      | 256M            | Asset storage |

**Buffer**: A 2.9G swap file is configured on the host to provide a safety buffer for peak loads.

## Rollback

- **Compose**: Revert to previous commit and run `docker compose up -d`.
- **Nginx**: Disable the vhost `rm /etc/nginx/sites-enabled/stroy.mostdef.ru && systemctl reload nginx`.
