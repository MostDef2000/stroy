# ADR 0005: v0.1 uses owner-only authentication

- Status: Accepted
- Date: 2026-09-25

## Context

STROY is a personal renovation tool. Its AI, rendering and storage workloads run on the owner's computer, but the web application must be available remotely at `https://stroy.mostdef.ru`.

Apartment photos, plans and generated designs are private data. A public registration system, organizations and RBAC would add complexity without serving the single-owner use case.

## Decision

STROY has one logical owner identity.

### Production

For the Internet-facing deployment:

- `stroy.mostdef.ru` is protected by an identity-aware edge access layer;
- the allow policy matches only the owner's configured identity;
- the home origin is reached through an outbound tunnel;
- the origin validates the signed edge identity/token, or the tunnel daemon validates it before forwarding;
- after successful edge authentication, STROY does not require a second login form;
- project, asset, job and generation routes are authenticated;
- infrastructure services are never directly published.

### Local development / fallback

A `local-password` mode may be supported for development or emergency fallback:

- password stored only as a strong hash (Argon2id preferred);
- HttpOnly session cookie;
- CSRF protection for state-changing cookie-authenticated requests;
- no public registration.

## Consequences

Positive:

- the owner can access STROY from anywhere;
- the home origin does not need to expose a public listening port in the preferred deployment;
- there is one user model rather than a full identity/RBAC subsystem;
- sensitive apartment data is not anonymously accessible.

Trade-offs:

- production access depends on the configured edge identity/tunnel provider;
- local-password mode still requires its own secure session implementation;
- adding collaborators later requires a new authorization model and ADR.
