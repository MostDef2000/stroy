// Zero-dependency unit tests for cameraAnchors.deriveAnchors.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/cameraAnchors.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/cameraAnchors.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import { deriveAnchors } from "../build/cameraAnchors.js";

const wall = (overrides = {}) => ({
  id: "wall.1",
  kind: "wall",
  geometry: {
    a: [0, 0],
    b: [1000, 0],
    dimensions_mm: { length: 1000, thickness: 100, height: 2800 }
  },
  ...overrides
});

test("empty / null / undefined entities produce no anchors", () => {
  assert.deepEqual(deriveAnchors([]), []);
  assert.deepEqual(deriveAnchors(null), []);
  assert.deepEqual(deriveAnchors(undefined), []);
});

test("wall corners follow a→b and +Z height", () => {
  const groups = deriveAnchors([wall()]);
  assert.equal(groups.length, 1);
  const [group] = groups;
  assert.equal(group.objectId, "wall.1");
  assert.equal(group.objectKind, "wall");
  assert.deepEqual(
    group.points.map((point) => point.worldMm),
    [
      [0, 0, 0],
      [1000, 0, 0],
      [0, 0, 2800],
      [1000, 0, 2800]
    ]
  );
  assert.deepEqual(
    group.points.map((point) => point.label),
    [
      "wall.1 · bottom-left",
      "wall.1 · bottom-right",
      "wall.1 · top-left",
      "wall.1 · top-right"
    ]
  );
  assert.deepEqual(
    group.points.map((point) => point.id),
    [
      "wall.1:bottom-left",
      "wall.1:bottom-right",
      "wall.1:top-left",
      "wall.1:top-right"
    ]
  );
});

test("door maps host wall t/width/height to world corners", () => {
  const groups = deriveAnchors([
    wall(),
    {
      id: "door.1",
      kind: "door",
      geometry: {
        host_wall_id: "wall.1",
        t: 0.5,
        width_mm: 900,
        height_mm: 2100,
        sill_mm: null
      },
      metadata: { opening_kind: "door" }
    }
  ]);
  const door = groups.find((group) => group.objectKind === "door");
  assert.ok(door, "door group is present");
  assert.equal(door.objectId, "door.1");
  // host a=[0,0] b=[1000,0], t=0.5 -> center 500, half-width 450.
  assert.deepEqual(
    door.points.map((point) => point.worldMm),
    [
      [50, 0, 0],
      [950, 0, 0],
      [50, 0, 2100],
      [950, 0, 2100]
    ]
  );
});

test("door t is a fraction along the host, not a millimetre offset", () => {
  const groups = deriveAnchors([
    wall(),
    {
      id: "door.2",
      kind: "door",
      geometry: {
        host_wall_id: "wall.1",
        t: 0.25,
        width_mm: 400,
        height_mm: 2100,
        sill_mm: 0
      }
    }
  ]);
  const door = groups.find((group) => group.objectKind === "door");
  assert.ok(door);
  // center = 0 + 1000 * 0.25 = 250; half = 200 -> left 50, right 450.
  assert.deepEqual(
    door.points.map((point) => point.worldMm),
    [
      [50, 0, 0],
      [450, 0, 0],
      [50, 0, 2100],
      [450, 0, 2100]
    ]
  );
});

test("window honours sill_mm as the bottom elevation", () => {
  const groups = deriveAnchors([
    { ...wall(), geometry: { a: [0, 0], b: [2000, 0], dimensions_mm: { height: 2800 } } },
    {
      id: "window.1",
      kind: "window",
      geometry: {
        host_wall_id: "wall.1",
        t: 0.5,
        width_mm: 1200,
        height_mm: 1400,
        sill_mm: 900
      },
      metadata: { opening_kind: "window" }
    }
  ]);
  const windowGroup = groups.find((group) => group.objectKind === "window");
  assert.ok(windowGroup);
  // center = 1000, half-width 600; z from 900 to 2300.
  assert.deepEqual(
    windowGroup.points.map((point) => point.worldMm),
    [
      [400, 0, 900],
      [1600, 0, 900],
      [400, 0, 2300],
      [1600, 0, 2300]
    ]
  );
});

