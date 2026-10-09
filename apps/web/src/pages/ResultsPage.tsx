import type { Generation, Job, RevisionSummary } from "../api";
import { DesignPanel } from "../DesignPanel";

// Results page: horizontal master-detail workspace (#152) — the history list
// on the left (internally scrollable), the selected entry's preview, details,
// before/after and restore/re-render actions on the right (#101 semantics).
export function ResultsPage({
  projectId,
  revisions,
  jobs,
  generations,
  currentRevisionId,
  cameraId,
  onRestore,
  onRerender,
  onShareWithDesigner
}: {
  projectId: string;
  revisions: RevisionSummary[];
  jobs: Job[];
  generations: Generation[];
  currentRevisionId: string | null;
  cameraId: string | null;
  onRestore: (revisionId: string) => Promise<void>;
  onRerender: (revisionId: string) => Promise<void>;
  /** R8 (#198): opens the designer brief for a variant (App-level wiring). */
  onShareWithDesigner: (variantId: string) => void;
}) {
  if (revisions.length === 0) {
    return (
      <section className="empty-state">
        <h2>Результатов пока нет</h2>
        <p>Создайте сцену и внесите изменения — история ревизий появится здесь.</p>
      </section>
    );
  }

  return (
    <DesignPanel
      projectId={projectId}
      revisions={revisions}
      jobs={jobs}
      generations={generations}
      currentRevisionId={currentRevisionId}
      cameraId={cameraId}
      onRestore={onRestore}
      onRerender={onRerender}
      onShareWithDesigner={onShareWithDesigner}
    />
  );
}
