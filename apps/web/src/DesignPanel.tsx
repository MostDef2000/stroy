import { useEffect, useMemo, useState } from "react";
import { api, Generation, Job, RevisionSummary } from "./api";

function shortId(value: string | null | undefined) {
  return value ? value.slice(0, 8) : "—";
}

function outputAsset(generation: Generation | undefined) {
  return generation?.manifest.output_asset_ids?.[0] ?? null;
}

function jobError(job: Job) {
  if (!job.error) return null;
  const code = typeof job.error["code"] === "string" ? job.error["code"] : "job_failed";
  const detail =
    typeof job.error["detail"] === "string"
      ? job.error["detail"]
      : JSON.stringify(job.error);
  return `${code}: ${detail}`;
}

export function DesignPanel({
  revisions,
  jobs,
  generations,
  currentRevisionId,
  cameraId,
  onRestore,
  onRerender
}: {
  revisions: RevisionSummary[];
  jobs: Job[];
  generations: Generation[];
  currentRevisionId: string | null;
  cameraId: string | null;
  onRestore: (revisionId: string) => Promise<void>;
  onRerender: (revisionId: string) => Promise<void>;
}) {
  const [beforeId, setBeforeId] = useState("");
  const [afterId, setAfterId] = useState("");

  useEffect(() => {
    if (generations.length === 0) {
      setBeforeId("");
      setAfterId("");
      return;
    }
    setAfterId((value) =>
      generations.some((item) => item.id === value) ? value : generations[0].id
    );
    setBeforeId((value) => {
      if (generations.some((item) => item.id === value)) return value;
      return generations[1]?.id ?? generations[0].id;
    });
  }, [generations]);

  const before = generations.find((item) => item.id === beforeId);
  const after = generations.find((item) => item.id === afterId);
  const instructionJobs = useMemo(
    () =>
      jobs.filter(
        (job) =>
          job.job_type === "llm.complete" &&
          (job.result?.["commands"] != null || job.error != null)
      ),
    [jobs]
  );

  return (
    <>
      <article className="panel design-timeline">
        <h2>Design timeline</h2>
        {instructionJobs.length === 0 && (
          <p className="muted">design instructions пока не завершались</p>
        )}
        {instructionJobs.slice(0, 10).map((job) => {
          const result = job.result ?? {};
          const commands = Array.isArray(result["commands"])
            ? (result["commands"] as Array<Record<string, unknown>>)
            : [];
          const finalRevision =
            typeof result["final_revision_id"] === "string"
              ? (result["final_revision_id"] as string)
              : null;
          return (
            <div className="design-event" key={job.id}>
              <div className="design-event-head">
                <span>instruction #{shortId(job.id)}</span>
                <span className="tag">{job.status}</span>
              </div>
              {commands.map((command, index) => (
                <div className="command-summary" key={`${job.id}-${index}`}>
                  <strong>{String(command["operation"] ?? "command")}</strong>
                  <span>{String(command["target_id"] ?? "")}</span>
                </div>
              ))}
              {finalRevision && (
                <small>
                  revision {shortId(finalRevision)}
                  {finalRevision === currentRevisionId ? " · current" : ""}
                </small>
              )}
              {jobError(job) && <div className="error">{jobError(job)}</div>}
            </div>
          );
        })}

        <h3>Revisions</h3>
        {revisions.slice(0, 12).map((revision) => (
          <div className="revision-line" key={revision.revision_id}>
            <span>rev {shortId(revision.revision_id)}</span>
            <span>{revision.command_id ? `cmd ${shortId(revision.command_id)}` : "snapshot"}</span>
            {revision.revision_id === currentRevisionId ? (
              <span className="tag">current</span>
            ) : (
              <button
                className="secondary"
                onClick={() => void onRestore(revision.revision_id)}
              >
                Undo/restore
              </button>
            )}
            {cameraId && (
              <button
                className="secondary"
                onClick={() => void onRerender(revision.revision_id)}
              >
                Re-render
              </button>
            )}
          </div>
        ))}
      </article>

      <article className="panel comparison-panel">
        <h2>Before / after</h2>
        {generations.length === 0 ? (
          <p className="muted">generation outputs пока нет</p>
        ) : (
          <>
            <div className="compare-selectors">
              <label>
                Before
                <select value={beforeId} onChange={(event) => setBeforeId(event.target.value)}>
                  {generations.map((item) => (
                    <option key={item.id} value={item.id}>
                      rev {shortId(item.design_revision_id)} · gen {shortId(item.id)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                After
                <select value={afterId} onChange={(event) => setAfterId(event.target.value)}>
                  {generations.map((item) => (
                    <option key={item.id} value={item.id}>
                      rev {shortId(item.design_revision_id)} · gen {shortId(item.id)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="compare-grid">
              {[["Before", before], ["After", after]].map(([label, generation]) => {
                const item = generation as Generation | undefined;
                const assetId = outputAsset(item);
                return (
                  <figure key={label as string}>
                    <figcaption>
                      <strong>{label as string}</strong>
                      <span>revision {shortId(item?.design_revision_id)}</span>
                    </figcaption>
                    {assetId ? (
                      <img src={api.assetUrl(assetId)} alt={label as string} />
                    ) : (
                      <div className="compare-empty">no output asset</div>
                    )}
                  </figure>
                );
              })}
            </div>
          </>
        )}
      </article>
    </>
  );
}
