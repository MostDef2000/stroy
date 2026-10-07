import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Attachment, type SceneDocument } from "../api";
import { countEntitiesByState, ENTITY_STATES, ENTITY_STATE_LABELS } from "../sceneLayers";
import { isDanglingTarget, isTaskOverdue } from "../attachments";
import { apiErrorText } from "../twinDesign";
import type { PageId } from "../nav";
import { computeProjectReadiness, type ReadinessInput, type ReadinessState } from "../overview";

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

// Overview: what is ready, what is missing, where to go next. Also hosts the
// golden-room demo-scene action while the project has no scene (#101).
// Everything renders inside .overview-page: an explicit page wrapper (#151)
// that keeps the workspace's top-aligned row model scoped to this page —
// without it, the stretched .workspace grid distributes the free viewport
// height across its implicit rows and the readiness cards grow tall.
//
// R1 additions (#169 card language, kept quiet): a compact scene-layer count
// card (As-is N · Structure N · Design N) and the project-level «Заметки и
// задачи» card with the done toggle and the overdue/dangling marks.
export function OverviewPage({
  readiness,
  projectId,
  scene,
  hasProject,
  hasScene,
  preparingScene,
  onCreateDemoScene,
  onNavigate
}: {
  readiness: ReadinessInput;
  projectId: string;
  scene: SceneDocument | null;
  hasProject: boolean;
  hasScene: boolean;
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

  return (
    <div className="overview-page">
      {!hasScene && (
        <section className="panel start-scene-panel">
          <h2>Начало работы</h2>
          <p className="muted">
            Сцена инициализируется автоматически. Если этого не произошло, создайте демонстрационную сцену.
          </p>
          <div className="pl-actions">
            <button onClick={onCreateDemoScene}>Golden room</button>
          </div>
          {preparingScene && <p className="muted">Готовим рабочее место…</p>}
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
                <button className="secondary" onClick={() => onNavigate(target)}>
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
