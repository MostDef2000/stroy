import { useEffect, useMemo, useRef, useState } from "react";
import { api, Generation, Job, RevisionSummary } from "./api";
import { ImageLightbox } from "./ImageLightbox";
import { ImagePreview } from "./ImagePreview";
import { statusLabel } from "./copy";
import {
  buildTimelineEntries,
  comparePairFor,
  groupEntriesByDay
} from "./resultsTimeline";
import type { TimelineAction, TimelineResult } from "./resultsTimeline";
import {
  defaultSelectionId,
  resolveSelection,
  revisionSelectionId
} from "./resultsSelection";
import type { SelectionDetail } from "./resultsSelection";

// Results master-detail workspace (#152): the history list lives on the left
// (internally scrollable), the selected entry's preview/details/compare and
// the restore/re-render actions live on the right. #101 semantics unchanged.

// Selection resolved against the app's full api types (camera_id etc.).
type ResultsSelection = SelectionDetail<Generation, RevisionSummary>;

function outputAsset(generation: Generation | undefined) {
  return generation?.manifest.output_asset_ids?.[0] ?? null;
}

function timeLabel(value: string) {
  return new Date(value).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit"
  });
}

function jobCommands(job: Job | undefined): Array<Record<string, unknown>> {
  const rawCommands = job?.result?.["commands"];
  return Array.isArray(rawCommands)
    ? (rawCommands as Array<Record<string, unknown>>)
    : [];
}

// Must match `--rt-compare-h` in styles.css: both before/after boxes get the
// same fixed bounded area, and the matching max-height keeps images contained
// (never cropped) inside it.
const COMPARE_MAX_HEIGHT = "min(48dvh, 440px)";

