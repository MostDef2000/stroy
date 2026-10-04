import { useEffect, useRef, useState } from "react";
import { PAGES, type PageId } from "./nav";

// Project title + horizontal page tabs + overflow menu (destructive actions).
// Deletion lives inside the «⋮» menu so it is not a primary control next to the
// title (#101).
export function ProjectHeader({
  projectName,
  revisionLabel,
  page,
  onPageChange,
  canDelete,
  deleteError,
  onDelete
}: {
  projectName: string;
  revisionLabel: string;
  page: PageId;
  onPageChange: (page: PageId) => void;
  canDelete: boolean;
  deleteError: string;
  onDelete: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Reopen the menu when a delete error arrives so the message is visible.
  useEffect(() => {
    if (deleteError) setMenuOpen(true);
  }, [deleteError]);

  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  return (
    <header className="project-header">
      <div className="project-heading">
        <h1>{projectName}</h1>
        <span>{revisionLabel}</span>
      </div>

      <div className="project-header-controls">
        <nav className="page-tabs" aria-label="Разделы проекта">
          {PAGES.map((entry) => (
            <button
              key={entry.id}
              type="button"
              className={page === entry.id ? "page-tab active" : "page-tab"}
              aria-current={page === entry.id ? "page" : undefined}
              onClick={() => onPageChange(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </nav>

        {canDelete && (
          <div className="overflow-menu" ref={menuRef}>
            <button
              type="button"
              className="overflow-menu-button"
              aria-label="Дополнительные действия"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              ⋮
            </button>
            {menuOpen && (
              <div className="overflow-dropdown" role="menu">
                <button
                  type="button"
                  className="danger"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onDelete();
                  }}
                >
                  Удалить проект
                </button>
                {deleteError && <div className="error">{deleteError}</div>}
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
