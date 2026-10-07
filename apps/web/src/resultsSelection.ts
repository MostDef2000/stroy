// Pure selection model for the Results master-detail workspace (#152).
//
// The app polls every 5 seconds and replaces every object identity, so the
// selection is stored as a stable string id (generation/job id, or a
// `rev:`-prefixed revision id for version rows) and re-resolved against the
// latest data on every render. This module is pure and unit-tested; the
// component in DesignPanel.tsx only renders what it returns.

import type {
  GenLike,
  TimelineAction,
  TimelineEntry,
  TimelineResult
} from "./resultsTimeline";

export interface RevisionLike {
  revision_id: string;
  created_at: string;
}

const REVISION_PREFIX = "rev:";

// Selection id for a version (revision) row. The prefix keeps revision ids
// disjoint from generation/job ids inside the single selection namespace.
export function revisionSelectionId(revisionId: string): string {
  return `${REVISION_PREFIX}${revisionId}`;
}

export function isRevisionSelection(selectionId: string): boolean {
  return selectionId.startsWith(REVISION_PREFIX);
}

export function revisionIdFromSelection(selectionId: string): string {
  return selectionId.slice(REVISION_PREFIX.length);
}

export type SelectionDetail<
  G extends GenLike = GenLike,
  R extends RevisionLike = RevisionLike
> =
  | {
      kind: "result";
      entry: TimelineResult;
      generation: G | undefined;
    }
  | { kind: "action"; entry: TimelineAction }
  | {
      kind: "revision";
      revision: R;
      isCurrent: boolean;
      /** Newest generation produced for this revision, if any. */
      generation: G | undefined;
    };

// Newest generation (by created_at) whose design revision matches, if any.
export function newestGenerationForRevision<G extends GenLike>(
  generations: G[],
  revisionId: string
): G | undefined {
  let newest: G | undefined;
  for (const generation of generations) {
    if (generation.design_revision_id !== revisionId) continue;
    if (!newest || newest.created_at < generation.created_at) newest = generation;
  }
  return newest;
}

// Resolve a selection id against the latest timeline/revisions/generations.
// Returns null when the id is unknown (e.g. data no longer contains it).
// Generic over the caller's generation/revision types so the resolved detail
// keeps their full shape (e.g. api.Generation keeps `camera_id`).
export function resolveSelection<
  G extends GenLike = GenLike,
  R extends RevisionLike = RevisionLike
>(input: {
  selectionId: string | null;
  entries: TimelineEntry[];
  revisions: R[];
  generations: G[];
  currentRevisionId: string | null;
}): SelectionDetail<G, R> | null {
  const { selectionId, entries, revisions, generations, currentRevisionId } = input;
  if (!selectionId) return null;

  if (isRevisionSelection(selectionId)) {
    const revisionId = revisionIdFromSelection(selectionId);
    const revision = revisions.find(
      (item) => item.revision_id === revisionId
    );
    if (!revision) return null;
    return {
      kind: "revision",
      revision,
      isCurrent: revisionId === currentRevisionId,
      generation: newestGenerationForRevision(generations, revisionId)
    };
  }

  const entry = entries.find((item) => item.id === selectionId);
  if (!entry) return null;
  if (entry.kind === "action") return { kind: "action", entry };
  return {
    kind: "result",
    entry,
    generation: generations.find((item) => item.id === entry.id)
  };
}

// Fallback selection when nothing (or something unknown) is selected:
// the newest timeline entry, else the newest revision, else nothing.
export function defaultSelectionId(
  entries: TimelineEntry[],
  revisions: RevisionLike[]
): string | null {
  // buildTimelineEntries sorts newest-first; keep it order-agnostic anyway.
  let newestEntry: TimelineEntry | undefined;
  for (const entry of entries) {
    if (!newestEntry || newestEntry.createdAt < entry.createdAt) newestEntry = entry;
  }
  if (newestEntry) return newestEntry.id;

  let newestRevision: RevisionLike | undefined;
  for (const revision of revisions) {
    if (!newestRevision || newestRevision.created_at < revision.created_at) {
      newestRevision = revision;
    }
  }
  return newestRevision ? revisionSelectionId(newestRevision.revision_id) : null;
}
