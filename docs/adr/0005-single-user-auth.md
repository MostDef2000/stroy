# ADR 0005: v0.1 uses single-owner native authentication

- Status: Accepted
- Date: 2026-09-25

## Context

STROY is reachable from the Internet at `https://stroy.mostdef.ru` through the owner's VPS. Apartment plans, photos and generated designs are private. The product has one owner and does not need registration, organizations or RBAC.

## Decision

Production authentication is implemented by STROY itself.

- One configured owner account.
- No public registration.
- Password is stored only as a strong Argon2id hash.
- Browser authentication uses an HttpOnly + Secure session cookie.
- State-changing requests use CSRF protection.
- Login is rate-limited/backed off against brute-force attempts.
- Sessions expire and can be invalidated by logout.
- Project, asset, job and generation endpoints require owner authentication.
- Health endpoints expose no sensitive project/runtime details.

Worker authentication is separate from browser owner authentication. A home GPU worker uses a dedicated high-entropy service credential and cannot use browser sessions.

A future TOTP/WebAuthn second factor can be added without changing the single-owner authorization model.

## Consequences

Positive:

- no external identity provider is required;
- authorization remains simple and aligned with the single-owner project;
- the VPS can authenticate access from any network.

Trade-offs:

- STROY owns secure password/session implementation;
- Internet exposure requires disciplined rate limiting, secure cookies, CSRF protection and patching;
- adding collaborators later requires a new authorization model and ADR.
