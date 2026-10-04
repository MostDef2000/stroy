// Single source of truth for owner-facing job status vocabulary (#108).
// Pure module: no React/api imports so it can be unit-tested in isolation.
export function statusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "в очереди";
    case "pending":
      return "ожидает";
    case "running":
      return "выполняется";
    case "leased":
      return "выполняется";
    case "succeeded":
      return "выполнено";
    case "failed":
      return "ошибка";
    case "cancelled":
      return "отменено";
    default:
      return status;
  }
}
