import { useState } from "react";
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
  workersOnline,
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
  workersOnline: boolean;
  onChanged: () => Promise<void>;
  onUpload: (file: File | null, role: AssetRole) => void;
  uploadProgress: number | null;
  instruction: string;
  onInstructionChange: (value: string) => void;
  onSubmitInstruction: (event: FormEvent) => void;
  onAnalyzeStyle: () => void;
  onNavigate: (page: PageId) => void;
}) {
  const [mode, setMode] = useState<"3d" | "photo">("3d");

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
      {!workersOnline && (
        <p className="worker-notice muted">
          Генерация временно недоступна: GPU-worker офлайн. Новые задачи будут ждать
          восстановления воркера.
        </p>
      )}

      <section className="page-upload-row" aria-label="Загрузка референса">
        <div className="mode-switch" role="group" aria-label="Режим редактирования">
          <button
            type="button"
            className={mode === "3d" ? "active" : ""}
            aria-pressed={mode === "3d"}
            onClick={() => setMode("3d")}
          >
            3D
          </button>
          <button
            type="button"
            className={mode === "photo" ? "active" : ""}
            aria-pressed={mode === "photo"}
            onClick={() => setMode("photo")}
          >
            Фото
          </button>
        </div>
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

      {mode === "3d" ? (
        <>
          <section className="canvas-panel">
            <SceneViewer
              scene={revision.scene}
              projectId={projectId}
              onChanged={onChanged}
            />
          </section>

          <form className="instruction-bar" onSubmit={onSubmitInstruction}>
            <input
              value={instruction}
              onChange={(event) => onInstructionChange(event.target.value)}
              placeholder="Например: сделай диван бежевым и убери стол"
              disabled={!revision}
            />
            <button disabled={!revision || !instruction.trim()}>Применить через AI</button>
          </form>
        </>
      ) : (
        <PhotoEditPanel
          projectId={projectId}
          revision={revision}
          assets={assets}
          jobs={jobs}
          generations={generations}
          onChanged={onChanged}
        />
      )}

      <details className="design-advanced">
        <summary>Дополнительно</summary>
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
      </details>
    </>
  );
}
