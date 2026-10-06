import { useMemo, useState } from "react";
import type {
  Asset,
  AssetRole,
  Generation,
  Job,
  RevisionSummary,
  StyleProfile,
  Worker
} from "../api";
import {
  compactJson,
  formatBytes,
  formatClock,
  formatDayClock,
  formatJobSubtitle,
  isJobCancellable,
  jobErrorText,
  jobProgressText,
  jobStatusTone,
  newestFirst,
  shortId,
  workerStatusView
} from "../diagnostics-format";

type ActivityTab = "jobs" | "revisions" | "generations";

const ACTIVITY_TABS: Array<{ id: ActivityTab; label: string }> = [
  { id: "jobs", label: "Задания" },
  { id: "revisions", label: "Версии" },
  { id: "generations", label: "Генерации" }
];

// Compact dashboard shows a bounded page of rows per activity tab; a
// "Показать ещё" control inside the scroll pane reveals the rest (#143:
// no permanent truncation, no unbounded page height).
const ACTIVITY_PAGE = 20;

const ASSET_ROLES: AssetRole[] = ["apartment", "reference", "derived"];

// Diagnostics dashboard (#143): bounded operator surface. Compute at top,
// one tabbed activity pane (jobs / revisions / generations), files and style
// profiles as bounded summaries, pipeline test secondary at the bottom.
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
  const [activityTab, setActivityTab] = useState<ActivityTab>("jobs");
  const [activityLimit, setActivityLimit] = useState(ACTIVITY_PAGE);

  // Newest-first display order. The API already returns jobs newest-first
  // (job_list: created_at DESC); newestFirst() is a defensive, idempotent
  // re-sort so the dashboard never relies on implicit API ordering semantics.
  // No API change.
  const sortedJobs = useMemo(() => newestFirst(jobs), [jobs]);
  const sortedRevisions = useMemo(() => newestFirst(revisions), [revisions]);
  const sortedGenerations = useMemo(() => newestFirst(generations), [generations]);

  const activityCounts: Record<ActivityTab, number> = {
    jobs: sortedJobs.length,
    revisions: sortedRevisions.length,
    generations: sortedGenerations.length
  };

  const selectActivityTab = (tab: ActivityTab) => {
    setActivityTab(tab);
    setActivityLimit(ACTIVITY_PAGE);
  };

  const showMore = () => setActivityLimit((limit) => limit + ACTIVITY_PAGE);

  const pagedItems: { jobs: Job[]; revisions: RevisionSummary[]; generations: Generation[] } = {
    jobs: sortedJobs,
    revisions: sortedRevisions,
    generations: sortedGenerations
  };
  const hiddenCount = Math.max(0, activityCounts[activityTab] - activityLimit);

  return (
    // Horizontal board (#143 follow-up): work blocks sit side by side on
    // desktop, zero vertical page scroll; <900px keeps the stacked #143
    // layout. Layout lives in styles.css ("diagnostics horizontal board").
    // .diag-stack stays on the root so its scoped tag/summary rules and the
    // narrow-screen fallback keep working.
    <div className="diag-board diag-stack">
      <section className="panel diag-compute">
        <div className="diag-panel-head">
          <h2>Вычисления</h2>
          <span className="diag-count">{workers.length}</span>
        </div>
        {workers.length === 0 && <p className="muted">worker ещё не зарегистрирован</p>}
        <div className="diag-worker-grid">
          {workers.map((worker) => {
            const status = workerStatusView(worker);
            return (
              <article className="diag-worker-card" key={worker.id}>
                <div className="diag-worker-head">
                  <span className="diag-worker-name">{worker.display_name ?? worker.id}</span>
                  <span className={`tag ${status.tone}`}>{status.label}</span>
                </div>
                <small className="diag-worker-meta">
                  {worker.models.length > 0 ? worker.models.join(", ") : "модели не заявлены"}
                  {worker.capabilities.length > 0 ? ` · ${worker.capabilities.join(", ")}` : ""}
                </small>
                {status.note && <small className="diag-worker-meta">{status.note}</small>}
              </article>
            );
          })}
        </div>
      </section>

      <section className="panel diag-activity">
        <div className="diag-panel-head">
          <h2>Активность</h2>
        </div>
        <div className="diag-tabs" role="tablist" aria-label="Активность проекта">
          {ACTIVITY_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`diag-tab-${tab.id}`}
              aria-selected={activityTab === tab.id}
              aria-controls="diag-activity-pane"
              className={activityTab === tab.id ? "diag-tab is-active" : "diag-tab"}
              onClick={() => selectActivityTab(tab.id)}
            >
              {tab.label}
              <span className="diag-tab-count">{activityCounts[tab.id]}</span>
            </button>
          ))}
        </div>
        <div
          key={activityTab}
          className="diag-scroll"
          role="tabpanel"
          id="diag-activity-pane"
          aria-labelledby={`diag-tab-${activityTab}`}
        >
          {activityTab === "jobs" && (
            <JobsPane jobs={pagedItems.jobs.slice(0, activityLimit)} onCancel={onCancel} />
          )}
          {activityTab === "revisions" && <RevisionsPane revisions={pagedItems.revisions.slice(0, activityLimit)} />}
          {activityTab === "generations" && <GenerationsPane generations={pagedItems.generations.slice(0, activityLimit)} />}
          {hiddenCount > 0 && (
            <button className="secondary diag-show-more" type="button" onClick={showMore}>
              Показать ещё {Math.min(ACTIVITY_PAGE, hiddenCount)} из {hiddenCount}
            </button>
          )}
        </div>
      </section>

      <section className="panel diag-files">
        <div className="diag-panel-head">
          <h2>Файлы</h2>
          <span className="diag-count">{assets.length}</span>
        </div>
        {assets.length === 0 && <p className="muted">файлов пока нет</p>}
        {assets.length > 0 && (
          <>
            <div className="diag-chip-row">
              {ASSET_ROLES.map((role) => {
                const count = assets.filter((asset) => asset.role === role).length;
                return (
                  <span
                    key={role}
                    className={count === 0 ? "diag-chip is-empty" : "diag-chip"}
                  >
                    {role} · {count}
                  </span>
                );
              })}
            </div>
            <div className="diag-scroll diag-files-scroll">
              {ASSET_ROLES.map((role) => {
                const group = assets.filter((asset) => asset.role === role);
                if (group.length === 0) return null;
                return (
                  <details className="diag-file-group" key={role} open>
                    <summary>
                      <span>{role}</span>
                      <span className="diag-count">{group.length}</span>
                    </summary>
                    {group.map((asset) => (
                      <AssetRow key={asset.id} asset={asset} />
                    ))}
                  </details>
                );
              })}
            </div>
          </>
        )}
      </section>

      {/* Fourth column (>=901px): style profiles with the pipeline test kept
          secondary underneath; dissolves (display:contents) into the stacked
          layout below 900px. */}
      <div className="diag-side">
        <section className="panel diag-styles">
          <div className="diag-panel-head">
            <h2>Профили стиля</h2>
            <span className="diag-count">{styleProfiles.length}</span>
          </div>
          {styleProfiles.length === 0 && (
            <p className="muted">профилей пока нет — запустите анализ стиля по reference-фото</p>
          )}
          {styleProfiles.length > 0 && (
            <div className="diag-scroll diag-styles-scroll">
              {styleProfiles.map((item) => (
                <StyleRow key={item.id} item={item} />
              ))}
            </div>
          )}
        </section>

        <section className="panel diag-pipeline" aria-label="Проверка пайплайна">
          <div className="diag-pipeline-row">
            <div>
              <h2>Проверка пайплайна</h2>
              <small className="muted">ручной прогон тестовой генерации (#101)</small>
            </div>
            <div className="pl-actions">
              <button type="button" className="secondary" onClick={onTestGeneration}>
                Тестовая генерация
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function JobRow({ job, onCancel }: { job: Job; onCancel: (jobId: string) => void }) {
  const tone = jobStatusTone(job.status);
  const progress = jobProgressText(job.progress);
  const errorText = jobErrorText(job.error);
  return (
    <div className="diag-row">
      <div className="diag-row-main">
        <span className="diag-row-title">{job.job_type}</span>
        <span className="diag-row-side">
          {progress && <span className="diag-progress">{progress}</span>}
          {/* Статус выводится один раз — здесь. */}
          <span className={tone ? `tag ${tone}` : "tag"}>{job.status}</span>
        </span>
      </div>
      <div className="diag-row-sub">
        <span className="mono">{formatJobSubtitle(job)}</span>
        <span className="diag-row-side">
          <span className="diag-time">{formatClock(job.created_at)}</span>
          {isJobCancellable(job.status) && (
            <button className="secondary diag-cancel" type="button" onClick={() => onCancel(job.id)}>
              Отменить
            </button>
          )}
        </span>
      </div>
      <details className="diag-details">
        <summary>Подробнее</summary>
        <div className="diag-details-grid">
          <DetailRow label="id" value={job.id} mono />
          {errorText && (
            <div className="diag-details-row">
              <span className="diag-k">error</span>
              <span className="diag-error-text">{errorText}</span>
            </div>
          )}
          <DetailRow label="error json" value={compactJson(job.error)} mono />
          <DetailRow label="progress" value={compactJson(job.progress)} mono />
          <DetailRow label="result" value={compactJson(job.result)} mono />
          <DetailRow label="created_at" value={job.created_at} mono />
          <DetailRow label="correlation_id" value={job.correlation_id ?? "—"} mono />
          <DetailRow label="idempotency_key" value={job.idempotency_key ?? "—"} mono />
          <DetailRow label="leased_to" value={job.leased_to ?? "—"} mono />
          <DetailRow label="lease_expires_at" value={job.lease_expires_at ?? "—"} mono />
          <DetailRow label="runtime_provenance" value={compactJson(job.runtime_provenance)} mono />
        </div>
      </details>
    </div>
  );
}

function JobsPane({ jobs, onCancel }: { jobs: Job[]; onCancel: (jobId: string) => void }) {
  if (jobs.length === 0) return <p className="muted">очередь пуста</p>;
  return (
    <>
      {jobs.map((job) => (
        <JobRow key={job.id} job={job} onCancel={onCancel} />
      ))}
    </>
  );
}

function RevisionRow({ item }: { item: RevisionSummary }) {
  return (
    <div className="diag-row">
      <div className="diag-row-main">
        <span className="diag-row-title">
          {item.command_id ? `cmd ${shortId(item.command_id)}` : "snapshot"}
        </span>
        <span className="diag-row-side">
          <span className="diag-time">{formatDayClock(item.created_at)}</span>
        </span>
      </div>
      <div className="diag-row-sub">
        <span className="mono">#{shortId(item.revision_id)}</span>
      </div>
      <details className="diag-details">
        <summary>Подробнее</summary>
        <div className="diag-details-grid">
          <DetailRow label="revision_id" value={item.revision_id} mono />
          <DetailRow label="parent_revision_id" value={item.parent_revision_id ?? "—"} mono />
          <DetailRow label="command_id" value={item.command_id ?? "—"} mono />
          <DetailRow label="content_hash" value={item.content_hash} mono />
          <DetailRow label="created_at" value={item.created_at} mono />
        </div>
      </details>
    </div>
  );
}

function RevisionsPane({ revisions }: { revisions: RevisionSummary[] }) {
  if (revisions.length === 0) return <p className="muted">версий пока нет</p>;
  return (
    <>
      {revisions.map((item) => (
        <RevisionRow key={item.revision_id} item={item} />
      ))}
    </>
  );
}

function GenerationRow({ item }: { item: Generation }) {
  return (
    <div className="diag-row">
      <div className="diag-row-main">
        <span className="diag-row-title">{item.manifest.model_profile || "generation"}</span>
        <span className="diag-row-side">
          <span className="tag">{item.manifest.output_asset_ids?.length ?? 0} assets</span>
        </span>
      </div>
      <div className="diag-row-sub">
        <span className="mono">#{shortId(item.id)}</span>
        <span className="diag-row-side">
          <span className="diag-time">{formatDayClock(item.created_at)}</span>
        </span>
      </div>
      <details className="diag-details">
        <summary>Подробнее</summary>
        <div className="diag-details-grid">
          <DetailRow label="id" value={item.id} mono />
          <DetailRow label="job_id" value={item.job_id} mono />
          <DetailRow label="scene_revision_id" value={item.scene_revision_id} mono />
          <DetailRow label="design_revision_id" value={item.design_revision_id} mono />
          <DetailRow label="camera_id" value={item.camera_id} mono />
          <DetailRow label="created_at" value={item.created_at} mono />
          <DetailRow
            label="workflow"
            value={`${item.manifest.workflow.id} @ ${item.manifest.workflow.version}`}
            mono
          />
          <DetailRow
            label="seed"
            value={item.manifest.seed != null ? String(item.manifest.seed) : "—"}
            mono
          />
          <DetailRow label="input_asset_ids" value={compactJson(item.manifest.input_asset_ids)} mono />
          <DetailRow label="output_asset_ids" value={compactJson(item.manifest.output_asset_ids)} mono />
          <DetailRow
            label="structured_conditioning"
            value={compactJson(item.manifest.structured_conditioning)}
            mono
          />
        </div>
      </details>
    </div>
  );
}

function GenerationsPane({ generations }: { generations: Generation[] }) {
  if (generations.length === 0) return <p className="muted">генераций пока нет</p>;
  return (
    <>
      {generations.map((item) => (
        <GenerationRow key={item.id} item={item} />
      ))}
    </>
  );
}

function AssetRow({ asset }: { asset: Asset }) {
  return (
    <div className="diag-row">
      <div className="diag-row-main">
        <span className="diag-row-title">{asset.original_name ?? `#${shortId(asset.id)}`}</span>
        <span className="diag-row-side">
          <span className="tag">{asset.provenance}</span>
          <span className="diag-time">{formatBytes(asset.size_bytes)}</span>
        </span>
      </div>
      <div className="diag-row-sub">
        <span className="mono">#{shortId(asset.id)}</span>
        <span className="diag-row-side">
          <span className="diag-time">{asset.media_type}</span>
          {asset.duplicate_of_asset_id && (
            <span className="diag-time">duplicate of #{shortId(asset.duplicate_of_asset_id)}</span>
          )}
        </span>
      </div>
      <details className="diag-details">
        <summary>Подробнее</summary>
        <div className="diag-details-grid">
          <DetailRow label="id" value={asset.id} mono />
          <DetailRow label="sha256" value={asset.sha256} mono />
          <DetailRow label="created_at" value={asset.created_at} mono />
          <DetailRow label="source_asset_id" value={asset.source_asset_id ?? "—"} mono />
          <DetailRow label="source_asset_ids" value={compactJson(asset.source_asset_ids)} mono />
          <DetailRow label="duplicate_of_asset_id" value={asset.duplicate_of_asset_id ?? "—"} mono />
          <DetailRow label="metadata" value={compactJson(asset.metadata)} mono />
        </div>
      </details>
    </div>
  );
}

function StyleRow({ item }: { item: StyleProfile }) {
  return (
    <div className="diag-row">
      <div className="diag-row-main">
        <span className="diag-row-title">
          {item.profile.labels.join(", ") || "без меток"}
        </span>
        <span className="diag-row-side">
          <span className="palette">
            {item.profile.palette.map((entry) => (
              <i
                key={entry.hex}
                title={`${entry.role}: ${entry.hex}`}
                style={{ backgroundColor: entry.hex }}
              />
            ))}
          </span>
        </span>
      </div>
      <div className="diag-row-sub">
        <span>
          {item.profile.materials.length} materials ·{" "}
          {item.profile.lighting?.temperature_k ?? "—"}K
          {item.profile.lighting?.intent?.length ? ` · ${item.profile.lighting.intent.join(", ")}` : ""}
        </span>
        <span className="diag-row-side">
          <span className="diag-time">{new Date(item.created_at).toLocaleDateString()}</span>
        </span>
      </div>
      <details className="diag-details">
        <summary>Подробнее</summary>
        <div className="diag-details-grid">
          <DetailRow label="id" value={item.id} mono />
          <DetailRow label="model_profile" value={item.model_profile ?? "—"} mono />
          <DetailRow label="correlation_id" value={item.correlation_id ?? "—"} mono />
          <DetailRow label="created_at" value={item.created_at} mono />
          <DetailRow label="palette" value={compactJson(item.profile.palette)} mono />
          <DetailRow label="materials" value={compactJson(item.profile.materials)} mono />
          <DetailRow label="lighting" value={compactJson(item.profile.lighting)} mono />
          <DetailRow label="forms" value={compactJson(item.profile.forms)} mono />
          <DetailRow
            label="negative_constraints"
            value={compactJson(item.profile.negative_constraints)}
            mono
          />
        </div>
      </details>
    </div>
  );
}

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="diag-details-row">
      <span className="diag-k">{label}</span>
      <span className={mono ? "mono" : undefined}>{value}</span>
    </div>
  );
}
