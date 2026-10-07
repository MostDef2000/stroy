import { useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Asset, AssetRole, Generation, Job, SceneRevision } from "../api";
import { computeCameraReadiness } from "../cameraReadiness";
import { CameraPanel } from "../CameraPanel";
import { PhotoEditPanel } from "../PhotoEditPanel";
import { ReplacementPanel } from "../ReplacementPanel";
import { SceneViewer } from "../SceneViewer";
import { TwinDesignPanel } from "../TwinDesignPanel";
import type { PageId } from "../nav";

const KIND_LABELS: Record<string, string> = {
  room: "Комната",
  floor: "Пол",
  wall: "Стена",
  furniture: "Мебель"
};

// Design page (#154): a persistent design-tool workspace — command bar on top
// (3D/Фото mode switch + reference/analyze actions), a dominant center surface
// shared by both modes (SceneViewer in 3D, the photo stage in Фото), a
// contextual right rail, the collapsed advanced strip and the persistent
// bottom AI composer. Scene manipulation, photo-first editing, calibration,
// twin design, replacement and style analysis. Requires a scene revision (#101).
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
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [replaceTargetId, setReplaceTargetId] = useState<string | null>(null);
  const advancedRef = useRef<HTMLDetailsElement | null>(null);
  const canvasRef = useRef<HTMLElement | null>(null);

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

  const readiness = computeCameraReadiness(revision.scene.cameras);

  // Contextual rail (#154): the entity selected in the 3D scene, with its
  // actions one glance away instead of far below the canvas.
  const selectedEntity = selectedEntityId
    ? revision.scene.entities.find((entity) => entity.id === selectedEntityId) ?? null
    : null;

  function openAdvanced() {
    const node = advancedRef.current;
    if (!node) return;
    node.open = true;
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function closeAdvanced() {
    const node = advancedRef.current;
    if (node) node.open = false;
    canvasRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <>
      {!workersOnline && (
        <p className="worker-notice muted">
          Генерация временно недоступна: GPU-worker офлайн. Новые задачи будут ждать
          восстановления воркера.
        </p>
      )}

      <div className="design-workspace">
        {/* Command bar (#154): mode switch + the reference/analyze actions. */}
        <section className="page-upload-row design-command-bar" aria-label="Панель управления дизайном">
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

        <div className="design-main">
          {/* In 3D mode the SceneViewer owns the shared center surface. It may
              remount on mode switches — same semantics as the base layout. */}
          {mode === "3d" && (
            <section className="canvas-panel" ref={canvasRef}>
              <SceneViewer
                scene={revision.scene}
                projectId={projectId}
                onChanged={onChanged}
                selectedId={selectedEntityId}
                onSelectEntity={setSelectedEntityId}
                onRequestReplace={(id) => {
                  setReplaceTargetId(id);
                  openAdvanced();
                }}
              />
            </section>
          )}

          {/* PhotoEditPanel stays mounted across mode switches so its local edit
              state (photo, prompt, region, pending job, iteration, base override)
              survives toggling to 3D and back. Hidden via CSS in 3D mode. In
              photo mode it spans the whole main row and its internal CSS grid
              puts the stage in the shared center surface position and the
              controls into the rail position. */}
          <div className={mode === "photo" ? "design-pane" : "design-pane pane-hidden"}>
            <PhotoEditPanel
              projectId={projectId}
              revision={revision}
              assets={assets}
              jobs={jobs}
              generations={generations}
              onChanged={onChanged}
            />
          </div>

          {/* Contextual rail (#154), 3D mode only — the photo mode fills this
              region with the photo controls inside the design pane. */}
          {mode === "3d" && (
            <aside className="design-rail" aria-label="Контекстные инструменты сцены">
              <div className={"camera-readiness " + readiness.state}>
                <span>
                  {readiness.label}
                  {readiness.detail ? ` · ${readiness.detail}` : ""}
                </span>
                <button type="button" className="secondary" onClick={openAdvanced}>
                  Настроить камеру
                </button>
              </div>

              {selectedEntity ? (
                <section className="design-selected" aria-label="Выбранный объект">
                  <h3>Выбранный объект</h3>
                  <p className="design-selected-name">
                    {selectedEntity.display_name ?? selectedEntity.id}
                  </p>
                  <p className="hint">{KIND_LABELS[selectedEntity.kind] ?? selectedEntity.kind}</p>
                  <div className="design-selected-actions">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        setReplaceTargetId(selectedEntity.id);
                        openAdvanced();
                      }}
                    >
                      Заменить по референсу
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setSelectedEntityId(null)}
                    >
                      Снять выделение
                    </button>
                  </div>
                  <p className="hint">Перетащите объект в сцене, чтобы разместить.</p>
                </section>
              ) : (
                <p className="hint">
                  Выберите объект в сцене — действия появятся здесь.
                </p>
              )}
            </aside>
          )}
        </div>

        {/* Advanced strip (#154): collapsed by default in both modes, so the
            camera/twin/replacement panels keep their state across mode
            switches (they are never unmounted). */}
        <details className="design-advanced" ref={advancedRef}>
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
              onEntityAdded={(id) => {
                setSelectedEntityId(id);
                closeAdvanced();
              }}
            />

            <ReplacementPanel
              projectId={projectId}
              revision={revision}
              assets={assets}
              onChanged={onChanged}
              initialTargetId={replaceTargetId}
            />
          </section>
        </details>

        {/* Persistent composer (#154): reachable in both modes; instruction
            state stays app-level (App.tsx props are unchanged). */}
        <form
          className="instruction-bar design-composer"
          aria-label="AI-инструкция к сцене"
          onSubmit={onSubmitInstruction}
        >
          <input
            value={instruction}
            onChange={(event) => onInstructionChange(event.target.value)}
            placeholder="Например: сделай диван бежевым и убери стол"
            aria-label="Описание изменения через AI"
            disabled={!revision}
          />
          <button disabled={!revision || !instruction.trim()}>Применить через AI</button>
        </form>
      </div>
    </>
  );
}
