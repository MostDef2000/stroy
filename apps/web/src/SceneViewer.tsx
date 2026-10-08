import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  PerspectiveCamera,
  Plane,
  Raycaster,
  Vector2,
  Vector3,
  type Camera,
  type WebGLRenderer
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { api, type SceneCamera, type SceneDocument, type SceneEntity } from "./api";
import { entityKindLabel } from "./copy";
import {
  buildMoveObjectCommand,
  buildRemoveObjectCommand,
  buildRotateZCommand,
  canDragEntity,
  pointerAngleRad,
  rotationFromPointerAngles,
  translationFromFloorPoints,
  type FloorPointMm,
  type MoveObjectParameters
} from "./furnitureDrag";
import {
  canonicalDimensionsToThree,
  canonicalPositionToThree,
  canonicalRotationToThreeQuaternion,
  roomViewSuggestionFromBounds,
  threeToCanonicalPosition,
  threeToCanonicalRotation,
  viewpointIntrinsicsFromPerspectiveCamera,
  type PlanBoundsMm,
  type RoomViewSuggestion
} from "./sceneMath";
import { groupViewpointOptions } from "./cameraReadiness";
import { apiErrorText, uniqueId } from "./twinDesign";

type PlanWallFrame = {
  translationMm: [number, number, number];
  rotationDeg: [number, number, number];
  dimensionsMm: [number, number, number];
};

/** Optimistic transform shown while dragging, keyed by entity id. */
type PreviewTransform = {
  translation_mm?: [number, number, number];
  rotation_deg?: [number, number, number];
};

type ThreeContext = { camera: Camera; gl: WebGLRenderer };

type DragSession = {
  entity: SceneEntity;
  element: HTMLElement;
  startFloor: FloorPointMm;
  startTranslation: [number, number, number];
  startRotation: [number, number, number];
  startPointerAngle: number;
  preview: PreviewTransform;
};

// Reused across pointer moves to avoid per-event allocations. The canonical
// scene is +Z up while three.js is +Y up (see sceneMath), so the floor is the
// three.js y=0 plane; the hit is mapped back into canonical floor mm below.
const floorPlane = new Plane(new Vector3(0, 1, 0), 0);
const floorHit = new Vector3();
const raycaster = new Raycaster();

function floorPointFromPointer(
  event: PointerEvent,
  camera: Camera,
  element: HTMLElement
): FloorPointMm | null {
  const rect = element.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return null;
  const ndc = new Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1
  );
  raycaster.setFromCamera(ndc, camera);
  const hit = raycaster.ray.intersectPlane(floorPlane, floorHit);
  if (!hit) return null;
  // three world (x, y, z) metres -> canonical (x, -z) floor millimetres.
  return { x: hit.x * 1000, y: -hit.z * 1000 };
}

function sameTriplet(
  a: [number, number, number],
  b: [number, number, number]
): boolean {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
}

function isConflictError(reason: unknown): boolean {
  const message = reason instanceof Error ? reason.message : String(reason);
  return /^409:/.test(message);
}

function planWallFrame(entity: SceneEntity): PlanWallFrame | null {
  if (entity.kind !== "wall") return null;
  const geometry = entity.geometry ?? {};
  const a = geometry["a"];
  const b = geometry["b"];
  const dimensions = geometry["dimensions_mm"];
  if (
    !Array.isArray(a) ||
    a.length !== 2 ||
    !Array.isArray(b) ||
    b.length !== 2 ||
    !dimensions ||
    typeof dimensions !== "object" ||
    Array.isArray(dimensions)
  ) {
    return null;
  }
  const [ax, ay] = a.map(Number);
  const [bx, by] = b.map(Number);
  const record = dimensions as Record<string, unknown>;
  const thickness = Number(record["thickness"]);
  const height = Number(record["height"]);
  if (![ax, ay, bx, by, thickness, height].every(Number.isFinite)) return null;
  const length = Math.hypot(bx - ax, by - ay);
  if (length <= 0 || thickness <= 0 || height <= 0) return null;
  return {
    translationMm: [(ax + bx) / 2, (ay + by) / 2, height / 2],
    rotationDeg: [0, 0, (Math.atan2(by - ay, bx - ax) * 180) / Math.PI],
    dimensionsMm: [length, thickness, height]
  };
}

