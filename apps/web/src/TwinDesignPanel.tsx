import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, Job, RenderRecord, SceneRevision } from "./api";
import {
  apiErrorText,
  buildAddFurnitureCommand,
  cameraOptionLabel,
  entityIdFromName,
  formatRenderSummary,
  renderRgbAssetId,
  sortedRendersNewestFirst,
  uniqueId
} from "./twinDesign";

type Props = {
  projectId: string;
  revision: SceneRevision;
  jobs: Job[];
  onChanged: () => Promise<void>;
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
export function TwinDesignPanel({ projectId, revision, jobs, onChanged }: Props) {
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

  useEffect(() => {
    void loadRenders();
  }, [loadRenders]);

  const activeRenderJob = useMemo(
    () => (pendingJobId ? jobs.find((job) => job.id === pendingJobId) ?? null : null),
    [jobs, pendingJobId]
  );

  useEffect(() => {
    if (!pendingJobId || activeRenderJob?.status !== "succeeded") return;
    setPendingJobId(null);
    setRenderInfo("Render finished.");
    void loadRenders();
  }, [activeRenderJob, pendingJobId, loadRenders]);

  useEffect(() => {
    if (!pendingJobId) return;
    if (activeRenderJob?.status !== "failed" && activeRenderJob?.status !== "cancelled") {
      return;
    }
    setPendingJobId(null);
    setRenderError(`Render job ${activeRenderJob.status}.`);
  }, [activeRenderJob, pendingJobId]);

  const renderStatus = useMemo(() => {
    if (!pendingJobId) return null;
    if (!activeRenderJob) return "queued";
    const fraction =
      typeof activeRenderJob.progress["fraction"] === "number"
        ? Math.round((activeRenderJob.progress["fraction"] as number) * 100)
        : null;
    return fraction === null ? activeRenderJob.status : `${activeRenderJob.status} · ${fraction}%`;
  }, [activeRenderJob, pendingJobId]);

  const orderedRenders = useMemo(() => sortedRendersNewestFirst(renders), [renders]);
  const selectedRender = useMemo(
    () => orderedRenders.find((item) => item.id === selectedRenderId) ?? null,
    [orderedRenders, selectedRenderId]
  );
  const selectedRgbId = selectedRender ? renderRgbAssetId(selectedRender.manifest) : null;

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
      const response = await api.applySceneCommand(projectId, command);
      setFurnitureInfo(
        `Добавлено ${entityId} · revision ${response.revision_id.slice(0, 8)}. Render to see the object.`
      );
      setLabel("");
      await onChanged();
    } catch (reason) {
      setFurnitureError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="panel td-panel">
      <div className="panel-heading td-heading">
        <h2>Twin design</h2>
      </div>

      <section className="td-section">
        <div className="td-head">
          <h3>Render view</h3>
        </div>
        {cameras.length === 0 ? (
          <p className="muted">
            В сцене нет камер — добавьте камеру в панели калибровки.
          </p>
        ) : (
          <>
            <div className="td-controls">
              <label>
                Camera
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
                {pendingJobId ? "Rendering…" : "Render view"}
              </button>
            </div>
            <p className="hint">
              renderer {RENDERER_PROFILE} · latest scene revision.
            </p>
            {renderStatus && <p className="muted td-status">Render job: {renderStatus}</p>}
            {renderError && <div className="error td-error">{renderError}</div>}
            {renderInfo && <div className="td-result">{renderInfo}</div>}
          </>
        )}
      </section>

      <section className="td-section">
        <div className="td-head">
          <h3>Renders</h3>
          <button type="button" className="secondary" onClick={() => void loadRenders()}>
            Refresh
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
              <img src={api.assetUrl(selectedRgbId)} alt={`Render ${selectedRender.id}`} />
            ) : (
              <p className="muted">У выбранного рендера нет rgb-пасса.</p>
            )}
          </div>
        )}
      </section>

      <section className="td-section">
        <div className="td-head">
          <h3>Add furniture</h3>
        </div>
        <form className="td-form" onSubmit={(event) => void addFurniture(event)}>
          <div className="td-grid">
            <label>
              Kind
              <input value="furniture" disabled />
            </label>
            <label>
              Name / label
              <input
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder="Диван"
              />
            </label>
            <label>
              Room
              <select value={roomId} onChange={(event) => setRoomId(event.target.value)}>
                <option value="">none</option>
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
              width mm
              <input type="number" value={dimW} onChange={(e) => setDimW(e.target.value)} />
            </label>
            <label>
              depth mm
              <input type="number" value={dimD} onChange={(e) => setDimD(e.target.value)} />
            </label>
            <label>
              height mm
              <input type="number" value={dimH} onChange={(e) => setDimH(e.target.value)} />
            </label>
            <label>
              pos x mm
              <input type="number" value={posX} onChange={(e) => setPosX(e.target.value)} />
            </label>
            <label>
              pos y mm
              <input type="number" value={posY} onChange={(e) => setPosY(e.target.value)} />
            </label>
            <label>
              pos z mm
              <input type="number" value={posZ} onChange={(e) => setPosZ(e.target.value)} />
            </label>
            <label>
              rotation z°
              <input type="number" value={rotZ} onChange={(e) => setRotZ(e.target.value)} />
            </label>
            <label>
              color
              <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
            </label>
          </div>

          <div className="td-actions">
            <button type="submit" disabled={busy}>
              {busy ? "Добавляем…" : "Add furniture"}
            </button>
          </div>
          {furnitureError && <div className="error td-error">{furnitureError}</div>}
          {furnitureInfo && <div className="td-result">{furnitureInfo}</div>}
        </form>
      </section>
    </article>
  );
}
