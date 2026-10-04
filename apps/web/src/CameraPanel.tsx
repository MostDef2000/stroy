import {
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { api, Asset, SceneCamera, SceneRevision } from "./api";
import { deriveAnchors } from "./cameraAnchors";

type Props = {
  projectId: string;
  revision: SceneRevision;
  assets: Asset[];
  onChanged: () => Promise<void>;
};

type Correspondence = {
  key: string;
  imagePx: [number, number];
  anchorId: string;
};

type SolveSummary = {
  residual: number | null;
  quality: number | null;
  count: number;
  transform: SceneCamera["transform"];
};

function numeric(value: string, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function formatTriple(values: [number, number, number], digits: number): string {
  return values.map((value) => value.toFixed(digits)).join(", ");
}

// Mirrors the request helper: the API error message is "<status>: <body>".
// Unwrap the backend's {"detail": {"code", "detail"}} envelope for display.
function apiErrorText(reason: unknown): string {
  const text = reason instanceof Error ? reason.message : String(reason);
  const match = /^(\d{3}):\s*([\s\S]*)$/.exec(text);
  if (!match) return text;
  try {
    const body = JSON.parse(match[2]) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const record = detail as Record<string, unknown>;
      const code = typeof record.code === "string" ? record.code : null;
      const message = typeof record.detail === "string" ? record.detail : JSON.stringify(detail);
      return code ? `${code}: ${message}` : message;
    }
  } catch {
    /* fall through to the raw body */
  }
  return `${match[1]}: ${match[2]}`;
}

function defaultCamera(): SceneCamera {
  return {
    id: "camera.main",
    width_px: 1600,
    height_px: 1000,
    intrinsics: { fx: 1200, fy: 1200, cx: 800, cy: 500 },
    transform: {
      translation_mm: [0, -5000, 1600],
      rotation_deg: [90, 0, 0]
    },
    calibration: {
      method: "manual",
      observations: []
    }
  };
}

export function CameraPanel({ projectId, revision, assets, onChanged }: Props) {
  const cameras = revision.scene.cameras;
  const [selectedId, setSelectedId] = useState(cameras[0]?.id ?? "new");
  const [draft, setDraft] = useState<SceneCamera>(cameras[0] ?? defaultCamera());
  const [error, setError] = useState("");
  const [rows, setRows] = useState<Correspondence[]>([]);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [solving, setSolving] = useState(false);
  const [solveError, setSolveError] = useState("");
  const [solveResult, setSolveResult] = useState<SolveSummary | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const nextRowKey = useRef(1);

  const sourcePhotos = useMemo(
    () => assets.filter((asset) => asset.media_type.startsWith("image/") && asset.role === "apartment"),
    [assets]
  );

  const anchors = useMemo(
    () => deriveAnchors(revision.scene.entities),
    [revision.scene.entities]
  );
  const anchorsById = useMemo(() => {
    const map = new Map<string, { worldMm: [number, number, number]; label: string }>();
    for (const group of anchors) {
      for (const point of group.points) {
        map.set(point.id, { worldMm: point.worldMm, label: point.label });
      }
    }
    return map;
  }, [anchors]);

  const linkedRows = useMemo(
    () => rows.filter((row) => row.anchorId && anchorsById.has(row.anchorId)),
    [rows, anchorsById]
  );
  const solveReady = linkedRows.length >= 3;

  function resetCalibration() {
    setRows([]);
    setNatural(null);
    setSolveError("");
    setSolveResult(null);
  }

  useEffect(() => {
    const camera = cameras.find((item) => item.id === selectedId);
    if (camera) {
      setDraft(camera);
      return;
    }
    if (selectedId !== "new" && cameras.length > 0) {
      setSelectedId(cameras[0].id);
      setDraft(cameras[0]);
    }
  }, [cameras, selectedId]);

  function updateVector(
    group: "translation_mm" | "rotation_deg",
    index: number,
    value: string
  ) {
    setDraft((current) => {
      const next = [...current.transform[group]] as [number, number, number];
      next[index] = numeric(value);
      return {
        ...current,
        transform: { ...current.transform, [group]: next }
      };
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const camera: SceneCamera = {
        ...draft,
        id: draft.id.trim(),
        width_px: Math.max(1, Math.round(draft.width_px)),
        height_px: Math.max(1, Math.round(draft.height_px)),
        provenance: draft.source_asset_id
          ? {
              source: "measured",
              asset_ids: [draft.source_asset_id],
              note: "manual camera setup"
            }
          : { source: "user", asset_ids: [], note: "manual camera setup" },
        calibration: {
          ...(draft.calibration ?? {}),
          method: draft.calibration?.method ?? "manual",
          observations: draft.calibration?.observations ?? []
        }
      };
      await api.upsertCamera(projectId, camera.id, revision.revision_id, camera);
      setSelectedId(camera.id);
      await onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function remove() {
    if (selectedId === "new") return;
    setError("");
    try {
      await api.deleteCamera(projectId, selectedId, revision.revision_id);
      setSelectedId("new");
      setDraft(defaultCamera());
      await onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function addCorrespondence(event: ReactPointerEvent<HTMLDivElement>) {
    const image = imgRef.current;
    if (!image || !image.naturalWidth || !image.naturalHeight) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    const u = clamp(
      Math.round((event.clientX - bounds.left) * (image.naturalWidth / bounds.width)),
      0,
      image.naturalWidth
    );
    const v = clamp(
      Math.round((event.clientY - bounds.top) * (image.naturalHeight / bounds.height)),
      0,
      image.naturalHeight
    );
    setRows((current) => [
      ...current,
      { key: `point-${nextRowKey.current++}`, imagePx: [u, v], anchorId: "" }
    ]);
  }

  function setRowAnchor(key: string, anchorId: string) {
    setRows((current) =>
      current.map((row) => (row.key === key ? { ...row, anchorId } : row))
    );
  }

  function removeRow(key: string) {
    setRows((current) => current.filter((row) => row.key !== key));
  }

  async function solve() {
    if (!solveReady || solving) return;
    const observations = linkedRows.map((row) => {
      const anchor = anchorsById.get(row.anchorId);
      return {
        world_mm: anchor?.worldMm ?? ([0, 0, 0] as [number, number, number]),
        image_px: row.imagePx,
        label: anchor?.label ?? null
      };
    });
    setSolving(true);
    setSolveError("");
    setSolveResult(null);
    try {
      const camera: SceneCamera = {
        ...draft,
        id: draft.id.trim(),
        width_px: Math.max(1, Math.round(draft.width_px)),
        height_px: Math.max(1, Math.round(draft.height_px)),
        // Seed from the current pose; clear stale quality/residual so the
        // backend recomputes both from the incoming correspondences.
        calibration: {
          method: "correspondences",
          observations
        }
      };
      const response = await api.upsertCamera(
        projectId,
        camera.id,
        revision.revision_id,
        camera,
        true
      );
      setDraft(response.camera);
      setSelectedId(response.camera.id);
      setSolveResult({
        residual: response.camera.calibration?.residual ?? null,
        quality: response.camera.calibration?.quality ?? null,
        count: observations.length,
        transform: response.camera.transform
      });
      await onChanged();
    } catch (reason) {
      setSolveError(apiErrorText(reason));
    } finally {
      setSolving(false);
    }
  }

  return (
    <article className="panel camera-panel">
      <div className="panel-heading">
        <h2>Camera calibration</h2>
        <select
          value={selectedId}
          onChange={(event) => {
            const id = event.target.value;
            setSelectedId(id);
            setDraft(cameras.find((camera) => camera.id === id) ?? defaultCamera());
            resetCalibration();
          }}
        >
          {cameras.map((camera) => (
            <option key={camera.id} value={camera.id}>{camera.id}</option>
          ))}
          <option value="new">+ new camera</option>
        </select>
      </div>

      <form className="camera-form" onSubmit={save}>
        <label>
          ID
          <input
            value={draft.id}
            onChange={(event) => setDraft({ ...draft, id: event.target.value })}
          />
        </label>
        <label>
          Source photo
          <select
            value={draft.source_asset_id ?? ""}
            onChange={(event) => {
              setDraft({ ...draft, source_asset_id: event.target.value || null });
              resetCalibration();
            }}
          >
            <option value="">none</option>
            {sourcePhotos.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.original_name ?? asset.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>

        <div className="camera-grid">
          {(["width_px", "height_px"] as const).map((key) => (
            <label key={key}>
              {key}
              <input
                type="number"
                value={draft[key]}
                onChange={(event) =>
                  setDraft({ ...draft, [key]: numeric(event.target.value, 1) })
                }
              />
            </label>
          ))}
          {(["fx", "fy", "cx", "cy"] as const).map((key) => (
            <label key={key}>
              {key}
              <input
                type="number"
                step="0.01"
                value={draft.intrinsics[key]}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    intrinsics: {
                      ...draft.intrinsics,
                      [key]: numeric(event.target.value)
                    }
                  })
                }
              />
            </label>
          ))}
        </div>

        <div className="camera-grid">
          {["x", "y", "z"].map((axis, index) => (
            <label key={`p-${axis}`}>
              pos {axis} mm
              <input
                type="number"
                value={draft.transform.translation_mm[index]}
                onChange={(event) => updateVector("translation_mm", index, event.target.value)}
              />
            </label>
          ))}
          {["x", "y", "z"].map((axis, index) => (
            <label key={`r-${axis}`}>
              rot {axis}°
              <input
                type="number"
                step="0.1"
                value={draft.transform.rotation_deg[index]}
                onChange={(event) => updateVector("rotation_deg", index, event.target.value)}
              />
            </label>
          ))}
        </div>

        <div className="camera-status">
          {draft.calibration?.residual != null && (
            <span>residual {draft.calibration.residual.toFixed(2)} px</span>
          )}
          {draft.calibration?.quality != null && (
            <span>quality {Math.round(draft.calibration.quality * 100)}%</span>
          )}
        </div>

        <div className="camera-actions">
          <button type="submit">Save camera</button>
          {selectedId !== "new" && (
            <button className="danger secondary" type="button" onClick={() => void remove()}>
              Delete
            </button>
          )}
        </div>
        {error && <div className="error">{error}</div>}
      </form>

      <section className="cc-section">
        <div className="cc-head">
          <h3>Calibrate from photo</h3>
        </div>

        {!draft.source_asset_id && (
          <p className="muted">
            Choose an apartment photo, then click matching wall, door and window corners on it.
          </p>
        )}

        {draft.source_asset_id && (
          <>
            <div className="cc-stage">
              <div className="cc-image-wrap">
                <img
                  ref={imgRef}
                  src={api.assetUrl(draft.source_asset_id)}
                  alt="Calibration photo"
                  draggable={false}
                  onLoad={(event) =>
                    setNatural({
                      w: event.currentTarget.naturalWidth,
                      h: event.currentTarget.naturalHeight
                    })
                  }
                  onError={() => setNatural(null)}
                />
                <div className="cc-overlay" onPointerDown={addCorrespondence}>
                  {natural &&
                    rows.map((row, index) => (
                      <span
                        key={row.key}
                        className={row.anchorId ? "cc-marker linked" : "cc-marker"}
                        style={{
                          left: `${(row.imagePx[0] / natural.w) * 100}%`,
                          top: `${(row.imagePx[1] / natural.h) * 100}%`
                        }}
                      >
                        {index + 1}
                      </span>
                    ))}
                </div>
              </div>
            </div>

            <p className="hint">
              {linkedRows.length} of {rows.length} points linked · ≥3 required, 5+ recommended.
              {natural ? ` Photo ${natural.w}×${natural.h}px.` : ""}
            </p>

            <ol className="cc-rows">
              {rows.map((row, index) => (
                <li key={row.key} className="cc-row">
                  <span className="cc-index">{index + 1}</span>
                  <span className="cc-px">
                    {row.imagePx[0]}, {row.imagePx[1]} px
                  </span>
                  <select
                    aria-label={`Anchor for point ${index + 1}`}
                    value={row.anchorId}
                    onChange={(event) => setRowAnchor(row.key, event.target.value)}
                  >
                    <option value="">Select a corner…</option>
                    {anchors.map((group) => (
                      <optgroup key={group.objectId} label={group.label}>
                        {group.points.map((point) => (
                          <option key={point.id} value={point.id}>
                            {point.label}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => removeRow(row.key)}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ol>

            {anchors.length === 0 && (
              <p className="muted">No wall, door or window geometry in this revision.</p>
            )}

            <div className="cc-actions">
              <button
                type="button"
                onClick={() => void solve()}
                disabled={!solveReady || solving}
              >
                {solving ? "Solving…" : "Solve camera"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={resetCalibration}
                disabled={rows.length === 0 && !solveResult && !solveError}
              >
                Clear
              </button>
            </div>

            {solveError && <div className="error cc-error">{solveError}</div>}

            {solveResult && (
              <div className="cc-result">
                <strong>Pose solved</strong>
                <span>
                  {solveResult.count} correspondences · residual{" "}
                  {solveResult.residual != null
                    ? `${solveResult.residual.toFixed(2)} px`
                    : "n/a"}
                  {" · "}quality{" "}
                  {solveResult.quality != null
                    ? `${Math.round(solveResult.quality * 100)}%`
                    : "n/a"}
                </span>
                <span>
                  position ({formatTriple(solveResult.transform.translation_mm, 1)} mm) ·
                  rotation ({formatTriple(solveResult.transform.rotation_deg, 2)}°)
                </span>
              </div>
            )}
          </>
        )}
      </section>
    </article>
  );
}