function dimensions(entity: SceneEntity): [number, number, number] {
  const geometry = entity.geometry ?? {};
  const raw = geometry["dimensions_mm"] ?? geometry["size_mm"];
  if (
    Array.isArray(raw) &&
    raw.length === 3 &&
    raw.every((value) => Number.isFinite(Number(value)))
  ) {
    return canonicalDimensionsToThree(raw.map(Number) as [number, number, number]);
  }
  return entity.kind === "wall" ? [4, 2.8, 0.12] : [0.8, 0.8, 0.8];
}

function isPlanSemanticOnly(entity: SceneEntity): boolean {
  const geometry = entity.geometry ?? {};
  if (entity.kind === "room" && Array.isArray(geometry["wall_ids"])) return true;
  return typeof geometry["host_wall_id"] === "string";
}

function planOverview(
  entities: SceneEntity[]
): { x: number; z: number; span: number } | null {
  const points: Array<[number, number]> = [];
  for (const entity of entities) {
    if (entity.kind !== "wall") continue;
    const geometry = entity.geometry ?? {};
    const a = geometry["a"];
    const b = geometry["b"];
    if (
      !Array.isArray(a) ||
      a.length !== 2 ||
      !Array.isArray(b) ||
      b.length !== 2
    ) {
      continue;
    }
    const ax = Number(a[0]);
    const ay = Number(a[1]);
    const bx = Number(b[0]);
    const by = Number(b[1]);
    if (![ax, ay, bx, by].every(Number.isFinite)) continue;
    points.push([ax, ay], [bx, by]);
  }
  if (points.length === 0) return null;
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  return {
    x: (minX + maxX) / 2000,
    z: -(minY + maxY) / 2000,
    span: Math.max(maxX - minX, maxY - minY) / 1000
  };
}

/** One per-room auto viewpoint candidate (#187): label + plan bounds. */
type RoomAutoView = {
  roomId: string;
  label: string;
  bounds: PlanBoundsMm;
};

/**
 * Rooms with their plan-space bounding boxes, derived the same way as
 * planOverview: wall endpoints from geometry a/b. Rooms without resolvable
 * wall geometry are skipped (no sane camera placement exists for them).
 * Pure helper — mirrors planOverview's parsing rules exactly.
 */
function roomAutoViews(entities: SceneEntity[]): RoomAutoView[] {
  const wallsById = new Map<string, { a: [number, number]; b: [number, number] }>();
  for (const entity of entities) {
    if (entity.kind !== "wall") continue;
    const geometry = entity.geometry ?? {};
    const a = geometry["a"];
    const b = geometry["b"];
    if (!Array.isArray(a) || a.length !== 2 || !Array.isArray(b) || b.length !== 2) {
      continue;
    }
    const ax = Number(a[0]);
    const ay = Number(a[1]);
    const bx = Number(b[0]);
    const by = Number(b[1]);
    if (![ax, ay, bx, by].every(Number.isFinite)) continue;
    wallsById.set(entity.id, { a: [ax, ay], b: [bx, by] });
  }
  const views: RoomAutoView[] = [];
  for (const entity of entities) {
    if (entity.kind !== "room") continue;
    const wallIds = (entity.geometry ?? {})["wall_ids"];
    if (!Array.isArray(wallIds)) continue;
    const points: Array<[number, number]> = [];
    for (const wallId of wallIds) {
      const wall = wallsById.get(String(wallId));
      if (!wall) continue;
      points.push(wall.a, wall.b);
    }
    if (points.length === 0) continue;
    const xs = points.map(([x]) => x);
    const ys = points.map(([, y]) => y);
    // F4 (review): unnamed rooms must not leak the raw room id into the
    // picker — auto-view labels fall back to the room's ordinal position
    // («Комната N» by auto-view index). roomHeading itself keeps its
    // id-fallback behavior (Q3, test-pinned) and is not used here.
    const name =
      typeof entity.display_name === "string" ? entity.display_name.trim() : "";
    views.push({
      roomId: entity.id,
      label: name !== "" ? name : `Комната ${views.length + 1}`,
      bounds: {
        minX: Math.min(...xs),
        maxX: Math.max(...xs),
        minY: Math.min(...ys),
        maxY: Math.max(...ys)
      }
    });
  }
  return views;
}

/** Publishes the three.js camera/renderer so the outer component can raycast. */
function SceneBridge({
  onReady
}: {
  onReady: (context: ThreeContext) => void;
}) {
  const { camera, gl } = useThree();

  useEffect(() => {
    onReady({ camera, gl });
  }, [camera, gl, onReady]);

  return null;
}

