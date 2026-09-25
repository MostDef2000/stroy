# ADR 0004: Physical geometry is explicitly protected

- Status: Accepted
- Date: 2026-09-25

## Context

Generative image systems can produce plausible results that no longer match the real apartment.

## Decision

Canonical entities expose explicit geometry, transform and material locks. Design commands and agent tools reject unauthorized changes to protected fields.

Generation is downstream visualization and never writes geometry back into canonical scene state.

## Consequences

Physical identity is protected. Generated pixels may still diverge, so geometry-preservation diagnostics are required.
