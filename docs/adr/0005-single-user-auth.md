# ADR 0005: v0.1 uses single-user local authentication

- Status: Accepted
- Date: 2026-09-25

## Context

STROY is a personal renovation tool running local AI on the owner's computer. The web UI may still be reachable through a browser or local network, and apartment photos/plans are private data.

A full identity platform, public sign-up, organizations and RBAC would add complexity without serving the current use case.

## Decision

v0.1 uses a single local owner account.

- No public registration.
- Username is configured locally.
- Password is stored only as a strong password hash; Argon2id is preferred.
- Authentication is represented by an HttpOnly browser session cookie.
- State-changing cookie-authenticated requests use CSRF protection.
- Session secrets are supplied through local environment/secrets configuration.
- All project/asset/generation endpoints require authentication except minimal health/login endpoints.
- Postgres, Redis, MinIO, Qwen, ComfyUI and Blender remain infrastructure-only services.
- If accessed beyond localhost, HTTPS is required at the application or reverse-proxy boundary.

## Consequences

Positive:

- private apartment data is not anonymously accessible;
- implementation remains small and appropriate for a single owner;
- no external identity provider is required.

Trade-offs:

- this is not a multi-user identity model;
- adding collaborators later will require a new ADR and user/project authorization model.
