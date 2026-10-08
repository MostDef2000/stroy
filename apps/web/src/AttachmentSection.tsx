import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Attachment, type AttachmentKind } from "./api";
import { apiErrorText } from "./twinDesign";
import { entityKindLabel, mappingConfidenceLabel, roomHeading } from "./copy";
import {
  buildAttachmentPayload,
  buildPhotoMappingMetadata,
  isApproxRoomMapping,
  isDanglingTarget,
  isTaskOverdue,
  mappingNeedsOrientationStep,
  mappingSuggestedRoomId,
  photoMappingMetadataView,
  type MappingWizardRoom,
  type MappingWizardTarget,
  type PhotoMappingMetadataView
} from "./attachments";
import type { AttachmentSceneLike } from "./attachments";

// R1 attachments section: the shared list + add-controls block used by the
// Plan room inspector (target_type=room) and the Design selected-entity rail
// (target_type=entity). Photos/files upload through the project asset
// endpoint — R6 (#183): image uploads carry role "photo", non-image files
// keep role "attachment" — then reference the created asset. Errors
// render inline; every mutation refetches the list so a concurrent edit in
// another panel cannot make us stale.
//
// R6 photo→room mapping (#183, minimal): in the room-targeted section, once
// canonical rooms exist (PlanEditor passes draftStatus === "committed"), the
// photo control becomes «Привязать к комнате» and stamps the attachment with
// {mapping: "owner_room", confidence: "approx"} — shown back with the
// approximate badge. Precise mapping is out of scope for R6 (disclosure).
//
// R7 mapping wizard (#184): in the room flow the upload first opens a two-step
// inline wizard — step 1 confirms WHICH room the photo shows (preselect +
// «предлагаем» when the plan has exactly one room), step 2 asks the
// orientation questions (only when the room offers wall/door/window targets):
// «На какую стену смотрим?» and «Из какого угла снятого?». The answers are
// stamped as photo mapping metadata v1 ({mapping:"owner_room",
// confidence:"approx", visible_targets, orientation_hint}) via the pure
// buildPhotoMappingMetadata. Mapped rows show confidence + visible-target
// chips (kind labels only — raw ids stay behind title attrs per #188) and,
// when the caller can resolve a camera for the photo, the «Уточнить ракурс»
// link into the calibration wizard.

function isImageFile(file: File): boolean {
  return file.type.startsWith("image/");
}

/** Files awaiting the mapping wizard (room flow only — one at a time). */
type PendingPhoto = {
  file: File;
  step: 1 | 2;
  roomId: string | null;
  looksAtTargetId: string;
  from: "" | "corner" | "center";
};

