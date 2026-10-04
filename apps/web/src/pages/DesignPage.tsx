import type { FormEvent } from "react";
import type { Asset, AssetRole, Generation, Job, SceneRevision } from "../api";
import { CameraPanel } from "../CameraPanel";
import { PhotoEditPanel } from "../PhotoEditPanel";
import { ReplacementPanel } from "../ReplacementPanel";
import { SceneViewer } from "../SceneViewer";
import { TwinDesignPanel } from "../TwinDesignPanel";
import type { PageId } from "../nav";

// Design page: scene manipulation, photo-first editing, calibration, twin
// design, replacement and style analysis. Requires a scene revision (#101).
export function DesignPage({
  projectId,
  revision,
  assets,
  jobs,
  generations,
  onChanged,
  onUpload,
  uploadProgress,
  instruction,
  onInstructionChange,
  onSubmitInstruction,
  onAnalyzeStyle,
  onNavigate
}: {
  projectId: string;
  revision: SceneRevision | null;
  assets: Asset[];
  jobs: Job[];
  generations: Generation[];
  onChanged: () => Promise<void>;
  onUpload: (file: File | null, role: AssetRole) => void;
  uploadProgress: number | null;
  instruction: string;
  onInstructionChange: (value: string) => void;
  onSubmitInstruction: (event: FormEvent) => void;
  onAnalyzeStyle: () => void;
  onNavigate: (page: PageId) => void;
}) {
  if (!revision) {
    return (
      <section className="empty-state">
        <h2>Сцена ещё не создана</h2>
        <p>Сцена инициализируется автоматически. Создайте комнату на странице «Обзор».</p>
        <button className="secondary" onClick={() => onNavigate("overview")}>
          Вернуться к обзору
        </button>
      </section>
    );
  }

  const referenceImages = assets.filter(
    (asset) => asset.role === "reference" && asset.media_type.startsWith("image/")
  ).length;

  return (
    <>
      <section className="page-upload-row" aria-label="Загрузка референса">
        <label className="upload">
          Загрузить референс
          <input
            type="file"
            onChange={(event) => {
              onUpload(event.target.files?.[0] ?? null, "reference");
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
        <div className="style-analyze-group">
          <button onClick={onAnalyzeStyle}>Анализ стиля</button>
          <p className="hint" id="style-hint">
            Анализ стиля требует 3–5 изображений с ролью «reference». Сейчас: {referenceImages}.
          </p>
        </div>
      </section>

      <section className="canvas-panel">
        <SceneViewer
          scene={revision.scene}
          projectId={projectId}
          onChanged={onChanged}
        />
      </section>

      <PhotoEditPanel
        projectId={projectId}
        revision={revision}
        assets={assets}
        jobs={jobs}
        generations={generations}
        onChanged={onChanged}
      />

      <form className="instruction-bar" onSubmit={onSubmitInstruction}>
        <input
          value={instruction}
          onChange={(event) => onInstructionChange(event.target.value)}
          placeholder="Например: сделай диван бежевым и убери стол"
          disabled={!revision}
        />
        <button disabled={!revision || !instruction.trim()}>Применить через AI</button>
      </form>

      <section className="dashboard-grid">
        <CameraPanel
          projectId={projectId}
          revision={revision}
          assets={assets}
          onChanged={onChanged}
        />

        <TwinDesignPanel
          projectId={projectId}
          revision={revision}
          jobs={jobs}
          onChanged={onChanged}
        />

        <ReplacementPanel
          projectId={projectId}
          revision={revision}
          assets={assets}
          onChanged={onChanged}
        />
      </section>
    </>
  );
}
