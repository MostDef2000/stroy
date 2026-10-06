import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Asset, Job, RenderRecord, SceneRevision } from "./api";
import { ImageLightbox } from "./ImageLightbox";
import { ImagePreview } from "./ImagePreview";
import { statusLabel } from "./copy";
import {
  apiErrorText,
  buildAddFurnitureCommand,
  buildRedesignInput,
  cameraOptionLabel,
  clampStrength,
  entityIdFromName,
  formatRenderSummary,
  redesignResultAssetId,
  REDESIGN_STRENGTH_DEFAULT,
  REDESIGN_STRENGTH_MAX,
  REDESIGN_STRENGTH_MIN,
  referenceImageAssets,
  renderRgbAssetId,
  sortedRendersNewestFirst,
  uniqueId
} from "./twinDesign";

type Props = {
  projectId: string;
  revision: SceneRevision;
  jobs: Job[];
  onChanged: () => Promise<void>;
  onEntityAdded?: (entityId: string) => void;
};

const RENDERER_PROFILE = "blender-cycles-v0";
const DEFAULT_COLOR = "#b8b0a4";

function numeric(value: string, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

// Twin-first design surface (#72 stream 3): render the calibrated twin and add
// furniture objects through the canonical scene-command endpoint. The `revision`
// prop is initial display data only: every mutation/request refetches the latest
// revision first so a concurrent edit in another panel cannot make us stale.
export function TwinDesignPanel({ projectId, revision, jobs, onChanged, onEntityAdded }: Props) {
  const cameras = revision.scene.cameras;
  const rooms = useMemo(
    () => revision.scene.entities.filter((entity) => entity.kind === "room"),
    [revision.scene.entities]
  );

  const [cameraId, setCameraId] = useState(cameras[0]?.id ?? "");
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [renderError, setRenderError] = useState("");
  const [renderInfo, setRenderInfo] = useState("");

  const [renders, setRenders] = useState<RenderRecord[]>([]);
  const [rendersError, setRendersError] = useState("");
  const [selectedRenderId, setSelectedRenderId] = useState<string | null>(null);

  // Design variant (whole-frame redesign from a render's rgb pass). The panel
  // does not receive the project asset list, so reference photos are fetched
  // here with the same role/media filter PhotoEditPanel uses.
  const [assets, setAssets] = useState<Asset[]>([]);
  const [variantPrompt, setVariantPrompt] = useState("");
  const [variantStrength, setVariantStrength] = useState(REDESIGN_STRENGTH_DEFAULT);
  const [variantReferenceId, setVariantReferenceId] = useState("");
  const [variantJobId, setVariantJobId] = useState<string | null>(null);
  const [variantError, setVariantError] = useState("");
  const [variantInfo, setVariantInfo] = useState("");
  const [variantResultAssetId, setVariantResultAssetId] = useState<string | null>(null);

  const [label, setLabel] = useState("");
  const [roomId, setRoomId] = useState("");
  const [dimW, setDimW] = useState("2000");
  const [dimD, setDimD] = useState("900");
  const [dimH, setDimH] = useState("800");
  const [posX, setPosX] = useState("0");
  const [posY, setPosY] = useState("0");
  const [posZ, setPosZ] = useState("0");
  const [rotZ, setRotZ] = useState("0");
  const [color, setColor] = useState(DEFAULT_COLOR);
  const [busy, setBusy] = useState(false);
  const [furnitureError, setFurnitureError] = useState("");
  const [furnitureInfo, setFurnitureInfo] = useState("");

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

  // Keep the camera selection valid as the scene revision changes.
  useEffect(() => {
    if (!cameras.some((camera) => camera.id === cameraId)) {
      setCameraId(cameras[0]?.id ?? "");
    }
  }, [cameras, cameraId]);

  useEffect(() => {
    setLabel("");
    setRoomId("");
    setFurnitureError("");
    setFurnitureInfo("");
    setSelectedRenderId(null);
    setVariantPrompt("");
    setVariantStrength(REDESIGN_STRENGTH_DEFAULT);
    setVariantReferenceId("");
    setVariantJobId(null);
    setVariantError("");
    setVariantInfo("");
    setVariantResultAssetId(null);
  }, [projectId]);

  const loadRenders = useCallback(async () => {
    try {
      const list = await api.listRenders(projectId);
      setRenders(list);
      setRendersError("");
    } catch (reason) {
      setRendersError(apiErrorText(reason));
    }
  }, [projectId]);

  const loadAssets = useCallback(async () => {
    try {
      setAssets(await api.assets(projectId));
    } catch {
      // Reference selection is optional; a failed asset fetch must not break
      // the render/furniture flows, so leave the list empty.
      setAssets([]);
    }
  }, [projectId]);

  useEffect(() => {
    void loadRenders();
    void loadAssets();
  }, [loadRenders, loadAssets]);

  const activeRenderJob = useMemo(
    () => (pendingJobId ? jobs.find((job) => job.id === pendingJobId) ?? null : null),
    [jobs, pendingJobId]
  );

  useEffect(() => {
    if (!pendingJobId || activeRenderJob?.status !== "succeeded") return;
    setPendingJobId(null);
    setRenderInfo("Рендер готов.");
    void loadRenders();
  }, [activeRenderJob, pendingJobId, loadRenders]);

  useEffect(() => {
    if (!pendingJobId) return;
    if (activeRenderJob?.status !== "failed" && activeRenderJob?.status !== "cancelled") {
      return;
    }
    setPendingJobId(null);
    setRenderError(`Задача рендера: ${statusLabel(activeRenderJob.status)}.`);
  }, [activeRenderJob, pendingJobId]);

  const renderStatus = useMemo(() => {
    if (!pendingJobId) return null;
    if (!activeRenderJob) return statusLabel("queued");
    const fraction =
      typeof activeRenderJob.progress["fraction"] === "number"
        ? Math.round((activeRenderJob.progress["fraction"] as number) * 100)
        : null;
    const label = statusLabel(activeRenderJob.status);
    return fraction === null ? label : `${label} · ${fraction}%`;
  }, [activeRenderJob, pendingJobId]);

  const orderedRenders = useMemo(() => sortedRendersNewestFirst(renders), [renders]);
  const selectedRender = useMemo(
    () => orderedRenders.find((item) => item.id === selectedRenderId) ?? null,
    [orderedRenders, selectedRenderId]
  );
  const selectedRgbId = selectedRender ? renderRgbAssetId(selectedRender.manifest) : null;

  const referenceAssets = useMemo(() => referenceImageAssets(assets), [assets]);

  const activeVariantJob = useMemo(
    () => (variantJobId ? jobs.find((job) => job.id === variantJobId) ?? null : null),
    [jobs, variantJobId]
  );

  useEffect(() => {
    if (!variantJobId || activeVariantJob?.status !== "succeeded") return;
    setVariantJobId(null);
    const resultAssetId = redesignResultAssetId(activeVariantJob);
    if (resultAssetId) {
      setVariantResultAssetId(resultAssetId);
      setVariantInfo("Вариант дизайна готов.");
    } else {
      setVariantError("Задача завершилась без выходного изображения.");
    }
    void loadAssets();
  }, [activeVariantJob, variantJobId, loadAssets]);

  useEffect(() => {
    if (!variantJobId) return;
    if (activeVariantJob?.status !== "failed" && activeVariantJob?.status !== "cancelled") {
      return;
    }
    setVariantJobId(null);
    setVariantError(`Вариант дизайна: ${statusLabel(activeVariantJob.status)}.`);
  }, [activeVariantJob, variantJobId]);

  const variantStatus = useMemo(() => {
    if (!variantJobId) return null;
    if (!activeVariantJob) return statusLabel("queued");
    const fraction =
      typeof activeVariantJob.progress["fraction"] === "number"
        ? Math.round((activeVariantJob.progress["fraction"] as number) * 100)
        : null;
    const label = statusLabel(activeVariantJob.status);
    return fraction === null ? label : `${label} · ${fraction}%`;
  }, [activeVariantJob, variantJobId]);

  async function startRender() {
    if (pendingJobId) return;
    setRenderError("");
    setRenderInfo("");
    try {
      // POST /renders resolves the latest revision server-side when
      // scene_revision_id is omitted, but camera_id must exist in that latest
      // scene — read the fresh cameras before submitting.
      const fresh = await api.scene(projectId);
      if (!fresh || fresh.scene.cameras.length === 0) {
        setRenderError("Сцена ещё не инициализирована или в ней нет камер.");
        return;
      }
      const camera = fresh.scene.cameras.some((item) => item.id === cameraId)
        ? cameraId
        : fresh.scene.cameras[0].id;
      if (camera !== cameraId) setCameraId(camera);
      const job = await api.createRender(projectId, {
        camera_id: camera,
        renderer_profile: RENDERER_PROFILE
      });
      setPendingJobId(job.id);
      await onChanged();
    } catch (reason) {
      setRenderError(apiErrorText(reason));
    }
  }

  async function addFurniture(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const name = label.trim();
    if (!name) {
      setFurnitureError("Укажите название предмета.");
      return;
    }
    setBusy(true);
    setFurnitureError("");
    setFurnitureInfo("");
    try {
      // The command base_revision_id must match the current latest revision, so
      // never trust the prop's revision id here.
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setFurnitureError("Сцена ещё не инициализирована.");
        return;
      }
      const suffix = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      const entityId = entityIdFromName(name, suffix);
      const command = buildAddFurnitureCommand({
        commandId: uniqueId(),
        baseRevisionId: fresh.revision_id,
        entityId,
        label: name,
        roomId: roomId || null,
        dimensionsMm: [numeric(dimW), numeric(dimD), numeric(dimH)],
        positionMm: [numeric(posX), numeric(posY), numeric(posZ)],
        rotationZdeg: numeric(rotZ),
        color
      });
      await api.applySceneCommand(projectId, command);
      onEntityAdded?.(entityId);
      setFurnitureInfo(
        "Объект добавлен и выбран в сцене — перетащите его, чтобы разместить."
      );
      setLabel("");
      await onChanged();
    } catch (reason) {
      setFurnitureError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function runDesignVariant() {
    if (variantJobId) return;
    const prompt = variantPrompt.trim();
    if (!prompt) {
      setVariantError("Опишите желаемый вариант дизайна.");
      return;
    }
    if (!selectedRgbId) {
      setVariantError("У выбранного рендера нет rgb-пасса — вариант недоступен.");
      return;
    }
    setVariantError("");
    setVariantInfo("");
    setVariantResultAssetId(null);
    try {
      // Same freshness rule as addFurniture: the redesign base_revision_id must
      // equal the current latest revision or the backend answers 409.
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setVariantError("Сцена ещё не инициализирована.");
        return;
      }
      const response = await api.createRedesign(
        projectId,
        buildRedesignInput({
          baseRevisionId: fresh.revision_id,
          baseAssetId: selectedRgbId,
          prompt,
          strength: variantStrength,
          referenceAssetId: variantReferenceId || null
        })
      );
      setVariantJobId(response.job.id);
      await onChanged();
    } catch (reason) {
      setVariantError(apiErrorText(reason));
    }
  }

  return (
    <article className="panel td-panel">
      <div className="panel-heading td-heading">
        <h2>Дизайн двойника</h2>
      </div>

      <section className="td-section">
        <div className="td-head">
          <h3>Рендер вида</h3>
        </div>
        {cameras.length === 0 ? (
          <p className="muted">
            В сцене нет камер — добавьте камеру в панели калибровки.
          </p>
        ) : (
          <>
            <div className="td-controls">
              <label>
                Камера
                <select
                  value={cameraId}
                  onChange={(event) => setCameraId(event.target.value)}
                >
                  {cameras.map((camera) => (
                    <option key={camera.id} value={camera.id}>
                      {cameraOptionLabel(camera)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={() => void startRender()}
                disabled={pendingJobId !== null}
              >
                {pendingJobId ? "Рендер…" : "Отрендерить вид"}
              </button>
            </div>
            <p className="hint">
              Рендерер {RENDERER_PROFILE} · последняя версия сцены.
            </p>
            {renderStatus && <p className="muted td-status">Задача рендера: {renderStatus}</p>}
            {renderError && <div className="error td-error">{renderError}</div>}
            {renderInfo && <div className="td-result">{renderInfo}</div>}
          </>
        )}
      </section>

      <section className="td-section">
        <div className="td-head">
          <h3>Рендеры</h3>
          <button type="button" className="secondary" onClick={() => void loadRenders()}>
            Обновить
          </button>
        </div>
        {rendersError && <div className="error td-error">{rendersError}</div>}
        {orderedRenders.length === 0 && <p className="muted">Рендеров пока нет.</p>}
        {orderedRenders.length > 0 && (
          <ul className="td-list">
            {orderedRenders.map((item) => {
              const rgbId = renderRgbAssetId(item.manifest);
              return (
                <li
                  key={item.id}
                  className={item.id === selectedRenderId ? "td-item selected" : "td-item"}
                >
                  <button
                    type="button"
                    className="td-item-button"
                    onClick={() =>
                      setSelectedRenderId(item.id === selectedRenderId ? null : item.id)
                    }
                  >
                    <span className="td-item-id">#{item.id.slice(0, 8)}</span>
                    <span className="td-item-camera">{item.camera_id}</span>
                    <span className="td-item-summary">{formatRenderSummary(item.manifest)}</span>
                    <span className="td-item-time">
                      {new Date(item.created_at).toLocaleString()}
                    </span>
                    <span className="tag">{rgbId ? "rgb" : "no rgb"}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {selectedRender && (
          <div className="td-preview">
            {selectedRgbId ? (
              <ImagePreview
                variant="bounded"
                src={api.assetUrl(selectedRgbId)}
                alt={`Рендер ${selectedRender.id}`}
                expandable
                onExpand={(trigger) =>
                  openLightbox(
                    api.assetUrl(selectedRgbId),
                    `Рендер ${selectedRender.id}`,
                    `Рендер ${selectedRender.id}`,
                    trigger
                  )
                }
              />
            ) : (
              <p className="muted">У выбранного рендера нет rgb-пасса.</p>
            )}
          </div>
        )}
      </section>

      {selectedRender && (
        <section className="td-section">
          <div className="td-head">
            <h3>Вариант дизайна</h3>
          </div>
          {!selectedRgbId ? (
            <p className="muted">
              У выбранного рендера нет rgb-пасса — вариант дизайна недоступен.
            </p>
          ) : (
            <div className="td-form">
              <p className="hint">
                img2img по rgb-пассу рендера #{selectedRender.id.slice(0, 8)} · последняя версия сцены.
              </p>
              <label>
                Инструкция
                <textarea
                  rows={3}
                  value={variantPrompt}
                  onChange={(event) => setVariantPrompt(event.target.value)}
                  placeholder="Опишите желаемый вариант дизайна…"
                />
              </label>
              <label>
                Сила · {variantStrength.toFixed(2)}
                <input
                  type="range"
                  min={REDESIGN_STRENGTH_MIN}
                  max={REDESIGN_STRENGTH_MAX}
                  step={0.05}
                  value={variantStrength}
                  onChange={(event) =>
                    setVariantStrength(clampStrength(Number(event.target.value)))
                  }
                />
              </label>
              <label>
                Референс стиля (необязательно)
                <select
                  value={variantReferenceId}
                  onChange={(event) => setVariantReferenceId(event.target.value)}
                >
                  <option value="">Нет — только инструкция</option>
                  {referenceAssets.map((asset) => (
                    <option key={asset.id} value={asset.id}>
                      {asset.original_name ?? asset.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="td-actions">
                <button
                  type="button"
                  onClick={() => void runDesignVariant()}
                  disabled={variantJobId !== null || !variantPrompt.trim()}
                >
                  {variantJobId ? "Выполняется…" : "Запустить вариант"}
                </button>
              </div>
              {variantStatus && (
                <p className="muted td-status">Вариант дизайна: {variantStatus}</p>
              )}
              {variantError && <div className="error td-error">{variantError}</div>}
              {variantInfo && <div className="td-result">{variantInfo}</div>}
              {variantResultAssetId && (
                <div className="td-preview">
                  <ImagePreview
                    variant="bounded"
                    src={api.assetUrl(variantResultAssetId)}
                    alt="Результат варианта дизайна"
                    expandable
                    onExpand={(trigger) =>
                      openLightbox(
                        api.assetUrl(variantResultAssetId),
                        "Результат варианта дизайна",
                        "Вариант дизайна",
                        trigger
                      )
                    }
                  />
                </div>
              )}
            </div>
          )}
        </section>
      )}

      <section className="td-section">
        <div className="td-head">
          <h3>Добавить мебель</h3>
        </div>
        <form className="td-form" onSubmit={(event) => void addFurniture(event)}>
          <div className="td-grid">
            <label>
              Название
              <input
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder="Диван"
              />
            </label>
            <label>
              Комната
              <select value={roomId} onChange={(event) => setRoomId(event.target.value)}>
                <option value="">без комнаты</option>
                {rooms.map((room) => (
                  <option key={room.id} value={room.id}>
                    {room.display_name ?? room.id}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="td-grid td-grid-3">
            <label>
              Ширина (мм)
              <input type="number" value={dimW} onChange={(e) => setDimW(e.target.value)} />
            </label>
            <label>
              Глубина (мм)
              <input type="number" value={dimD} onChange={(e) => setDimD(e.target.value)} />
            </label>
            <label>
              Высота (мм)
              <input type="number" value={dimH} onChange={(e) => setDimH(e.target.value)} />
            </label>
          </div>

          <p className="muted">Тип: мебель</p>

          <details className="td-advanced">
            <summary>Точные параметры</summary>
            <div className="td-grid td-grid-3">
              <label>
                Позиция X (мм)
                <input type="number" value={posX} onChange={(e) => setPosX(e.target.value)} />
              </label>
              <label>
                Позиция Y (мм)
                <input type="number" value={posY} onChange={(e) => setPosY(e.target.value)} />
              </label>
              <label>
                Позиция Z (мм)
                <input type="number" value={posZ} onChange={(e) => setPosZ(e.target.value)} />
              </label>
              <label>
                Поворот Z (°)
                <input type="number" value={rotZ} onChange={(e) => setRotZ(e.target.value)} />
              </label>
              <label>
                Цвет
                <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
              </label>
            </div>
          </details>

          <div className="td-actions">
            <button type="submit" disabled={busy}>
              {busy ? "Добавляем…" : "Добавить"}
            </button>
          </div>
          {furnitureError && <div className="error td-error">{furnitureError}</div>}
          {furnitureInfo && <div className="td-result">{furnitureInfo}</div>}
        </form>
      </section>

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
