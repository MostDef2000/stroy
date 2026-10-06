import type { PageId } from "../nav";
import { computeProjectReadiness, type ReadinessInput, type ReadinessState } from "../overview";

const STATE_LABEL: Record<ReadinessState, string> = {
  ok: "Готово",
  pending: "Ожидает",
  attention: "Требует внимания"
};

// Overview: what is ready, what is missing, where to go next. Also hosts the
// golden-room demo-scene action while the project has no scene (#101).
// Everything renders inside .overview-page: an explicit page wrapper (#151)
// that keeps the workspace's top-aligned row model scoped to this page —
// without it, the stretched .workspace grid distributes the free viewport
// height across its implicit rows and the readiness cards grow tall.
export function OverviewPage({
  readiness,
  hasProject,
  hasScene,
  preparingScene,
  onCreateDemoScene,
  onNavigate
}: {
  readiness: ReadinessInput;
  hasProject: boolean;
  hasScene: boolean;
  preparingScene: boolean;
  onCreateDemoScene: () => void;
  onNavigate: (page: PageId) => void;
}) {
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
    </div>
  );
}
