import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  api,
  type Attachment,
  type ProgressAcceptPostBody,
  type ProgressCompareDeviation,
  type ProgressSnapshot,
  type ProgressSnapshotPostBody,
  type ProgressStage,
  type SceneVariant
} from "./api";
import { ImagePreview } from "./ImagePreview";
import { apiErrorText } from "./twinDesign";
import {
  ACCEPT_UNAVAILABLE_HINT,
  CONFIDENCE_LABELS,
  OBSERVED_KIND_LABELS,
  PROVENANCE_LABELS,
  STAGE_LABELS,
  acceptChangeFor,
  buildAcceptCommand,
  compareTargetDescription,
  confidenceLabel,
  deviationDisplayLine,
  deviationStatusLabel,
  observedKindLabel,
  provenanceLabel,
  stageLabel
} from "./progress";
import type {
  ProgressConfidence,
  ProgressDeviationPostBody,
  ProgressObservedKind,
  ProgressPlannedRef,
  ProgressProvenance
} from "./api";

// R12 renovation progress (#162): the «Прогресс ремонта» section of the
// Results workspace. Snapshot list (compact cards) + create form + per-
// snapshot compare view with the deviation review flow (status transitions
// and the accept-into-scene command). All mutations are refetch-based (no
// optimistic updates), mirroring the VariantBudgetCard/AttachmentSection
// patterns; errors surface as inline RU messages with 409 codes mapped to
// human text.

/** Wire stage order for the stage select (BE enum order). */
const STAGE_OPTIONS: readonly ProgressStage[] = [
  "original",
  "demolition",
  "rough",
  "fitout",
  "current",
  "completed"
];

/** 409/4xx progress fact codes → RU text (BE _progress_http_error body). */
const PROGRESS_ERROR_LABELS: Record<string, string> = {
  invalid_status_transition: "Недопустимый переход статуса отклонения.",
  accepted_immutable: "Принятое отклонение изменить нельзя.",
  deviation_not_confirmed: "Перед принятием отклонение нужно подтвердить.",
  variant_not_approved: "Вариант должен быть утверждён, чтобы принимать в него.",
  variant_archived: "Вариант находится в архиве.",
  variant_not_found: "Вариант не найден — обновите список вариантов.",
  revision_conflict: "Версия сцены обновилась — повторите принятие.",
  command_rejected:
    "Сцену изменить нельзя: объект заблокирован или защищён (Keep/Lock).",
  deviation_not_found: "Отклонение не найдено — обновите список.",
  invalid_accept_envelope: "Не удалось сформировать команду принятия.",
  invalid_accept_target: "Не удалось определить цель принятия."
};

/** Map an api error to RU text, resolving known progress codes to phrases. */
function progressActionError(reason: unknown): string {
  const text = apiErrorText(reason);
  const colon = text.indexOf(":");
  const code = colon > 0 ? text.slice(0, colon).trim() : "";
  return PROGRESS_ERROR_LABELS[code] ?? text;
}

/** 422 validation rejections (enum/shape) carry no fact code — frame the raw
 * detail in RU instead of dumping bare JSON on the owner. */
function progressFormError(reason: unknown): string {
  const text = progressActionError(reason);
  return /^422/.test(text) ? `Сервер отклонил форму: ${text}` : text;
}

/** What the embedded accept change does, in RU (preset set is honest-only —
 * only remove_object exists today). */
const ACCEPT_CHANGE_LABELS: Record<string, string> = {
  remove_object: "удалить объект из сцены"
};

/** Planned-target type select labels for the deviation form. */
const DEV_TARGET_TYPE_LABELS: Record<"entity" | "room" | "scene", string> = {
  entity: "Объект",
  room: "Комната",
  scene: "Сцена"
};

function shortRev(revisionId: string): string {
  return revisionId.length > 8 ? revisionId.slice(0, 8) : revisionId;
}