export function AttachmentSection({
  projectId,
  targetType,
  targetId,
  scene = null,
  heading = "Вложения",
  roomMappingEnabled = false,
  mappingRooms = null,
  mappingTargetsByRoom = null,
  refinableCameraByAssetId = null,
  onRefineCamera = null
}: {
  projectId: string;
  targetType: "room" | "entity";
  targetId: string;
  /** Current scene — enables the muted «цель удалена» dangling mark. */
  scene?: AttachmentSceneLike;
  heading?: string;
  /** R6 (#183): canonical rooms exist → room photos can be mapped (approx). */
  roomMappingEnabled?: boolean;
  /** R7 (#184): candidate rooms for the mapping wizard's confirm step. */
  mappingRooms?: MappingWizardRoom[] | null;
  /** R7 (#184): selectable wall/door/window targets per room id. */
  mappingTargetsByRoom?: Record<string, MappingWizardTarget[]> | null;
  /** R7 (#184): photo asset id → linked calibration camera id. */
  refinableCameraByAssetId?: Record<string, string> | null;
  /** R7 (#184): opens the CameraPanel wizard for the linked camera. */
  onRefineCamera?: ((cameraId: string) => void) | null;
}) {
  const [items, setItems] = useState<Attachment[]>([]);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [noteBody, setNoteBody] = useState("");
  const [taskBody, setTaskBody] = useState("");
  const [taskDue, setTaskDue] = useState("");
  // R7 (#184): pending photo moving through the mapping wizard (room flow).
  const [pendingPhoto, setPendingPhoto] = useState<PendingPhoto | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await api.listAttachments(projectId, {
        target_type: targetType,
        target_id: targetId
      });
      setItems(list);
      setLoadError("");
    } catch (reason) {
      setLoadError(apiErrorText(reason));
    }
  }, [projectId, targetType, targetId]);

  useEffect(() => {
    setNoteBody("");
    setTaskBody("");
    setTaskDue("");
    setActionError("");
    setPendingPhoto(null);
    void load();
  }, [load]);

  async function addAttachment(
    kind: AttachmentKind,
    body: string | null,
    dueDate: string | null,
    metadata?: object
  ) {
    if (busy) return;
    setActionError("");
    const result = buildAttachmentPayload({ targetType, targetId, kind, body, dueDate, metadata });
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    setBusy(true);
    try {
      await api.createAttachment(projectId, result.payload);
      if (kind === "note") setNoteBody("");
      if (kind === "task") {
        setTaskBody("");
        setTaskDue("");
      }
      await load();
    } catch (reason) {
      setActionError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  // R7 (#184): the room-photo flow routes through the mapping wizard; every
  // other kind (and the whole non-room flow) uploads immediately as before.
  async function attachFile(file: File | null) {
    if (!file || busy) return;
    setActionError("");
    if (targetType === "room" && roomMappingEnabled && isImageFile(file)) {
      setPendingPhoto({
        file,
        step: 1,
        roomId: mappingSuggestedRoomId(mappingRooms ?? [], targetId),
        looksAtTargetId: "",
        from: ""
      });
      return;
    }
    setBusy(true);
    try {
      // R6 (#183): image uploads carry role "photo" (real room photos);
      // non-image files keep the generic role "attachment". The created
      // asset is then referenced by the photo/file attachment row.
      const asset = await api.upload(
        projectId,
        file,
        isImageFile(file) ? "photo" : "attachment",
        () => undefined
      );
      const result = buildAttachmentPayload({
        targetType,
        targetId,
        kind: isImageFile(file) ? "photo" : "file",
        assetId: asset.id,
        // Legacy fallback (R6 minimal): a photo attached in the ROOM context
        // while canonical rooms exist, when the caller supplied no wizard
        // rooms, still stamps the approximate owner-room mapping.
        metadata:
          targetType === "room" && roomMappingEnabled && isImageFile(file)
            ? { mapping: "owner_room", confidence: "approx" }
            : undefined
      });
      if (!result.ok) {
        setActionError(result.error);
        return;
      }
      await api.createAttachment(projectId, result.payload);
      await load();
    } catch (reason) {
      setActionError(apiErrorText(reason));
    } finally {
      setBusy(false);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
    }
  }

  function advanceWizard() {
    // F1 (review): the step transition and the submit decision happen in this
    // event handler, never inside a state updater — StrictMode double-invokes
    // updaters, which used to double-run the async submit (two uploads, two
    // attachments). Reading the render-scope state is safe here: a click
    // handler runs once per event with the state of its own render.
    const current = pendingPhoto;
    if (!current) return;
    const targets = mappingTargetsByRoom?.[current.roomId ?? ""] ?? [];
    // Orientation questions appear only when the room offers targets;
    // otherwise step 2 is skipped entirely.
    if (current.step === 1 && mappingNeedsOrientationStep(targets)) {
      setPendingPhoto({ ...current, step: 2, looksAtTargetId: "" });
      return;
    }
    // Clear BEFORE the async submit: the wizard closes immediately, the file
    // reference is held locally, and a second click cannot re-trigger the
    // upload (the in-flight guard below stays as the belt-and-suspenders).
    setPendingPhoto(null);
    void submitMappedPhoto(current);
  }

  function closeWizard() {
    setPendingPhoto(null);
    if (uploadInputRef.current) uploadInputRef.current.value = "";
  }

  // Wizard submit: upload the waiting photo, stamp mapping metadata v1 from
  // the confirmed answers, create the attachment. The typed builder returns
  // validation errors instead of throwing; wire rejections (422) surface via
  // apiErrorText like every other mutation.
  async function submitMappedPhoto(photo: PendingPhoto) {
    if (busy) return;
    const targets = mappingTargetsByRoom?.[photo.roomId ?? ""] ?? [];
    const chosen = targets.find(
      (target) => target.targetId === photo.looksAtTargetId
    );
    const metadataResult = buildPhotoMappingMetadata(
      {
        roomId: photo.roomId,
        looksAtTargetId: photo.looksAtTargetId || null,
        looksAtKind: photo.looksAtTargetId
          ? targets.find((target) => target.targetId === photo.looksAtTargetId)?.kind ?? null
          : null,
        from: photo.from || null
      },
      targets.map((target) => ({
        target_type: "entity" as const,
        target_id: target.targetId,
        kind: target.kind
      }))
    );
    if (!metadataResult.ok) {
      setActionError(metadataResult.error);
      closeWizard();
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      const asset = await api.upload(projectId, photo.file, "photo", () => undefined);
      const result = buildAttachmentPayload({
        targetType,
        targetId,
        kind: "photo",
        assetId: asset.id,
        metadata: metadataResult.metadata
      });
      if (!result.ok) {
        setActionError(result.error);
        return;
      }
      await api.createAttachment(projectId, result.payload);
      setPendingPhoto(null);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
      await load();
    } catch (reason) {
      setActionError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function toggleDone(attachment: Attachment) {
    if (busy) return;
    setActionError("");
    setBusy(true);
    try {
      await api.patchAttachment(projectId, attachment.id, { done: !attachment.done });
      await load();
    } catch (reason) {
      setActionError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function remove(attachment: Attachment) {
    if (busy) return;
    setActionError("");
    setBusy(true);
    try {
      await api.deleteAttachment(projectId, attachment.id);
      await load();
    } catch (reason) {
      setActionError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  // R7 (#184): wizard mode — room flow with caller-supplied candidate rooms.
  // Without them the legacy direct-stamp upload stays active.
  const wizardMode =
    targetType === "room" && roomMappingEnabled && (mappingRooms?.length ?? 0) > 0;
  const wizardRooms = mappingRooms ?? [];
  const suggestsRoom = wizardMode && wizardRooms.length === 1;
  const wizardRoomName =
    wizardRooms.find((room) => room.id === pendingPhoto?.roomId)?.name ?? null;

  return (
    <section className="attachment-section" aria-label={heading}>
      <h4>{heading}</h4>

      {loadError && <div className="error">{loadError}</div>}

      {items.length === 0 && !loadError && (
        <p className="muted">Пока ничего не прикреплено.</p>
      )}

      {items.length > 0 && (
        <ul className="attachment-list">
          {items.map((item) => {
            const overdue = isTaskOverdue(item);
            const dangling = isDanglingTarget(item, scene);
            const mapping =
              item.kind === "photo"
                ? photoMappingMetadataView(item.metadata)
                : null;
            const refineCameraId =
              mapping && item.asset_id && refinableCameraByAssetId
                ? refinableCameraByAssetId[item.asset_id] ?? null
                : null;
            return (
              <li
                key={item.id}
                className={
                  "attachment-item" + (overdue ? " attachment-overdue" : "")
                }
              >
                {item.kind === "task" && (
                  <input
                    type="checkbox"
                    checked={item.done}
                    disabled={busy}
                    aria-label="Задача выполнена"
                    onChange={() => void toggleDone(item)}
                  />
                )}
                <div className="attachment-item-main">
                  <span className={item.kind === "task" && item.done ? "attachment-done-text" : ""}>
                    {item.body ?? attachmentKindText(item)}
                  </span>
                  {item.kind === "task" && item.due_date && (
                    <small className="muted"> · срок {item.due_date}</small>
                  )}
                  {overdue && <small className="attachment-overdue-mark"> · просрочено</small>}
                  {dangling && <small className="muted"> · цель удалена</small>}
                  {/* R6/#184: photo→room mapping mark — the badge carries the
                      confidence qualifier, the note stays plain. */}
                  {item.kind === "photo" && mapping && (
                    <span className="attachment-mapping-note">
                      {" "}· Фото привязано к комнате{" "}
                      <span className="approx-badge">
                        {mappingConfidenceLabel(mapping.confidence)}
                      </span>
                    </span>
                  )}
                  {/* R7 (#184): visible-target chips — kind labels only; the
                      raw entity id stays available via the title (#188). */}
                  {mapping && mapping.visibleTargets.length > 0 && (
                    <span className="mapping-target-chips">
                      {mapping.visibleTargets.map((target) => (
                        <span
                          key={`${target.target_id}:${target.kind}`}
                          className="mapping-chip"
                          title={target.target_id}
                        >
                          {entityKindLabel(target.kind)}
                        </span>
                      ))}
                    </span>
                  )}
                  {/* R7 (#184): refine path — when the photo's asset is the
                      source of a calibration camera, jump straight into that
                      camera's wizard. */}
                  {refineCameraId && onRefineCamera && (
                    <button
                      type="button"
                      className="secondary mapping-refine-link"
                      onClick={() => onRefineCamera(refineCameraId)}
                    >
                      Уточнить ракурс
                    </button>
                  )}
                  {(item.kind === "photo" || item.kind === "file") && item.asset_id && (
                    <a href={api.assetUrl(item.asset_id)} target="_blank" rel="noreferrer">
                      {item.kind === "photo" ? "открыть фото" : "открыть файл"}
                    </a>
                  )}
                </div>
                <button
                  type="button"
                  className="secondary attachment-delete"
                  disabled={busy}
                  aria-label="Удалить вложение"
                  onClick={() => void remove(item)}
                >
                  ✕
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {/* R7 (#184): inline mapping wizard — never a modal, never on canvas.
          Step 1 confirms the room; step 2 (only when the room offers targets)
          asks the orientation questions. */}
      {pendingPhoto && wizardMode && (
        <form
          className="mapping-wizard"
          onSubmit={(event) => {
            event.preventDefault();
            if (pendingPhoto.step === 1) {
              advanceWizard();
            } else {
              // Same F1 discipline as advanceWizard: clear before the async
              // work so the wizard cannot be re-submitted while in flight.
              const current = pendingPhoto;
              setPendingPhoto(null);
              void submitMappedPhoto(current);
            }
          }}
        >
          {pendingPhoto.step === 1 && (
            <>
              <h5>Привязка фото к комнате</h5>
              {suggestsRoom && (
                <p className="hint">
                  Предлагаем комнату:{" "}
                  <strong>{roomHeading({ id: pendingPhoto.roomId ?? "", name: wizardRoomName })}</strong>
                </p>
              )}
              <label className="mapping-wizard-room">
                Какую комнату показывает фото?
                <select
                  value={pendingPhoto.roomId ?? ""}
                  onChange={(event) =>
                    setPendingPhoto({ ...pendingPhoto, roomId: event.target.value })
                  }
                >
                  {(mappingRooms ?? []).map((room) => (
                    <option key={room.id} value={room.id}>
                      {roomHeading(room)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="mapping-wizard-actions">
                <button type="submit" disabled={!pendingPhoto.roomId || busy}>
                  Далее
                </button>
                <button type="button" className="secondary" onClick={closeWizard} disabled={busy}>
                  Отмена
                </button>
              </div>
            </>
          )}
          {pendingPhoto.step === 2 && (
            <>
              <h5>Ориентация фото</h5>
              <label className="mapping-wizard-field">
                На какую стену смотрим?
                <select
                  value={pendingPhoto.looksAtTargetId}
                  onChange={(event) =>
                    setPendingPhoto({ ...pendingPhoto, looksAtTargetId: event.target.value })
                  }
                >
                  <option value="">Не выбирать</option>
                  {(mappingTargetsByRoom?.[pendingPhoto.roomId ?? ""] ?? []).map((target) => (
                    <option key={target.targetId} value={target.targetId} title={target.targetId}>
                      {target.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="mapping-wizard-field">
                Из какого угла снято?
                <select
                  value={pendingPhoto.from}
                  onChange={(event) =>
                    setPendingPhoto({
                      ...pendingPhoto,
                      from: event.target.value as "" | "corner" | "center"
                    })
                  }
                >
                  <option value="">Не выбирать</option>
                  <option value="corner">Из угла</option>
                  <option value="center">Из центра комнаты</option>
                </select>
              </label>
              <div className="mapping-wizard-actions">
                <button type="submit" disabled={busy}>
                  {busy ? "Прикрепляем…" : "Привязать фото"}
                </button>
                <button type="button" className="secondary" onClick={closeWizard} disabled={busy}>
                  Отмена
                </button>
              </div>
            </>
          )}
        </form>
      )}

      <div className="attachment-form">
        <input
          value={noteBody}
          onChange={(event) => setNoteBody(event.target.value)}
          placeholder="Заметка…"
          aria-label="Текст заметки"
        />
        <button
          type="button"
          className="secondary"
          disabled={busy || !noteBody.trim()}
          onClick={() => void addAttachment("note", noteBody, null)}
        >
          + Заметка
        </button>
        <input
          value={taskBody}
          onChange={(event) => setTaskBody(event.target.value)}
          placeholder="Задача…"
          aria-label="Описание задачи"
        />
        <input
          type="date"
          value={taskDue}
          onChange={(event) => setTaskDue(event.target.value)}
          aria-label="Срок задачи"
        />
        <button
          type="button"
          className="secondary"
          disabled={busy || (!taskBody.trim() && !taskDue)}
          onClick={() => void addAttachment("task", taskBody, taskDue || null)}
        >
          + Задача
        </button>
        <label className="upload attachment-upload">
          {/* R6 (#183): in the room context with canonical rooms the photo
              control IS the mapping control; its availability is the mapping
              gate (tooltip carries the reason). */}
          {targetType === "room" && roomMappingEnabled ? "Привязать к комнате" : "Фото/файл"}
          <input
            ref={uploadInputRef}
            type="file"
            disabled={busy}
            title={
              targetType === "room" && !roomMappingEnabled
                ? "Привязка фото к комнатам откроется после создания 3D из плана."
                : undefined
            }
            onChange={(event) => {
              void attachFile(event.target.files?.[0] ?? null);
            }}
          />
        </label>
      </div>

      {targetType === "room" && !roomMappingEnabled && (
        <p className="hint">
          Привязка фото к комнатам откроется после создания 3D из плана.
        </p>
      )}
      {targetType === "room" && roomMappingEnabled && (
        <p className="hint">Точная привязка фото появится позже.</p>
      )}

      {actionError && <div className="error">{actionError}</div>}
    </section>
  );
}

function attachmentKindText(item: Attachment): string {
  if (item.kind === "photo") return "Фото";
  if (item.kind === "file") return "Файл";
  return item.kind;
}
