// Zero-dependency unit tests for the productImport helpers (R3 product
// import flow).
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/productImport.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/productImport.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPatchPayload,
  buildPlaceCommand,
  dimsValid,
  missingFields,
  normalizeDims,
  rejectReasonLabel
} from "../build/productImport.js";

// Full candidate fixture: everything present, dims valid.
const sofa = {
  id: "cand-1",
  project_id: "p-1",
  source_url: "https://shop.example.com/sofa",
  title: "Диван «Комфорт»",
  brand: "Stroydom",
  model: "SD-100",
  price: 42990,
  currency: "RUB",
  width_mm: 2200,
  depth_mm: 900,
  height_mm: 800,
  provenance: "extracted",
  preview_asset_id: "asset-preview-1",
  metadata: {},
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z"
};

test("normalizeDims trims non-numeric noise and rejects garbage", () => {
  assert.deepEqual(normalizeDims({ width_mm: 1200, depth_mm: "900 мм", height_mm: " 80,5 " }), {
    width_mm: 1200,
    depth_mm: 900,
    height_mm: 80.5
  });
  // Absent, empty, non-numeric and non-finite are "missing", never 0.
  assert.deepEqual(normalizeDims({ width_mm: "", depth_mm: "абв", height_mm: null }), {
    width_mm: null,
    depth_mm: null,
    height_mm: null
  });
  assert.deepEqual(normalizeDims({ width_mm: Number.NaN }), {
    width_mm: null,
    depth_mm: null,
    height_mm: null
  });
  assert.deepEqual(normalizeDims(null), {
    width_mm: null,
    depth_mm: null,
    height_mm: null
  });
});

test("dimsValid matrix: missing/zero/negative fail, all-positive passes", () => {
  assert.equal(dimsValid({}), false);
  assert.equal(dimsValid(null), false);
  assert.equal(dimsValid({ width_mm: 100, depth_mm: 200 }), false);
  assert.equal(dimsValid({ width_mm: 0, depth_mm: 200, height_mm: 100 }), false);
  assert.equal(dimsValid({ width_mm: -5, depth_mm: 200, height_mm: 100 }), false);
  assert.equal(dimsValid({ width_mm: "абв", depth_mm: 200, height_mm: 100 }), false);
  assert.equal(dimsValid({ width_mm: 100, depth_mm: 200, height_mm: 100 }), true);
  // Form strings parse the same way as candidate numbers.
  assert.equal(dimsValid({ width_mm: "2200", depth_mm: "900 мм", height_mm: 800 }), true);
});

test("missingFields lists the missing dimensions in width→depth→height order", () => {
  assert.deepEqual(missingFields({}), ["width_mm", "depth_mm", "height_mm"]);
  assert.deepEqual(missingFields({ width_mm: 100 }), ["depth_mm", "height_mm"]);
  assert.deepEqual(missingFields({ width_mm: 100, depth_mm: 0, height_mm: 300 }), ["depth_mm"]);
  assert.deepEqual(missingFields({ width_mm: 100, depth_mm: 200, height_mm: 300 }), []);
  assert.deepEqual(missingFields(null), ["width_mm", "depth_mm", "height_mm"]);
});

test("buildPlaceCommand emits the exact add_object core for a full candidate", () => {
  const command = buildPlaceCommand(sofa, "object.sofa.1");
  assert.deepEqual(command, {
    operation: "add_object",
    target_id: "object.sofa.1",
    parameters: {
      entity: {
        id: "object.sofa.1",
        kind: "furniture",
        display_name: "Диван «Комфорт»",
        state: "design",
        transform: { translation_mm: [0, 0, 0], rotation_deg: [0, 0, 0], scale: [1, 1, 1] },
        locks: { geometry: false, transform: false, material: false },
        provenance: {
          source: "imported",
          asset_ids: ["asset-preview-1"],
          note: "added from product import"
        },
        metadata: {
          product_candidate_id: "cand-1",
          source_url: "https://shop.example.com/sofa",
          price: 42990,
          currency: "RUB",
          brand: "Stroydom",
          model: "SD-100",
          preview_asset_id: "asset-preview-1"
        },
        geometry: { dimensions_mm: [2200, 900, 800] }
      }
    }
  });
  // Backend invariant: target_id must equal the added entity id.
  assert.equal(command.target_id, command.parameters.entity.id);
});

test("buildPlaceCommand metadata carries only present keys", () => {
  const command = buildPlaceCommand({ id: "cand-2", title: "Стол" }, "object.table.1");
  assert.deepEqual(command.parameters.entity.metadata, {
    product_candidate_id: "cand-2"
  });
  // No dimensions → no geometry key (the UI gates placement on dimsValid).
  assert.equal("geometry" in command.parameters.entity, false);
  // provenance still carries the imported source with no phantom asset ids.
  assert.deepEqual(command.parameters.entity.provenance, {
    source: "imported",
    asset_ids: [],
    note: "added from product import"
  });
});

