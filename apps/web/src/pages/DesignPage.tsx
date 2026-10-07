import { useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, type Asset, type AssetRole, type Generation, type Job, type SceneDocument, type SceneRevision } from "../api";
import { AttachmentSection } from "../AttachmentSection";
import { LayerBadge } from "../LayerBadge";
import { computeCameraReadiness } from "../cameraReadiness";
import { CameraPanel } from "../CameraPanel";
import { PhotoEditPanel } from "../PhotoEditPanel";
import { ReplacementPanel } from "../ReplacementPanel";
import { SceneViewer } from "../SceneViewer";
import { TwinDesignPanel } from "../TwinDesignPanel";
import {
  buildSetStateCommand,
  ENTITY_STATES,
  ENTITY_STATE_LABELS,
  entityState,
  filterEntitiesByState,
  type EntityState
} from "../sceneLayers";
import { apiErrorText, uniqueId } from "../twinDesign";
import type { PageId } from "../nav";

const KIND_LABELS: Record<string, string> = {
  room: "Комната",
  floor: "Пол",
  wall: "Стена",
  furniture: "Мебель"
};

// The structural shell (floors/walls/rooms) always reaches SceneViewer even
// when its layer is hidden: planOverview() frames the overview camera from
// wall entities and the shell is the context every layer sits in.
function isShellEntity(entity: SceneDocument["entities"][number]): boolean {
  return entity.kind === "floor" || entity.kind === "wall" || entity.kind === "room";
}

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
  // R1 layers: which entity states the 3D view shows. All visible by default
  // (byte-equal scene passes through), at least one layer stays on.
  const [visibleStates, setVisibleStates] = useState<EntityState[]>([...ENTITY_STATES]);
  const [stateBusy, setStateBusy] = useState(false);
  const [stateError, setStateError] = useState("");
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

  // R1 layer filter: hide the entities of the unchecked layers before the
  // scene reaches SceneViewer. With every layer on, the original scene object
  // passes through unchanged. Cameras are never filtered, so the camera
  // toolbar and the calibrated-pose overlay are unaffected by the toggles.
  const sceneForViewer = useMemo(() => {
    const scene = revision.scene;
    if (visibleStates.length === ENTITY_STATES.length) return scene;
    const shell = scene.entities.filter(isShellEntity);
    const content = filterEntitiesByState(
      { ...scene, entities: scene.entities.filter((entity) => !isShellEntity(entity)) },
      visibleStates
    );
    return { ...scene, entities: [...shell, ...content.entities] };
  }, [revision.scene, visibleStates]);

  function toggleState(state: EntityState) {
    setVisibleStates((current) => {
      if (current.includes(state)) {
        // Keep at least one layer visible so the viewer never goes blank.
        return current.length > 1
          ? current.filter((item) => item !== state)
          : current;
      }
      return ENTITY_STATES.filter(
        (item) => item === state || current.includes(item)
      );
    });
  }

  // R1 set_state: same freshness rule as the other scene-command senders —
  // the base_revision_id must equal the current latest revision.
  async function applyState(entityId: string, state: EntityState) {
    if (stateBusy) return;
    setStateBusy(true);
    setStateError("");
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setStateError("Сцена ещё не инициализирована.");
        return;
      }
      await api.applySceneCommand(
        projectId,
        buildSetStateCommand({
          commandId: uniqueId(),
          baseRevisionId: fresh.revision_id,
          targetId: entityId,
          state
        })
      );
      await onChanged();
    } catch (reason) {
      setStateError(apiErrorText(reason));
    } finally {
      setStateBusy(false);
    }
  }

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
          {/* R1 layer toggles: filter the entities SceneViewer renders. The
              structural shell (floors/walls/rooms) always stays visible. */}
          <div className="mode-switch" role="group" aria-label="Слои сцены">
            {ENTITY_STATES.map((state) => {
              const active = visibleStates.includes(state);
              return (
                <button
                  key={state}
                  type="button"
                  className={active ? "active" : ""}
                  aria-pressed={active}
                  onClick={() => toggleState(state)}
                >
                  {ENTITY_STATE_LABELS[state]}
                </button>
              );
            })}
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
                scene={sceneForViewer}
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
                  <p className="hint">
                    <LayerBadge state={entityState(selectedEntity)} />{" "}
                    {KIND_LABELS[selectedEntity.kind] ?? selectedEntity.kind}
                  </p>
                  {/* R1: move the selected object between the as-is /
                      structure / design layers via the set_state command. */}
                  <label className="design-state-select">
                    Слой
                    <select
                      value={entityState(selectedEntity)}
                      disabled={stateBusy}
                      onChange={(event) =>
                        void applyState(selectedEntity.id, event.target.value as EntityState)
                      }
                    >
                      {ENTITY_STATES.map((state) => (
                        <option key={state} value={state}>
                          {ENTITY_STATE_LABELS[state]}
                        </option>
                      ))}
                    </select>
                  </label>
                  {stateError && <div className="error">{stateError}</div>}
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
                  {/* R1: attachments pinned to the selected entity. */}
                  <AttachmentSection
                    projectId={projectId}
                    targetType="entity"
                    targetId={selectedEntity.id}
                    scene={revision.scene}
                  />
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
