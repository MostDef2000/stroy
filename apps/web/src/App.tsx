import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Project, SceneDocument, SceneRevision, Worker } from "./api";
import { SceneViewer } from "./SceneViewer";
import "./styles.css";

function goldenRoom(projectId: string): SceneDocument {
  return {
    scene_id: "scene.golden-room",
    project_id: projectId,
    cameras: [],
    entities: [
      {
        id: "surface.floor.living",
        kind: "floor",
        transform: { translation_mm: [0, 0, -50], rotation_deg: [0, 0, 0], scale: [1, 1, 1] },
        geometry: { dimensions_mm: [5000, 4000, 100] },
        locks: { geometry: true, transform: true, material: false }
      },
      {
        id: "surface.wall.living.north",
        kind: "wall",
        transform: { translation_mm: [0, 2000, 1400], rotation_deg: [0, 0, 0], scale: [1, 1, 1] },
        geometry: { dimensions_mm: [5000, 120, 2800] },
        locks: { geometry: true, transform: true, material: false }
      },
      {
        id: "surface.wall.living.west",
        kind: "wall",
        transform: { translation_mm: [-2500, 0, 1400], rotation_deg: [0, 0, 90], scale: [1, 1, 1] },
        geometry: { dimensions_mm: [4000, 120, 2800] },
        locks: { geometry: true, transform: true, material: false }
      },
      {
        id: "object.sofa.main",
        kind: "furniture",
        transform: { translation_mm: [0, 1200, 450], rotation_deg: [0, 0, 0], scale: [1, 1, 1] },
        geometry: { dimensions_mm: [2200, 900, 900] },
        locks: { geometry: false, transform: false, material: false },
        metadata: { color: "#b8b0a4" }
      },
      {
        id: "object.coffee_table.main",
        kind: "furniture",
        transform: { translation_mm: [0, 0, 250], rotation_deg: [0, 0, 0], scale: [1, 1, 1] },
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
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [revision, setRevision] = useState<SceneRevision | null>(null);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [newProject, setNewProject] = useState("");
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    const [projectList, workerList] = await Promise.all([api.projects(), api.workers()]);
    setProjects(projectList);
    setWorkers(workerList);
    setSelected((current) => current ?? projectList[0]?.id ?? null);
  }, []);

  useEffect(() => {
    api.me()
      .then(() => {
        setAuthenticated(true);
        return refresh();
      })
      .catch(() => setAuthenticated(false));
  }, [refresh]);

  useEffect(() => {
    if (!selected) {
      setRevision(null);
      return;
    }
    api.scene(selected).then(setRevision).catch((e) => setMessage(String(e)));
  }, [selected]);

  if (authenticated === null) return <main className="loading">STROY</main>;
  if (!authenticated) {
    return (
      <Login
        onLogin={() => {
          setAuthenticated(true);
          void refresh();
        }}
      />
    );
  }

  const online = workers.filter((worker) => worker.online);

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!newProject.trim()) return;
    const project = await api.createProject(newProject.trim());
    setNewProject("");
    await refresh();
    setSelected(project.id);
  }

  async function createDemoScene() {
    if (!selected) return;
    const created = await api.createScene(selected, goldenRoom(selected));
    setRevision(created);
  }

  async function upload(file: File | null) {
    if (!file || !selected) return;
    setMessage("Загрузка...");
    const asset = await api.upload(selected, file);
    setMessage(`Asset ${asset.id} uploaded`);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">STROY</div>
        <div className={online.length ? "worker online" : "worker offline"}>
          GPU worker: {online.length ? "online" : "offline"}
        </div>

        <form onSubmit={createProject} className="new-project">
          <input
            placeholder="Новый проект"
            value={newProject}
            onChange={(e) => setNewProject(e.target.value)}
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
            <h1>{projects.find((p) => p.id === selected)?.name ?? "Проект"}</h1>
            <span>{revision ? `revision ${revision.revision_id.slice(0, 8)}` : "scene not initialized"}</span>
          </div>
          <div className="actions">
            {!revision && selected && <button onClick={() => void createDemoScene()}>Golden room</button>}
            {selected && (
              <label className="upload">
                Загрузить файл
                <input type="file" onChange={(e) => void upload(e.target.files?.[0] ?? null)} />
              </label>
            )}
          </div>
        </header>

        <section className="canvas-panel">
          <SceneViewer scene={revision?.scene ?? null} />
        </section>

        <section className="status-panel">
          <strong>Compute</strong>
          {workers.length === 0 && <span>worker ещё не зарегистрирован</span>}
          {workers.map((worker) => (
            <span key={worker.id}>
              {worker.display_name ?? worker.id}: {worker.online ? "online" : "offline"} ·{" "}
              {worker.models.join(", ")}
            </span>
          ))}
          {message && <span>{message}</span>}
        </section>
      </main>
    </div>
  );
}
