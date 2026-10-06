import { useEffect, useRef, useState } from "react";
import { PAGES, type PageId } from "./nav";

// Project title + horizontal page tabs + overflow menu. Since #142 this is the
// single place for account and project actions: the account identity and
// logout sit above the destructive delete entry (#101), inside the same «⋮»
// menu.
export function ProjectHeader({
  projectName,
  revisionLabel,
  page,
  onPageChange,
  canDelete,
  deleteError,
  onDelete,
  accountLabel,
  onLogout
}: {
  projectName: string;
  revisionLabel: string;
  page: PageId;
  onPageChange: (page: PageId) => void;
  canDelete: boolean;
  deleteError: string;
  onDelete: () => void;
  accountLabel: string;
  onLogout: () => void;
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

        {/* The «⋮» menu is the single place for account + project actions
            (#142): it renders on every page so logout is always reachable,
            while the destructive delete entry stays gated on a selection. */}
        <div className="overflow-menu" ref={menuRef}>
          <button
            type="button"
            className="overflow-menu-button"
            aria-label="Меню проекта и аккаунта"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            ⋮
          </button>
          {menuOpen && (
            <div className="overflow-dropdown" role="menu">
              <div className="menu-account-header" role="presentation">
                <span className="menu-eyebrow">Аккаунт</span>
                <div className="menu-account-row">
                  <span className="account-avatar" aria-hidden="true">
                    {accountLabel.trim().charAt(0).toUpperCase() || "В"}
                  </span>
                  <span className="menu-account-texts">
                    <span className="account-name">{accountLabel}</span>
                    <span className="account-role">Владелец</span>
                  </span>
                </div>
              </div>
              <div className="menu-divider" aria-hidden="true" />
              <button
                type="button"
                className="logout"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  onLogout();
                }}
              >
                Выйти из аккаунта
              </button>
              {canDelete && (
                <>
                  <div className="menu-divider" aria-hidden="true" />
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
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
