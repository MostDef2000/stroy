import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  api,
  Asset,
  AssetRole,
  Generation,
  Job,
  Project,
  RevisionSummary,
  SceneDocument,
  SceneRevision,
  StyleProfile,
  Worker
} from "./api";
import { CameraPanel } from "./CameraPanel";
import { DesignPanel } from "./DesignPanel";
import { ReplacementPanel } from "./ReplacementPanel";
import { SceneViewer } from "./SceneViewer";
import "./styles.css";

function goldenRoom(projectId: string): SceneDocument {
  return {
    scene_id: "scene.golden-room",
    project_id: projectId,
    cameras: [
      {
        id: "camera.living.entry",
        width_px: 1600,
        height_px: 1000,
        intrinsics: { fx: 1200, fy: 1200, cx: 800, cy: 500 },
        transform: {
          translation_mm: [0, -6500, 1700],
          rotation_deg: [-15, 0, 0]
        },
        calibration: { quality: 1, residual: 0 }
      }
    ],
    entities: [
      {
        id: "surface.floor.living",
        kind: "floor",
        transform: {
          translation_mm: [0, 0, -50],
          rotation_deg: [0, 0, 0],
          scale: [1, 1, 1]
        },
        geometry: { dimensions_mm: [5000, 4000, 100] },
        locks: { geometry: true, transform: true, material: false }
      },
      {
        id: "surface.wall.living.north",
        kind: "wall",
        transform: {
          translation_mm: [0, 2000, 1400],
          rotation_deg: [0, 0, 0],
          scale: [1, 1, 1]
        },
        geometry: { dimensions_mm: [5000, 120, 2800] },
        locks: { geometry: true, transform: true, material: false }
      },
      {
        id: "surface.wall.living.west",
        kind: "wall",
        transform: {
          translation_mm: [-2500, 0, 1400],
          rotation_deg: [0, 0, 90],
          scale: [1, 1, 1]
        },
        geometry: { dimensions_mm: [4000, 120, 2800] },
        locks: { geometry: true, transform: true, material: false }
      },
      {
        id: "object.sofa.main",
        kind: "furniture",
        transform: {
          translation_mm: [0, 1200, 450],
          rotation_deg: [0, 0, 0],
          scale: [1, 1, 1]
        },
        geometry: { dimensions_mm: [2200, 900, 900] },
        locks: { geometry: false, transform: false, material: false },
        metadata: { color: "#b8b0a4" }
      },
      {
        id: "object.coffee_table.main",
        kind: "furniture",
        transform: {
          translation_mm: [0, 0, 250],
          rotation_deg: [0, 0, 0],
          scale: [1, 1, 1]
        },
        geometry: { dimensions_mm: [1000, 600, 500] },
        locks: { geometry: false, transform: false, material: false }
      }
    ]
  };
}

function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("owner");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      setError("");
      await api.login(username, password);
      onLogin();
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="brand">STROY</div>
        <p>Личный проект ремонта квартиры</p>
        <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Логин" />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Пароль"
        />
        <button>Войти</button>
        {error && <div className="error">{error}</div>}
      </form>
    </main>
  );
}

