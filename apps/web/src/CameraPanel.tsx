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

  // Guided calibration wizard (#105), mirroring the plan-stage strip (#104):
  // a step is done from its own rule and the first not-done step is current.
  const stepDone = [
    Boolean(draft.source_asset_id),
    rows.length > 0,
    linkedRows.length >= 3,
    solveResult !== null
  ];
  const stepLabels = [
    "Выберите фото квартиры",
    "Отметьте углы на фото",
    "Сопоставьте углы со сценой",
    "Решение камеры"
  ];
  const stepHints: Array<string | null> = [
    "Выберите фото, снятое из точки съёмки комнаты.",
    "Кликайте по видимым углам стен, дверей и окон на фото.",
    null,
    null
  ];
  const currentStepIndex = stepDone.findIndex((done) => !done);
  const activeHint = currentStepIndex >= 0 ? stepHints[currentStepIndex] : null;
  const dimLabels: Record<"width_px" | "height_px", string> = {
    width_px: "Ширина (px)",
    height_px: "Высота (px)"
  };

  function stepClass(index: number): string {
    if (stepDone[index]) return "done";
    return index === currentStepIndex ? "current" : "todo";
  }

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
        <h2>Калибровка камеры</h2>
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
          <option value="new">+ новая камера</option>
        </select>
      </div>

      <ol className="cw-steps">
        {stepLabels.map((label, index) => (
          <li
            key={label}
            className={stepClass(index)}
            aria-current={index === currentStepIndex ? "step" : undefined}
          >
            {label}
          </li>
        ))}
      </ol>
      {activeHint && <p className="cw-step-hint">{activeHint}</p>}

      {/* Step 1 — pick the apartment photo shot from the camera position. */}
      <div className="cw-step">
        <label className="cw-photo-pick">
          Выберите фото квартиры…
          <select
            value={draft.source_asset_id ?? ""}
            onChange={(event) => {
              setDraft({ ...draft, source_asset_id: event.target.value || null });
              resetCalibration();
            }}
          >
            <option value="">нет</option>
            {sourcePhotos.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.original_name ?? asset.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Step 2 — mark matching wall / door / window corners on the photo. */}
      <div className="cw-step">
        {draft.source_asset_id ? (
          <div className="cc-stage">
            <div className="cc-image-wrap">
              <img
                ref={imgRef}
                src={api.assetUrl(draft.source_asset_id)}
                alt="Фото для калибровки"
                draggable={false}
                onLoad={(event) => {
                  const w = event.currentTarget.naturalWidth;
                  const h = event.currentTarget.naturalHeight;
                  setNatural({ w, h });
                  // Keep camera pixel dimensions in sync with the photo so the
                  // solver and the image agree without manual editing.
                  setDraft((current) => ({
                    ...current,
                    width_px: Math.max(1, Math.round(w)),
                    height_px: Math.max(1, Math.round(h))
                  }));
                }}
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
        ) : (
          <p className="muted">Сначала выберите фото квартиры.</p>
        )}
      </div>

      {/* Step 3 — link every marked corner to a scene anchor. */}
      <div className="cw-step">
        <p className="hint">
          {linkedRows.length} из {rows.length} точек сопоставлено · минимум 3, лучше 5+
        </p>

        <ol className="cc-rows">
          {rows.map((row, index) => (
            <li key={row.key} className="cc-row">
              <span className="cc-index">{index + 1}</span>
              <span className="cc-px">
                {row.imagePx[0]}, {row.imagePx[1]} px
              </span>
              <select
                aria-label={`Угол для точки ${index + 1}`}
                value={row.anchorId}
                onChange={(event) => setRowAnchor(row.key, event.target.value)}
              >
                <option value="">Выберите угол…</option>
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
                Убрать
              </button>
            </li>
          ))}
        </ol>

        {anchors.length === 0 && (
          <p className="muted">В этой ревизии нет геометрии стен, дверей или окон.</p>
        )}
      </div>

      {/* Step 4 — solve the camera pose. */}
      <div className="cw-step">
        <div className="cc-actions">
          <button
            type="button"
            onClick={() => void solve()}
            disabled={!solveReady || solving}
          >
            {solving ? "Решение…" : "Определить позу камеры"}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={resetCalibration}
            disabled={rows.length === 0 && !solveResult && !solveError}
          >
            Очистить
          </button>
        </div>

        {solveError && <div className="error cc-error">{solveError}</div>}

        {solveResult && (
          <div className="cc-result">
            <strong>Поза камеры определена</strong>
            <span>
              {solveResult.count} точек · качество{" "}
              {solveResult.quality != null
                ? `${Math.round(solveResult.quality * 100)}%`
                : "n/a"}
              {" · "}residual{" "}
              {solveResult.residual != null
                ? `${solveResult.residual.toFixed(2)} px`
                : "n/a"}
            </span>
          </div>
        )}
      </div>

      {/* Advanced raw parameters — hidden unless a power user needs them. */}
      <details className="camera-advanced">
        <summary>Параметры камеры</summary>
        <form className="camera-form" onSubmit={save}>
          <label>
            Идентификатор
            <input
              value={draft.id}
              onChange={(event) => setDraft({ ...draft, id: event.target.value })}
            />
          </label>

          <div className="camera-grid">
            {(["width_px", "height_px"] as const).map((key) => (
              <label key={key}>
                {dimLabels[key]}
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
                Позиция {axis.toUpperCase()} (мм)
                <input
                  type="number"
                  value={draft.transform.translation_mm[index]}
                  onChange={(event) => updateVector("translation_mm", index, event.target.value)}
                />
              </label>
            ))}
            {["x", "y", "z"].map((axis, index) => (
              <label key={`r-${axis}`}>
                Поворот {axis.toUpperCase()} (°)
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

          {solveResult && (
            <div className="camera-status">
              <span>
                position ({formatTriple(solveResult.transform.translation_mm, 1)} mm)
              </span>
              <span>
                rotation ({formatTriple(solveResult.transform.rotation_deg, 2)}°)
              </span>
            </div>
          )}

          <div className="camera-actions">
            <button type="submit">Сохранить параметры</button>
            {selectedId !== "new" && (
              <button className="danger secondary" type="button" onClick={() => void remove()}>
                Удалить камеру
              </button>
            )}
          </div>
          {error && <div className="error">{error}</div>}
        </form>
      </details>
    </article>
  );
}
