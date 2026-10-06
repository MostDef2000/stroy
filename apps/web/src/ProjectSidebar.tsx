import { useEffect, useRef, useState } from "react";
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
  onLogout,
  accountLabel
}: {
  projects: Project[];
  selected: string | null;
  workersOnline: boolean;
  newProject: string;
  onNewProjectChange: (value: string) => void;
  onCreateProject: (event: FormEvent) => void;
  onSelectProject: (id: string) => void;
  onLogout: () => void;
  accountLabel: string;
}) {
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!accountMenuOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (accountRef.current && !accountRef.current.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setAccountMenuOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [accountMenuOpen]);

  return (
    <aside className="sidebar">
      <div className="brand">STROY</div>
      <div className={workersOnline ? "worker online" : "worker offline"}>
        {workersOnline ? "Обработка доступна" : "Обработка недоступна"}
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

      <div className="sidebar-account" ref={accountRef}>
        <button
          type="button"
          className="account-button"
          aria-label={`Аккаунт: ${accountLabel}. ${accountMenuOpen ? "Закрыть меню" : "Открыть меню"}`}
          aria-haspopup="menu"
          aria-expanded={accountMenuOpen}
          onClick={() => setAccountMenuOpen((open) => !open)}
        >
          <span className="account-avatar" aria-hidden="true">
            {accountLabel.trim().charAt(0).toUpperCase() || "В"}
          </span>
          <span className="account-name">{accountLabel}</span>
          <span className="account-role">Владелец</span>
          <span className="account-chevron" aria-hidden="true">
            ⋮
          </span>
        </button>
        {accountMenuOpen && (
          <div className="overflow-dropdown account-menu" role="menu">
            <button
              type="button"
              className="logout"
              role="menuitem"
              onClick={() => {
                setAccountMenuOpen(false);
                onLogout();
              }}
            >
              Выйти
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
