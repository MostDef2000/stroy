import type { Generation, Job, RevisionSummary } from "../api";
import { DesignPanel } from "../DesignPanel";

// Results page: revision history / timeline / restore / re-render (#101).
export function ResultsPage({
  revisions,
  jobs,
  generations,
  currentRevisionId,
  cameraId,
  onRestore,
  onRerender
}: {
  revisions: RevisionSummary[];
  jobs: Job[];
  generations: Generation[];
  currentRevisionId: string | null;
  cameraId: string | null;
  onRestore: (revisionId: string) => Promise<void>;
  onRerender: (revisionId: string) => Promise<void>;
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
    <section className="dashboard-grid">
      <DesignPanel
        revisions={revisions}
        jobs={jobs}
        generations={generations}
        currentRevisionId={currentRevisionId}
        cameraId={cameraId}
        onRestore={onRestore}
        onRerender={onRerender}
      />
    </section>
  );
}