function EntityMesh({
  entity,
  selected,
  debugLocks,
  preview,
  onSelect,
  onDragStart,
  onHoverChange
}: {
  entity: SceneEntity;
  selected: boolean;
  debugLocks: boolean;
  preview: PreviewTransform | null;
  onSelect: () => void;
  onDragStart: (event: ThreeEvent<PointerEvent>, entity: SceneEntity) => void;
  onHoverChange: (over: boolean) => void;
}) {
  const wallFrame = planWallFrame(entity);
  const size = wallFrame
    ? canonicalDimensionsToThree(wallFrame.dimensionsMm)
    : dimensions(entity);
  const baseP =
    wallFrame?.translationMm ?? entity.transform?.translation_mm ?? [0, 0, 0];
  const baseR =
    wallFrame?.rotationDeg ?? entity.transform?.rotation_deg ?? [0, 0, 0];
  const p = preview?.translation_mm ?? baseP;
  const r = preview?.rotation_deg ?? baseR;
  const draggable = canDragEntity(entity);
  const geometryLocked = Boolean(entity.locks?.geometry);
  const color =
    (entity.metadata?.["color"] as string | undefined) ??
    (selected
      ? "#d8b37a"
      : debugLocks && geometryLocked
        ? "#d16645"
        : "#b7b9bd");

  return (
    <mesh
      position={canonicalPositionToThree(p)}
      quaternion={canonicalRotationToThreeQuaternion(r)}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      onPointerDown={
        draggable ? (event) => onDragStart(event, entity) : undefined
      }
      onPointerOver={
        draggable
          ? (event) => {
              event.stopPropagation();
              onHoverChange(true);
            }
          : undefined
      }
      onPointerOut={draggable ? () => onHoverChange(false) : undefined}
    >
      <boxGeometry args={size} />
      <meshStandardMaterial
        color={color}
        wireframe={debugLocks && geometryLocked}
        transparent
        opacity={entity.kind === "wall" ? 0.72 : 1}
      />
    </mesh>
  );
}

function CameraController({
  calibrated,
  overview,
  autoView,
  resetKey,
  sceneKey
}: {
  calibrated: SceneCamera | null;
  overview: { x: number; z: number; span: number } | null;
  /** R7 (#187): per-room auto viewpoint placement (not persisted). */
  autoView: RoomViewSuggestion | null;
  resetKey: number;
  sceneKey: string;
}) {
  const { camera } = useThree();

  useEffect(() => {
    if (!(camera instanceof PerspectiveCamera)) return;

    if (!calibrated) {
      camera.quaternion.identity();
      camera.fov = 45;
      camera.aspect = 1.6;
      if (overview) {
        const distance = Math.max(6, overview.span * 0.95);
        camera.position.set(
          overview.x + distance * 0.7,
          distance * 0.8,
          overview.z + distance * 0.7
        );
        camera.lookAt(overview.x, 1.1, overview.z);
      } else {
        camera.position.set(6, 5, 6);
        camera.lookAt(0, 0.8, 0);
      }
      camera.updateProjectionMatrix();
      return;
    }

    camera.position.set(...canonicalPositionToThree(calibrated.transform.translation_mm));
    camera.quaternion.copy(
      canonicalRotationToThreeQuaternion(calibrated.transform.rotation_deg)
    );

    const { fx, fy, cx, cy } = calibrated.intrinsics;
    const width = calibrated.width_px;
    const height = calibrated.height_px;
    const near = 0.01;
    const far = 1000;

    camera.near = near;
    camera.far = far;
    camera.aspect = width / height;
    camera.fov = (2 * Math.atan(height / (2 * fy)) * 180) / Math.PI;

    camera.projectionMatrix.set(
      (2 * fx) / width,
      0,
      1 - (2 * cx) / width,
      0,
      0,
      (2 * fy) / height,
      (2 * cy) / height - 1,
      0,
      0,
      0,
      -(far + near) / (far - near),
      (-2 * far * near) / (far - near),
      0,
      0,
      -1,
      0
    );
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
  }, [calibrated, camera, resetKey, sceneKey]);

  // R7 (#187): auto room views fly the default camera to the suggested pose;
  // a separate effect so an overview/calibrated change still wins via the
  // effect above. The suggestion object identity changes with the selection.
  useEffect(() => {
    if (!autoView || !(camera instanceof PerspectiveCamera)) return;
    camera.quaternion.identity();
    camera.position.set(...canonicalPositionToThree(autoView.positionMm));
    camera.lookAt(...canonicalPositionToThree(autoView.looksAtMm));
    camera.updateProjectionMatrix();
  }, [autoView, camera, resetKey, sceneKey]);

  return null;
}

