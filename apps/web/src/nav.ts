// Task-oriented information architecture for the project shell (#101).
// Pure data module: no React, no side effects.

export type PageId = "overview" | "plan" | "design" | "results" | "diagnostics";

export const PAGES: { id: PageId; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "plan", label: "План" },
  { id: "design", label: "Дизайн" },
  { id: "results", label: "Результаты" },
  { id: "diagnostics", label: "Диагностика" }
];
