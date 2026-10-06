import { PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, Asset, AssetRole, Generation, Job, SceneRevision } from "./api";
import { ImageLightbox } from "./ImageLightbox";
import { ImagePreview } from "./ImagePreview";
import { statusLabel } from "./copy";

type Rect = { x: number; y: number; w: number; h: number };
type EditAction = "Replace" | "Remove" | "Restyle";
type Pending = { jobId: string; revisionId: string; baseAssetId: string };
type Iteration = { baseAssetId: string; resultAssetId: string; resultRevisionId: string };

// Model-facing prompt values — deliberately English; do not translate.
const ACTION_PROMPTS: Record<EditAction, string> = {
  Replace: "replace the selected object with a new object matching the reference",
  Remove: "remove the selected object and naturally fill the background",
  Restyle: "restyle the selected object to match the reference"
};
const ACTION_LABELS: Record<EditAction, string> = {
  Replace: "Заменить",
  Remove: "Убрать",
  Restyle: "Переделать"
};
const MIN_SELECTION_PX = 8;

function errorText(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
function jobError(job: Job | null): string | null {
  if (!job?.error) return null;
  const code = typeof job.error["code"] === "string" ? (job.error["code"] as string) : "job_failed";
  const detail =
    typeof job.error["detail"] === "string" ? (job.error["detail"] as string) : JSON.stringify(job.error);
  return `${code}: ${detail}`;
}
function outputFromJob(job: Job | null): string | null {
  const raw = job?.result?.["output_asset_ids"];
  return Array.isArray(raw) && typeof raw[0] === "string" ? raw[0] : null;
}

export function PhotoEditPanel({
  projectId, revision, assets, jobs, generations, onChanged
}: {
  projectId: string;
  revision: SceneRevision;
  assets: Asset[];
  jobs: Job[];
  generations: Generation[];
  onChanged: () => Promise<void>;
}) {
  const imageAssets = useMemo(() => ({
    photos: assets.filter((asset) => asset.media_type.startsWith("image/") && (asset.role === "apartment" || asset.role === "derived")),
    references: assets.filter((asset) => asset.role === "reference" && asset.media_type.startsWith("image/"))
  }), [assets]);

  const [photoId, setPhotoId] = useState("");
  const [referenceId, setReferenceId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [action, setAction] = useState<EditAction>("Replace");
  const [shape, setShape] = useState<"rectangle" | "silhouette">("rectangle");
  const [ipaWeight, setIpaWeight] = useState(0.85);
  const [strength, setStrength] = useState(0.6);
  const [rect, setRect] = useState<Rect | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [photoProgress, setPhotoProgress] = useState<number | null>(null);
  const [referenceProgress, setReferenceProgress] = useState<number | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [iteration, setIteration] = useState<Iteration | null>(null);
  const [revisionOverride, setRevisionOverride] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  // Shared lightbox (#150): primitives only — App.tsx polls every 5s and
  // replaces object identities, so lightbox state must never hold objects.
  const [lightbox, setLightbox] = useState<{
    src: string;
    alt: string;
    title: string;
  } | null>(null);
  const lightboxTriggerRef = useRef<HTMLElement | null>(null);

  function openLightbox(src: string, alt: string, title: string, trigger: HTMLElement) {
    lightboxTriggerRef.current = trigger;
    setLightbox({ src, alt, title });
  }

  const resolvedPhotoId = photoId || imageAssets.photos[0]?.id || "";
  const baseRevisionId = revisionOverride ?? revision.revision_id;

  useEffect(() => {
    setPhotoId(""); setReferenceId(""); setPrompt(""); setRect(null);
    setIteration(null); setPending(null); setRevisionOverride(null); setError("");
  }, [projectId]);

  useEffect(() => { setRect(null); setIteration(null); }, [resolvedPhotoId]);

  const activeJob = useMemo(
    () => (pending ? jobs.find((job) => job.id === pending.jobId) ?? null : null),
    [jobs, pending]
  );

  useEffect(() => {
    if (!pending || activeJob?.status !== "succeeded") return;
    const fallback = generations.find((generation) => generation.manifest.design_revision_id === pending.revisionId)
      ?.manifest.output_asset_ids?.[0];
    const resultAssetId = outputFromJob(activeJob) ?? fallback ?? null;
    if (resultAssetId) setIteration({ baseAssetId: pending.baseAssetId, resultAssetId, resultRevisionId: pending.revisionId });
    setPending(null);
  }, [activeJob, generations, pending]);

  function pointerPos(event: ReactPointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return { x: clamp(event.clientX - bounds.left, 0, bounds.width), y: clamp(event.clientY - bounds.top, 0, bounds.height) };
  }
  function startDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!resolvedPhotoId) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = pointerPos(event);
    dragStart.current = point;
    setDragging(true);
    setRect({ x: point.x, y: point.y, w: 0, h: 0 });
  }
  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!dragging || !dragStart.current) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const point = pointerPos(event);
    const start = dragStart.current;
    const x = Math.min(start.x, point.x);
    const y = Math.min(start.y, point.y);
    setRect({ x, y, w: Math.min(bounds.width, Math.max(start.x, point.x)) - x, h: Math.min(bounds.height, Math.max(start.y, point.y)) - y });
  }
  function endDrag() {
    setDragging(false);
    dragStart.current = null;
    setRect((current) => (current && current.w >= MIN_SELECTION_PX && current.h >= MIN_SELECTION_PX ? current : null));
  }

  async function uploadFile(
    file: File | null,
    role: AssetRole,
    setProgress: (percent: number | null) => void,
    onAsset?: (assetId: string) => void
  ) {
    if (!file) return;
    setError("");
    try {
      setProgress(0);
      const asset = await api.upload(projectId, file, role, setProgress);
      onAsset?.(asset.id);
      await onChanged();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      window.setTimeout(() => setProgress(null), 800);
    }
  }

  function handlePhoto(file: File | null) {
    return uploadFile(file, "apartment", setPhotoProgress, (assetId) => {
      setPhotoId(assetId); setRect(null); setIteration(null);
    });
  }
  function handleReference(file: File | null) {
    return uploadFile(file, "reference", setReferenceProgress, setReferenceId);
  }

  function naturalRegion(): [number, number, number, number] | null {
    const image = imgRef.current;
    if (!image || !rect || !image.naturalWidth || !image.clientWidth) return null;
    const sx = image.naturalWidth / image.clientWidth;
    const sy = image.naturalHeight / image.clientHeight;
    const region: [number, number, number, number] = [
      clamp(Math.round(rect.x * sx), 0, image.naturalWidth),
      clamp(Math.round(rect.y * sy), 0, image.naturalHeight),
      clamp(Math.round((rect.x + rect.w) * sx), 0, image.naturalWidth),
      clamp(Math.round((rect.y + rect.h) * sy), 0, image.naturalHeight)
    ];
    return region[2] - region[0] < 1 || region[3] - region[1] < 1 ? null : region;
  }

  async function accept(job: Job, revisionId: string, baseAssetId: string) {
    setPending({ jobId: job.id, revisionId, baseAssetId });
    setRevisionOverride(revisionId);
    await onChanged();
  }
  async function submitRegion() {
    if (!resolvedPhotoId || !prompt.trim() || busy) return;
    const region = naturalRegion();
    if (!region) return setError("Фото ещё загружается или выделение слишком мало.");
    setBusy(true); setError("");
    try {
      const response = await api.createRegionReplacement(projectId, {
        base_revision_id: baseRevisionId, base_asset_id: resolvedPhotoId, mask_region: region,
        prompt: prompt.trim(), reference_asset_id: referenceId || undefined,
        ipa_weight: referenceId ? ipaWeight : undefined, shape
      });
      await accept(response.job, response.revision_id, resolvedPhotoId);
    } catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  async function submitFullFrame() {
    if (!resolvedPhotoId || !prompt.trim() || busy) return;
    setBusy(true); setError("");
    try {
      const response = await api.createRedesign(projectId, {
        base_revision_id: baseRevisionId, base_asset_id: resolvedPhotoId,
        reference_asset_id: referenceId || undefined, prompt: prompt.trim(), strength
      });
      await accept(response.job, response.revision_id, resolvedPhotoId);
    } catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  function applyResultAsBase() {
    if (!iteration) return;
    setPhotoId(iteration.resultAssetId);
    setRevisionOverride(iteration.resultRevisionId);
    setIteration(null); setRect(null); setPrompt("");
  }

  const fraction = typeof activeJob?.progress["fraction"] === "number" ? Math.round((activeJob.progress["fraction"] as number) * 100) : null;
  const phase = typeof activeJob?.progress["phase"] === "string" ? (activeJob.progress["phase"] as string) : null;
  const progressText = activeJob ? [phase, fraction !== null ? `${fraction}%` : null].filter(Boolean).join(" · ") : "";

  const referencePicker = (
    <>
      <label>
        Референс (необязательно)
        <select value={referenceId} onChange={(event) => setReferenceId(event.target.value)}>
          <option value="">Нет — только инструкция</option>
          {imageAssets.references.map((asset) => (
            <option key={asset.id} value={asset.id}>{asset.original_name ?? asset.id.slice(0, 8)}</option>
          ))}
        </select>
      </label>
      <label className="upload">
        Загрузить референс
        <input type="file" accept="image/*" onChange={(event) => void handleReference(event.target.files?.[0] ?? null)} />
      </label>
      {referenceProgress !== null && <span className="muted">Загрузка референса… {referenceProgress}%</span>}
    </>
  );

  return (
    <article className="panel photo-edit-panel">
      <div className="panel-heading pe-heading">
        <h2>Редактор фото</h2>
        <select aria-label="Рабочее фото" value={resolvedPhotoId} onChange={(event) => setPhotoId(event.target.value)}>
          {imageAssets.photos.length === 0 && <option value="">Фотографий пока нет</option>}
          {resolvedPhotoId && !imageAssets.photos.some((asset) => asset.id === resolvedPhotoId) && (
            <option value={resolvedPhotoId}>текущее фото</option>
          )}
          {imageAssets.photos.map((asset) => (
            <option key={asset.id} value={asset.id}>{asset.original_name ?? asset.id.slice(0, 8)}</option>
          ))}
        </select>
      </div>

      <div className="pe-upload-row">
        <label className="upload">
          Загрузить фото
          <input type="file" accept="image/*" onChange={(event) => void handlePhoto(event.target.files?.[0] ?? null)} />
        </label>
        {photoProgress !== null && <span className="muted">Загрузка фото… {photoProgress}%</span>}
      </div>

      {!resolvedPhotoId && <p className="muted">Загрузите фото комнаты, чтобы начать редактирование.</p>}

      {resolvedPhotoId && (
        <>
          <div className="pe-stage">
            <div className="pe-image-wrap">
              <img ref={imgRef} src={api.assetUrl(resolvedPhotoId)} alt="Рабочее фото" draggable={false} />
              <div
                className="pe-overlay"
                onPointerDown={startDrag}
                onPointerMove={moveDrag}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
              >
                {rect && <div className="pe-bbox" style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }} />}
              </div>
            </div>
          </div>

          <div className="pe-toolbar">
            {rect ? (
              <div className="pe-chips">
                {(Object.keys(ACTION_PROMPTS) as EditAction[]).map((name) => (
                  <button
                    key={name}
                    type="button"
                    className={action === name ? "secondary active" : "secondary"}
                    onClick={() => { setAction(name); setPrompt(ACTION_PROMPTS[name]); }}
                  >
                    {ACTION_LABELS[name]}
                  </button>
                ))}
              </div>
            ) : (
              <p className="hint">Выделите область на фото или переделайте весь кадр.</p>
            )}

            <label>
              Инструкция правки
              <textarea
                rows={3}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder={rect ? "Опишите правку области…" : "Опишите переделку комнаты…"}
              />
            </label>
            {referencePicker}

            {rect ? (
              <>
                <details className="pe-advanced">
                  <summary>Параметры</summary>
                  {referenceId && (
                    <label>
                      Вес IP-Adapter · {ipaWeight.toFixed(2)}
                      <input type="range" min={0} max={1} step={0.05} value={ipaWeight} onChange={(event) => setIpaWeight(Number(event.target.value))} />
                    </label>
                  )}
                  <label>
                    Форма маски
                    <select value={shape} onChange={(event) => setShape(event.target.value as "rectangle" | "silhouette")}>
                      <option value="rectangle">Прямоугольник</option>
                      <option value="silhouette">Силуэт</option>
                    </select>
                  </label>
                </details>
                <div className="pe-actions">
                  <button type="button" onClick={() => void submitRegion()} disabled={busy || !prompt.trim()}>Запустить правку области</button>
                  <button type="button" className="secondary" onClick={() => setRect(null)} disabled={busy}>Сбросить выделение</button>
                </div>
              </>
            ) : (
              <>
                <details className="pe-advanced">
                  <summary>Параметры</summary>
                  <label>
                    Сила переделки · {strength.toFixed(2)}
                    <input type="range" min={0.2} max={0.95} step={0.05} value={strength} onChange={(event) => setStrength(Number(event.target.value))} />
                  </label>
                </details>
                <button type="button" onClick={() => void submitFullFrame()} disabled={busy || !prompt.trim()}>Запустить переделку</button>
              </>
            )}
          </div>
        </>
      )}

      {iteration && (
        <div className="pe-result">
          <div className="compare-grid">
            {([["До", iteration.baseAssetId, "рабочее фото"],
              ["После", iteration.resultAssetId, `ревизия ${iteration.resultRevisionId.slice(0, 8)}`]] as const).map(
              ([label, assetId, caption]) => (
                <figure key={label}>
                  <figcaption><strong>{label}</strong><span>{caption}</span></figcaption>
                  <ImagePreview
                    variant="bounded"
                    src={api.assetUrl(assetId)}
                    alt={`${label} — ${caption}`}
                    expandable
                    onExpand={(trigger) =>
                      openLightbox(
                        api.assetUrl(assetId),
                        `${label} — ${caption}`,
                        `Правка фото — ${label}`,
                        trigger
                      )
                    }
                  />
                </figure>
              )
            )}
          </div>
          <button type="button" onClick={applyResultAsBase}>Сделать основным</button>
        </div>
      )}

      {pending && (
        <p className={activeJob?.status === "failed" ? "error" : "muted"}>
          Правка: {statusLabel(activeJob?.status ?? "pending")}{progressText ? ` · ${progressText}` : ""}
        </p>
      )}
      {jobError(activeJob) && <div className="error">{jobError(activeJob)}</div>}
      {error && <div className="error">{error}</div>}

      <ImageLightbox
        open={lightbox !== null}
        src={lightbox?.src ?? null}
        alt={lightbox?.alt ?? ""}
        title={lightbox?.title}
        onClose={() => setLightbox(null)}
        returnFocusRef={lightboxTriggerRef}
      />
    </article>
  );
}