function OverviewOrbitControls({
  enabled,
  interactionLocked,
  overview,
  orbitTarget,
  resetKey,
  sceneKey
}: {
  enabled: boolean;
  interactionLocked: boolean;
  overview: { x: number; z: number; span: number } | null;
  /** R7 (#187): orbit target for the current view (auto views aim at the room). */
  orbitTarget: [number, number, number] | null;
  resetKey: number;
  sceneKey: string;
}) {
  const { camera, gl } = useThree();
  const controlsRef = useRef<OrbitControls | null>(null);

  useEffect(() => {
    if (!enabled || !(camera instanceof PerspectiveCamera)) return;

    const controls = new OrbitControls(camera, gl.domElement);
    controls.enableDamping = false;
    controls.enablePan = true;
    controls.screenSpacePanning = true;
    controls.minDistance = Math.max(1.5, (overview?.span ?? 6) * 0.15);
    controls.maxDistance = Math.max(30, (overview?.span ?? 6) * 4);
    controls.maxPolarAngle = Math.PI / 2 - 0.03;
    if (orbitTarget) {
      controls.target.set(...orbitTarget);
    } else {
      controls.target.set(overview?.x ?? 0, overview ? 1.1 : 0.8, overview?.z ?? 0);
    }
    controls.update();
    controlsRef.current = controls;

    return () => {
      controls.dispose();
      controlsRef.current = null;
    };
  }, [camera, enabled, gl, orbitTarget, resetKey, sceneKey]);

  // Disable orbit/pan while a furniture drag is in flight or the pointer rests
  // on a draggable mesh, without recreating the controls (which would reset the
  // camera target).
  useEffect(() => {
    if (controlsRef.current) controlsRef.current.enabled = !interactionLocked;
  }, [interactionLocked]);

  return null;
}

