# ADR 0006: Remote access uses a public hostname with private origin

- Status: Accepted
- Date: 2026-09-25

## Context

STROY must be reachable from anywhere at `https://stroy.mostdef.ru`, while Qwen, FLUX/ComfyUI, Blender, PostgreSQL, Redis and MinIO remain on the owner's computer.

Home Internet connections may have changing public IPs, NAT/CGNAT, and should not expose internal AI/storage services.

## Decision

The preferred v0.1 deployment publishes only the STROY web/API surface through an outbound tunnel.

Primary profile:

- hostname: `stroy.mostdef.ru`;
- edge access control: owner identity only;
- tunnel: outbound from the home machine;
- origin listener: loopback/private container network;
- no public port forwarding required;
- local infrastructure/model services remain private.

The initial implementation target is Cloudflare Tunnel + Cloudflare Access because it supports public hostnames, owner-only access policies and outbound-only origin connectivity.

A direct public-IP + Caddy deployment remains a fallback profile when desired/possible.

## Consequences

Positive:

- remote access works without exposing the home origin directly;
- dynamic IP/CGNAT are less likely to block deployment;
- edge authentication happens before requests reach the application;
- infrastructure ports remain private.

Trade-offs:

- remote availability depends on the tunnel/edge provider;
- tunnel credentials and DNS configuration become operational dependencies;
- direct-provider independence requires maintaining the fallback deployment profile.
