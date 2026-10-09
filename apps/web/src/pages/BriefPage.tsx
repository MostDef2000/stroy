import { useEffect, useMemo, useRef, useState } from "react";
import { api, type BriefDocument, type BriefNotes } from "../api";
import {
  BRIEF_BACK_LABEL,
  BRIEF_CHECKLIST_TITLE,
  BRIEF_DOWNLOAD_LABEL,
  BRIEF_NEEDS_LABEL,
  BRIEF_PRIVACY_NOTE,
  BRIEF_QUESTIONS_LABEL,
  briefBannerLines,
  briefNotesSaveIndicator,
  briefSectionTitle,
  renderStageLabel
} from "../copy";
import {
  briefImageKey,
  briefPhotoLabel,
  briefPhotoMappingNote,
  briefRenderDisclaimer,
  briefRoomLabel,
  briefScaleLine,
  briefSectionIds,
  briefShortHash,
  collectBriefImages,
  createNotesPersister,
  downloadBriefPdf,
  filterIncludedImages,
  groupIntentEntities,
  type BriefImageItem,
  type BriefSectionId
} from "../briefView";
import {
  DEFAULT_BUDGET_CURRENCY,
  STATUS_LABELS,
  budgetKindLabel,
  formatMoney
} from "../sceneVariants";
import { apiErrorText } from "../twinDesign";

// R8 designer brief (#198): the owner-facing document handed to the designer.
// Screen: preview (the printable sections) + composer rail (hidden on print).
// The composer persists needs/wishes + questions through PATCH /brief-notes
// (debounced like the PlanEditor autosave, flushed on blur and on unmount);
// the privacy checklist decides which images reach the preview and the
// printout (unchecked = removed from the DOM, not merely hidden).
//
// Visible labels never contain raw ids (#188): room/entity/photo labels come
// from briefView helpers, BE warnings render verbatim.

const NOTES_MAX_LENGTH = 8000;
const NOTES_DEBOUNCE_MS = 800;

type NotesState = { needs_wishes: string; questions_to_discuss: string };

const EMPTY_NOTES: NotesState = { needs_wishes: "", questions_to_discuss: "" };