test("architectural openings are grouped as windows", () => {
  const groups = deriveAnchors([
    wall(),
    {
      id: "arch.1",
      kind: "architectural",
      geometry: {
        host_wall_id: "wall.1",
        t: 0.5,
        width_mm: 800,
        height_mm: 2000,
        sill_mm: 0
      }
    }
  ]);
  const arch = groups.find((group) => group.objectId === "arch.1");
  assert.ok(arch);
  assert.equal(arch.objectKind, "window");
});

test("orphan opening (unknown host) is skipped", () => {
  const groups = deriveAnchors([
    {
      id: "door.orphan",
      kind: "door",
      geometry: {
        host_wall_id: "missing.wall",
        t: 0.5,
        width_mm: 900,
        height_mm: 2100,
        sill_mm: 0
      }
    }
  ]);
  assert.deepEqual(groups, []);
});

test("coincident corners are deduped (zero-height wall collapses to a segment)", () => {
  const groups = deriveAnchors([
    {
      ...wall(),
      geometry: { a: [0, 0], b: [1000, 0], dimensions_mm: { height: 0 } }
    }
  ]);
  assert.equal(groups.length, 1);
  assert.deepEqual(
    groups[0].points.map((point) => point.worldMm),
    [
      [0, 0, 0],
      [1000, 0, 0]
    ]
  );
});

test("zero-length wall (a === b) is skipped", () => {
  const groups = deriveAnchors([
    {
      id: "wall.degenerate",
      kind: "wall",
      geometry: { a: [5, 5], b: [5, 5], dimensions_mm: { height: 2800 } }
    }
  ]);
  assert.deepEqual(groups, []);
});

test("malformed wall geometry is skipped", () => {
  const malformed = [
    { id: "wall.no-a", kind: "wall", geometry: { b: [1, 0], dimensions_mm: { height: 10 } } },
    { id: "wall.no-b", kind: "wall", geometry: { a: [0, 0], dimensions_mm: { height: 10 } } },
    { id: "wall.no-height", kind: "wall", geometry: { a: [0, 0], b: [1, 0] } },
    {
      id: "wall.bad-coord",
      kind: "wall",
      geometry: { a: [0, 0], b: ["x", 0], dimensions_mm: { height: 10 } }
    },
    { id: "", kind: "wall", geometry: { a: [0, 0], b: [1, 0], dimensions_mm: { height: 10 } } },
    { kind: "wall", geometry: { a: [0, 0], b: [1, 0], dimensions_mm: { height: 10 } } }
  ];
  assert.deepEqual(deriveAnchors(malformed), []);
});

test("malformed opening geometry is skipped", () => {
  const groups = deriveAnchors([
    wall(),
    {
      id: "door.no-width",
      kind: "door",
      geometry: { host_wall_id: "wall.1", t: 0.5, height_mm: 2000 }
    },
    {
      id: "door.zero-width",
      kind: "door",
      geometry: { host_wall_id: "wall.1", t: 0.5, width_mm: 0, height_mm: 2000 }
    },
    {
      id: "door.bad-t",
      kind: "door",
      geometry: { host_wall_id: "wall.1", t: "nope", width_mm: 800, height_mm: 2000 }
    },
    {
      id: "door.no-geometry",
      kind: "door"
    }
  ]);
  // Only the wall survives; every opening is malformed.
  assert.deepEqual(
    groups.map((group) => group.objectId),
    ["wall.1"]
  );
});

test("groups keep entity order", () => {
  const groups = deriveAnchors([
    wall({ id: "wall.a" }),
    wall({ id: "wall.b" })
  ]);
  assert.deepEqual(
    groups.map((group) => group.objectId),
    ["wall.a", "wall.b"]
  );
});