function shortId(value: string | null | undefined) {
  return value ? value.slice(0, 8) : "—";
}

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [revision, setRevision] = useState<SceneRevision | null>(null);
  const [revisions, setRevisions] = useState<RevisionSummary[]>([]);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [generations, setGenerations] = useState<Generation[]>([]);
  const [styleProfiles, setStyleProfiles] = useState<StyleProfile[]>([]);
  const [newProject, setNewProject] = useState("");
  const [instruction, setInstruction] = useState("");
  const [uploadRole, setUploadRole] = useState<AssetRole>("apartment");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [message, setMessage] = useState("");

  const refreshProjectsAndWorkers = useCallback(async () => {
    const [projectList, workerList] = await Promise.all([api.projects(), api.workers()]);
    setProjects(projectList);
    setWorkers(workerList);
    setSelected((current) => current ?? projectList[0]?.id ?? null);
  }, []);

  const refreshProject = useCallback(async (projectId: string) => {
    const [scene, history, projectAssets, projectJobs, projectGenerations, projectStyles] = await Promise.all([
      api.scene(projectId),
      api.revisions(projectId),
      api.assets(projectId),
      api.jobs(projectId),
      api.generations(projectId),
      api.styleProfiles(projectId)
    ]);
    setRevision(scene);
    setRevisions(history);
    setAssets(projectAssets);
    setJobs(projectJobs);
    setGenerations(projectGenerations);
    setStyleProfiles(projectStyles);
  }, []);

  useEffect(() => {
    api.me()
      .then(() => {
        setAuthenticated(true);
        return refreshProjectsAndWorkers();
      })
      .catch(() => setAuthenticated(false));
  }, [refreshProjectsAndWorkers]);

  useEffect(() => {
    if (!selected) {
      setRevision(null);
      setRevisions([]);
      setAssets([]);
      setJobs([]);
      setGenerations([]);
      setStyleProfiles([]);
      return;
    }
    refreshProject(selected).catch((error) => setMessage(String(error)));
  }, [selected, refreshProject]);

  useEffect(() => {
    if (!authenticated) return;
    const timer = window.setInterval(() => {
      void refreshProjectsAndWorkers();
      if (selected) void refreshProject(selected);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [authenticated, refreshProject, refreshProjectsAndWorkers, selected]);

  if (authenticated === null) return <main className="loading">STROY</main>;
  if (!authenticated) {
    return (
      <Login
        onLogin={() => {
          setAuthenticated(true);
          void refreshProjectsAndWorkers();
        }}
      />
    );
  }

  const onlineWorkers = workers.filter((worker) => worker.online);
  const currentProject = projects.find((project) => project.id === selected);

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!newProject.trim()) return;
    const project = await api.createProject(newProject.trim());
    setNewProject("");
    await refreshProjectsAndWorkers();
    setSelected(project.id);
  }

  async function createDemoScene() {
    if (!selected) return;
    await api.createScene(selected, goldenRoom(selected));
    await refreshProject(selected);
  }

  async function upload(file: File | null) {
    if (!file || !selected) return;
    try {
      setUploadProgress(0);
      setMessage("Загрузка...");
      const asset = await api.upload(
        selected,
        file,
        uploadRole,
        setUploadProgress
      );
      setMessage(`Asset ${asset.id} uploaded as ${asset.role}`);
      await refreshProject(selected);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      window.setTimeout(() => setUploadProgress(null), 800);
    }
  }

  async function submitInstruction(event: FormEvent) {
    event.preventDefault();
    if (!selected || !revision || !instruction.trim()) return;
    const text = instruction.trim();
    setInstruction("");
    await api.designInstruction(selected, text);
    setMessage("AI edit queued");
    await refreshProject(selected);
  }

  async function queueFakeGeneration() {
    if (!selected) return;
    await api.createJob(
      selected,
      "image.generate",
      ["image_generation"],
      revision ? { scene_revision_id: revision.revision_id } : {}
    );
    setMessage("Generation job queued");
    await refreshProject(selected);
  }

  async function cancel(jobId: string) {
    if (!selected) return;
    await api.cancelJob(jobId);
    setMessage(`Job ${shortId(jobId)} cancelled`);
    await refreshProject(selected);
  }

  async function rerenderRevision(targetRevisionId: string) {
    if (!selected || !revision) return;
    const cameraId = revision.scene.cameras[0]?.id;
    if (!cameraId) {
      setMessage("Нельзя запустить generation без камеры.");
      return;
    }
    await api.createGeneration(
      selected,
      targetRevisionId,
      cameraId,
      "re-render selected design revision"
    );
    setMessage(`Generation queued for revision ${shortId(targetRevisionId)}`);
    await refreshProject(selected);
  }

  async function restore(targetRevisionId: string) {
    if (!selected || !revision || targetRevisionId === revision.revision_id) return;
    await api.revert(selected, revision.revision_id, targetRevisionId);
    setMessage(`Restored revision ${shortId(targetRevisionId)}`);
    await refreshProject(selected);
  }

  async function analyzeStyleFromReferences() {
    if (!selected) return;
    const references = assets.filter(
      (asset) => asset.role === "reference" && asset.media_type.startsWith("image/")
    );
    if (references.length === 0) {
      setMessage("Загрузите reference-изображение, чтобы запустить анализ стиля.");
      return;
    }
    await api.analyzeStyle(selected, references.map((asset) => asset.id));
    setMessage("Style analysis queued");
    await refreshProject(selected);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">STROY</div>
        <div className={onlineWorkers.length ? "worker online" : "worker offline"}>
          GPU worker: {onlineWorkers.length ? "online" : "offline"}
        </div>

        <form onSubmit={createProject} className="new-project">
          <input
            placeholder="Новый проект"
            value={newProject}
            onChange={(event) => setNewProject(event.target.value)}
          />
          <button>+</button>
        </form>

        <nav>
          {projects.map((project) => (
            <button
              key={project.id}
              className={selected === project.id ? "project active" : "project"}
              onClick={() => setSelected(project.id)}
            >
              {project.name}
            </button>
          ))}
        </nav>

        <button
          className="logout"
          onClick={() => void api.logout().then(() => setAuthenticated(false))}
        >
          Выйти
        </button>
      </aside>

      <main className="workspace">
        <header>
          <div>
            <h1>{currentProject?.name ?? "Проект"}</h1>
            <span>
              {revision ? `revision ${shortId(revision.revision_id)}` : "scene not initialized"}
            </span>
          </div>
          <div className="actions">
            {!revision && selected && (
              <button onClick={() => void createDemoScene()}>Golden room</button>
            )}
            {selected && (
              <>
                <button onClick={() => void queueFakeGeneration()}>Test generation</button>
                <button onClick={() => void analyzeStyleFromReferences()}>Анализ стиля</button>
                <div className="upload-controls">
                  <select
                    value={uploadRole}
                    onChange={(event) => setUploadRole(event.target.value as AssetRole)}
                    aria-label="Asset role"
                  >
                    <option value="apartment">Квартира</option>
                    <option value="reference">Референс</option>
                  </select>
                  <label className="upload">
                    Загрузить файл
                    <input
                      type="file"
                      onChange={(event) => void upload(event.target.files?.[0] ?? null)}
                    />
                  </label>
                </div>
              </>
            )}
          </div>
        </header>

        {uploadProgress !== null && (
          <div className="upload-progress" aria-label="Upload progress">
            <div style={{ width: `${uploadProgress}%` }} />
            <span>{uploadProgress}%</span>
          </div>
        )}

        <section className="canvas-panel">
          <SceneViewer scene={revision?.scene ?? null} />
        </section>

        <form className="instruction-bar" onSubmit={submitInstruction}>
          <input
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="Например: сделай диван бежевым и убери стол"
            disabled={!revision}
          />
          <button disabled={!revision || !instruction.trim()}>Применить через AI</button>
        </form>

        <section className="dashboard-grid">
          {selected && revision && (
            <CameraPanel
              projectId={selected}
              revision={revision}
              assets={assets}
              onChanged={() => refreshProject(selected)}
            />
          )}

          {selected && revision && (
            <ReplacementPanel
              projectId={selected}
              revision={revision}
              assets={assets}
              onChanged={() => refreshProject(selected)}
            />
          )}

          <article className="panel">
            <h2>Compute</h2>
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
            <h2>Jobs</h2>
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
                    <button className="secondary" onClick={() => void cancel(job.id)}>
                      Cancel
                    </button>
                  )}
                </div>
              );
            })}
          </article>

          <article className="panel">
            <h2>Style profiles</h2>
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
            <h2>Assets</h2>
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

          <DesignPanel
            revisions={revisions}
            jobs={jobs}
            generations={generations}
            currentRevisionId={revision?.revision_id ?? null}
            cameraId={revision?.scene.cameras[0]?.id ?? null}
            onRestore={restore}
            onRerender={rerenderRevision}
          />
        </section>

        {message && <section className="status-panel">{message}</section>}
      </main>
    </div>
  );
}
