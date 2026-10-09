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
import { AppShell } from "./AppShell";
import { styleAnalysisMaxMessage, styleAnalysisMinMessage } from "./copy";
import { deleteConfirmText, nextStateAfterDelete } from "./projectDelete";
import { type PageId } from "./nav";
import { type ReadinessInput } from "./overview";
import { classifyAssetRole, type SetupStatus } from "./setup";
import type { SelectionContext } from "./surfaceEdits";
import { DesignPage } from "./pages/DesignPage";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PlanPage } from "./pages/PlanPage";
import { ResultsPage } from "./pages/ResultsPage";
import { BriefPage } from "./pages/BriefPage";
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
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    try {
      setError("");
      await api.login(username, password);
      onLogin();
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
    } finally {
      setSubmitting(false);
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
        <button disabled={submitting}>{submitting ? "Входим…" : "Войти"}</button>
        {error && (
          <div className="error" aria-live="polite">
            {error}
          </div>
        )}
      </form>
    </main>
  );
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
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [sceneReadyFor, setSceneReadyFor] = useState<string | null>(null);
  const [preparingScene, setPreparingScene] = useState(false);
  const [page, setPage] = useState<PageId>("overview");
  const [username, setUsername] = useState("Владелец");
  // R6 guided setup (#183): fetched in the refreshProject cycle, rendered by
  // the Overview page. Null while no project is selected or the setup route
  // has not answered yet.
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  // R8 designer brief (#198): variant id the brief page is open for (null =
  // closed). A destination state rather than a nav-rail page — opened from
  // the Results «Поделиться с дизайнером» button, closed by the page's own
  // back button.
  const [briefVariantId, setBriefVariantId] = useState<string | null>(null);

  const refreshProjectsAndWorkers = useCallback(async () => {
    const [projectList, workerList] = await Promise.all([api.projects(), api.workers()]);
    setProjects(projectList);
    setWorkers(workerList);
    setSelected((current) => current ?? projectList[0]?.id ?? null);
  }, []);

  const refreshProject = useCallback(async (projectId: string) => {
    const [scene, history, projectAssets, projectJobs, projectGenerations, projectStyles, setupStatus] =
      await Promise.all([
        api.scene(projectId),
        api.revisions(projectId),
        api.assets(projectId),
        api.jobs(projectId),
        api.generations(projectId),
        api.styleProfiles(projectId),
        api.getSetup(projectId)
      ]);
    setRevision(scene);
    setRevisions(history);
    setAssets(projectAssets);
    setJobs(projectJobs);
    setGenerations(projectGenerations);
    setStyleProfiles(projectStyles);
    setSetup(setupStatus);
    setSceneReadyFor(projectId);
  }, []);

  useEffect(() => {
    api.me()
      .then((me) => {
        if (me && typeof me.username === "string" && me.username) {
          setUsername(me.username);
        }
        setAuthenticated(true);
        return refreshProjectsAndWorkers();
      })
      .catch(() => setAuthenticated(false));
  }, [refreshProjectsAndWorkers]);

  // R8 (#198): switching projects (or deselecting) closes the brief — it is
  // bound to the project it was opened from.
  useEffect(() => {
    setBriefVariantId(null);
  }, [selected]);

  useEffect(() => {
    if (!selected) {
      setRevision(null);
      setRevisions([]);
      setAssets([]);
      setJobs([]);
      setGenerations([]);
      setStyleProfiles([]);
      setSetup(null);
      setSceneReadyFor(null);
      setPreparingScene(false);
      return;
    }
    refreshProject(selected).catch((error) => setMessage(String(error)));
  }, [selected, refreshProject]);

  // R6 (#183): no scene is ever created automatically. A fresh project starts
  // empty; the plan-first guidance on the Overview page leads the owner
  // through the setup steps, and the golden-room scene remains available as
  // the explicit demo action (createDemoScene below).

  useEffect(() => {
    if (!authenticated) return;
    const timer = window.setInterval(() => {
      void refreshProjectsAndWorkers();
      if (selected) void refreshProject(selected);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [authenticated, refreshProject, refreshProjectsAndWorkers, selected]);

  // R8 (#198): the Results share button opens the brief. The page state is
  // pinned to "results" so the brief's back button always lands there.
  // Rules of hooks: this must stay ABOVE the early returns below — the hook
  // count has to be identical across the loading → logged-in transition
  // (same defect class as the R7 DesignPage fix).
  const openBriefFromResults = useCallback((variantId: string) => {
    setPage("results");
    setBriefVariantId(variantId);
  }, []);

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
  const workersOnline = onlineWorkers.length > 0;
  const currentProject = projects.find((project) => project.id === selected);

  const readiness: ReadinessInput = {
    hasScene: revision !== null,
    sceneCameraCount: revision?.scene.cameras.length ?? 0,
    calibratedCameraCount:
      revision?.scene.cameras.filter((camera) => camera.calibration != null).length ?? 0,
    // R6: plan uploads now carry role "plan"; role "apartment" stays
    // legacy-accepted, so both roles count toward the plan readiness card.
    apartmentAssetCount: assets.filter(
      (asset) => classifyAssetRole(asset.role) === "plan" || classifyAssetRole(asset.role) === "legacy_apartment"
    ).length,
    referenceAssetCount: assets.filter((asset) => asset.role === "reference").length,
    revisionCount: revisions.length,
    activeJobCount: jobs.filter(
      (job) => !["succeeded", "failed", "cancelled"].includes(job.status)
    ).length
  };

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!newProject.trim()) return;
    const project = await api.createProject(newProject.trim());
    setNewProject("");
    await refreshProjectsAndWorkers();
    setSelected(project.id);
  }

  async function removeProject() {
    if (!selected || !currentProject) return;
    if (!window.confirm(deleteConfirmText(currentProject.name))) return;
    setDeleteError("");
    try {
      const outcome = await api.deleteProject(selected);
      // 409/generic errors keep the current selection; no refetch needed.
      if (outcome === "busy" || outcome === "error") {
        const decision = nextStateAfterDelete(outcome);
        if (decision.status === "error") setDeleteError(decision.message);
        return;
      }
      // Successful delete (204) or already-deleted (404): refetch the list
      // FIRST and decide from the FRESH list. Deciding from the in-render
      // `projects` could race a concurrent change (5s poll, another tab) and
      // strand the UI in the empty state while projects still exist.
      const projectList = await api.projects();
      setProjects(projectList);
      const decision = nextStateAfterDelete(outcome, projectList);
      setSelected(decision.status === "empty" ? null : projectList[0]?.id ?? null);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : String(error));
    }
  }

  async function createDemoScene() {
    if (!selected) return;
    setPreparingScene(true);
    try {
      await api.createScene(selected, goldenRoom(selected));
      // Q1 quick-win: the demo creation is the documented way out of a
      // «сцена не создана» 404 — drop the cached null so the next read
      // sees the fresh scene.
      api.invalidateSceneCache(selected);
      await refreshProject(selected);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setPreparingScene(false);
    }
  }

  async function upload(file: File | null, role: AssetRole) {
    if (!file || !selected) return;
    try {
      setUploadProgress(0);
      setMessage("Загрузка...");
      const asset = await api.upload(
        selected,
        file,
        role,
        setUploadProgress
      );
      setMessage(`Файл загружен (роль: ${asset.role})`);
      await refreshProject(selected);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      window.setTimeout(() => setUploadProgress(null), 800);
    }
  }

  // R10 (#186): DesignPage builds the selection context from the entity
  // selected at submit time and hands it over here; undefined = nothing is
  // selected, the payload stays exactly {text} as before.
  async function submitInstruction(
    event: FormEvent,
    selectionContext?: SelectionContext
  ) {
    event.preventDefault();
    if (!selected || !revision || !instruction.trim()) return;
    const text = instruction.trim();
    setInstruction("");
    await api.designInstruction(selected, text, selectionContext);
    setMessage("Правка фото поставлена в обработку");
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
    setMessage("Тестовая генерация запущена");
    await refreshProject(selected);
  }

  async function cancel(jobId: string) {
    if (!selected) return;
    await api.cancelJob(jobId);
    setMessage("Задача отменена");
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
    setMessage("Генерация запущена");
    await refreshProject(selected);
  }

  async function restore(targetRevisionId: string) {
    if (!selected || !revision || targetRevisionId === revision.revision_id) return;
    await api.revert(selected, revision.revision_id, targetRevisionId);
    setMessage("Версия восстановлена");
    await refreshProject(selected);
  }

  async function analyzeStyleFromReferences() {
    if (!selected) return;
    const references = assets.filter(
      (asset) => asset.role === "reference" && asset.media_type.startsWith("image/")
    );
    if (references.length < 3) {
      setMessage(styleAnalysisMinMessage(references.length));
      return;
    }
    if (references.length > 5) {
      setMessage(styleAnalysisMaxMessage(references.length));
      return;
    }
    try {
      await api.analyzeStyle(selected, references.map((asset) => asset.id));
      setMessage("Анализ стиля поставлен в обработку");
      await refreshProject(selected);
    } catch (err) {
      setMessage(`Не удалось запустить анализ стиля: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // R8 (#198): the Results share button opens the brief — openBriefFromResults
  // lives above the early returns (see the comment there).

  return (
    <AppShell
      projects={projects}
      selected={selected}
      workersOnline={workersOnline}
      newProject={newProject}
      onNewProjectChange={setNewProject}
      onCreateProject={createProject}
      onSelectProject={(id) => {
        setSelected(id);
        setDeleteError("");
      }}
      onLogout={() =>
        void api
          .logout()
          .catch(() => undefined)
          .finally(() => setAuthenticated(false))
      }
      accountLabel={username}
      projectName={currentProject?.name ?? "Проект"}
      revisionLabel={revision ? "Сцена готова" : "Сцена ещё не создана"}
      page={page}
      onPageChange={setPage}
      canDelete={Boolean(selected)}
      deleteError={deleteError}
      onDelete={() => void removeProject()}
      message={message}
    >
      {/* R8 (#198): the designer brief replaces the page slot while open
          (destination page with its own back button, no nav-rail entry). */}
      {selected && briefVariantId && (
        <BriefPage
          projectId={selected}
          variantId={briefVariantId}
          onBack={() => setBriefVariantId(null)}
        />
      )}

      {selected && !briefVariantId && page === "overview" && (
        <OverviewPage
          readiness={readiness}
          projectId={selected}
          scene={revision?.scene ?? null}
          hasProject={Boolean(currentProject)}
          hasScene={revision !== null}
          setup={setup}
          preparingScene={preparingScene}
          onCreateDemoScene={() => void createDemoScene()}
          onNavigate={setPage}
        />
      )}

      {selected && !briefVariantId && page === "plan" && (
        <PlanPage
          projectId={selected}
          assets={assets}
          jobs={jobs}
          workersOnline={workersOnline}
          onChanged={() => refreshProject(selected)}
          onUpload={upload}
          uploadProgress={uploadProgress}
        />
      )}

      {selected && !briefVariantId && page === "design" && (
        <DesignPage
          projectId={selected}
          revision={revision}
          assets={assets}
          jobs={jobs}
          generations={generations}
          workersOnline={workersOnline}
          onChanged={() => refreshProject(selected)}
          onUpload={upload}
          uploadProgress={uploadProgress}
          instruction={instruction}
          onInstructionChange={setInstruction}
          onSubmitInstruction={submitInstruction}
          onAnalyzeStyle={() => void analyzeStyleFromReferences()}
          onNavigate={setPage}
        />
      )}

      {selected && !briefVariantId && page === "results" && (
        <ResultsPage
          projectId={selected}
          revisions={revisions}
          jobs={jobs}
          generations={generations}
          currentRevisionId={revision?.revision_id ?? null}
          cameraId={revision?.scene.cameras[0]?.id ?? null}
          onRestore={restore}
          onRerender={rerenderRevision}
          onShareWithDesigner={openBriefFromResults}
        />
      )}

      {selected && !briefVariantId && page === "diagnostics" && (
        <DiagnosticsPage
          workers={workers}
          jobs={jobs}
          revisions={revisions}
          generations={generations}
          styleProfiles={styleProfiles}
          assets={assets}
          onCancel={(jobId) => void cancel(jobId)}
          onTestGeneration={() => void queueFakeGeneration()}
        />
      )}
    </AppShell>
  );
}
