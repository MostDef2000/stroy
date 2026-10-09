// Task-oriented information architecture for the project shell (#101).
// Pure data module: no React, no side effects.
//
// "brief" (#198) is a destination page, not a tab: it opens from the Results
// «Поделиться с дизайнером» button (App keeps a separate briefVariantId
// state), has no nav-rail entry (PAGES below stays unchanged) and carries its
// own back button «Назад к результатам».
export type PageId =
  | "overview"
  | "plan"
  | "design"
  | "results"
  | "brief"
  | "diagnostics";

export const PAGES: { id: PageId; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "plan", label: "План" },
  { id: "design", label: "Дизайн" },
  { id: "results", label: "Результаты" },
  { id: "diagnostics", label: "Диагностика" }
];
