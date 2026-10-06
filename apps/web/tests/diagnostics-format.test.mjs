// Zero-dependency unit tests for the diagnostics dashboard helpers
// (#143 layout + #144 worker status).
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/diagnostics-format.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/diagnostics-format.js (build/ is
// gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  compactJson,
  formatBytes,
  formatClock,
  formatDayClock,
  formatJobSubtitle,
  isJobCancellable,
  jobErrorText,
  jobProgressText,
  jobStatusTone,
  newestFirst,
  shortId,
  workerStatusView
} from "../build/diagnostics-format.js";

// "YYYY-MM-DDTHH:MM" without an offset parses as LOCAL time, so these
// assertions hold in any timezone.
const LOCAL_ISO = "2026-10-06T18:31:00";

test("shortId keeps the first 8 chars", () => {
  assert.equal(shortId("20a37dcd-1c2e-4f0a-9b1d-2f3a4b5c6d7e"), "20a37dcd");
});

test("shortId falls back to — for null/undefined/empty", () => {
  assert.equal(shortId(null), "—");
  assert.equal(shortId(undefined), "—");
  assert.equal(shortId(""), "—");
});

test("newestFirst orders by created_at descending", () => {
  const items = [
    { id: "a", created_at: "2026-10-01T10:00:00" },
    { id: "c", created_at: "2026-10-03T10:00:00" },
    { id: "b", created_at: "2026-10-02T10:00:00" }
  ];
  assert.deepEqual(
    newestFirst(items).map((item) => item.id),
    ["c", "b", "a"]
  );
});

test("newestFirst does not mutate the input array", () => {
  const items = [
    { id: "a", created_at: "2026-10-01T10:00:00" },
    { id: "b", created_at: "2026-10-02T10:00:00" }
  ];
  const snapshot = items.map((item) => item.id);
  newestFirst(items);
  assert.deepEqual(items.map((item) => item.id), snapshot);
});

test("newestFirst is stable for equal timestamps", () => {
  const items = [
    { id: "first", created_at: "2026-10-01T10:00:00" },
    { id: "second", created_at: "2026-10-01T10:00:00" }
  ];
  assert.deepEqual(
    newestFirst(items).map((item) => item.id),
    ["first", "second"]
  );
});

test("formatClock renders HH:MM in local time", () => {
  assert.equal(formatClock(LOCAL_ISO), "18:31");
  assert.equal(formatClock("2026-01-02T03:04:00"), "03:04");
});

test("formatClock returns — for null/invalid input", () => {
  assert.equal(formatClock(null), "—");
  assert.equal(formatClock(undefined), "—");
  assert.equal(formatClock("not-a-date"), "—");
});

test("formatDayClock prepends the local day and month", () => {
  assert.equal(formatDayClock(LOCAL_ISO), "06.10 18:31");
});

test("formatDayClock returns — for null/invalid input", () => {
  assert.equal(formatDayClock(null), "—");
  assert.equal(formatDayClock("garbage"), "—");
});

test("formatBytes formats bytes below 1 KB", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(856), "856 B");
  assert.equal(formatBytes(1023), "1023 B");
});

test("formatBytes rounds fractional KB/MB to one decimal", () => {
  assert.equal(formatBytes(1024), "1 KB");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(1024 * 1024), "1 MB");
  assert.equal(formatBytes(1.5 * 1024 * 1024), "1.5 MB");
});

test("formatBytes collapses to integers from 100 units up", () => {
  assert.equal(formatBytes(200 * 1024 * 1024), "200 MB");
  assert.equal(formatBytes(1024 ** 4), "1 TB");
});

test("formatBytes returns — for negative or non-finite sizes", () => {
  assert.equal(formatBytes(-5), "—");
  assert.equal(formatBytes(Number.NaN), "—");
  assert.equal(formatBytes(Number.POSITIVE_INFINITY), "—");
});

test("jobProgressText joins phase and fraction", () => {
  assert.equal(jobProgressText({ phase: "render", fraction: 0.42 }), "render · 42%");
});

