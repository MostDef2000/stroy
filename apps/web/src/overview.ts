import type { PageId } from "./nav";

// Readiness model for the Overview page (#101). Pure and total: every branch
// returns an item, nothing throws, no React involved.

export type ReadinessState = "ok" | "pending" | "attention";

export type ReadinessInput = {
  hasScene: boolean;
  sceneCameraCount: number;
  calibratedCameraCount: number;
  apartmentAssetCount: number;
  referenceAssetCount: number;
  revisionCount: number;
  activeJobCount: number;
};

export type ReadinessItem = {
  id: string;
  label: string;
  state: ReadinessState;
  detail: string;
  actionPage: PageId | null;
};

export function computeProjectReadiness(input: ReadinessInput): ReadinessItem[] {
  const {
    hasScene,
    sceneCameraCount,
    calibratedCameraCount,
    apartmentAssetCount,
    referenceAssetCount,
    revisionCount,
    activeJobCount
  } = input;

  const plan: ReadinessItem =
    apartmentAssetCount > 0
      ? {
          id: "plan",
          label: "План",
          state: "ok",
          detail: `Планов квартиры: ${apartmentAssetCount}. Референсов: ${referenceAssetCount}.`,
          actionPage: null
        }
      : {
          id: "plan",
          label: "План",
          state: "pending",
          detail: "Загрузите план квартиры, чтобы начать разметку.",
          actionPage: "plan"
        };

  const scene: ReadinessItem = hasScene
    ? {
        id: "scene",
        label: "3D сцена",
        state: "ok",
        detail: "Сцена создана.",
        actionPage: null
      }
    : {
        id: "scene",
        label: "3D сцена",
        state: "attention",
        // R6 (#183): nothing initializes automatically anymore — the 3D is
        // created from the checked plan on the plan page.
        detail: "Сцена ещё не создана — создайте 3D из плана на странице «План».",
        actionPage: "overview"
      };

  const camera: ReadinessItem = !hasScene
    ? {
        id: "camera",
        label: "Камера",
        state: "attention",
        detail: "Сначала нужна 3D-сцена.",
        actionPage: "overview"
      }
    : sceneCameraCount === 0
      ? {
          id: "camera",
          label: "Камера",
          state: "attention",
          detail: "В сцене нет камер.",
          actionPage: "design"
        }
      : calibratedCameraCount > 0
        ? {
            id: "camera",
            label: "Камера",
            state: "ok",
            detail: `Откалибровано камер: ${calibratedCameraCount} из ${sceneCameraCount}.`,
            actionPage: null
          }
        : {
            id: "camera",
            label: "Камера",
            state: "pending",
            detail: "Камера не откалибрована.",
            actionPage: "design"
          };

  const design: ReadinessItem =
    revisionCount > 1
      ? {
          id: "design",
          label: "Дизайн",
          state: "ok",
          detail: `Ревизий дизайна: ${revisionCount}.`,
          actionPage: null
        }
      : revisionCount === 1
        ? {
            id: "design",
            label: "Дизайн",
            state: "pending",
            detail: "Есть только базовый план. Внесите изменения.",
            actionPage: "design"
          }
        : {
            id: "design",
            label: "Дизайн",
            state: "pending",
            detail: "Дизайн ещё не начат.",
            actionPage: "overview"
          };

  const jobs: ReadinessItem =
    activeJobCount === 0
      ? {
          id: "jobs",
          label: "Активные задачи",
          state: "ok",
          detail: "Активных задач нет.",
          actionPage: null
        }
      : {
          id: "jobs",
          label: "Активные задачи",
          state: "pending",
          detail: `В работе задач: ${activeJobCount}.`,
          actionPage: "diagnostics"
        };

  return [plan, scene, camera, design, jobs];
}
