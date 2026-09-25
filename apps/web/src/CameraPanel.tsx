import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, Asset, SceneCamera, SceneRevision } from "./api";

type Props = {
  projectId: string;
  revision: SceneRevision;
  assets: Asset[];
  onChanged: () => Promise<void>;
};

function numeric(value: string, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
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

  const sourcePhotos = useMemo(
    () => assets.filter((asset) => asset.media_type.startsWith("image/") && asset.role === "apartment"),
    [assets]
  );

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
            onChange={(event) =>
              setDraft({ ...draft, source_asset_id: event.target.value || null })
            }
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
    </article>
  );
}
