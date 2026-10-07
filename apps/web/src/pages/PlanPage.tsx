import type { Asset, AssetRole, Job } from "../api";
import { PlanEditor } from "../PlanEditor";

// Plan page: apartment-plan upload (fixed role) + the plan editor. Only the
// controls relevant to plan work appear here (#101). Since #153 the upload
// control and the plan-source chips live inside the editor's compact footer
// strip instead of a page-level upload row.
export function PlanPage({
  projectId,
  assets,
  jobs,
  workersOnline,
  onChanged,
  onUpload,
  uploadProgress
}: {
  projectId: string;
  assets: Asset[];
  jobs: Job[];
  workersOnline: boolean;
  onChanged: () => Promise<void>;
  onUpload: (file: File | null, role: AssetRole) => void;
  uploadProgress: number | null;
}) {
  return (
    <>
      {!workersOnline && (
        <p className="worker-notice muted">
          Генерация временно недоступна: GPU-worker офлайн. Новые задачи будут ждать
          восстановления воркера.
        </p>
      )}

      <PlanEditor
        projectId={projectId}
        assets={assets}
        jobs={jobs}
        onChanged={onChanged}
        onUpload={onUpload}
        uploadProgress={uploadProgress}
      />
    </>
  );
}
