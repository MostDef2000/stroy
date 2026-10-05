# VPS Operational Runbook

Target hostname: `stroy.mostdef.ru`. Production deploys are executed by the
GitHub Actions workflow `.github/workflows/deploy.yml` (issue #130); this file
is the operator runbook for the host side.

## Topology

```text
Browser
  |
  | HTTPS (443)
  v
Host Nginx (Ubuntu 24.04)
  |
  +--> /var/www/stroy.mostdef.ru/current  (symlink -> releases/<sha>)
  |
  +--> Proxy (127.0.0.1:8000) --> API container  (image stroy-api:<sha>)
                                     |
                                     +--> PostgreSQL (Private Net)
                                     +--> Redis (Private Net)
                                     +--> MinIO (Private Net)

ACME webroot (stable, nginx :80 only): /var/www/stroy-acme/.well-known/acme-challenge/
```

The wrapper **never** writes to `/var/www/stroy-acme`; it is owned by
certbot/nginx and is independent of every release.

## Canonical deploy flow (GitHub Actions)

1. Merge to `main` and wait for `ci.yml` to go green (all five jobs:
   `blender-smoke`, `contracts-and-python`, `deployment-config`, `web`,
   `security-gates`).
2. Run the **deploy** workflow (`workflow_dispatch`) from `main` with the full
   40-hex commit SHA.
3. The workflow's jobs:
   - **gate** — refuses any ref but `refs/heads/main`, validates the SHA and
     that it is an ancestor of `origin/main`, then selects the latest
     `ci.yml` run for that SHA (`event=push`, `branch=main`, completed,
     success) and requires all five jobs to have `success`.
   - **build** — checks out the pinned SHA, builds `stroy-api:<sha>`, builds
     `apps/web/dist`, bundles `deploy/vps/` config, writes and re-verifies
     `release-manifest.json`, and uploads one `deploy-envelope-<sha>.tar.gz`
     artifact (retained 14 days; the server keeps its own copies).
   - **deploy** — `environment: production`; re-verifies the artifact against
     the manifest, then over SSH (pinned host key, `StrictHostKeyChecking=yes`)
     calls the root-owned wrapper: `receive-v1 <sha>`, `apply-v1 <sha>`,
     `status-v1`. `ssh-keyscan` is forbidden and never used.

Owner setup (one time): create the `production` environment (optionally with
required reviewers / a wait timer) and set secrets `DEPLOY_SSH_KEY`,
`DEPLOY_HOST`, `DEPLOY_USER`, plus repository variable `DEPLOY_KNOWN_HOSTS`
(the pinned host key, e.g. from `ssh-keyscan stroy.mostdef.ru`). These are
owner-managed; the workflow cannot create them. `DEPLOY_USER` is `root`: the
forced-command key lives in root's `authorized_keys` (see bootstrap).

## Branch protection (required checks, post-merge)

The deploy gate only trusts a completed, successful `ci.yml` **push** run on
`main`. Keep that invariant enforced after #130 merges:

- Require status checks before merging on `main`, and require **all five**
  checks: `blender-smoke`, `contracts-and-python`, `deployment-config`,
  `security-gates`, `web`. Job id == check name: `ci.yml` declares no per-job
  `name:`, so the GitHub check name is the job id.
- Require branches to be up to date before merging.
- Do not allow bypassing the above settings (single-owner repo: leaving it
  enforced is what makes `event=push` + `head_branch=main` + all-jobs-success
  meaningful for the gate).

## One-time VPS bootstrap

1. **DNS**: `stroy.mostdef.ru` A record points at the VPS.
2. **Root forced-command key + wrapper** (the only SSH entry point):
   ```bash
   sudo install -d -m 0755 /usr/local/lib/stroy-deploy
   sudo install -m 0644 scripts/deploy/bundle_utils.py /usr/local/lib/stroy-deploy/bundle_utils.py
   sudo install -m 0755 deploy/vps/deploy-wrapper /usr/local/sbin/stroy-deploy-wrapper
   ```
   Add the deploy public key to **root's** `authorized_keys` with a forced
   command:
   ```text
   command="/usr/local/sbin/stroy-deploy-wrapper",no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding ssh-ed25519 AAAA... deploy@stroy
   ```
   > **Blast radius (honest).** There is no separate deploy account and no
   > `sudo`/docker group to isolate: the wrapper itself runs as root because it
   > loads docker images, writes `/etc/nginx` and reloads systemd units. The
   > forced command is the ONLY channel the key grants (only the three
   > allowlisted verbs are reachable), but every verb runs as root, so theft of
   > the deploy private key is host-root compromise. This single-owner trust
   > model was accepted by the #130 validator. Break-glass root console access
   > is unchanged and independent of this key.
3. **Directory layout**:
   ```bash
   sudo mkdir -p /var/opt/stroy-deploy/{staging,releases,config-history}
   sudo mkdir -p /opt/stroy-deploy/config
   sudo mkdir -p /var/www/stroy.mostdef.ru/releases /var/www/stroy-acme
   ```
4. **ACME webroot** (stable, outside the release tree):
   ```bash
   sudo mkdir -p /var/www/stroy-acme/.well-known/acme-challenge
   ```
5. **TLS certificate** (fresh host only). Serve the stable webroot from a
   minimal temporary `:80` block, then issue the certificate:
   ```nginx
   server {
       listen 80;
       server_name stroy.mostdef.ru;
       location ^~ /.well-known/acme-challenge/ { root /var/www/stroy-acme; try_files $uri =404; }
   }
   ```
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot certonly --webroot -w /var/www/stroy-acme -d stroy.mostdef.ru
   ```
6. **Initial nginx copies** (handoff installs these once; afterwards the
   wrapper installs the canonical copies from every verified envelope):
   ```bash
   sudo install -m 0644 deploy/vps/nginx/stroy-ratelimit.conf.example /etc/nginx/conf.d/stroy-ratelimit.conf
   sudo install -m 0644 deploy/vps/nginx/stroy.mostdef.ru.conf.example /etc/nginx/sites-available/stroy.mostdef.ru
   sudo ln -sfn /etc/nginx/sites-available/stroy.mostdef.ru /etc/nginx/sites-enabled/stroy.mostdef.ru
   sudo nginx -t && sudo systemctl reload nginx
   ```
7. **Environment file**: `deploy/vps/.env` on the host (based on
   `env.example`), plus the optional `deploy/vps/.worker-env`, owned by the
   operator. The wrapper symlinks them into `/opt/stroy-deploy/config/` so
   compose resolves its service-level `env_file`.
   - **Argon2 hashes and compose interpolation**: Compose interpolates `$`
     inside `env_file` values, corrupting `$argon2id$...`; store such values
     with doubled dollars (`$$argon2id$$...`) and verify the in-container
     sha256 against the original.
   - **MinIO image**: `minio/minio` is no longer pullable from Docker Hub
     (images removed in 2025; quay.io rejects anonymous pulls). On a fresh
     machine pre-pull and retag:
     ```bash
     docker pull cr.yandex/mirror/minio/minio
     docker tag cr.yandex/mirror/minio/minio minio/minio:latest
     ```
     (The live VPS already has this tag.)
8. **Confirm production compose identity before the first apply.** The wrapper
   passes `-p vps` (read from the release manifest) to *every* `docker compose`
   call, so the live containers/volumes must belong to the project `vps`. Check
   the running api container's compose label on the server:
   ```bash
   docker inspect <api-container> \
     --format '{{index .Config.Labels "com.docker.compose.project"}}'
   # expect: vps
   ```
   If it prints anything else (e.g. `config`), stop and reconcile (`docker
   compose -p vps ...` / volume migration) before the first `apply-v1` —
   otherwise compose starts a second, empty stack and the site goes dark.
9. **Bootstrap release A — seed the rollback baseline BEFORE the first B.**
   A wrapper `apply-v1` can only roll back to a release the host already has,
   and the nginx template it installs serves `/var/www/stroy.mostdef.ru/current`
   — both must exist *before* the first workflow deploy. The live VPS was
   originally deployed by hand with `docker compose up -d --build` at
   `8033b4a7e560a1e4d50e30fed95d89c6d0a4c8d9` (release **A**). On the server, as
   root, run the whole sequence below to seed A through the wrapper itself:
   ```bash
   A=8033b4a7e560a1e4d50e30fed95d89c6d0a4c8d9
   ROOT="/var/opt/stroy-deploy/bootstrap/$A"
   mkdir -p "$ROOT"

   # (a) tag the currently running image as the release A image
   API_IMAGE_ID="$(docker inspect <api-container> --format '{{.Image}}')"
   docker tag "$API_IMAGE_ID" "stroy-api:$A"

   # (b) snapshot the CURRENT serving web root exactly as served
   tar -C /var/www/stroy.mostdef.ru/current -czf "$ROOT/web-dist.tar.gz" .

   # (c) deploy-config from the repo tree at A (deploy/vps contents at root)
   mkdir -p "$ROOT/deploy-config"
   git -C <repo> fetch --no-tags origin "$A"
   git -C <repo> archive --format=tar "$A" deploy/vps \
     | tar -x -C "$ROOT/deploy-config" --strip-components=2
   tar -C "$ROOT/deploy-config" -czf "$ROOT/deploy-config.tar.gz" .

   # (d) api image archive + manifest (server copy of the helper)
   docker save "stroy-api:$A" | gzip -9 > "$ROOT/api-image.tar.gz"
   ID="$(docker image inspect "stroy-api:$A" --format '{{.Id}}')"
   python3 /usr/local/lib/stroy-deploy/bundle_utils.py build-manifest \
     "$ROOT" "$A" "$ID" 1 "manual"

   # (e) pack and feed the envelope through the wrapper's verbs
   tar -C "$ROOT" -czf "$ROOT/deploy-envelope-$A.tar.gz" .
   SSH_ORIGINAL_COMMAND="receive-v1 $A" /usr/local/sbin/stroy-deploy-wrapper \
     < "$ROOT/deploy-envelope-$A.tar.gz"
   SSH_ORIGINAL_COMMAND="apply-v1 $A" /usr/local/sbin/stroy-deploy-wrapper
   ```
   `ci_run_id` must be a positive integer (the manifest schema rejects `0` and
   strings); there is no CI run for the bootstrap release, so use the integer
   placeholder `1` together with `ci_run_url="manual"` as shown.
   Step (e) installs the new nginx template, the `/current` symlink and
   `current.json` in one verified step — this *is* the live-config sync.
10. **Verify, then deploy B.** Confirm the site and all five security headers
    (`status-v1`, plus a `curl -sD - -o /dev/null https://stroy.mostdef.ru/`),
    then run the first workflow deploy **B**. B's rollback target is now A,
    retained under `/var/opt/stroy-deploy/releases/$A` and
    `/var/www/stroy.mostdef.ru/releases/$A`.

   > **Why A itself cannot be deployed by the workflow.** `deploy.yml`'s
   > `build` job checks out the requested SHA and calls `scripts/deploy/*`; at
   > A (`8033b4a`) those helpers did not exist, so a `deploy.yml` run for A
   > fails. Rollback to A is therefore **server-retention only**: while A is
   > still staged, `apply-v1 A` works; if it was pruned, re-seed it (retag the
   > retained image `stroy-api:$A` and `receive-v1`/`apply-v1` the A envelope).

## Web distribution

Web assets are no longer built or copied by hand on the host. The `build` job
produces `web-dist.tar.gz`; `apply-v1` extracts it to
`/var/www/stroy.mostdef.ru/releases/<sha>/` and atomically repoints the
`current` symlink. The ACME webroot is untouched by deploys.

## Health checks

- **Ingress**: `https://stroy.mostdef.ru/health` and `/ready` return 200. Plain
  `curl http://127.0.0.1:8000/health` returns 400 by design
  (`TrustedHostMiddleware`); pass `-H 'Host: stroy.mostdef.ru'`.
- `apply-v1` fails the deploy unless the API health endpoint is ready and the
  public ingress returns 200 **with all five security headers**.
- **Containers**: `docker compose -p vps -f /opt/stroy-deploy/config/docker-compose.yml ps`.
  The `-p vps` is mandatory: it is the production project identity the wrapper
  always uses; omitting it selects a throwaway project derived from the config
  directory and shows an empty stack.

## Troubleshooting

### Worker registration fails with `400 Invalid host header`

`TrustedHostMiddleware` validates every request's `Host`. The worker in the
compose network reaches the api as `http://api:8000` (`Host: api:8000`). If
`STROY_TRUSTED_HOSTS` lists only the public domain, registration is rejected
and the worker loops on restart.

Fix: add the internal hostname to `deploy/vps/.env`, then redeploy (the wrapper
recreates the api):

```bash
# .env
STROY_TRUSTED_HOSTS=stroy.mostdef.ru,api
```

Starlette strips the port before matching, so `api` matches `Host: api:8000`
while the public domain keeps working through nginx.

## Backup and Restore

### Database (PostgreSQL)
- **Backup**: `docker compose exec postgres pg_dump -U stroy stroy > stroy_backup.sql`
- **Restore**: `cat stroy_backup.sql | docker compose exec -T postgres psql -U stroy stroy`

### Storage (MinIO)
- **Backup**: create a tar archive of the `stroy_minio` volume.
- **Restore**: extract the archive into the MinIO data directory.

## Resource-Limit Rationale

The VPS is resource-constrained (1 CPU core, ~0.9 GiB available RAM). To prevent OOM kills and ensure stability:

| Service  | Mem Limit | Mem Reservation | Note |
|----------|-----------|-----------------|------|
| API      | 256M      | 192M            | Main logic |
| Postgres | 224M      | 128M            | Metadata |
| Redis    | 96M       | 64M             | Coordination (maxmemory 48mb) |
| MinIO    | 320M      | 256M            | Asset storage |

**Buffer**: a 2.9G swap file on the host covers peak loads.

## Partial state and recovery

`apply-v1` is **not globally atomic**. It is ordered so the highest-risk
validation happens before any host-serving change, and so the recorded state
never lies:

1. **`receive-v1`** stages a fully verified, immutable release under
   `/var/opt/stroy-deploy/releases/<sha>` (idempotent) before anything else.
2. **nginx phase** — config and both nginx files are installed and `nginx -t`
   is run *before* any switch. If `nginx -t` fails, the wrapper restores the
   previous vhost **and** `conf.d/stroy-ratelimit.conf`, removes any symlink it
   created, also restores the pre-apply deploy configuration (same as the
   post-switch rollback), and exits: the production site is untouched (nginx
   was never reloaded) and `current.json` still describes the release that is
   actually live.
3. **`transitioning` marker** — `current.json` is written with
   `{"status":"transitioning","from":<prev>,"to":<sha>}` *before* the image is
   loaded, compose is (re)started, or the web symlink is switched.
4. **post-switch failure** (docker load/image-id, compose up, web publish,
   nginx reload, health/header checks) — the wrapper rewrites `current.json`
   with `status: "failed"` and then attempts an automatic best-effort rollback,
   logging every step:
   - restore the previous **deploy config** (compose file + nginx pair) from the
     unique config-history snapshot (`<timestamp>.<pid>`) and recreate the
     `.env`/`.worker-env` symlinks;
   - repoint the `current` web symlink to the previous release when
     `/var/www/stroy.mostdef.ru/releases/<prev>` still exists;
   - `docker compose -p vps up -d --no-build` with `stroy-api:<prev>` **only if**
     that image still exists locally (`docker image inspect`);
   - `nginx -t && systemctl reload nginx`.

   On a fully successful rollback `current.json` is rewritten as
   `{"status":"rolled_back_to","from":<sha>,"to":<prev>}` with `sha` set to
   `<prev>`. If any rollback step fails, the wrapper emits `WRAP-ERR` lines and
   leaves `status: "failed"` rather than pretending recovery succeeded. Either
   way the apply exits non-zero.

**Worst case** (host in an unknown/partial state, or the previous image was
already pruned): re-dispatch `deploy.yml` for the previous sha from the
retention window (see **Rollback** below); if no good release survives, use the
database/storage backups and break-glass root access.

> `current.json` is the wrapper's *intended* view, not a filesystem snapshot.
> `status: "failed"` means the host may have any mixture of old/new nginx,
> image, and web release; `status: "rolled_back_to"` means every rollback step
> reported success.

## Rollback

1. Pick the previous good `sha` still present under
   `/var/opt/stroy-deploy/releases/` (retention keeps the 5 newest by deploy
   time; `status-v1` lists them).
2. Dispatch `deploy.yml` with that `sha`. This is a **re-deploy of the old
   artifact**, not an automatic rebuild.
3. Migration caveat: migrations must be **additive only**. The wrapper never
   runs an automatic down-migration, and rolling back across an incompatible
   (destructive) migration can lose data — restore from the database backup
   instead.

## Break-glass

A sysadmin with root on the host can bypass the wrapper entirely (edit config,
run compose, restore nginx). Any such intervention is outside CI provenance
and **must be recorded** in the deploy handoff / journal, including the reason
and the exact commands run, so the next wrapper deploy has an honest baseline.

## Maintenance note

Keep `deploy/vps/nginx/` current in the repo; the wrapper installs the
canonical nginx copies from each verified envelope, so a host config drift is
corrected on the next successful `apply-v1`.