export function BriefPage({
  projectId,
  variantId,
  onBack
}: {
  projectId: string;
  variantId: string;
  onBack: () => void;
}) {
  const [brief, setBrief] = useState<BriefDocument | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notesError, setNotesError] = useState("");
  // Composer state; savedNotes is the last PATCH-accepted baseline (dirty =
  // composer differs from it), mirroring the PlanEditor autosave model.
  const [notes, setNotes] = useState<NotesState>(EMPTY_NOTES);
  const [savedNotes, setSavedNotes] = useState<NotesState>(EMPTY_NOTES);
  const [saving, setSaving] = useState(false);
  const [images, setImages] = useState<BriefImageItem[]>([]);

  // Brief + notes on mount (and on target change). The notes GET failing
  // alone must not blank the page — the doc's notes snapshot seeds instead.
  useEffect(() => {
    let cancelled = false;
    setBrief(null);
    setLoaded(false);
    setError("");
    setImages([]);
    api
      .fetchDesignBrief(projectId, variantId)
      .then(async (doc) => {
        if (cancelled) return;
        let saved: BriefNotes | null = null;
        try {
          saved = await api.getBriefNotes(projectId);
        } catch {
          saved = null;
        }
        if (cancelled) return;
        const seed: NotesState = {
          needs_wishes: saved?.needs_wishes ?? doc.notes?.needs_wishes ?? "",
          questions_to_discuss:
            saved?.questions_to_discuss ?? doc.notes?.questions_to_discuss ?? ""
        };
        setBrief(doc);
        setNotes(seed);
        setSavedNotes(seed);
        setImages(collectBriefImages(doc));
        setLoaded(true);
      })
      .catch((reason) => {
        if (cancelled) return;
        setError(apiErrorText(reason));
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, variantId]);

  // Notes autosave: one debounced PATCH per editing burst (800ms quiet
  // period), plus an on-blur and an on-unmount flush so nothing is lost when
  // the owner leaves the page mid-edit.
  const persister = useMemo(
    () =>
      createNotesPersister({
        debounceMs: NOTES_DEBOUNCE_MS,
        patch: async (next: BriefNotes) => {
          setSaving(true);
          try {
            await api.patchBriefNotes(projectId, {
              needs_wishes: next.needs_wishes,
              questions_to_discuss: next.questions_to_discuss
            });
            setSavedNotes({
              needs_wishes: next.needs_wishes ?? "",
              questions_to_discuss: next.questions_to_discuss ?? ""
            });
            setNotesError("");
          } catch (reason) {
            // The draft stays dirty; the next edit/blur retries.
            setNotesError(apiErrorText(reason));
          } finally {
            setSaving(false);
          }
        }
      }),
    [projectId]
  );

  useEffect(
    () => () => {
      void persister.flush().catch(() => undefined);
      persister.dispose();
    },
    [persister]
  );

  const notesRef = useRef(notes);
  notesRef.current = notes;

  function editNotes(field: keyof NotesState, value: string) {
    const next = { ...notesRef.current, [field]: value };
    notesRef.current = next;
    setNotes(next);
    persister.schedule({
      needs_wishes: next.needs_wishes,
      questions_to_discuss: next.questions_to_discuss
    });
  }

  function toggleImage(key: string) {
    setImages((items) =>
      items.map((item) =>
        item.key === key ? { ...item, checked: !item.checked } : item
      )
    );
  }

  const notesDirty =
    notes.needs_wishes !== savedNotes.needs_wishes ||
    notes.questions_to_discuss !== savedNotes.questions_to_discuss;

  const included = useMemo(() => filterIncludedImages(images), [images]);
  const includedKeys = useMemo(
    () => new Set(included.map((item) => item.key)),
    [included]
  );
  const hasNeedsWishes = notes.needs_wishes.trim() !== "";
  const hasQuestions = notes.questions_to_discuss.trim() !== "";

  const intentGroups = useMemo(
    () => (brief ? groupIntentEntities(brief.furniture_intents, brief.rooms) : []),
    [brief]
  );

  const sections = useMemo(
    () =>
      brief
        ? briefSectionIds({
            brief,
            includedKeys,
            hasNeedsWishes,
            hasQuestions
          })
        : [],
    [brief, includedKeys, hasNeedsWishes, hasQuestions]
  );

  if (error) {
    return (
      <section className="brief-page" aria-label="Бриф для дизайнера">
        <div className="brief-toolbar">
          <button type="button" className="secondary" onClick={onBack}>
            {BRIEF_BACK_LABEL}
          </button>
        </div>
        <div className="error" aria-live="polite">
          {error}
        </div>
      </section>
    );
  }

  if (!loaded || !brief) {
    return (
      <section className="brief-page" aria-label="Бриф для дизайнера">
        <p className="muted">Собираем бриф…</p>
      </section>
    );
  }

  const banner = briefBannerLines(brief.warnings);
  const headHash = briefShortHash(brief.variant.head_scene_revision_hash);
  const renderItems = included.filter((item) => item.kind === "render");
  const photoItems = included.filter((item) => item.kind === "photo");
  const referenceItems = included.filter((item) => item.kind === "reference");
  const checklistRenders = images.filter((item) => item.kind === "render");
  const checklistPhotos = images.filter((item) => item.kind === "photo");
  const checklistReferences = images.filter((item) => item.kind === "reference");

  function renderSectionBody(id: BriefSectionId) {
    // Unreachable in practice (sections render only below the loaded guard);
    // the guard gives the switch a non-null document to read from.
    if (!brief) return null;
    switch (id) {
      case "notes":
        return <p className="brief-text">{notes.needs_wishes}</p>;
      case "plan":
        return (
          <div className="brief-plan">
            {brief?.plan?.asset_id && (
              <img
                className="brief-plan-image"
                src={api.assetUrl(brief.plan.asset_id)}
                alt="План квартиры"
              />
            )}
            <p className="brief-scale-line">{briefScaleLine(brief)}</p>
          </div>
        );
      case "rooms":
        return (
          <ul className="brief-room-list">
            {(brief?.rooms ?? []).map((room) => (
              <li key={room.id}>
                <span className="brief-room-name">{briefRoomLabel(room)}</span>
                {room.notes && (
                  <span className="brief-room-notes">{room.notes}</span>
                )}
              </li>
            ))}
          </ul>
        );
      case "furniture":
        return (
          <>
            {(intentGroups ?? []).map((group) => (
              <div className="brief-intent-group" key={group.key}>
                <h4>{group.label}</h4>
                <ul>
                  {group.entities.map((entity) => (
                    <li key={entity.id}>
                      {entity.label}
                      {entity.roomLabel ? ` · ${entity.roomLabel}` : ""}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </>
        );
      case "renders":
        return (
          <div className="brief-render-grid">
            {brief?.renders
              ?.map((render, index) => ({ render, index }))
              .filter(
                ({ render, index }) =>
                  render.asset_id &&
                  includedKeys.has(briefImageKey("render", index, render.asset_id))
              )
              .map(({ render, index }) => (
                <figure
                  className="brief-image-card"
                  key={briefImageKey("render", index, render.asset_id)}
                >
                  <img
                    src={api.assetUrl(render.asset_id)}
                    alt={`Рендер ${index + 1}`}
                  />
                  <figcaption>
                    <span className="brief-stage-chip">
                      {renderStageLabel(render.stage)}
                    </span>
                    <span className="brief-disclaimer">
                      {briefRenderDisclaimer(render.label)}
                    </span>
                  </figcaption>
                </figure>
              ))}
          </div>
        );
      case "photos":
        return (
          <div className="brief-render-grid">
            {brief?.photos
              ?.map((photo, index) => ({ photo, index }))
              .filter(
                ({ photo, index }) =>
                  photo.asset_id &&
                  includedKeys.has(briefImageKey("photo", index, photo.asset_id))
              )
              .map(({ photo, index }) => {
                const mappingNote = briefPhotoMappingNote(photo.mapping);
                return (
                  <figure
                    className="brief-image-card"
                    key={briefImageKey("photo", index, photo.asset_id)}
                  >
                    <img
                      src={api.assetUrl(photo.asset_id)}
                      alt={briefPhotoLabel(photo.caption, index)}
                    />
                    <figcaption>
                      <span className="brief-caption">
                        {briefPhotoLabel(photo.caption, index)}
                      </span>
                      {mappingNote && (
                        <span className="brief-mapping">{mappingNote}</span>
                      )}
                    </figcaption>
                  </figure>
                );
              })}
          </div>
        );
      case "style": {
        const style = brief?.style_direction;
        const sourceText = style?.source_text?.trim() ?? "";
        return (
          <>
            {sourceText !== "" && (
              <p className="brief-text">{style?.source_text}</p>
            )}
            {referenceItems.length > 0 && (
              <div className="brief-ref-grid">
                {referenceItems.map((item) => (
                  <img
                    key={item.key}
                    className="brief-ref-thumb"
                    src={api.assetUrl(item.assetId)}
                    alt={item.label}
                  />
                ))}
              </div>
            )}
          </>
        );
      }
      case "budget":
        return (
          <>
            <p className="brief-disclaimer-line">{brief?.budget?.disclaimer}</p>
            <table className="brief-budget-table">
              <thead>
                <tr>
                  <th>Наименование</th>
                  <th>Категория</th>
                  <th>Стоимость</th>
                </tr>
              </thead>
              <tbody>
                {(brief?.budget?.items ?? []).map((item) => (
                  <tr key={item.id}>
                    <td>{item.label}</td>
                    <td>{budgetKindLabel(item.kind)}</td>
                    <td>
                      {item.amount == null
                        ? "не указана"
                        : formatMoney(item.amount, item.currency ?? DEFAULT_BUDGET_CURRENCY)}
                      {item.quantity != null ? ` × ${item.quantity}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        );
      case "questions":
        return <p className="brief-text">{notes.questions_to_discuss}</p>;
    }
  }

  return (
    <section className="brief-page" aria-label="Бриф для дизайнера">
      <div className="brief-toolbar">
        <button type="button" className="secondary brief-back" onClick={onBack}>
          {BRIEF_BACK_LABEL}
        </button>
        <button
          type="button"
          className="brief-download"
          onClick={() => downloadBriefPdf()}
        >
          {BRIEF_DOWNLOAD_LABEL}
        </button>
      </div>

      <div className="brief-layout">
        <div className="brief-preview">
          <header className="brief-doc-header">
            <h2>{brief.project.name}</h2>
            <p className="brief-variant-line">
              <strong>{brief.variant.title}</strong>
              <span className="brief-status">
                {STATUS_LABELS[brief.variant.status] ?? brief.variant.status}
              </span>
              <span className="brief-date">
                {new Date(
                  brief.variant.head_scene_revision_created_at ??
                    brief.project.created_at
                ).toLocaleString("ru-RU")}
              </span>
            </p>
            {headHash && (
              <p className="brief-provenance">Ревизия {headHash}</p>
            )}
            <div className="brief-warnings">
              {banner.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          </header>

          {sections.map((id) => (
            <section className="brief-section" key={id} data-section={id}>
              <h3 className="brief-section-title">{briefSectionTitle(id)}</h3>
              {renderSectionBody(id)}
            </section>
          ))}
        </div>

        <aside className="brief-composer">
          <h3 className="brief-composer-title">
            Заметки для дизайнера
            <span className="brief-save-indicator" aria-live="polite">
              {briefNotesSaveIndicator({ saving, dirty: notesDirty })}
            </span>
          </h3>
          {notesError && <div className="error">{notesError}</div>}

          <label className="brief-field" htmlFor="brief-needs">
            {BRIEF_NEEDS_LABEL}
          </label>
          <textarea
            id="brief-needs"
            value={notes.needs_wishes}
            maxLength={NOTES_MAX_LENGTH}
            onChange={(event) => editNotes("needs_wishes", event.target.value)}
            onBlur={() => void persister.flush()}
            placeholder="Что важно учесть: стиль, приоритеты, ограничения…"
          />

          <label className="brief-field" htmlFor="brief-questions">
            {BRIEF_QUESTIONS_LABEL}
          </label>
          <textarea
            id="brief-questions"
            value={notes.questions_to_discuss}
            maxLength={NOTES_MAX_LENGTH}
            onChange={(event) =>
              editNotes("questions_to_discuss", event.target.value)
            }
            onBlur={() => void persister.flush()}
            placeholder="Вопросы, которые нужно обсудить с дизайнером…"
          />

          {images.length > 0 && (
            <div className="brief-checklist">
              <h4>{BRIEF_CHECKLIST_TITLE}</h4>
              {checklistRenders.length > 0 && (
                <div className="brief-check-group">
                  <h5>Рендеры</h5>
                  {checklistRenders.map((item) => (
                    <label key={item.key} className="brief-check">
                      <input
                        type="checkbox"
                        checked={item.checked}
                        onChange={() => toggleImage(item.key)}
                      />
                      {item.label}
                    </label>
                  ))}
                </div>
              )}
              {checklistPhotos.length > 0 && (
                <div className="brief-check-group">
                  <h5>Фотографии квартиры</h5>
                  {checklistPhotos.map((item) => (
                    <label key={item.key} className="brief-check">
                      <input
                        type="checkbox"
                        checked={item.checked}
                        onChange={() => toggleImage(item.key)}
                      />
                      {item.label}
                    </label>
                  ))}
                </div>
              )}
              {checklistReferences.length > 0 && (
                <div className="brief-check-group">
                  <h5>Референсы стиля</h5>
                  <p className="brief-privacy-note">{BRIEF_PRIVACY_NOTE}</p>
                  {checklistReferences.map((item) => (
                    <label key={item.key} className="brief-check">
                      <input
                        type="checkbox"
                        checked={item.checked}
                        onChange={() => toggleImage(item.key)}
                      />
                      {item.label}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
