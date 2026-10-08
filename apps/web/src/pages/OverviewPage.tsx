import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Attachment, type SceneDocument } from "../api";
import { countEntitiesByState, ENTITY_STATES, ENTITY_STATE_LABELS } from "../sceneLayers";
import { isDanglingTarget, isTaskOverdue } from "../attachments";
import { apiErrorText } from "../twinDesign";
import type { PageId } from "../nav";
import { computeProjectReadiness, type ReadinessInput, type ReadinessState } from "../overview";
import {
  buildSetupCopy,
  ctaFromSetup,
  resolveSetupSteps,
  setupStepLinkLabel,
  setupStepPage,
  type SetupStatus
} from "../setup";

const STATE_LABEL: Record<ReadinessState, string> = {
  ok: "Готово",
  pending: "Ожидает",
  attention: "Требует внимания"
};

const ATTACHMENT_KIND_LABELS: Record<Attachment["kind"], string> = {
  photo: "Фото",
  note: "Заметка",
  file: "Файл",
  task: "Задача"
};

// Overview: what is ready, what is missing, where to go next. The golden-room
// demo-scene action stays here as an EXPLICIT secondary action with the
// approximation badge — R6 (#183) removed the automatic scene initialization,
// so fresh projects get plan-first guidance instead.
// Everything renders inside .overview-page: an explicit page wrapper (#151)
// that keeps the workspace's top-aligned row model scoped to this page —
// without it, the stretched .workspace grid distributes the free viewport
// height across its implicit rows and the readiness cards grow tall.
//
// R1 additions (#169 card language, kept quiet): a compact scene-layer count
// card (Как есть N · Конструктив N · Дизайн N — RU labels per #188) and the
// project-level «Заметки и задачи» card with the done toggle and the
// overdue/dangling marks.
//
// R6 (#183): the «Настройка проекта» section is the guided six-step checklist
// driven by GET /projects/{id}/setup. It owns the SINGLE primary CTA
// (setup.next_action until ready_for_design, then the design hand-off); the
// readiness cards below stay secondary/quiet while the setup CTA exists.
export function OverviewPage({
  readiness,
  projectId,
  scene,
  hasProject,
  hasScene,
  setup,
  preparingScene,
  onCreateDemoScene,
  onNavigate
}: {
  readiness: ReadinessInput;
  projectId: string;
  scene: SceneDocument | null;
  hasProject: boolean;
  hasScene: boolean;
  setup: SetupStatus | null;
  preparingScene: boolean;
  onCreateDemoScene: () => void;
  onNavigate: (page: PageId) => void;
}) {
  // Project attachments are fetched in-page (the panel pattern — e.g.
  // TwinDesignPanel fetches its own assets/renders). The list refetches after
  // each done-toggle; other tabs' edits appear on the next App poll reload.
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachmentsError, setAttachmentsError] = useState("");
  const [taskBusy, setTaskBusy] = useState(false);

  const loadAttachments = useCallback(async () => {
    try {
      const list = await api.listAttachments(projectId);
      setAttachments(list);
      setAttachmentsError("");
    } catch (reason) {
      setAttachmentsError(apiErrorText(reason));
    }
  }, [projectId]);

  useEffect(() => {
    void loadAttachments();
  }, [loadAttachments]);

  // Layer counts (R1): null while the scene has not loaded, so the card is
  // skipped instead of rendering an empty counts line.
  const layerCounts = useMemo(() => (scene ? countEntitiesByState(scene) : null), [scene]);

  async function toggleTaskDone(attachment: Attachment) {
    if (taskBusy) return;
    setTaskBusy(true);
    try {
      await api.patchAttachment(projectId, attachment.id, { done: !attachment.done });
      await loadAttachments();
    } catch (reason) {
      setAttachmentsError(apiErrorText(reason));
    } finally {
      setTaskBusy(false);
    }
  }

  if (!hasProject) {
    return (
      <div className="overview-page">
        <section className="empty-state">
          <h2>Проект не выбран</h2>
          <p>Создайте проект в левой панели или выберите существующий.</p>
        </section>
      </div>
    );
  }

  const items = computeProjectReadiness(readiness);

  // ONE primary next action (#188 §2): while the setup status is known, the
  // setup CTA is THE primary action and every readiness card keeps a quiet
  // secondary «Перейти». Without a setup status (route not answering yet) the
  // pre-R6 fallback applies: the first non-ok readiness item gets the primary.
  const setupOwnsCta = setup !== null;
  const primaryId = items.find((item) => item.state !== "ok")?.id ?? null;

  // R6 setup section inputs (null while the setup route has not answered).
  const steps = setup ? resolveSetupSteps(setup) : [];
  const copy = setup ? buildSetupCopy(setup) : { known: [], confirm: [] };
  const cta = setup ? ctaFromSetup(setup) : null;

  return (
    <div className="overview-page">
      {!hasScene && (
        <section className="panel start-scene-panel">
          <h2>Начало работы</h2>
          <p className="muted">
            Начните с загрузки плана квартиры на странице «План» — 3D-сцена появится из
            проверенного плана. Демо-сцену можно создать и без плана.
          </p>
          <div className="pl-actions">
            <button type="button" className="secondary" onClick={onCreateDemoScene}>
              Создать демо-сцену
            </button>
            <span className="approx-badge">Демо-сцена, приблизительно</span>
          </div>
          {preparingScene && <p className="muted">Готовим рабочее место…</p>}
        </section>
      )}

      {setup && (
        <section className="panel setup-panel" aria-label="Настройка проекта">
          <div className="panel-heading">
            <h2>Настройка проекта</h2>
          </div>

          <ol className="setup-steps">
            {steps.map((step) => (
              <li key={step.id} className={step.state} aria-current={step.state === "current" ? "step" : undefined}>
                <span className="setup-step-label">{step.label}</span>
                {step.state === "done" && (
                  <>
                    <span className="setup-step-mark" aria-label="Готово">
                      ✓
                    </span>
                    <button
                      type="button"
                      className="secondary setup-step-link"
                      onClick={() => onNavigate(setupStepPage(step.id))}
                    >
                      {setupStepLinkLabel(step.id)}
                    </button>
                  </>
                )}
              </li>
            ))}
          </ol>

          <div className="setup-columns">
            <div className="setup-known">
              <h3>Что STROY знает</h3>
              <ul>
                {copy.known.map((line, index) => (
                  <li key={index}>{line}</li>
                ))}
              </ul>
            </div>
            {copy.confirm.length > 0 && (
              <div className="setup-confirm">
                <h3>Что нужно подтвердить</h3>
                <ul>
                  {copy.confirm.map((line, index) => (
                    <li key={index}>{line}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {cta && (
            <div className="setup-cta">
              <button
                type="button"
                disabled={cta.disabled}
                onClick={() => onNavigate(cta.page)}
              >
                {cta.label}
              </button>
              {cta.disabled && cta.reason && <p className="muted">{cta.reason}</p>}
            </div>
          )}
        </section>
      )}

      <section className="readiness-grid" aria-label="Готовность проекта">
        {items.map((item) => {
          const target = item.state !== "ok" ? item.actionPage : null;
          return (
            <article className={`readiness-card ${item.state}`} key={item.id}>
              <div className="readiness-card-head">
                <h3>{item.label}</h3>
                <span className={`readiness-badge ${item.state}`}>{STATE_LABEL[item.state]}</span>
              </div>
              <p>{item.detail}</p>
              {target && target !== "overview" && (
                <button
                  className={setupOwnsCta || item.id !== primaryId ? "secondary" : undefined}
                  onClick={() => onNavigate(target)}
                >
                  Перейти
                </button>
              )}
            </article>
          );
        })}
      </section>

      <section className="readiness-grid" aria-label="Слои сцены и заметки проекта">
        {layerCounts && (
          <article className="readiness-card ok">
            <div className="readiness-card-head">
              <h3>Слои сцены</h3>
              <span className="readiness-badge ok">{scene?.entities.length ?? 0}</span>
            </div>
            <p>
              {ENTITY_STATES.map(
                (state) => `${ENTITY_STATE_LABELS[state]} ${layerCounts[state]}`
              ).join(" · ")}
            </p>
          </article>
        )}

        <article className="readiness-card ok attachment-card">
          <div className="readiness-card-head">
            <h3>Заметки и задачи</h3>
            <span className="readiness-badge ok">{attachments.length}</span>
          </div>
          {attachmentsError && <div className="error">{attachmentsError}</div>}
          {!attachmentsError && attachments.length === 0 && (
            <p className="muted">Заметок и задач пока нет.</p>
          )}
          {attachments.length > 0 && (
            <ul className="overview-attachment-list">
              {attachments.map((item) => {
                const overdue = isTaskOverdue(item);
                const dangling = isDanglingTarget(item, scene);
                return (
                  <li
                    key={item.id}
                    className={"overview-attachment" + (overdue ? " attachment-overdue" : "")}
                  >
                    {item.kind === "task" && (
                      <input
                        type="checkbox"
                        checked={item.done}
                        disabled={taskBusy}
                        aria-label="Задача выполнена"
                        onChange={() => void toggleTaskDone(item)}
                      />
                    )}
                    <span className={item.kind === "task" && item.done ? "attachment-done-text" : ""}>
                      {item.body ?? ATTACHMENT_KIND_LABELS[item.kind]}
                    </span>
                    {item.kind === "task" && item.due_date && (
                      <small className="muted"> · срок {item.due_date}</small>
                    )}
                    {overdue && <small className="attachment-overdue-mark"> · просрочено</small>}
                    {dangling && <small className="muted"> · цель удалена</small>}
                    {(item.kind === "photo" || item.kind === "file") && item.asset_id && (
                      <a href={api.assetUrl(item.asset_id)} target="_blank" rel="noreferrer">
                        открыть
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </article>
      </section>
    </div>
  );
}
