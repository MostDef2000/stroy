import type { FormEvent } from "react";
import type { Project } from "./api";

// Extracted sidebar from the pre-#101 App layout: brand, worker indicator,
// new-project form, project list nav, logout. Presentational only.
export function ProjectSidebar({
  projects,
  selected,
  workersOnline,
  newProject,
  onNewProjectChange,
  onCreateProject,
  onSelectProject,
  onLogout
}: {
  projects: Project[];
  selected: string | null;
  workersOnline: boolean;
  newProject: string;
  onNewProjectChange: (value: string) => void;
  onCreateProject: (event: FormEvent) => void;
  onSelectProject: (id: string) => void;
  onLogout: () => void;
}) {
  return (
    <aside className="sidebar">
      <div className="brand">STROY</div>
      <div className={workersOnline ? "worker online" : "worker offline"}>
        GPU worker: {workersOnline ? "online" : "offline"}
      </div>

      <form onSubmit={onCreateProject} className="new-project">
        <input
          placeholder="Новый проект"
          value={newProject}
          onChange={(event) => onNewProjectChange(event.target.value)}
        />
        <button>+</button>
      </form>

      <nav>
        {projects.map((project) => (
          <button
            key={project.id}
            className={selected === project.id ? "project active" : "project"}
            onClick={() => onSelectProject(project.id)}
          >
            {project.name}
          </button>
        ))}
      </nav>

      <button className="logout" onClick={onLogout}>
        Выйти
      </button>
    </aside>
  );
}