export function SceneViewer({
  scene,
  projectId,
  onChanged,
  selectedId,
  onSelectEntity,
  onRequestReplace
}: {
  scene: SceneDocument | null;
  projectId: string | null;
  onChanged: () => Promise<void>;
  selectedId: string | null;
  onSelectEntity: (id: string | null) => void;
  onRequestReplace: (id: string) => void;
}) {
  const [cameraId, setCameraId] = useState<string>("overview");
  const [debugLocks, setDebugLocks] = useState(false);
  const [viewResetKey, setViewResetKey] = useState(0);
  const [preview, setPreview] = useState<Record<string, PreviewTransform>>({});
  const [dragActive, setDragActive] = useState(false);
  const [hoverDraggable, setHoverDraggable] = useState(false);
  const [dragError, setDragError] = useState("");
  // Shared pending-operation guard: held across the drag-commit pipeline
  // (fetch -> applySceneCommand -> onChanged) AND the contextual command
  // actions (rotate/remove), so neither can race the other's stale transform.
  const [pendingOperation, setPendingOperation] = useState(false);
  const [three, setThree] = useState<ThreeContext | null>(null);
  const dragRef = useRef<DragSession | null>(null);

  const entities = useMemo(() => scene?.entities ?? [], [scene]);
  const renderableEntities = useMemo(
    () => entities.filter((entity) => !isPlanSemanticOnly(entity)),
    [entities]
  );
  const overview = useMemo(() => planOverview(entities), [entities]);
  const sceneKey = scene ? `${scene.project_id}:${scene.scene_id}` : "no-scene";
  const gridSize = Math.max(12, Math.ceil((overview?.span ?? 9) * 1.35));
  const gridDivisions = Math.max(12, Math.min(80, Math.round(gridSize * 2)));
  const selected = entities.find((entity) => entity.id === selectedId) ?? null;

  // R7 (#187): grouped viewpoint picker state — persisted cameras split into
  // saved/photo groups with #188-safe labels, per-room auto views derived
  // from the plan geometry (never persisted until explicitly saved).
  const autoViews = useMemo(() => roomAutoViews(entities), [entities]);
  const viewpointGroups = useMemo(
    () => groupViewpointOptions(scene?.cameras ?? []),
    [scene]
  );
  const isAutoView = cameraId.startsWith("auto:");
  const selectedAutoView = useMemo(() => {
    if (!isAutoView) return null;
    const roomId = cameraId.slice("auto:".length);
    const view = autoViews.find((item) => item.roomId === roomId) ?? null;
    return view ? roomViewSuggestionFromBounds(view.bounds) : null;
  }, [autoViews, cameraId, isAutoView]);
  const orbitEnabled = cameraId === "overview" || isAutoView;
  // Auto views orbit around the room centre (the looks-at point); the
  // overview keeps the plan-frame target the controls already knew.
  const orbitTarget = useMemo<[number, number, number] | null>(
    () =>
      selectedAutoView
        ? canonicalPositionToThree(selectedAutoView.looksAtMm)
        : null,
    [selectedAutoView]
  );

  const calibrated =
    cameraId === "overview" || isAutoView
      ? null
      : scene?.cameras.find((camera) => camera.id === cameraId) ?? null;

  useEffect(() => {
    if (
      cameraId !== "overview" &&
      !isAutoView &&
      !scene?.cameras.some((camera) => camera.id === cameraId)
    ) {
      setCameraId("overview");
    }
    if (selectedId && !entities.some((entity) => entity.id === selectedId)) {
      onSelectEntity(null);
    }
  }, [cameraId, entities, isAutoView, onSelectEntity, scene, selectedId]);

  useEffect(() => {
    if (!three) return;
    three.gl.domElement.style.cursor = dragActive
      ? "grabbing"
      : hoverDraggable
        ? "grab"
        : "";
  }, [three, dragActive, hoverDraggable]);

  const handleThreeReady = useCallback((context: ThreeContext) => {
    setThree(context);
  }, []);

  const handleHoverChange = useCallback((over: boolean) => {
    setHoverDraggable(over);
  }, []);

  const handleDragStart = useCallback(
    (event: ThreeEvent<PointerEvent>, entity: SceneEntity) => {
      if (!three || !canDragEntity(entity)) return;
      // A contextual command or drag commit is in flight: ignore the pointer
      // down without touching propagation so orbit/pan still work, matching
      // the early-return style for non-draggable entities.
      if (pendingOperation) return;
      if (event.nativeEvent.button !== 0) return;
      const startFloor = floorPointFromPointer(
        event.nativeEvent,
        three.camera,
        three.gl.domElement
      );
      if (!startFloor) return;
      // Keep orbit/pan from claiming this pointer sequence.
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      onSelectEntity(entity.id);
      setDragError("");

      const translation = entity.transform?.translation_mm ?? [0, 0, 0];
      const rotation = entity.transform?.rotation_deg ?? [0, 0, 0];
      const startTranslation: [number, number, number] = [
        translation[0],
        translation[1],
        translation[2]
      ];
      const startRotation: [number, number, number] = [
        rotation[0],
        rotation[1],
        rotation[2]
      ];
      dragRef.current = {
        entity,
        element: three.gl.domElement,
        startFloor,
        startTranslation,
        startRotation,
        startPointerAngle: pointerAngleRad(
          { x: startTranslation[0], y: startTranslation[1] },
          startFloor
        ),
        preview: {
          translation_mm: startTranslation,
          rotation_deg: startRotation
        }
      };
      setDragActive(true);
    },
    [onSelectEntity, pendingOperation, three]
  );

  const handlePointerMove = useCallback(
    (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || !three) return;
      const floor = floorPointFromPointer(
        event,
        three.camera,
        drag.element
      );
      if (!floor) return;

      if (event.shiftKey) {
        const angle = pointerAngleRad(
          { x: drag.startTranslation[0], y: drag.startTranslation[1] },
          floor
        );
        const nextZ = rotationFromPointerAngles({
          startRotationZdeg: drag.startRotation[2],
          startPointerAngleRad: drag.startPointerAngle,
          currentPointerAngleRad: angle
        });
        const currentRotation = drag.preview.rotation_deg ?? drag.startRotation;
        drag.preview = {
          ...drag.preview,
          rotation_deg: [currentRotation[0], currentRotation[1], nextZ]
        };
      } else {
        const nextTranslation = translationFromFloorPoints({
          startFloor: drag.startFloor,
          currentFloor: floor,
          startTranslationMm: drag.startTranslation
        });
        drag.preview = { ...drag.preview, translation_mm: nextTranslation };
      }

      setPreview((current) => ({
        ...current,
        [drag.entity.id]: drag.preview
      }));
    },
    [three]
  );

  const commitMove = useCallback(
    async (entity: SceneEntity, parameters: MoveObjectParameters) => {
      if (!projectId) return;
      // Hold the shared guard for the whole in-flight commit so a contextual
      // command cannot read a stale pre-drag transform while the refresh lands.
      setPendingOperation(true);
      try {
        // The command base_revision_id must equal the current latest revision,
        // so never trust the prop's revision here (same rule as TwinDesignPanel).
        const fresh = await api.scene(projectId);
        if (!fresh) throw new Error("Сцена ещё не инициализирована.");
        const send = (baseRevisionId: string) =>
          api.applySceneCommand(
            projectId,
            buildMoveObjectCommand({
              commandId: uniqueId(),
              baseRevisionId,
              entity,
              parameters
            })
          );
        try {
          await send(fresh.revision_id);
        } catch (reason) {
          // 409: a concurrent edit moved the base revision — retry once against
          // a freshly refetched revision, then give up.
          if (!isConflictError(reason)) throw reason;
          const retry = await api.scene(projectId);
          if (!retry) throw reason;
          await send(retry.revision_id);
        }
        await onChanged();
      } catch (reason) {
        setDragError(apiErrorText(reason));
      } finally {
        // Drop the optimistic transform; the refreshed scene (or the revert on
        // error) is now authoritative.
        setPreview((current) => {
          const next = { ...current };
          delete next[entity.id];
          return next;
        });
        setPendingOperation(false);
      }
    },
    [onChanged, projectId]
  );

  // Shared sender for the contextual actions: mirrors commitMove's
  // fetch-fresh-scene + single 409 retry + applySceneCommand sequence exactly,
  // but lets each action build its own command from the fresh revision.
  const sendSceneCommand = useCallback(
    async (build: (baseRevisionId: string) => Record<string, unknown>) => {
      if (!projectId) throw new Error("Проект не выбран.");
      const fresh = await api.scene(projectId);
      if (!fresh) throw new Error("Сцена ещё не инициализирована.");
      const send = (baseRevisionId: string) =>
        api.applySceneCommand(projectId, build(baseRevisionId));
      try {
        await send(fresh.revision_id);
      } catch (reason) {
        if (!isConflictError(reason)) throw reason;
        const retry = await api.scene(projectId);
        if (!retry) throw reason;
        await send(retry.revision_id);
      }
    },
    [projectId]
  );

  const handleRotateSelected = useCallback(async () => {
    if (!selected || pendingOperation) return;
    setPendingOperation(true);
    setDragError("");
    try {
      await sendSceneCommand((baseRevisionId) =>
        // Rotation-only payload built by the pure, unit-tested helper: it never
        // reads the selected prop's translation, so a stale pre-drag value
        // cannot revert a just-committed move.
        buildRotateZCommand({
          commandId: uniqueId(),
          baseRevisionId,
          entity: selected
        })
      );
      await onChanged();
    } catch (reason) {
      setDragError(apiErrorText(reason));
    } finally {
      setPendingOperation(false);
    }
  }, [onChanged, pendingOperation, selected, sendSceneCommand]);

  const handleRemoveSelected = useCallback(async () => {
    if (!selected || pendingOperation) return;
    setPendingOperation(true);
    setDragError("");
    try {
      await sendSceneCommand((baseRevisionId) =>
        buildRemoveObjectCommand({
          commandId: uniqueId(),
          baseRevisionId,
          targetId: selected.id
        })
      );
      await onChanged();
      onSelectEntity(null);
    } catch (reason) {
      setDragError(apiErrorText(reason));
    } finally {
      setPendingOperation(false);
    }
  }, [onChanged, onSelectEntity, pendingOperation, selected, sendSceneCommand]);

  // R7 (#187): «Сохранить ракурс» — extract the current overview pose into a
  // persisted saved-viewpoint camera. Available in overview mode only (the
  // owner frames the flat with orbit/pan, then saves it). The extraction uses
  // the inverse sceneMath transforms; the pose is stored as a brand-new
  // camera (fresh uuid, viewpoint_kind "saved") with the optimistic-lock
  // base_revision_id (CAS) and the shared single-409-retry pattern.
  const handleSaveViewpoint = useCallback(async () => {
    if (!three || !projectId || pendingOperation) return;
    if (cameraId !== "overview") return;
    const camera = three.camera;
    if (!(camera instanceof PerspectiveCamera)) return;
    const rect = three.gl.domElement.getBoundingClientRect();
    const widthPx = Math.max(1, Math.round(rect.width));
    const heightPx = Math.max(1, Math.round(rect.height));
    const positionMm = threeToCanonicalPosition([
      camera.position.x,
      camera.position.y,
      camera.position.z
    ]);
    const rotationDeg = threeToCanonicalRotation(camera.quaternion);
    const intrinsics = viewpointIntrinsicsFromPerspectiveCamera({
      fovDeg: camera.fov,
      widthPx,
      heightPx
    });
    setPendingOperation(true);
    setDragError("");
    try {
      const savedCamera: SceneCamera = {
        id: uniqueId(),
        width_px: widthPx,
        height_px: heightPx,
        intrinsics,
        transform: { translation_mm: positionMm, rotation_deg: rotationDeg },
        provenance: { source: "user", note: "saved from 3D overview" },
        label: null,
        viewpoint_kind: "saved"
      };
      // The CAS base_revision_id must equal the current latest revision —
      // never trust the prop's revision here (same freshness rule as the
      // command senders above).
      const fresh = await api.scene(projectId);
      if (!fresh) throw new Error("Сцена ещё не инициализирована.");
      const send = (baseRevisionId: string) =>
        api.upsertCamera(projectId, savedCamera.id, baseRevisionId, savedCamera);
      try {
        await send(fresh.revision_id);
      } catch (reason) {
        if (!isConflictError(reason)) throw reason;
        const retry = await api.scene(projectId);
        if (!retry) throw reason;
        await send(retry.revision_id);
      }
      // F2 (review): refresh FIRST, then switch. The refreshed scene now
      // contains the saved camera, so the stale-scene fallback effect cannot
      // reset the selection to «Текущий обзор» in the window before the
      // refresh lands. If onChanged throws, the catch below still surfaces
      // the error and the fallback leaves the view on the overview.
      await onChanged();
      setCameraId(savedCamera.id);
    } catch (reason) {
      setDragError(apiErrorText(reason));
    } finally {
      setPendingOperation(false);
    }
  }, [cameraId, onChanged, pendingOperation, projectId, three]);

  const handlePointerUp = useCallback(() => {
    const drag = dragRef.current;
    if (!drag) return;
    dragRef.current = null;
    setDragActive(false);

    const parameters: MoveObjectParameters = {};
    if (
      drag.preview.translation_mm &&
      !sameTriplet(drag.preview.translation_mm, drag.startTranslation)
    ) {
      parameters.translation_mm = drag.preview.translation_mm;
    }
    if (
      drag.preview.rotation_deg &&
      drag.preview.rotation_deg[2] !== drag.startRotation[2]
    ) {
      parameters.rotation_deg = drag.preview.rotation_deg;
    }

    if (Object.keys(parameters).length === 0) {
      // A plain click (or a move that snapped back) must not create a revision.
      setPreview((current) => {
        const next = { ...current };
        delete next[drag.entity.id];
        return next;
      });
      return;
    }
    void commitMove(drag.entity, parameters);
  }, [commitMove]);

  useEffect(() => {
    if (!dragActive) return;
    const onMove = (event: PointerEvent) => handlePointerMove(event);
    const onEnd = () => handlePointerUp();
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd);
    window.addEventListener("pointercancel", onEnd);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
      window.removeEventListener("pointercancel", onEnd);
    };
  }, [dragActive, handlePointerMove, handlePointerUp]);

  return (
    <div className="viewer-shell">
      <div className="viewer-toolbar">
        {/* R7 (#187): grouped viewpoint switcher — current overview, saved
            viewpoints (incl. legacy cameras), photo viewpoints and per-room
            auto views. Raw camera/room ids stay behind option titles (#188). */}
        <select
          className="viewer-viewpoint-select"
          value={cameraId}
          onChange={(event) => setCameraId(event.target.value)}
          aria-label="Ракурс камеры"
        >
          <option value="overview">Текущий обзор</option>
          {viewpointGroups.saved.length > 0 && (
            <optgroup label="Сохранённые ракурсы">
              {viewpointGroups.saved.map((option) => (
                <option key={option.id} value={option.id} title={option.title}>
                  {option.label}
                </option>
              ))}
            </optgroup>
          )}
          {viewpointGroups.photo.length > 0 && (
            <optgroup label="Ракурсы по фото">
              {viewpointGroups.photo.map((option) => (
                <option key={option.id} value={option.id} title={option.title}>
                  {option.label}
                </option>
              ))}
            </optgroup>
          )}
          {autoViews.length > 0 && (
            <optgroup label="Авто-ракурсы">
              {autoViews.map((view) => (
                <option
                  key={view.roomId}
                  value={`auto:${view.roomId}`}
                  title={view.roomId}
                >
                  {`Авто · ${view.label}`}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <button
          type="button"
          className="secondary"
          onClick={() => setViewResetKey((value) => value + 1)}
          disabled={cameraId !== "overview"}
          title={cameraId === "overview" ? "Сбросить общий вид" : "Переключитесь на общий вид, чтобы сбросить камеру"}
        >
          Сбросить вид
        </button>
        {/* R7 (#187): save the framed overview as a persisted viewpoint. */}
        {cameraId === "overview" && (
          <button
            type="button"
            className="secondary"
            onClick={() => void handleSaveViewpoint()}
            disabled={pendingOperation}
            title="Сохранить текущий обзор как ракурс"
          >
            {pendingOperation ? "Сохраняем…" : "Сохранить ракурс"}
          </button>
        )}
        <button
          className={debugLocks ? "secondary active" : "secondary"}
          onClick={() => setDebugLocks((value) => !value)}
        >
          Блокировки геометрии
        </button>
      </div>

      <div
        className="viewer"
        style={
          calibrated
            ? { aspectRatio: `${calibrated.width_px} / ${calibrated.height_px}` }
            : undefined
        }
      >
        <Canvas camera={{ position: [6, 5, 6], fov: 45 }}>
          <SceneBridge onReady={handleThreeReady} />
          <CameraController
            calibrated={calibrated}
            overview={overview}
            autoView={selectedAutoView}
            resetKey={viewResetKey}
            sceneKey={sceneKey}
          />
          <OverviewOrbitControls
            enabled={orbitEnabled}
            interactionLocked={dragActive || hoverDraggable}
            overview={overview}
            orbitTarget={orbitTarget}
            resetKey={viewResetKey}
            sceneKey={sceneKey}
          />
          <ambientLight intensity={1.4} />
          <directionalLight position={[4, 8, 4]} intensity={2} />
          <gridHelper
            args={[gridSize, gridDivisions]}
            position={[overview?.x ?? 0, -0.01, overview?.z ?? 0]}
          />
          {renderableEntities.map((entity) => (
            <EntityMesh
              key={entity.id}
              entity={entity}
              selected={selectedId === entity.id}
              debugLocks={debugLocks}
              preview={preview[entity.id] ?? null}
              onSelect={() => onSelectEntity(entity.id)}
              onDragStart={handleDragStart}
              onHoverChange={handleHoverChange}
            />
          ))}
        </Canvas>
        {dragError && <div className="viewer-error error">{dragError}</div>}
        <div className="viewer-caption">
          {selected ? (
            <>
              <strong>{selected.display_name ?? selected.id}</strong>
              <span>
                {entityKindLabel(selected.kind)}
                {selected.locks?.geometry ? " · геометрия заблокирована" : ""}
                {selected.locks?.transform ? " · перемещение заблокировано" : ""}
                {selected.locks?.material ? " · материал заблокирован" : ""}
              </span>
              {canDragEntity(selected) && (
                <span>перетащите, чтобы переместить · Shift — поворот</span>
              )}
              {selected.kind === "furniture" && (
                <div className="caption-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void handleRotateSelected()}
                    disabled={Boolean(
                      selected.locks?.transform ||
                        selected.locks?.geometry ||
                        dragActive ||
                        pendingOperation
                    )}
                  >
                    Повернуть на 90°
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void handleRemoveSelected()}
                    disabled={Boolean(
                      selected.locks?.geometry || dragActive || pendingOperation
                    )}
                  >
                    Удалить
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => onRequestReplace(selected.id)}
                    disabled={dragActive || pendingOperation}
                  >
                    Заменить по референсу
                  </button>
                </div>
              )}
            </>
          ) : calibrated ? (
            <span className="approx-badge">Камера сопоставлена приблизительно</span>
          ) : (
            "вращение — левая кнопка · зум — колесо · панорама — правая кнопка · клик по объекту — карточка"
          )}
        </div>
      </div>
    </div>
  );
}