test("buildPlaceCommand falls back through the display_name chain", () => {
  // title wins
  assert.equal(
    buildPlaceCommand({ id: "c", title: "Стул", brand: "B", model: "M" }).parameters.entity
      .display_name,
    "Стул"
  );
  // then brand + model joined
  assert.equal(
    buildPlaceCommand({ id: "c", brand: "Ikea", model: "POÄNG" }).parameters.entity.display_name,
    "Ikea POÄNG"
  );
  // then the fixed ru fallback; blank strings behave like absent ones
  assert.equal(buildPlaceCommand({ id: "c" }).parameters.entity.display_name, "Товар");
  assert.equal(
    buildPlaceCommand({ id: "c", title: "   ", brand: " ", model: "" }).parameters.entity
      .display_name,
    "Товар"
  );
});

test("buildPlaceCommand derives a scene-safe entity id when none is given", () => {
  const command = buildPlaceCommand({ id: "c", title: "Диван" });
  assert.match(command.target_id, /^object\.[\w-]+\.[0-9a-f-]+$/);
  assert.equal(command.target_id, command.parameters.entity.id);
});

test("buildPatchPayload sends only entered fields and omits nulls", () => {
  assert.deepEqual(buildPatchPayload({}), {});
  assert.deepEqual(buildPatchPayload({ title: null, brand: "", price: null }), {});
  assert.deepEqual(
    buildPatchPayload({
      title: "Диван",
      brand: null,
      model: "",
      price: "42990",
      currency: " RUB ",
      material: "ткань, лак",
      color: "бежевый",
      width_mm: "2200",
      depth_mm: "900 мм",
      height_mm: 800
    }),
    {
      title: "Диван",
      price: 42990,
      currency: "RUB",
      material_descriptors: ["ткань", "лак"],
      color_descriptors: ["бежевый"],
      width_mm: 2200,
      depth_mm: 900,
      height_mm: 800
    }
  );
  // Unparseable/non-positive dimensions are omitted, never sent as 0/garbage.
  assert.deepEqual(buildPatchPayload({ width_mm: "абв", depth_mm: "0", height_mm: "-5" }), {});
});

test("rejectReasonLabel maps known guard reasons and defaults the rest", () => {
  const rejected = (body) => new Error(`422: ${JSON.stringify(body)}`);
  // FastAPI-style detail envelope (the shape api.ts surfaces verbatim).
  assert.equal(
    rejectReasonLabel(rejected({ detail: { code: "url_rejected", reason: "private_address" } })),
    "Внутренние адреса запрещены"
  );
  assert.equal(
    rejectReasonLabel(rejected({ detail: { code: "url_rejected", reason: "timeout" } })),
    "Сайт не ответил вовремя"
  );
  assert.equal(
    rejectReasonLabel(rejected({ detail: { code: "url_rejected", reason: "response_too_large" } })),
    "Страница слишком большая"
  );
  // Flat body without the detail envelope.
  assert.equal(
    rejectReasonLabel(rejected({ code: "url_rejected", reason: "timeout" })),
    "Сайт не ответил вовремя"
  );
  // Unknown reason code, missing reason, non-JSON body and non-Error input
  // all fall back to the generic label.
  assert.equal(rejectReasonLabel(rejected({ detail: { code: "url_rejected" } })), "Ссылка отклонена");
  assert.equal(rejectReasonLabel(rejected({ detail: { code: "url_rejected", reason: "dns" } })), "Ссылка отклонена");
  assert.equal(rejectReasonLabel(new Error("500: boom")), "Ссылка отклонена");
  assert.equal(rejectReasonLabel("timeout"), "Ссылка отклонена");
});

test("rejectReasonLabel covers the full backend guard taxonomy", () => {
  const rejected = (reason) =>
    new Error(`422: ${JSON.stringify({ detail: { code: "url_rejected", reason } })}`);
  assert.equal(rejectReasonLabel(rejected("dns_failed")), "Не удалось определить адрес сайта");
  assert.equal(
    rejectReasonLabel(rejected("redirect_private_address")),
    "Перенаправление на внутренний адрес запрещено"
  );
  assert.equal(
    rejectReasonLabel(rejected("too_many_redirects")),
    "Слишком много перенаправлений"
  );
  assert.equal(
    rejectReasonLabel(rejected("unsupported_content_type")),
    "Неподдерживаемый тип содержимого"
  );
});
