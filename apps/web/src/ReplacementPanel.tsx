import { FormEvent, useMemo, useState } from "react";
import { api, Asset, SceneRevision } from "./api";

export function ReplacementPanel({
  projectId,
  revision,
  assets,
  onChanged
}: {
  projectId: string;
  revision: SceneRevision;
  assets: Asset[];
  onChanged: () => Promise<void>;
}) {
  const furniture = useMemo(
    () =>
      revision.scene.entities.filter(
        (entity) => entity.kind === "furniture" && !entity.locks?.geometry
      ),
    [revision]
  );
  const references = useMemo(
    () =>
      assets.filter(
        (asset) => asset.role === "reference" && asset.media_type.startsWith("image/")
      ),
    [assets]
  );
  const cameras = revision.scene.cameras;

  const [targetId, setTargetId] = useState("");
  const [referenceId, setReferenceId] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [prompt, setPrompt] = useState(
    "replace selected furniture with the reference object while preserving the room"
  );
  const [result, setResult] = useState("");
  const [error, setError] = useState("");

  const resolvedTarget = targetId || furniture[0]?.id || "";
  const resolvedReference = referenceId || references[0]?.id || "";
  const resolvedCamera = cameraId || cameras[0]?.id || "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!resolvedTarget || !resolvedReference || !resolvedCamera) return;
    setError("");
    setResult("");
    try {
      const response = await api.createReplacement(
        projectId,
        revision.revision_id,
        resolvedTarget,
        resolvedReference,
        resolvedCamera,
        prompt
      );
      const [x0, y0, x1, y1] = response.affected_region.bbox_px;
      setResult(
        `revision ${response.revision_id.slice(0, 8)} · bbox ${x0},${y0}–${x1},${y1}`
      );
      await onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <article className="panel replacement-panel">
      <h2>Replace from reference</h2>
      <form onSubmit={submit} className="replacement-form">
        <label>
          Target furniture
          <select value={resolvedTarget} onChange={(event) => setTargetId(event.target.value)}>
            {furniture.map((entity) => (
              <option key={entity.id} value={entity.id}>
                {entity.display_name ?? entity.id}
              </option>
            ))}
          </select>
        </label>

        <label>
          Reference image
          <select
            value={resolvedReference}
            onChange={(event) => setReferenceId(event.target.value)}
          >
            {references.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.original_name ?? asset.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>

        <label>
          Camera
          <select value={resolvedCamera} onChange={(event) => setCameraId(event.target.value)}>
            {cameras.map((camera) => (
              <option key={camera.id} value={camera.id}>
                {camera.id}
              </option>
            ))}
          </select>
        </label>

        <label>
          Edit instruction
          <input value={prompt} onChange={(event) => setPrompt(event.target.value)} />
        </label>

        <button
          disabled={!resolvedTarget || !resolvedReference || !resolvedCamera || !prompt.trim()}
        >
          Replace object
        </button>

        {references.length === 0 && (
          <p className="muted">Upload an image with role “reference” first.</p>
        )}
        {furniture.length === 0 && <p className="muted">No editable furniture in scene.</p>}
        {result && <p className="replacement-result">{result}</p>}
        {error && <div className="error">{error}</div>}
      </form>
    </article>
  );
}
