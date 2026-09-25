# ADR 0002: Design edits use typed commands and immutable revisions

- Status: Accepted
- Date: 2026-09-25

## Context

Arbitrary JSON mutation makes undo/redo, conflict detection and LLM safety difficult.

## Decision

Edits are accepted through typed commands validated against entity locks and an expected base revision. Successful commands produce immutable revisions.

## Consequences

History, undo/redo and agent actions become inspectable. Command schema changes require versioning/migrations.
