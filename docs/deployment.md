# Remote Deployment

## Goal

The owner can open **https://stroy.mostdef.ru** from anywhere while all AI, storage and rendering workloads remain on the home computer.

## Preferred topology

```text
Browser
  |
  | HTTPS
  v
stroy.mostdef.ru
  |
  v
Cloudflare Access
  |   allow: owner identity only
  v
Cloudflare Tunnel
  |   outbound-only connection from home
  v
STROY Web/API
  |
  +--> PostgreSQL (loopback/private)
  +--> Redis      (loopback/private)
  +--> MinIO      (loopback/private)
  +--> Qwen       (loopback/private)
  +--> ComfyUI    (loopback/private)
  +--> Blender    (local process)
```

## Why this is the default

- no router port-forwarding is required;
- no static public IP is required;
- it works behind common home NAT/CGNAT setups;
- the public hostname can be protected before traffic reaches the home machine;
- only the STROY application is published, not its infrastructure services.

## Edge access policy

Create a self-hosted application for `stroy.mostdef.ru`.

Policy intent:

- default deny;
- allow only the owner's chosen identity/email;
- require re-authentication on a reasonable interval;
- do not expose an unauthenticated bypass hostname.

The origin must validate authenticated requests. Prefer the tunnel provider's built-in Access protection/token validation or validate the signed Access token in the STROY ingress adapter.

## Origin binding

The STROY production HTTP listener should bind to loopback or a private container network and be reachable by the tunnel daemon, not by the public Internet.

PostgreSQL, Redis, MinIO, Qwen and ComfyUI stay bound to loopback/private networking.

## Authentication modes

STROY should support:

- `cloudflare-access` — production/default for `stroy.mostdef.ru`;
- `local-password` — local development or emergency fallback;
- `disabled` — tests only.

In `cloudflare-access` mode, do not show a second application login form after edge authentication.

## DNS

Preferred deployment uses a tunnel-backed public hostname for `stroy.mostdef.ru`.

The exact DNS setup depends on where `mostdef.ru` is hosted. A full Cloudflare DNS setup is simplest, but a partial/CNAME setup can also be used when supported by the DNS provider/account configuration.

## Direct-public-IP fallback

If Tunnel/Access is not used, the fallback is:

```text
Internet
  -> router 80/443
  -> Caddy
  -> STROY Web/API
```

Requirements:

- DNS for `stroy.mostdef.ru` must resolve to the home public IP;
- dynamic DNS is needed if the ISP changes the address;
- ports 80/443 must be forwarded;
- Caddy terminates and renews HTTPS certificates;
- origin authentication remains mandatory;
- firewall rules must expose only the reverse proxy;
- infrastructure/model ports remain private.

This is a fallback because it exposes the home public IP and depends on ISP/NAT conditions.

## Operational rules

- no model/runtime admin UI is exposed publicly;
- no MinIO console is exposed publicly;
- secrets stay in local environment/secret storage;
- tunnel credentials are never committed;
- generated apartment assets are authenticated/private by default;
- maintain backups of PostgreSQL metadata and the asset bucket;
- remote access must fail closed if the identity layer is unavailable or misconfigured.
