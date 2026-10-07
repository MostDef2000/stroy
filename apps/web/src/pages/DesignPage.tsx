import { useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, type Asset, type AssetRole, type Generation, type Job, type SceneDocument, type SceneRevision, type ValidationReport } from "../api";
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
import {
  buildSetIntentCommand,
  buildSetLocksCommand,
  entityIntent,
  intentActionBlocked,
  INTENT_LABELS,
  type DesignIntent,
  type EntityLocksPatch
} from "../sceneIntent";
import { ruleLabel, summaryLine, topResults } from "../sceneValidation";
import { apiErrorText, uniqueId } from "../twinDesign";
import type { PageId } from "../nav";

const KIND_LABELS: Record<string, string> = {
  room: "Комната",
  floor: "Пол",
  wall: "Стена",
  furniture: "Мебель"
};

// Rail display order for the intent toggles (keep first: it is the neutral
// "leave as is" choice; clear is rendered separately when an intent is set).
const INTENT_ORDER: readonly DesignIntent[] = ["keep", "replace", "remove"];

function isConflict(reason: unknown): boolean {
  const message = reason instanceof Error ? reason.message : String(reason);
  return /^409:/.test(message);
}

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
  // R2 intents/locks: one command channel shared by the intent toggles and
  // the locks checkboxes (same freshness rule and error surface as set_state).
  const [intentBusy, setIntentBusy] = useState(false);
  const [intentError, setIntentError] = useState("");
  const [intentConflict, setIntentConflict] = useState(false);
  // R2 design check: the report is fetched only on demand (manual button);
  // drags and commits never re-run it.
  const [checkReport, setCheckReport] = useState<ValidationReport | null>(null);
  const [checkBusy, setCheckBusy] = useState(false);
  const [checkError, setCheckError] = useState("");
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

  // R2 set_intent / set_locks: the builders return the exact command core
  // (operation/target_id/parameters); this dispatcher wraps it into the full
  // DesignCommand envelope — schema_version, fresh base_revision_id, user
  // origin — mirroring buildSetStateCommand's field-for-field shape.
  async function applyEntityCommand(command: {
    operation: "set_intent" | "set_locks";
    target_id: string;
    parameters: Record<string, unknown>;
  }) {
    if (intentBusy) return;
    setIntentBusy(true);
    setIntentError("");
    setIntentConflict(false);
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setIntentError("Сцена ещё не инициализирована.");
        return;
      }
      await api.applySceneCommand(projectId, {
        schema_version: "0.1.0",
        command_id: uniqueId(),
        base_revision_id: fresh.revision_id,
        operation: command.operation,
        target_id: command.target_id,
        parameters: command.parameters,
        reference_asset_ids: [],
        origin: "user",
        request_text: null
      });
      await onChanged();
    } catch (reason) {
      setIntentError(apiErrorText(reason));
      setIntentConflict(isConflict(reason));
    } finally {
      setIntentBusy(false);
    }
  }

  function applyIntent(intent: DesignIntent | null) {
    if (!selectedEntity) return;
    void applyEntityCommand(buildSetIntentCommand(selectedEntity.id, intent));
  }

  // Partial lock merge: send only the single toggled key, never the whole
  // locks object (the backend merges the patch into the entity's locks).
  function applyLock(
    key: keyof EntityLocksPatch,
    value: boolean
  ) {
    if (!selectedEntity) return;
    const patch: EntityLocksPatch = {};
    patch[key] = value;
    void applyEntityCommand(buildSetLocksCommand(selectedEntity.id, patch));
  }

  // R2 design check: manual run against the current latest revision.
  async function runDesignCheck() {
    if (checkBusy) return;
    setCheckBusy(true);
    setCheckError("");
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setCheckError("Сцена ещё не инициализирована.");
        return;
      }
      setCheckReport(
        await api.validateScene(projectId, { scene_revision_id: fresh.revision_id })
      );
    } catch (reason) {
      setCheckError(apiErrorText(reason));
    } finally {
      setCheckBusy(false);
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

  // Compact rail card list: top-3 results in severity order (computed once).
  const checkTopResults = checkReport ? topResults(checkReport) : [];

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
                  {/* R2: design intent for the selected object — the UI
                      mirror of the backend set_intent command and its guard
                      matrix. Structural entities (Слой: Structure) may only
                      be kept. */}
                  <div className="design-intent-row" role="group" aria-label="Намерение по объекту">
                    {INTENT_ORDER.map((intent) => {
                      const active = entityIntent(selectedEntity) === intent;
                      return (
                        <button
                          key={intent}
                          type="button"
                          className={"design-intent-toggle" + (active ? " active" : "")}
                          aria-pressed={active}
                          disabled={
                            intentBusy ||
                            (intent !== "keep" &&
                              intentActionBlocked(selectedEntity, intent))
                          }
                          onClick={() => void applyIntent(intent)}
                        >
                          {INTENT_LABELS[intent]}
                        </button>
                      );
                    })}
                  </div>
                  {entityIntent(selectedEntity) !== null && (
                    <button
                      type="button"
                      className="secondary design-intent-clear"
                      disabled={intentBusy}
                      onClick={() => void applyIntent(null)}
                    >
                      Сбросить
                    </button>
                  )}
                  {intentError && <div className="error">{intentError}</div>}
                  {intentConflict && (
                    <p className="hint design-intent-conflict">
                      Сервер отклонил изменение (409) — проверьте блокировки и
                      запустите «Проверку дизайна».
                    </p>
                  )}
                  {/* R2: per-entity locks via set_locks; each checkbox sends
                      only its own key (partial merge on the backend). */}
                  <details className="design-intent-locks">
                    <summary>Блокировки</summary>
                    <label className="design-intent-lock">
                      <input
                        type="checkbox"
                        checked={selectedEntity.locks?.existence ?? false}
                        disabled={intentBusy}
                        onChange={(event) =>
                          void applyLock("existence", event.target.checked)
                        }
                      />
                      Должен остаться
                    </label>
                    <label className="design-intent-lock">
                      <input
                        type="checkbox"
                        checked={selectedEntity.locks?.transform ?? false}
                        disabled={intentBusy}
                        onChange={(event) =>
                          void applyLock("transform", event.target.checked)
                        }
                      />
                      Положение
                    </label>
                    <label className="design-intent-lock">
                      <input
                        type="checkbox"
                        checked={selectedEntity.locks?.geometry ?? false}
                        disabled={intentBusy}
                        onChange={(event) =>
                          void applyLock("geometry", event.target.checked)
                        }
                      />
                      Геометрия/размеры
                    </label>
                    <label className="design-intent-lock">
                      <input
                        type="checkbox"
                        checked={selectedEntity.locks?.material ?? false}
                        disabled={intentBusy}
                        onChange={(event) =>
                          void applyLock("material", event.target.checked)
                        }
                      />
                      Материал/цвет
                    </label>
                  </details>
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

              {/* R2 design check: compact scene-level card in the rail (never
                  on canvas). Manual run only — drags and commits never
                  refresh the report. */}
              <details className="design-check">
                <summary>
                  <span className="design-check-title">Проверка дизайна</span>
                  {checkReport && (
                    <span className="design-check-summary">
                      {summaryLine(checkReport)}
                    </span>
                  )}
                  {checkReport && (
                    <span className="design-check-counts">
                      {(["error", "warning", "info"] as const).map((severity) => (
                        <span
                          key={severity}
                          className={`design-check-count design-check-count--${severity}`}
                        >
                          {checkReport.summary[severity]}
                        </span>
                      ))}
                    </span>
                  )}
                </summary>
                {checkTopResults.length > 0 && (
                  <ul className="design-check-list">
                    {checkTopResults.map((result, index) => (
                      <li
                        key={`${result.rule_id}-${index}`}
                        className={`design-check-item design-check-item--${result.severity}`}
                      >
                        <span className="design-check-rule">
                          {ruleLabel(result.rule_id)}
                        </span>
                        {(result.measured_mm != null ||
                          result.expected_min_mm != null) && (
                          <span className="design-check-mm">
                            {result.measured_mm != null
                              ? `${result.measured_mm} мм`
                              : "—"}
                            {result.expected_min_mm != null
                              ? ` · мин. ${result.expected_min_mm} мм`
                              : ""}
                          </span>
                        )}
                        {result.entity_ids.length > 0 && (
                          <span className="design-check-ids">
                            {result.entity_ids.map((id) => (
                              <code key={id} className="design-check-id">
                                {id}
                              </code>
                            ))}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {checkError && <div className="error">{checkError}</div>}
                <button
                  type="button"
                  className="secondary design-check-run"
                  disabled={checkBusy}
                  onClick={() => void runDesignCheck()}
                >
                  {checkBusy ? "Проверяем…" : "Запустить проверку"}
                </button>
              </details>
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