const RESTORE_WARNING =
  "Возврат откатит сцену к состоянию на выбранный момент; изменения после него будут отменены.";

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
  // Selection is a stable string id (generation/job id, or `rev:`-prefixed
  // revision id) — the 5s poll replaces all objects, so selection must never
  // key by object identity.
  const [selectionId, setSelectionId] = useState<string | null>(null);
  const [detailMode, setDetailMode] = useState<"single" | "compare">("single");
  const [confirmRestoreId, setConfirmRestoreId] = useState<string | null>(null);
  // Shared lightbox (#150): primitives only — the app polls every 5s and
  // replaces object identities, so lightbox state must never hold objects.
  const [lightbox, setLightbox] = useState<{
    src: string;
    alt: string;
    title: string;
  } | null>(null);
  const lightboxTriggerRef = useRef<HTMLElement | null>(null);
  const selectedRowRef = useRef<HTMLButtonElement | null>(null);

  function openLightbox(src: string, alt: string, title: string, trigger: HTMLElement) {
    lightboxTriggerRef.current = trigger;
    setLightbox({ src, alt, title });
  }

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

  const currentRevision = revisions.find(
    (revision) => revision.revision_id === currentRevisionId
  );

  const entries = useMemo(
    () => buildTimelineEntries({ jobs, generations, currentRevisionId }),
    [jobs, generations, currentRevisionId]
  );
  const groups = useMemo(() => groupEntriesByDay(entries), [entries]);

  // Re-resolve the selection against the latest data on every change; fall
  // back to the newest entry (else newest revision) when the stored id is
  // unknown or nothing was selected yet.
  const selection = useMemo(() => {
    const resolved = resolveSelection({
      selectionId,
      entries,
      revisions,
      generations,
      currentRevisionId
    });
    if (resolved) return resolved;
    return resolveSelection({
      selectionId: defaultSelectionId(entries, revisions),
      entries,
      revisions,
      generations,
      currentRevisionId
    });
  }, [selectionId, entries, revisions, generations, currentRevisionId]);

  const selectedKey = selection
    ? selection.kind === "revision"
      ? revisionSelectionId(selection.revision.revision_id)
      : selection.entry.id
    : null;

  // Compare mode only makes sense for one selection; reset when it moves.
  useEffect(() => {
    setDetailMode("single");
  }, [selectedKey]);

  // Keep the selected master row visible when the selection moves
  // programmatically (fallback after a poll). Skip the initial mount.
  const lastScrolledKey = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedKey) return;
    if (lastScrolledKey.current === null) {
      lastScrolledKey.current = selectedKey;
      return;
    }
    if (lastScrolledKey.current === selectedKey) return;
    lastScrolledKey.current = selectedKey;
    selectedRowRef.current?.scrollIntoView({ block: "nearest" });
  }, [selectedKey]);

  function handleRestoreClick(revisionId: string) {
    if (confirmRestoreId !== revisionId) {
      setConfirmRestoreId(revisionId);
      return;
    }
    setConfirmRestoreId(null);
    void onRestore(revisionId);
  }

  function select(id: string) {
    setSelectionId(id);
  }

  // Before/after pair for the selected render — always derived, never stored
  // (object identities are replaced by the poll).
  const beforeGeneration = useMemo(() => {
    if (!selection || selection.kind !== "result") return undefined;
    const pair = comparePairFor(generations, selection.entry.id);
    return pair.beforeId
      ? generations.find((item) => item.id === pair.beforeId)
      : undefined;
  }, [selection, generations]);

  // The revision the restore/re-render actions target for this selection.
  function revisionTarget(selection: ResultsSelection): {
    revisionId: string;
    isCurrent: boolean;
  } | null {
    if (selection.kind === "revision") {
      return {
        revisionId: selection.revision.revision_id,
        isCurrent: selection.isCurrent
      };
    }
    if (selection.kind === "result") {
      return {
        revisionId: selection.entry.designRevisionId,
        isCurrent: selection.entry.designRevisionId === currentRevisionId
      };
    }
    return null; // action-only entries offer no restore/re-render
  }

  // Restore (two-step inline confirm) + re-render for the selected context.
  function renderRevisionActions(target: {
    revisionId: string;
    isCurrent: boolean;
  }) {
    const confirming = confirmRestoreId === target.revisionId;
    return (
      <>
        {!target.isCurrent && (
          <button
            className={confirming ? "rt-confirm" : "secondary"}
            data-restore-button="true"
            onClick={() => handleRestoreClick(target.revisionId)}
          >
            {confirming ? "Подтвердить возврат?" : "Вернуть"}
          </button>
        )}
        {cameraId && (
          <button
            className="secondary"
            onClick={() => void onRerender(target.revisionId)}
          >
            Сделать рендер
          </button>
        )}
      </>
    );
  }

  function renderActionCommands(commands: Array<Record<string, unknown>>, entryId: string) {
    return commands.map((command, index) => (
      <div key={`${entryId}-${index}`}>
        {String(command["operation"] ?? "command")}
        {command["target_id"] != null ? ` · ${String(command["target_id"])}` : ""}
      </div>
    ));
  }

  // --- Master rows -------------------------------------------------------

  function renderResultRow(entry: TimelineResult) {
    const selected = selectedKey === entry.id;
    const isCurrent = entry.designRevisionId === currentRevisionId;
    const assetId = entry.outputAssetId;
    return (
      <div className={`rt-row${selected ? " selected" : ""}`} key={entry.id}>
        <button
          type="button"
          className="rt-row-hit"
          aria-current={selected ? "true" : undefined}
          aria-label={`${entry.label}, ${timeLabel(entry.createdAt)}${
            isCurrent ? ", текущая" : ""
          }`}
          ref={selected ? selectedRowRef : undefined}
          onClick={() => select(entry.id)}
        />
        <div className="rt-row-content">
          {assetId && (
            <ImagePreview
              variant="thumbnail"
              aspectRatio="4/3"
              src={api.assetUrl(assetId)}
              alt={entry.label}
              expandable
              onExpand={(trigger) =>
                openLightbox(api.assetUrl(assetId), entry.label, entry.label, trigger)
              }
            />
          )}
          <div className="rt-row-meta">
            <span className="rt-row-label">{entry.label}</span>
            <span className="rt-time">{timeLabel(entry.createdAt)}</span>
            {isCurrent && <span className="tag">текущая</span>}
          </div>
        </div>
      </div>
    );
  }

  function renderActionRow(entry: TimelineAction) {
    const selected = selectedKey === entry.id;
    const commands = jobCommands(jobs.find((item) => item.id === entry.id));
    return (
      <div
        className={`rt-row rt-row--action${selected ? " selected" : ""}`}
        key={entry.id}
      >
        <button
          type="button"
          className="rt-row-hit"
          aria-current={selected ? "true" : undefined}
          aria-label={`${entry.label}, ${timeLabel(entry.createdAt)}`}
          ref={selected ? selectedRowRef : undefined}
          onClick={() => select(entry.id)}
        />
        <div className="rt-row-content">
          <div className="rt-row-meta">
            <span className="rt-row-label">{entry.label}</span>
            <span className="rt-time">{timeLabel(entry.createdAt)}</span>
            <span className="tag">{statusLabel(entry.status)}</span>
          </div>
          <details className="rt-details">
            <summary>Детали</summary>
            <div>Задание: {entry.id}</div>
            {entry.finalRevisionId && <div>Ревизия: {entry.finalRevisionId}</div>}
            {renderActionCommands(commands, entry.id)}
            {entry.errorText && <div className="error">{entry.errorText}</div>}
          </details>
        </div>
      </div>
    );
  }

  // --- Detail pane -------------------------------------------------------

  function renderResultDetail(selection: Extract<ResultsSelection, { kind: "result" }>) {
    const { entry } = selection;
    const generation = selection.generation;
    const assetId = entry.outputAssetId;
    const isCurrent = entry.designRevisionId === currentRevisionId;
    const target = revisionTarget(selection);
    const beforeAssetId = beforeGeneration
      ? outputAsset(beforeGeneration)
      : null;
    return (
      <>
        <div className="rt-detail-head">
          <div className="rt-detail-title">
            <strong>{entry.label}</strong>
            <span className="rt-time">
              {new Date(entry.createdAt).toLocaleString("ru-RU")}
            </span>
            {isCurrent && <span className="tag">текущая</span>}
          </div>
          {target && !target.isCurrent && <p className="rt-warning">{RESTORE_WARNING}</p>}
          {target && (
            <div className="rt-detail-actions">
              {assetId &&
                (detailMode === "single" ? (
                  <button className="secondary" onClick={() => setDetailMode("compare")}>
                    Сравнить
                  </button>
                ) : (
                  <button className="secondary" onClick={() => setDetailMode("single")}>
                    К рендеру
                  </button>
                ))}
              {renderRevisionActions(target)}
            </div>
          )}
        </div>

        {assetId && detailMode === "single" && (
          <figure className="rt-detail-media">
            <ImagePreview
              variant="bounded"
              src={api.assetUrl(assetId)}
              alt={entry.label}
              expandable
              onExpand={(trigger) =>
                openLightbox(api.assetUrl(assetId), entry.label, entry.label, trigger)
              }
            />
          </figure>
        )}

        {assetId && detailMode === "compare" && (
          <div className="compare-grid rt-compare">
            <figure>
              <figcaption>
                <strong>До</strong>
                <small>
                  {beforeGeneration
                    ? new Date(beforeGeneration.created_at).toLocaleString("ru-RU")
                    : "—"}
                </small>
              </figcaption>
              {beforeAssetId ? (
                <ImagePreview
                  variant="bounded"
                  maxHeight={COMPARE_MAX_HEIGHT}
                  src={api.assetUrl(beforeAssetId)}
                  alt="До"
                  expandable
                  onExpand={(trigger) =>
                    openLightbox(api.assetUrl(beforeAssetId), "До", "До", trigger)
                  }
                />
              ) : (
                <div className="compare-empty">нет предыдущего рендера</div>
              )}
            </figure>
            <figure>
              <figcaption>
                <strong>После</strong>
                <small>{new Date(entry.createdAt).toLocaleString("ru-RU")}</small>
              </figcaption>
              <ImagePreview
                variant="bounded"
                maxHeight={COMPARE_MAX_HEIGHT}
                src={api.assetUrl(assetId)}
                alt="После"
                expandable
                onExpand={(trigger) =>
                  openLightbox(api.assetUrl(assetId), "После", "После", trigger)
                }
              />
            </figure>
          </div>
        )}

        {!assetId && <p className="muted">У этого рендера нет изображения.</p>}

        <details className="rt-details">
          <summary>Детали</summary>
          <div>Рендер: {entry.id}</div>
          <div>Ревизия: {entry.designRevisionId}</div>
          {generation && <div>Камера: {generation.camera_id}</div>}
        </details>
      </>
    );
  }

  function renderActionDetail(entry: TimelineAction) {
    const commands = jobCommands(jobs.find((item) => item.id === entry.id));
    return (
      <>
        <div className="rt-detail-head">
          <div className="rt-detail-title">
            <strong>{entry.label}</strong>
            <span className="rt-time">
              {new Date(entry.createdAt).toLocaleString("ru-RU")}
            </span>
            <span className="tag">{statusLabel(entry.status)}</span>
          </div>
        </div>
        <details className="rt-details">
          <summary>Детали</summary>
          <div>Задание: {entry.id}</div>
          {entry.finalRevisionId && <div>Ревизия: {entry.finalRevisionId}</div>}
          {renderActionCommands(commands, entry.id)}
          {entry.errorText && <div className="error">{entry.errorText}</div>}
        </details>
      </>
    );
  }

  function renderRevisionDetail(
    selection: Extract<ResultsSelection, { kind: "revision" }>
  ) {
    const created = new Date(selection.revision.created_at).toLocaleString("ru-RU");
    const generation = selection.generation;
    const assetId = outputAsset(generation);
    return (
      <>
        <div className="rt-detail-head">
          <div className="rt-detail-title">
            <strong>Версия</strong>
            <span className="rt-time">{created}</span>
            {selection.isCurrent && <span className="tag">текущая</span>}
          </div>
          {!selection.isCurrent && <p className="rt-warning">{RESTORE_WARNING}</p>}
          <div className="rt-detail-actions">
            {renderRevisionActions({
              revisionId: selection.revision.revision_id,
              isCurrent: selection.isCurrent
            })}
          </div>
        </div>
        {assetId ? (
          <figure className="rt-detail-media">
            <ImagePreview
              variant="bounded"
              src={api.assetUrl(assetId)}
              alt={`Рендер версии от ${created}`}
              expandable
              onExpand={(trigger) =>
                openLightbox(
                  api.assetUrl(assetId),
                  `Рендер версии от ${created}`,
                  `Рендер версии от ${created}`,
                  trigger
                )
              }
            />
          </figure>
        ) : (
          <p className="muted">Для этой версии нет рендеров.</p>
        )}
        {generation && (
          <details className="rt-details">
            <summary>Детали</summary>
            <div>Рендер: {generation.id}</div>
            <div>Ревизия: {selection.revision.revision_id}</div>
            <div>Камера: {generation.camera_id}</div>
          </details>
        )}
      </>
    );
  }

  return (
    <>
      <div className="rt-workspace">
        <aside className="panel rt-master">
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

          <div className="rt-list">
            {entries.length === 0 ? (
              <p className="muted">Пока нет действий дизайнера</p>
            ) : (
              groups.map((group) => (
                <section className="rt-day" key={group.key}>
                  <h3>{group.label}</h3>
                  {group.entries.map((entry) =>
                    entry.kind === "action"
                      ? renderActionRow(entry)
                      : renderResultRow(entry)
                  )}
                </section>
              ))
            )}

            <details className="rt-versions" open>
              <summary>Версии</summary>
              {revisions.slice(0, 12).map((revision) => {
                const rowId = revisionSelectionId(revision.revision_id);
                const selected = selectedKey === rowId;
                const isCurrent = revision.revision_id === currentRevisionId;
                return (
                  <button
                    type="button"
                    className={`rt-row-hit${selected ? " selected" : ""}`}
                    key={revision.revision_id}
                    aria-current={selected ? "true" : undefined}
                    ref={selected ? selectedRowRef : undefined}
                    onClick={() => select(rowId)}
                  >
                    <span className="rt-row-meta">
                      <span className="rt-row-label">
                        {new Date(revision.created_at).toLocaleString("ru-RU")}
                      </span>
                      {isCurrent && <span className="tag">текущая</span>}
                    </span>
                  </button>
                );
              })}
            </details>
          </div>
        </aside>

        <section className="panel rt-detail" aria-label="Выбранный элемент истории">
          <h2>Просмотр</h2>
          {!selection ? (
            <p className="muted">Выберите элемент истории слева.</p>
          ) : selection.kind === "result" ? (
            renderResultDetail(selection)
          ) : selection.kind === "action" ? (
            renderActionDetail(selection.entry)
          ) : (
            renderRevisionDetail(selection)
          )}
        </section>
      </div>

      <ImageLightbox
        open={lightbox !== null}
        src={lightbox?.src ?? null}
        alt={lightbox?.alt ?? ""}
        title={lightbox?.title}
        onClose={() => setLightbox(null)}
        returnFocusRef={lightboxTriggerRef}
      />
    </>
  );
}
