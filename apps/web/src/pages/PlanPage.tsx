import type { Asset, AssetRole, Job } from "../api";
import { PlanEditor } from "../PlanEditor";

// Plan page: apartment-plan upload (fixed role) + the plan editor. Only the
// controls relevant to plan work appear here (#101).
export function PlanPage({
  projectId,
  assets,
  jobs,
  onChanged,
  onUpload,
  uploadProgress
}: {
  projectId: string;
  assets: Asset[];
  jobs: Job[];
  onChanged: () => Promise<void>;
  onUpload: (file: File | null, role: AssetRole) => void;
  uploadProgress: number | null;
}) {
  return (
    <>
      <section className="page-upload-row" aria-label="Загрузка плана квартиры">
        <label className="upload">
          Загрузить план квартиры
          <input
            type="file"
            onChange={(event) => {
              onUpload(event.target.files?.[0] ?? null, "apartment");
              event.target.value = "";
            }}
          />
        </label>
        {uploadProgress !== null && (
          <div className="upload-progress" aria-label="Upload progress">
            <div style={{ width: `${uploadProgress}%` }} />
            <span>{uploadProgress}%</span>
          </div>
        )}
      </section>

      <PlanEditor
        projectId={projectId}
        assets={assets}
        jobs={jobs}
        onChanged={onChanged}
      />
    </>
  );
}