test("jobProgressText renders phase or fraction alone", () => {
  assert.equal(jobProgressText({ phase: "render" }), "render");
  assert.equal(jobProgressText({ fraction: 1 }), "100%");
});

test("jobProgressText returns null without phase/fraction", () => {
  assert.equal(jobProgressText({}), null);
  assert.equal(jobProgressText({ fraction: "not-a-number" }), null);
});

test("formatJobSubtitle matches the #143 example row", () => {
  assert.equal(
    formatJobSubtitle({ id: "20a37dcd-1c2e-4f0a-9b1d-2f3a4b5c6d7e", attempt: 1 }),
    "#20a37dcd · попытка 1"
  );
});

test("isJobCancellable keeps terminal statuses out", () => {
  assert.equal(isJobCancellable("queued"), true);
  assert.equal(isJobCancellable("pending"), true);
  assert.equal(isJobCancellable("running"), true);
  assert.equal(isJobCancellable("leased"), true);
  assert.equal(isJobCancellable("succeeded"), false);
  assert.equal(isJobCancellable("failed"), false);
  assert.equal(isJobCancellable("cancelled"), false);
});

test("jobStatusTone maps semantic statuses", () => {
  assert.equal(jobStatusTone("succeeded"), "ok");
  assert.equal(jobStatusTone("failed"), "err");
  assert.equal(jobStatusTone("cancelled"), "warn");
  assert.equal(jobStatusTone("running"), null);
  assert.equal(jobStatusTone("queued"), null);
});

test("jobErrorText renders code and detail", () => {
  assert.equal(jobErrorText({ code: "render_failed", detail: "boom" }), "render_failed: boom");
});

test("jobErrorText falls back to job_failed and JSON detail", () => {
  assert.equal(jobErrorText({ detail: "boom" }), "job_failed: boom");
  assert.equal(jobErrorText("boom"), 'job_failed: "boom"');
  assert.equal(jobErrorText(null), null);
  assert.equal(jobErrorText(undefined), null);
});

test("compactJson stringifies values and returns — for null/undefined", () => {
  assert.equal(compactJson({ a: 1 }), '{"a":1}');
  assert.equal(compactJson([1, 2]), "[1,2]");
  assert.equal(compactJson(null), "—");
  assert.equal(compactJson(undefined), "—");
});

test("compactJson survives circular structures", () => {
  const circular = {};
  circular.self = circular;
  assert.equal(compactJson(circular), "—");
});

// ---------------------------------------------------------------------------
// Worker status (#144): tag + «Сейчас:»/«Свободен» line on worker cards.
// ---------------------------------------------------------------------------

test("workerStatusView: offline tag with no note", () => {
  assert.deepEqual(workerStatusView({ online: false, busy: true }), {
    label: "offline",
    tone: "offline",
    note: null
  });
});

test("workerStatusView: idle online worker is Свободен", () => {
  assert.deepEqual(workerStatusView({ online: true, busy: false }), {
    label: "online",
    tone: "online",
    note: "Свободен"
  });
});

test("workerStatusView: busy worker shows ● ЗАНЯТ (warn) with current job type", () => {
  assert.deepEqual(
    workerStatusView({
      online: true,
      busy: true,
      current_job: { id: "j1", job_type: "render", status: "running", project_id: "p1" }
    }),
    { label: "● ЗАНЯТ", tone: "warn", note: "Сейчас: render" }
  );
});

test("workerStatusView: busy without usable current_job keeps the tag, drops the note", () => {
  assert.deepEqual(workerStatusView({ online: true, busy: true, current_job: null }).note, null);
  assert.deepEqual(
    workerStatusView({ online: true, busy: true, current_job: { job_type: null } }).note,
    null
  );
  const noJob = workerStatusView({ online: true, busy: true });
  assert.equal(noJob.label, "● ЗАНЯТ");
  assert.equal(noJob.note, null);
});

test("workerStatusView: missing busy flag (old server payload) reads as free", () => {
  const view = workerStatusView({ online: true });
  assert.equal(view.label, "online");
  assert.equal(view.note, "Свободен");
});
