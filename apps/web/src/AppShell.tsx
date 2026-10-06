import type { FormEvent, ReactNode } from "react";
import type { Project } from "./api";
import type { PageId } from "./nav";
import { ProjectHeader } from "./ProjectHeader";
import { ProjectSidebar } from "./ProjectSidebar";

// Presentational application shell: sidebar + project header (page tabs) +
// active page slot + global message/status strip. It owns no data and fetches
// nothing; App supplies every value and callback.
export function AppShell({
  projects,
  selected,
  workersOnline,
  newProject,
  onNewProjectChange,
  onCreateProject,
  onSelectProject,
  onLogout,
  accountLabel,
  projectName,
  revisionLabel,
  page,
  onPageChange,
  canDelete,
  deleteError,
  onDelete,
  message,
  children
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
  projectName: string;
  revisionLabel: string;
  page: PageId;
  onPageChange: (page: PageId) => void;
  canDelete: boolean;
  deleteError: string;
  onDelete: () => void;
  message: string;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <ProjectSidebar
        projects={projects}
        selected={selected}
        workersOnline={workersOnline}
        newProject={newProject}
        onNewProjectChange={onNewProjectChange}
        onCreateProject={onCreateProject}
        onSelectProject={onSelectProject}
      />

      <main className="workspace">
        <ProjectHeader
          projectName={projectName}
          revisionLabel={revisionLabel}
          page={page}
          onPageChange={onPageChange}
          canDelete={canDelete}
          deleteError={deleteError}
          onDelete={onDelete}
          accountLabel={accountLabel}
          onLogout={onLogout}
        />

        {children}

        {message && <section className="status-panel">{message}</section>}
      </main>
    </div>
  );
}
