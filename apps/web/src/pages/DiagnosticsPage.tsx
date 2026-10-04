import type {
  Asset,
  AssetRole,
  Generation,
  Job,
  RevisionSummary,
  StyleProfile,
  Worker
} from "../api";

function shortId(value: string | null | undefined) {
  return value ? value.slice(0, 8) : "—";
}

// Diagnostics page: compute inventory, job queue (with cancel), style profiles
// and assets, plus the manual test-generation trigger (#101).
export function DiagnosticsPage({
  workers,
  jobs,
  revisions,
  generations,
  styleProfiles,
  assets,
  onCancel,
  onTestGeneration
}: {
  workers: Worker[];
  jobs: Job[];
  revisions: RevisionSummary[];
  generations: Generation[];
  styleProfiles: StyleProfile[];
  assets: Asset[];
  onCancel: (jobId: string) => void;
  onTestGeneration: () => void;
}) {
  return (
    <>
      <section className="panel test-generation-panel">
        <h2>Проверка пайплайна</h2>
        <div className="pl-actions">
          <button onClick={onTestGeneration}>Тестовая генерация</button>
        </div>
      </section>

      <section className="dashboard-grid">
        <article className="panel">
          <h2>Вычисления</h2>
          {workers.length === 0 && <p className="muted">worker ещё не зарегистрирован</p>}
          {workers.map((worker) => (
            <div className="row" key={worker.id}>
              <span>{worker.display_name ?? worker.id}</span>
              <span className={worker.online ? "tag online" : "tag offline"}>
                {worker.online ? "online" : "offline"}
              </span>
              <small>{worker.models.join(", ")}</small>
            </div>
          ))}
        </article>

        <article className="panel">
          <h2>Задания</h2>
          {jobs.length === 0 && <p className="muted">очередь пуста</p>}
          {jobs.slice(0, 8).map((job) => {
            const fraction =
              typeof job.progress["fraction"] === "number"
                ? Math.round((job.progress["fraction"] as number) * 100)
                : null;
            const phase =
              typeof job.progress["phase"] === "string"
                ? (job.progress["phase"] as string)
                : null;
            const cancellable = !["succeeded", "failed", "cancelled"].includes(job.status);
            return (
              <div className="row" key={job.id}>
                <span>{job.job_type}</span>
                <span className="tag">{job.status}</span>
                <small>
                  #{shortId(job.id)} · attempt {job.attempt}
                  {phase ? ` · ${phase}` : ""}
                  {fraction !== null ? ` · ${fraction}%` : ""}
                </small>
                {cancellable && (
                  <button className="secondary" onClick={() => onCancel(job.id)}>
                    Отменить
                  </button>
                )}
              </div>
            );
          })}
        </article>

        <article className="panel">
          <h2>Профили стиля</h2>
          {styleProfiles.length === 0 && (
            <p className="muted">
              профилей пока нет — запустите анализ стиля по reference-фото
            </p>
          )}
          {styleProfiles.slice(0, 6).map((item) => (
            <div className="row" key={item.id}>
              <span>
                {item.profile.labels.join(", ") || "без меток"}
              </span>
              <span className="palette">
                {item.profile.palette.map((entry) => (
                  <i
                    key={entry.hex}
                    title={`${entry.role}: ${entry.hex}`}
                    style={{ backgroundColor: entry.hex }}
                  />
                ))}
              </span>
              <small>
                {item.profile.materials.length} materials ·{" "}
                {item.profile.lighting?.temperature_k ?? "—"}K ·{" "}
                {new Date(item.created_at).toLocaleDateString()}
              </small>
            </div>
          ))}
        </article>

        <article className="panel">
          <h2>Файлы</h2>
          {assets.length === 0 && <p className="muted">файлов пока нет</p>}
          {(["apartment", "reference", "derived"] as AssetRole[]).map((role) => {
            const group = assets.filter((asset) => asset.role === role);
            if (group.length === 0) return null;
            return (
              <div className="asset-group" key={role}>
                <h3>{role}</h3>
                {group.slice(0, 8).map((asset) => (
                  <div className="row" key={asset.id}>
                    <span>{asset.original_name ?? shortId(asset.id)}</span>
                    <span className="tag">{asset.provenance}</span>
                    <small>
                      {asset.media_type} · {Math.ceil(asset.size_bytes / 1024)} KB
                      {asset.duplicate_of_asset_id
                        ? ` · duplicate of ${shortId(asset.duplicate_of_asset_id)}`
                        : ""}
                    </small>
                  </div>
                ))}
              </div>
            );
          })}
        </article>

        <article className="panel">
          <h2>Версии и генерации</h2>
          <h3>Версии</h3>
          {revisions.length === 0 && <p className="muted">версий пока нет</p>}
          {revisions.slice(0, 12).map((item) => (
            <div className="row" key={item.revision_id}>
              <span>{item.command_id ? `cmd ${item.command_id}` : "snapshot"}</span>
              <span className="tag">{new Date(item.created_at).toLocaleString()}</span>
              <small className="mono">
                {item.revision_id}
                {item.parent_revision_id ? ` · parent ${item.parent_revision_id}` : ""}
                {` · hash ${item.content_hash.slice(0, 12)}`}
              </small>
            </div>
          ))}

          <h3>Генерации</h3>
          {generations.length === 0 && <p className="muted">генераций пока нет</p>}
          {generations.slice(0, 12).map((item) => (
            <div className="row" key={item.id}>
              <span>generation</span>
              <span className="tag">{item.manifest.output_asset_ids?.length ?? 0} assets</span>
              <small className="mono">
                {item.id} · job {item.job_id} · design {item.design_revision_id} · camera{" "}
                {item.camera_id} · {new Date(item.created_at).toLocaleString()}
              </small>
            </div>
          ))}
        </article>
      </section>
    </>
  );
}
