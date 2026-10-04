// Pure helpers that turn the client-side scene geometry into candidate 3D
// anchor points for camera calibration from a photo. No React, no network, no
// dependency on the generated scene document beyond the structural shape below
// so the module can be unit-tested in isolation.

export type Vec3 = [number, number, number];

export type AnchorObjectKind = "wall" | "door" | "window";

export type AnchorPoint = {
  /** Stable, unique key for a single corner. */
  id: string;
  /** Human label, e.g. "wall.1 · bottom-left". */
  label: string;
  /** Owning object id (grouping key). */
  objectId: string;
  objectKind: AnchorObjectKind;
  /** Canonical +Z-up world position in millimetres. */
  worldMm: Vec3;
};

export type AnchorGroup = {
  objectId: string;
  objectKind: AnchorObjectKind;
  /** Label shown as the <optgroup> heading. */
  label: string;
  points: AnchorPoint[];
};

/** Minimal structural view of a scene entity (matches `SceneEntity`). */
export type SceneEntityLike = {
  id?: string | null;
  kind?: string | null;
  geometry?: Record<string, unknown> | null;
};

const OPENING_KINDS: Record<string, AnchorObjectKind> = {
  door: "door",
  window: "window",
  architectural: "window"
};

function toFinite(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function toVector2(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length < 2) return null;
  const x = toFinite(value[0]);
  const y = toFinite(value[1]);
  if (x === null || y === null) return null;
  return [x, y];
}

function toRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

function pointId(objectId: string, corner: string): string {
  return `${objectId}:${corner}`;
}

/**
 * Build the four corners of a vertical rectangle in the canonical world frame.
 * `left`/`right` follow the wall direction a→b and `bottom`/`top` follow +Z.
 * Corners that coincide (e.g. a zero-height wall) are collapsed, first wins.
 */
function rectangleCorners(
  left: [number, number],
  right: [number, number],
  bottomZ: number,
  topZ: number
): Array<{ corner: string; worldMm: Vec3 }> {
  const candidates: Array<{ corner: string; worldMm: Vec3 }> = [
    { corner: "bottom-left", worldMm: [left[0], left[1], bottomZ] },
    { corner: "bottom-right", worldMm: [right[0], right[1], bottomZ] },
    { corner: "top-left", worldMm: [left[0], left[1], topZ] },
    { corner: "top-right", worldMm: [right[0], right[1], topZ] }
  ];
  const seen = new Set<string>();
  const unique: Array<{ corner: string; worldMm: Vec3 }> = [];
  for (const candidate of candidates) {
    const key = candidate.worldMm.map(round6).join(",");
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(candidate);
  }
  return unique;
}

function groupFor(
  objectId: string,
  objectKind: AnchorObjectKind,
  corners: Array<{ corner: string; worldMm: Vec3 }>
): AnchorGroup {
  return {
    objectId,
    objectKind,
    label: objectId,
    points: corners.map(({ corner, worldMm }) => ({
      id: pointId(objectId, corner),
      label: `${objectId} · ${corner}`,
      objectId,
      objectKind,
      worldMm
    }))
  };
}

function wallGroup(entity: SceneEntityLike): AnchorGroup | null {
  const id = typeof entity.id === "string" && entity.id ? entity.id : null;
  if (!id) return null;
  const geometry = toRecord(entity.geometry);
  const a = toVector2(geometry?.["a"]);
  const b = toVector2(geometry?.["b"]);
  const dimensions = toRecord(geometry?.["dimensions_mm"]);
  const height = dimensions ? toFinite(dimensions["height"]) : null;
  if (!a || !b || height === null || height < 0) return null;
  if (Math.hypot(b[0] - a[0], b[1] - a[1]) <= 0) return null;
  return groupFor(id, "wall", rectangleCorners(a, b, 0, height));
}

function openingGroup(
  entity: SceneEntityLike,
  walls: Map<string, { a: [number, number]; b: [number, number]; height: number }>
): AnchorGroup | null {
  const id = typeof entity.id === "string" && entity.id ? entity.id : null;
  if (!id) return null;
  const kind = OPENING_KINDS[String(entity.kind)];
  if (!kind) return null;
  const geometry = toRecord(entity.geometry);
  if (!geometry) return null;
  const hostId = geometry["host_wall_id"];
  if (typeof hostId !== "string") return null;
  const host = walls.get(hostId);
  if (!host) return null;
  const t = toFinite(geometry["t"]);
  const width = toFinite(geometry["width_mm"]);
  const height = toFinite(geometry["height_mm"]);
  const sill = toFinite(geometry["sill_mm"]) ?? 0;
  if (t === null || width === null || height === null || width <= 0 || height < 0) {
    return null;
  }
  const dx = host.b[0] - host.a[0];
  const dy = host.b[1] - host.a[1];
  const length = Math.hypot(dx, dy);
  if (length <= 0) return null;
  const ux = dx / length;
  const uy = dy / length;
  const centerX = host.a[0] + dx * t;
  const centerY = host.a[1] + dy * t;
  const half = width / 2;
  const left: [number, number] = [centerX - ux * half, centerY - uy * half];
  const right: [number, number] = [centerX + ux * half, centerY + uy * half];
  return groupFor(id, kind, rectangleCorners(left, right, sill, sill + height));
}

/**
 * Derive candidate calibration anchors (wall / door / window corners) from the
 * client-side scene entities. Entities with missing or malformed geometry are
 * skipped. Returns groups in entity order; each group holds its corners.
 */
export function deriveAnchors(entities: readonly SceneEntityLike[] | null | undefined): AnchorGroup[] {
  if (!entities || entities.length === 0) return [];

  // Index valid walls first so openings can resolve their host segment.
  const walls = new Map<string, { a: [number, number]; b: [number, number]; height: number }>();
  for (const entity of entities) {
    if (String(entity.kind) !== "wall") continue;
    const id = typeof entity.id === "string" && entity.id ? entity.id : null;
    if (!id) continue;
    const geometry = toRecord(entity.geometry);
    const a = toVector2(geometry?.["a"]);
    const b = toVector2(geometry?.["b"]);
    const dimensions = toRecord(geometry?.["dimensions_mm"]);
    const height = dimensions ? toFinite(dimensions["height"]) : null;
    if (!a || !b || height === null || height < 0) continue;
    walls.set(id, { a, b, height });
  }

  const groups: AnchorGroup[] = [];
  for (const entity of entities) {
    const group =
      String(entity.kind) === "wall" ? wallGroup(entity) : openingGroup(entity, walls);
    if (group && group.points.length > 0) groups.push(group);
  }
  return groups;
}
