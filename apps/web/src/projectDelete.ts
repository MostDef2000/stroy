// Pure, zero-dependency decision logic for deleting a project from the UI.
//
// Backend contract (src/stroy/api/routes.py :: project_delete):
//   204 -> deleted
//   404 -> unknown project; treat as already deleted
//   409 -> {"code": "project_busy"} while any project job is non-terminal
// The component maps the HTTP status through `deleteOutcomeFromStatus` and then
// `nextStateAfterDelete` to pick the next UI state. Keeping this pure makes it
// unit-testable without a DOM (see tests/projectDelete.test.mjs).

export type ProjectDeleteOutcome =
  | "deleted"
  | "already-deleted"
  | "busy"
  | "error";

export const PROJECT_BUSY_MESSAGE = "Проект занят: дождитесь завершения джоб";
export const PROJECT_DELETE_ERROR_MESSAGE = "Не удалось удалить проект";

export type ProjectDeleteDecision =
  | { status: "select-next" }
  | { status: "empty" }
  | { status: "error"; message: string };

/** HTTP status from DELETE /api/v1/projects/{id} -> semantic outcome. */
export function deleteOutcomeFromStatus(status: number): ProjectDeleteOutcome {
  if (status === 204) return "deleted";
  if (status === 404) return "already-deleted";
  if (status === 409) return "busy";
  return "error";
}

/**
 * Decide what the project list should do after a delete attempt.
 *
 * `freshProjects` must be the list refetched AFTER the delete completed, not the
 * in-render list the button was clicked from. A concurrent change (5s poll,
 * another tab) can make the render-time list stale; deciding from it could pick
 * the empty state while projects still exist. 204 and 404 share the same refresh
 * path; 409 keeps the current selection and surfaces the busy message; anything
 * else surfaces a generic error.
 */
export function nextStateAfterDelete(
  outcome: ProjectDeleteOutcome,
  freshProjects: readonly { id: string }[] = []
): ProjectDeleteDecision {
  if (outcome === "deleted" || outcome === "already-deleted") {
    return freshProjects.length > 0 ? { status: "select-next" } : { status: "empty" };
  }
  if (outcome === "busy") {
    return { status: "error", message: PROJECT_BUSY_MESSAGE };
  }
  return { status: "error", message: PROJECT_DELETE_ERROR_MESSAGE };
}

/** Confirmation prompt text; always includes the project name, never only its id. */
export function deleteConfirmText(projectName: string): string {
  return `Удалить проект «${projectName}»? Действие необратимо.`;
}
