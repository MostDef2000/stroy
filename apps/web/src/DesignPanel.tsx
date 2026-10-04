import { useEffect, useMemo, useState } from "react";
import { api, Generation, Job, RevisionSummary } from "./api";
import { statusLabel } from "./copy";
import {
  buildTimelineEntries,
  comparePairFor,
  groupEntriesByDay
} from "./resultsTimeline";

function outputAsset(generation: Generation | undefined) {
  return generation?.manifest.output_asset_ids?.[0] ?? null;
}

function timeLabel(value: string) {
  return new Date(value).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function DesignPanel({
  revisions,
  jobs,
  generations,
  currentRevisionId,
  cameraId,
  onRestore,
  onRerender
}: {
  revisions: RevisionSummary[];
  jobs: Job[];
  generations: Generation[];
  currentRevisionId: string | null;
  cameraId: string | null;
  onRestore: (revisionId: string) => Promise<void>;
  onRerender: (revisionId: string) => Promise<void>;
}) {
  const [beforeId, setBeforeId] = useState("");
  const [afterId, setAfterId] = useState("");
  const [confirmRestoreId, setConfirmRestoreId] = useState<string | null>(null);

  useEffect(() => {
    if (generations.length === 0) {
      setBeforeId("");
      setAfterId("");
      return;
    }
    setAfterId((value) =>
      generations.some((item) => item.id === value) ? value : generations[0].id
    );
    setBeforeId((value) => {
      if (generations.some((item) => item.id === value)) return value;
      return generations[1]?.id ?? generations[0].id;
    });
  }, [generations]);

  // Two-step restore confirmation times out after 5s of inactivity.
  useEffect(() => {
    if (!confirmRestoreId) return;
    const timer = setTimeout(() => setConfirmRestoreId(null), 5000);
    return () => clearTimeout(timer);
  }, [confirmRestoreId]);

  // Any click outside a restore button cancels the pending confirmation
  // (capture phase so it sees clicks before they bubble anywhere).
  useEffect(() => {
    if (!confirmRestoreId) return;
    function handleDocumentClick(event: MouseEvent) {
      if (!(event.target as HTMLElement).closest?.("[data-restore-button]")) {
        setConfirmRestoreId(null);
      }
    }
    document.addEventListener("click", handleDocumentClick, true);
    return () => document.removeEventListener("click", handleDocumentClick, true);
  }, [confirmRestoreId]);

  // A refreshed revision list or a changed current revision invalidates any
  // pending restore confirmation.
  useEffect(() => {
    setConfirmRestoreId(null);
  }, [revisions, currentRevisionId]);

  const before = generations.find((item) => item.id === beforeId);
  const after = generations.find((item) => item.id === afterId);
  const currentRevision = revisions.find(
    (revision) => revision.revision_id === currentRevisionId
  );

  const entries = useMemo(
    () => buildTimelineEntries({ jobs, generations, currentRevisionId }),
    [jobs, generations, currentRevisionId]
  );
  const groups = useMemo(() => groupEntriesByDay(entries), [entries]);

  function handleRestoreClick(revisionId: string) {
    if (confirmRestoreId !== revisionId) {
      setConfirmRestoreId(revisionId);
      return;
    }
    setConfirmRestoreId(null);
    void onRestore(revisionId);
  }

  function handleCompare(entryId: string) {
    setAfterId(entryId);
    setBeforeId(comparePairFor(generations, entryId).beforeId ?? "");
  }

  return (
    <>
      <article className="panel design-timeline">
        <h2>История дизайна</h2>

        <div className="rt-header">
          <strong>Текущая версия</strong>
          {currentRevision ? (
            <span>
              {new Date(currentRevision.created_at).toLocaleString("ru-RU")}{" "}
              <span className="tag">текущая</span>
            </span>
          ) : (
            <span className="muted">нет данных</span>
          )}
        </div>

        {entries.length === 0 ? (
          <p className="muted">Пока нет действий дизайнера</p>
        ) : (
          <div className="rt-feed">
            {groups.map((group) => (
              <section className="rt-day" key={group.key}>
                <h3>{group.label}</h3>
                {group.entries.map((entry) => {
                  if (entry.kind === "action") {
                    const job = jobs.find((item) => item.id === entry.id);
                    const rawCommands = job?.result?.["commands"];
                    const commands = Array.isArray(rawCommands)
                      ? (rawCommands as Array<Record<string, unknown>>)
                      : [];
                    return (
                      <article className="rt-entry" key={entry.id}>
                        <div className="rt-entry-head">
                          <div className="rt-label">{entry.label}</div>
                          <span className="rt-time">{timeLabel(entry.createdAt)}</span>
                          <span className="tag">{statusLabel(entry.status)}</span>
                        </div>
                        <details className="rt-details">
                          <summary>Детали</summary>
                          <div>Задание: {entry.id}</div>
                          {entry.finalRevisionId && (
                            <div>Ревизия: {entry.finalRevisionId}</div>
                          )}
                          {commands.map((command, index) => (
                            <div key={`${entry.id}-${index}`}>
                              {String(command["operation"] ?? "command")}
                              {command["target_id"] != null
                                ? ` · ${String(command["target_id"])}`
                                : ""}
                            </div>
                          ))}
                          {entry.errorText && (
                            <div className="error">{entry.errorText}</div>
                          )}
                        </details>
                      </article>
                    );
                  }

                  const generation = generations.find(
                    (item) => item.id === entry.id
                  );
                  return (
                    <article className="rt-entry" key={entry.id}>
                      {entry.outputAssetId && (
                        <div className="rt-preview">
                          <img
                            src={api.assetUrl(entry.outputAssetId)}
                            alt={entry.label}
                          />
                        </div>
                      )}
                      <div className="rt-entry-head">
                        <div className="rt-label">{entry.label}</div>
                        <span className="rt-time">{timeLabel(entry.createdAt)}</span>
                        <button
                          className="secondary"
                          onClick={() => handleCompare(entry.id)}
                        >
                          Сравнить
                        </button>
                      </div>
                      <details className="rt-details">
                        <summary>Детали</summary>
                        <div>Рендер: {entry.id}</div>
                        <div>Ревизия: {entry.designRevisionId}</div>
                        {generation && <div>Камера: {generation.camera_id}</div>}
                      </details>
                    </article>
                  );
                })}
              </section>
            ))}
          </div>
        )}

        <details className="rt-versions" open>
          <summary>Версии</summary>
          <p className="rt-warning">
            Возврат откатит сцену к состоянию на выбранный момент; изменения
            после него будут отменены.
          </p>
          {revisions.slice(0, 12).map((revision) => {
            const isCurrent = revision.revision_id === currentRevisionId;
            const confirming = confirmRestoreId === revision.revision_id;
            return (
              <div className="revision-line" key={revision.revision_id}>
                <span>
                  {new Date(revision.created_at).toLocaleString("ru-RU")}
                </span>
                {isCurrent ? (
                  <span className="tag">текущая</span>
                ) : (
                  <button
                    className={confirming ? "rt-confirm" : "secondary"}
                    data-restore-button="true"
                    onClick={() => handleRestoreClick(revision.revision_id)}
                  >
                    {confirming ? "Подтвердить возврат?" : "Вернуть"}
                  </button>
                )}
                {cameraId && (
                  <button
                    className="secondary"
                    onClick={() => void onRerender(revision.revision_id)}
                  >
                    Сделать рендер
                  </button>
                )}
              </div>
            );
          })}
        </details>
      </article>

      <article className="panel comparison-panel">
        <h2>До / После</h2>
        {generations.length === 0 ? (
          <p className="muted">Пока нет рендеров</p>
        ) : (
          <>
            <div className="compare-selectors">
              <label>
                До
                <select
                  value={beforeId}
                  onChange={(event) => setBeforeId(event.target.value)}
                >
                  {generations.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                      title={`revision ${item.design_revision_id}`}
                    >
                      {new Date(item.created_at).toLocaleString("ru-RU")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                После
                <select
                  value={afterId}
                  onChange={(event) => setAfterId(event.target.value)}
                >
                  {generations.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                      title={`revision ${item.design_revision_id}`}
                    >
                      {new Date(item.created_at).toLocaleString("ru-RU")}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="compare-grid">
              {[["До", before], ["После", after]].map(([label, generation]) => {
                const item = generation as Generation | undefined;
                const assetId = outputAsset(item);
                return (
                  <figure key={label as string}>
                    <figcaption>
                      <strong>{label as string}</strong>
                      <small
                        title={
                          item ? `revision ${item.design_revision_id}` : undefined
                        }
                      >
                        {item
                          ? new Date(item.created_at).toLocaleString("ru-RU")
                          : "—"}
                      </small>
                    </figcaption>
                    {assetId ? (
                      <img src={api.assetUrl(assetId)} alt={label as string} />
                    ) : (
                      <div className="compare-empty">нет изображения</div>
                    )}
                  </figure>
                );
              })}
            </div>
          </>
        )}
      </article>
    </>
  );
}