export function ProgressSnapshotsPanel({
  projectId,
  variants,
  currentRevisionId,
  openLightbox
}: {
  projectId: string;
  /** Project variants: compare-target picking + title resolution. */
  variants: SceneVariant[];
  /** Canonical scene head — the CAS base for canonical accepts. */
  currentRevisionId: string | null;
  openLightbox: (src: string, alt: string, title: string, trigger: HTMLElement) => void;
}) {
  const [snapshots, setSnapshots] = useState<ProgressSnapshot[]>([]);
  const [photos, setPhotos] = useState<Attachment[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  // Create form.
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [stage, setStage] = useState<ProgressStage>("current");
  const [capturedAt, setCapturedAt] = useState("");
  const [note, setNote] = useState("");
  const [targetVariantId, setTargetVariantId] = useState("");
  const [pickedAttachmentIds, setPickedAttachmentIds] = useState<string[]>([]);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState("");

  // Two-step delete confirmation (5s timeout, VariantDetail pattern).
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  // Expanded snapshot id → the compare/review view replaces the card tail.
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [list, photoList] = await Promise.all([
        api.listProgressSnapshots(projectId),
        // Photo attachments feed both the card thumbnails and the picker.
        api.listAttachments(projectId, { kind: "photo" })
      ]);
      setSnapshots(list);
      setPhotos(photoList);
      setError("");
    } catch (reason) {
      setError(apiErrorText(reason));
    } finally {
      setLoaded(true);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Pending delete confirmation expires after 5s like the other two-step
  // confirms in this workspace.
  useEffect(() => {
    if (!confirmDeleteId) return;
    const timer = setTimeout(() => setConfirmDeleteId(null), 5000);
    return () => clearTimeout(timer);
  }, [confirmDeleteId]);

  const photoById = useMemo(
    () => new Map(photos.map((photo) => [photo.id, photo])),
    [photos]
  );

  const variantTitles = useMemo(
    () => new Map(variants.map((variant) => [variant.id, variant.title])),
    [variants]
  );

  // Approved variants only (owner decision: snapshots pin to the approved
  // variant; the accept endpoint additionally requires approved at runtime).
  const approvedVariants = useMemo(
    () => variants.filter((variant) => variant.status === "approved"),
    [variants]
  );

  function toggleExpand(snapshotId: string) {
    setExpandedId((current) => (current === snapshotId ? null : snapshotId));
  }

  function toggleAttachment(attachmentId: string) {
    setPickedAttachmentIds((ids) =>
      ids.includes(attachmentId)
        ? ids.filter((id) => id !== attachmentId)
        : [...ids, attachmentId]
    );
  }

  async function createSnapshot(event: FormEvent) {
    event.preventDefault();
    if (createBusy) return;
    if (!name.trim()) {
      setCreateError("Укажите название снимка.");
      return;
    }
    if (!capturedAt) {
      setCreateError("Укажите дату съёмки.");
      return;
    }
    setCreateBusy(true);
    setCreateError("");
    try {
      // datetime-local yields "YYYY-MM-DDTHH:mm" — pad seconds so the value
      // is a full ISO datetime the backend parses deterministically.
      const capturedIso = capturedAt.length === 16 ? `${capturedAt}:00` : capturedAt;
      const body: ProgressSnapshotPostBody = {
        name: name.trim(),
        stage,
        captured_at: capturedIso
      };
      if (note.trim()) body.note = note.trim();
      if (pickedAttachmentIds.length > 0) {
        body.attachment_ids = pickedAttachmentIds;
      }
      if (targetVariantId) {
        body.compare_target = { kind: "variant", variant_id: targetVariantId };
      }
      await api.createProgressSnapshot(projectId, body);
      setName("");
      setNote("");
      setCapturedAt("");
      setTargetVariantId("");
      setPickedAttachmentIds([]);
      setCreateOpen(false);
      await reload();
    } catch (reason) {
      setCreateError(progressActionError(reason));
    } finally {
      setCreateBusy(false);
    }
  }

  async function removeSnapshot(snapshotId: string) {
    if (deleteBusy) return;
    setDeleteBusy(true);
    setError("");
    try {
      await api.deleteProgressSnapshot(projectId, snapshotId);
      setConfirmDeleteId(null);
      if (expandedId === snapshotId) setExpandedId(null);
      await reload();
    } catch (reason) {
      setError(progressActionError(reason));
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <section className="panel progress-panel" aria-label="Прогресс ремонта">
      <div className="progress-head">
        <h2>Прогресс ремонта</h2>
        {createOpen ? (
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setCreateOpen(false);
              setCreateError("");
            }}
          >
            Отмена
          </button>
        ) : (
          <button
            type="button"
            className="secondary"
            onClick={() => setCreateOpen(true)}
          >
            + Новый снимок
          </button>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      {createOpen && (
        <form
          className="progress-create"
          onSubmit={(event) => void createSnapshot(event)}
        >
          <div className="progress-create-row">
            <label className="progress-create-field">
              <span>Название</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Например, Демонтаж — неделя 2"
                aria-label="Название снимка"
              />
            </label>
            <label className="progress-create-field">
              <span>Этап</span>
              <select
                value={stage}
                onChange={(event) => setStage(event.target.value as ProgressStage)}
                aria-label="Этап ремонта"
              >
                {STAGE_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {STAGE_LABELS[option]}
                  </option>
                ))}
              </select>
            </label>
            <label className="progress-create-field">
              <span>Дата съёмки</span>
              <input
                type="datetime-local"
                value={capturedAt}
                onChange={(event) => setCapturedAt(event.target.value)}
                aria-label="Дата съёмки"
              />
            </label>
          </div>
          <label className="progress-create-field">
            <span>Заметка</span>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={2}
              placeholder="Необязательно"
              aria-label="Заметка к снимку"
            />
          </label>
          <div className="progress-create-row">
            <label className="progress-create-field">
              <span>Сверять с</span>
              <select
                value={targetVariantId}
                onChange={(event) => setTargetVariantId(event.target.value)}
                aria-label="Цель сверки"
              >
                <option value="">Каноничная сцена</option>
                {approvedVariants.map((variant) => (
                  <option key={variant.id} value={variant.id}>
                    Вариант {variant.title}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="progress-create-field">
            <span>Фото со стройки</span>
            {photos.length === 0 ? (
              <p className="muted">В проекте пока нет фото-вложений.</p>
            ) : (
              <div className="progress-pick">
                {photos.map((photo) => (
                  <label key={photo.id} className="progress-pick-item">
                    <input
                      type="checkbox"
                      checked={pickedAttachmentIds.includes(photo.id)}
                      onChange={() => toggleAttachment(photo.id)}
                    />
                    <span>
                      {photo.body?.trim() ||
                        new Date(photo.created_at).toLocaleString("ru-RU")}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>
          {createError && <div className="error">{createError}</div>}
          <div className="progress-create-actions">
            <button type="submit" disabled={createBusy}>
              {createBusy ? "Сохраняем…" : "Создать снимок"}
            </button>
          </div>
        </form>
      )}

      {!loaded && <p className="muted">Загрузка снимков…</p>}
      {loaded && snapshots.length === 0 && (
        <p className="muted">
          Снимков прогресса пока нет — зафиксируйте первый этап кнопкой «Новый
          снимок».
        </p>
      )}

      <div className="progress-list">
        {snapshots.map((snapshot) => (
          <div className="progress-card" key={snapshot.id}>
            <div className="progress-card-head">
              <strong className="progress-card-name">{snapshot.name}</strong>
              <span className={`progress-badge progress-badge--${snapshot.stage}`}>
                {stageLabel(snapshot.stage)}
              </span>
              <span className="rt-time">
                {new Date(snapshot.captured_at).toLocaleString("ru-RU")}
              </span>
              <div className="progress-card-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => toggleExpand(snapshot.id)}
                >
                  {expandedId === snapshot.id ? "Свернуть" : "Сверить"}
                </button>
                <button
                  type="button"
                  className={confirmDeleteId === snapshot.id ? "rt-confirm" : "secondary"}
                  disabled={deleteBusy}
                  onClick={() =>
                    confirmDeleteId === snapshot.id
                      ? void removeSnapshot(snapshot.id)
                      : setConfirmDeleteId(snapshot.id)
                  }
                >
                  {confirmDeleteId === snapshot.id
                    ? "Подтвердить удаление?"
                    : "Удалить"}
                </button>
              </div>
            </div>
            <div className="progress-card-meta">
              <span className="progress-target">
                {compareTargetDescription(snapshot.compare_target, variantTitles)}
              </span>
              <span className="progress-badge">
                {provenanceLabel(snapshot.provenance)}
              </span>
              {(() => {
                const counts =
                  snapshot.deviation_counts ??
                  { open: 0, confirmed: 0, accepted: 0, discarded: 0 };
                return (
                  <span className="progress-counts">
                    <span className="progress-count progress-count--open">
                      Открыто: {counts.open}
                    </span>
                    <span className="progress-count progress-count--confirmed">
                      Подтверждено: {counts.confirmed}
                    </span>
                    <span className="progress-count progress-count--accepted">
                      Принято: {counts.accepted}
                    </span>
                    <span className="progress-count progress-count--discarded">
                      Отклонено: {counts.discarded}
                    </span>
                  </span>
                );
              })()}
            </div>
            {snapshot.note && <p className="progress-note">{snapshot.note}</p>}
            {snapshot.attachment_ids.length > 0 && (
              <div className="progress-photos">
                {snapshot.attachment_ids.map((attachmentId) => {
                  const photo = photoById.get(attachmentId);
                  if (!photo) return null; // attachment deleted elsewhere
                  const title = photo.body?.trim() || "Фото со стройки";
                  return (
                    <ImagePreview
                      key={attachmentId}
                      variant="thumbnail"
                      aspectRatio="4/3"
                      src={api.assetUrl(photo.asset_id ?? "")}
                      alt={title}
                      expandable
                      onExpand={(trigger) =>
                        openLightbox(
                          api.assetUrl(photo.asset_id ?? ""),
                          title,
                          title,
                          trigger
                        )
                      }
                    />
                  );
                })}
              </div>
            )}
            {expandedId === snapshot.id && (
              <SnapshotCompareView
                projectId={projectId}
                snapshotId={snapshot.id}
                photoById={photoById}
                approvedVariants={approvedVariants}
                currentRevisionId={currentRevisionId}
                openLightbox={openLightbox}
                onChanged={reload}
              />
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

// Compare/review view for one expanded snapshot: summary counts + the
// deviation rows with per-status actions and the inline accept flow.
function SnapshotCompareView({
  projectId,
  snapshotId,
  photoById,
  approvedVariants,
  currentRevisionId,
  openLightbox,
  onChanged
}: {
  projectId: string;
  snapshotId: string;
  photoById: Map<string, Attachment>;
  approvedVariants: SceneVariant[];
  currentRevisionId: string | null;
  openLightbox: (src: string, alt: string, title: string, trigger: HTMLElement) => void;
  onChanged: () => Promise<void>;
}) {
  const [compare, setCompare] = useState<
    Awaited<ReturnType<typeof api.compareProgressSnapshot>> | null
  >(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  const [actionBusyId, setActionBusyId] = useState<string | null>(null);
  // Row-scoped action error: {id, text} so the message renders under the row
  // whose PATCH/accept failed (single shared text would repeat on every row).
  const [actionError, setActionError] = useState<{
    id: string;
    text: string;
  } | null>(null);
  const [notice, setNotice] = useState("");

  // Inline accept flow: which deviation's sub-form is open + the chosen
  // target ("canonical" or an approved variant id).
  const [acceptingId, setAcceptingId] = useState<string | null>(null);
  const [acceptTargetValue, setAcceptTargetValue] = useState("canonical");
  // deviation id → accepted revision id (short-lived, this view session only;
  // the compare payload itself does not carry the accepted revision link).
  const [acceptedRevisions, setAcceptedRevisions] = useState<
    Record<string, string>
  >({});
  // Effective canonical head for the accept CAS: the currentRevisionId prop
  // lags behind the app scene poll (≤5s), so a successful canonical accept
  // seeds this shadow with the response's fresh revision id — an immediate
  // second accept uses the NEW head instead of 409-ing on a stale one.
  const [canonicalHead, setCanonicalHead] = useState(currentRevisionId);

  useEffect(() => {
    setCanonicalHead(currentRevisionId);
  }, [currentRevisionId]);

  // Inline deviation-creation form (M6): planned-vs-observed facts captured
  // by hand against this snapshot. Mirror of the snapshot-create form.
  const [devFormOpen, setDevFormOpen] = useState(false);
  const [devTargetType, setDevTargetType] = useState<"entity" | "room" | "scene">(
    "entity"
  );
  const [devEntityId, setDevEntityId] = useState("");
  const [devRoomId, setDevRoomId] = useState("");
  const [devPropertyPath, setDevPropertyPath] = useState("");
  const [devObservedKind, setDevObservedKind] = useState<ProgressObservedKind>(
    "missing"
  );
  const [devObservedValue, setDevObservedValue] = useState("");
  const [devProvenance, setDevProvenance] = useState<ProgressProvenance>("observed");
  const [devConfidence, setDevConfidence] = useState<ProgressConfidence>("medium");
  const [devEvidenceIds, setDevEvidenceIds] = useState<string[]>([]);
  const [devNote, setDevNote] = useState("");
  const [devBusy, setDevBusy] = useState(false);
  const [devError, setDevError] = useState("");

  const allPhotos = useMemo(() => [...photoById.values()], [photoById]);

  const reload = useCallback(async () => {
    try {
      setCompare(await api.compareProgressSnapshot(projectId, snapshotId));
      setError("");
    } catch (reason) {
      setError(apiErrorText(reason));
    } finally {
      setLoaded(true);
    }
  }, [projectId, snapshotId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  function openAccept(deviation: ProgressCompareDeviation) {
    setAcceptingId(deviation.id);
    setAcceptTargetValue("canonical");
    setNotice("");
    setActionError(null);
    // UI review round 2 (D2): the inline form for the last deviation row
    // opens below the fold at 1280×800 — reveal it instead of hoping the
    // owner scrolls.
    requestAnimationFrame(() => {
      document
        .querySelector(`[data-accept-form="${deviation.id}"]`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }

  function toggleDevEvidence(attachmentId: string) {
    setDevEvidenceIds((ids) =>
      ids.includes(attachmentId)
        ? ids.filter((id) => id !== attachmentId)
        : [...ids, attachmentId]
    );
  }

  async function createDeviation(event: FormEvent) {
    event.preventDefault();
    if (devBusy) return;
    const planned: ProgressPlannedRef = { target_type: devTargetType };
    if (devTargetType === "entity") {
      if (!devEntityId.trim()) {
        setDevError("Укажите идентификатор объекта из плана.");
        return;
      }
      planned.entity_id = devEntityId.trim();
    }
    if (devTargetType === "room") {
      if (!devRoomId.trim()) {
        setDevError("Укажите идентификатор комнаты из плана.");
        return;
      }
      planned.room_id = devRoomId.trim();
    }
    if (devPropertyPath.trim()) planned.property_path = devPropertyPath.trim();
    const body: ProgressDeviationPostBody = {
      planned,
      // Observed value rides the wire as a plain JSON string (owner input).
      observed: { kind: devObservedKind, value: devObservedValue },
      provenance: devProvenance,
      confidence: devConfidence
    };
    if (devEvidenceIds.length > 0) body.evidence_attachment_ids = devEvidenceIds;
    if (devNote.trim()) body.note = devNote.trim();
    setDevBusy(true);
    setDevError("");
    try {
      await api.createProgressDeviation(projectId, snapshotId, body);
      setDevEntityId("");
      setDevRoomId("");
      setDevPropertyPath("");
      setDevObservedValue("");
      setDevEvidenceIds([]);
      setDevNote("");
      setDevFormOpen(false);
      await onChanged();
      await reload();
    } catch (reason) {
      setDevError(progressFormError(reason));
    } finally {
      setDevBusy(false);
    }
  }

  async function patchStatus(
    deviationId: string,
    status: "open" | "confirmed" | "discarded"
  ) {
    if (actionBusyId) return;
    setActionBusyId(deviationId);
    setActionError(null);
    setNotice("");
    try {
      await api.patchProgressDeviation(projectId, deviationId, { status });
      await reload();
      await onChanged();
    } catch (reason) {
      setActionError({ id: deviationId, text: progressActionError(reason) });
    } finally {
      setActionBusyId(null);
    }
  }

  async function runAccept(deviation: ProgressCompareDeviation) {
    if (actionBusyId) return;
    const change = acceptChangeFor(deviation);
    if (!change) return; // button is disabled without a preset — defensive
    setActionBusyId(deviation.id);
    setActionError(null);
    setNotice("");
    try {
      let baseRevisionId: string;
      let target: ProgressSnapshotAcceptTarget;
      if (acceptTargetValue === "canonical") {
        if (!canonicalHead) {
          setActionError({
            id: deviation.id,
            text: "Сцена ещё не создана — принять в каноничную сцену нельзя."
          });
          return;
        }
        baseRevisionId = canonicalHead;
        target = { kind: "canonical" };
      } else {
        // Fresh head right before the send: a stale CAS head would 409.
        const variant = await api.getVariant(projectId, acceptTargetValue);
        if (!variant) {
          setActionError({
            id: deviation.id,
            text: "Вариант не найден — обновите список вариантов."
          });
          return;
        }
        if (variant.status !== "approved") {
          setActionError({
            id: deviation.id,
            text: "Вариант должен быть утверждён, чтобы принимать в него."
          });
          return;
        }
        baseRevisionId = variant.head_scene_revision_id;
        target = {
          kind: "variant",
          variant_id: variant.id,
          expected_head_revision_id: variant.head_scene_revision_id
        };
      }
      const command = buildAcceptCommand({
        deviationId: deviation.id,
        change,
        baseRevisionId
      });
      const response = await api.acceptProgressDeviation(
        projectId,
        deviation.id,
        { target, command }
      );
      setAcceptedRevisions((prev) => ({
        ...prev,
        [deviation.id]: response.revision.revision_id
      }));
      // Canonical accept moved the canonical head — seed the CAS shadow so an
      // immediate second accept targets the fresh head, not the stale prop.
      if (acceptTargetValue === "canonical") {
        setCanonicalHead(response.revision.revision_id);
      }
      setNotice(
        `Принято в сцену — ревизия ${shortRev(response.revision.revision_id)}.`
      );
      setAcceptingId(null);
      // Parent refetch first (snapshot counts + canonical head propagation),
      // then the compare view's own reload.
      await onChanged();
      await reload();
    } catch (reason) {
      setActionError({ id: deviation.id, text: progressActionError(reason) });
    } finally {
      setActionBusyId(null);
    }
  }

  if (!loaded) {
    return <p className="muted">Загрузка сверки…</p>;
  }
  if (error && !compare) {
    return <div className="error">{error}</div>;
  }
  if (!compare) return null;

  const summary = compare.summary;

  return (
    <div className="progress-compare">
      <div className="progress-summary">
        <span className="progress-count progress-count--open">
          Открыто: {summary.open}
        </span>
        <span className="progress-count progress-count--confirmed">
          Подтверждено: {summary.confirmed}
        </span>
        <span className="progress-count progress-count--accepted">
          Принято: {summary.accepted}
        </span>
        <span className="progress-count progress-count--discarded">
          Отклонено: {summary.discarded}
        </span>
        <span className="progress-count">Всего: {summary.total}</span>
        <button
          type="button"
          className="secondary progress-dev-trigger"
          onClick={() => {
            setDevFormOpen((open) => !open);
            setDevError("");
          }}
        >
          {devFormOpen ? "Скрыть форму" : "Зафиксировать отклонение"}
        </button>
      </div>

      {notice && <p className="hint progress-notice">{notice}</p>}
      {error && <div className="error">{error}</div>}

      {devFormOpen && (
        <form
          className="progress-create progress-dev-form"
          onSubmit={(event) => void createDeviation(event)}
        >
          <div className="progress-create-row">
            <label className="progress-create-field">
              <span>Что сверяем</span>
              <select
                value={devTargetType}
                onChange={(event) =>
                  setDevTargetType(
                    event.target.value as "entity" | "room" | "scene"
                  )
                }
                aria-label="Планируемая цель"
              >
                {(Object.keys(DEV_TARGET_TYPE_LABELS) as Array<
                  "entity" | "room" | "scene"
                >).map((option) => (
                  <option key={option} value={option}>
                    {DEV_TARGET_TYPE_LABELS[option]}
                  </option>
                ))}
              </select>
            </label>
            {devTargetType === "entity" && (
              <label className="progress-create-field">
                <span>Объект (id из плана)</span>
                <input
                  value={devEntityId}
                  onChange={(event) => setDevEntityId(event.target.value)}
                  placeholder="object.sofa.main"
                  aria-label="Идентификатор объекта из плана"
                />
              </label>
            )}
            {devTargetType === "room" && (
              <label className="progress-create-field">
                <span>Комната (id из плана)</span>
                <input
                  value={devRoomId}
                  onChange={(event) => setDevRoomId(event.target.value)}
                  placeholder="room.1"
                  aria-label="Идентификатор комнаты из плана"
                />
              </label>
            )}
          </div>
          {devTargetType === "entity" && (
            <p className="hint">
              Идентификатор объекта из плана (например object.sofa.main)
            </p>
          )}
          {devTargetType === "room" && (
            <p className="hint">
              Идентификатор комнаты из плана (например room.1)
            </p>
          )}
          <div className="progress-create-row">
            <label className="progress-create-field">
              <span>Параметр (необязательно)</span>
              <input
                value={devPropertyPath}
                onChange={(event) => setDevPropertyPath(event.target.value)}
                placeholder="например color или material_ref"
                aria-label="Путь параметра"
              />
            </label>
            <label className="progress-create-field">
              <span>Тип наблюдения</span>
              <select
                value={devObservedKind}
                onChange={(event) =>
                  setDevObservedKind(event.target.value as ProgressObservedKind)
                }
                aria-label="Тип наблюдения"
              >
                {(Object.keys(OBSERVED_KIND_LABELS) as Array<ProgressObservedKind>).map(
                  (option) => (
                    <option key={option} value={option}>
                      {OBSERVED_KIND_LABELS[option]}
                    </option>
                  )
                )}
              </select>
            </label>
            <label className="progress-create-field">
              <span>Наблюдаемое значение</span>
              <input
                value={devObservedValue}
                onChange={(event) => setDevObservedValue(event.target.value)}
                placeholder="что фактически на объекте"
                aria-label="Наблюдаемое значение"
              />
            </label>
          </div>
          <div className="progress-create-row">
            <label className="progress-create-field">
              <span>Источник данных</span>
              <select
                value={devProvenance}
                onChange={(event) =>
                  setDevProvenance(event.target.value as ProgressProvenance)
                }
                aria-label="Источник данных отклонения"
              >
                {(Object.keys(PROVENANCE_LABELS) as Array<ProgressProvenance>).map(
                  (option) => (
                    <option key={option} value={option}>
                      {PROVENANCE_LABELS[option]}
                    </option>
                  )
                )}
              </select>
            </label>
            <label className="progress-create-field">
              <span>Достоверность</span>
              <select
                value={devConfidence}
                onChange={(event) =>
                  setDevConfidence(event.target.value as ProgressConfidence)
                }
                aria-label="Достоверность отклонения"
              >
                {(Object.keys(CONFIDENCE_LABELS) as Array<ProgressConfidence>).map(
                  (option) => (
                    <option key={option} value={option}>
                      {CONFIDENCE_LABELS[option]}
                    </option>
                  )
                )}
              </select>
            </label>
          </div>
          <label className="progress-create-field">
            <span>Заметка</span>
            <textarea
              value={devNote}
              onChange={(event) => setDevNote(event.target.value)}
              rows={2}
              placeholder="Необязательно"
              aria-label="Заметка к отклонению"
            />
          </label>
          <div className="progress-create-field">
            <span>Подтверждающие фото</span>
            {allPhotos.length === 0 ? (
              <p className="muted">В проекте пока нет фото-вложений.</p>
            ) : (
              <div className="progress-pick">
                {allPhotos.map((photo) => (
                  <label key={photo.id} className="progress-pick-item">
                    <input
                      type="checkbox"
                      checked={devEvidenceIds.includes(photo.id)}
                      onChange={() => toggleDevEvidence(photo.id)}
                    />
                    <span>
                      {photo.body?.trim() ||
                        new Date(photo.created_at).toLocaleString("ru-RU")}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>
          {devError && <div className="error">{devError}</div>}
          <div className="progress-create-actions">
            <button type="submit" disabled={devBusy}>
              {devBusy ? "Фиксируем…" : "Зафиксировать"}
            </button>
          </div>
        </form>
      )}

      {compare.deviations.length === 0 && (
        <p className="muted">Отклонений пока не зафиксировано.</p>
      )}

      {compare.deviations.map((deviation) => {
        const change = acceptChangeFor(deviation);
        const busy = actionBusyId === deviation.id;
        return (
          <div
            key={deviation.id}
            className={`progress-dev progress-dev--${deviation.status}${
              deviation.planned.dangling ? " progress-dev--dangling" : ""
            }`}
          >
            <div className="progress-dev-head">
              <span className={`progress-status progress-status--${deviation.status}`}>
                {deviationStatusLabel(deviation.status)}
              </span>
              <span className="progress-dev-line">
                {deviationDisplayLine(deviation)}
              </span>
            </div>
            <div className="progress-dev-meta">
              <span className="progress-badge">
                {observedKindLabel(deviation.observed.kind)}
              </span>
              <span className="progress-badge">
                {provenanceLabel(deviation.provenance)}
              </span>
              <span className="progress-badge">
                Достоверность: {confidenceLabel(deviation.confidence)}
              </span>
            </div>
            {deviation.note && <p className="progress-note">{deviation.note}</p>}
            {deviation.evidence_attachment_ids.length > 0 && (
              <div className="progress-photos">
                {deviation.evidence_attachment_ids.map((attachmentId) => {
                  const photo = photoById.get(attachmentId);
                  if (!photo) return null;
                  const title = photo.body?.trim() || "Подтверждающее фото";
                  return (
                    <ImagePreview
                      key={attachmentId}
                      variant="thumbnail"
                      aspectRatio="4/3"
                      src={api.assetUrl(photo.asset_id ?? "")}
                      alt={title}
                      expandable
                      onExpand={(trigger) =>
                        openLightbox(
                          api.assetUrl(photo.asset_id ?? ""),
                          title,
                          title,
                          trigger
                        )
                      }
                    />
                  );
                })}
              </div>
            )}
            {actionError && actionError.id === deviation.id && (
              <div className="error">{actionError.text}</div>
            )}
            {acceptingId === deviation.id ? (
              <div className="progress-accept" data-accept-form={deviation.id}>
                <p className="hint">
                  Изменение сцены:{" "}
                  {ACCEPT_CHANGE_LABELS[change?.operation ?? ""] ??
                    "недоступно"}. Команда применится как обычное изменение
                  сцены и попадёт в историю.
                </p>
                <label className="progress-accept-target">
                  <span>Куда принять</span>
                  <select
                    value={acceptTargetValue}
                    onChange={(event) => setAcceptTargetValue(event.target.value)}
                    aria-label="Цель принятия"
                  >
                    <option value="canonical">Каноничная сцена</option>
                    {approvedVariants.map((variant) => (
                      <option key={variant.id} value={variant.id}>
                        Вариант {variant.title}
                      </option>
                    ))}
                  </select>
                </label>
                {actionError && actionError.id === deviation.id && (
                  <div className="error">{actionError.text}</div>
                )}
                <div className="progress-dev-actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void runAccept(deviation)}
                  >
                    {busy ? "Применяем…" : "Подтвердить принятие"}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => {
                      setAcceptingId(null);
                      setActionError(null);
                    }}
                  >
                    Отмена
                  </button>
                </div>
              </div>
            ) : (
              <div className="progress-dev-actions">
                {deviation.status === "open" && (
                  <>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy}
                      onClick={() => void patchStatus(deviation.id, "confirmed")}
                    >
                      Подтвердить
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy}
                      onClick={() => void patchStatus(deviation.id, "discarded")}
                    >
                      Отклонить
                    </button>
                  </>
                )}
                {deviation.status === "confirmed" && (
                  <>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy || !change}
                      title={change ? undefined : ACCEPT_UNAVAILABLE_HINT}
                      aria-label={
                        change ? undefined : ACCEPT_UNAVAILABLE_HINT
                      }
                      onClick={() => openAccept(deviation)}
                    >
                      Принять в сцену
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy}
                      onClick={() => void patchStatus(deviation.id, "open")}
                    >
                      Вернуть в открытые
                    </button>
                  </>
                )}
                {deviation.status === "accepted" && (
                  <span className="progress-accepted-info">
                    Принято
                    {acceptedRevisions[deviation.id]
                      ? ` · ревизия ${shortRev(acceptedRevisions[deviation.id])}`
                      : ""}
                  </span>
                )}
                {deviation.status === "discarded" && (
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => void patchStatus(deviation.id, "open")}
                  >
                    Вернуть
                  </button>
                )}
              </div>
            )}
            {!change && deviation.status === "confirmed" && acceptingId !== deviation.id && (
              <p className="hint progress-accept-hint">
                {ACCEPT_UNAVAILABLE_HINT}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Local alias to keep the accept target union readable in runAccept. */
type ProgressSnapshotAcceptTarget = import("./api").ProgressAcceptPostBody["target"];
