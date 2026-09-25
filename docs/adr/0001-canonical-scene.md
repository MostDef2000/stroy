# ADR 0001: Canonical scene is independent of runtimes

- Status: Accepted
- Date: 2026-09-25

## Context

Blender, Three.js and image-generation workflows represent scenes differently. Making any of them canonical would couple product data to an implementation detail.

## Decision

STROY owns a versioned canonical scene model. Blender files, GLB exports, masks and generated images are derived artifacts.

## Consequences

Runtimes can be replaced and semantic IDs remain stable, at the cost of maintaining explicit adapters.
