import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Attachment, type AttachmentKind } from "./api";
import { apiErrorText } from "./twinDesign";
import {
  buildAttachmentPayload,
  isApproxRoomMapping,
  isDanglingTarget,
  isTaskOverdue
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

function isImageFile(file: File): boolean {
  return file.type.startsWith("image/");
}

export function AttachmentSection({
  projectId,
  targetType,
  targetId,
  scene = null,
  heading = "Вложения",
  roomMappingEnabled = false
}: {
  projectId: string;
  targetType: "room" | "entity";
  targetId: string;
  /** Current scene — enables the muted «цель удалена» dangling mark. */
  scene?: AttachmentSceneLike;
  heading?: string;
  /** R6 (#183): canonical rooms exist → room photos can be mapped (approx). */
  roomMappingEnabled?: boolean;
}) {
  const [items, setItems] = useState<Attachment[]>([]);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [noteBody, setNoteBody] = useState("");
  const [taskBody, setTaskBody] = useState("");
  const [taskDue, setTaskDue] = useState("");
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
    void load();
  }, [load]);

  async function addAttachment(kind: AttachmentKind, body: string | null, dueDate: string | null) {
    if (busy) return;
    setActionError("");
    const result = buildAttachmentPayload({ targetType, targetId, kind, body, dueDate });
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

  async function attachFile(file: File | null) {
    if (!file || busy) return;
    setActionError("");
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
        // R6 (#183): a photo attached in the ROOM context while canonical
        // rooms exist is an approximate owner-room mapping. The room guard
        // matches the label/hint gating (entity rail never stamps mapping).
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
                  {/* R6 (#183): approximate photo→room mapping mark — the
                      badge carries the qualifier, the note stays plain. */}
                  {item.kind === "photo" && isApproxRoomMapping(item.metadata) && (
                    <span className="attachment-mapping-note">
                      {" "}· Фото привязано к комнате{" "}
                      <span className="approx-badge">приблизительно</span>
                    </span>
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
