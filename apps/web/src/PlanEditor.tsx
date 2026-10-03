import {
  PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import {
  api,
  Asset,
  Job,
  PlanDraft,
  PlanOpening,
  PlanOpeningKind,
  PlanRoom,
  PlanWall
} from "./api";

type Selection =
  | { kind: "wall"; wallId: string }
  | { kind: "opening"; wallId: string; openingId: string }
  | { kind: "room"; roomId: string }
  | null;

type Mode = "select" | "add-wall" | "set-scale";

type Drag =
  | { type: "endpoint"; wallId: string; end: 1 | 2 }
  | { type: "opening"; wallId: string; openingId: string }
  | null;

const SNAP_DISPLAY_PX = 8;
const DEFAULT_WALL_THICKNESS_MM = 150;
const DEFAULT_WALL_THICKNESS_PX = 20;
const DEFAULT_OPENING_WIDTH_MM = 900;
const DEFAULT_OPENING_HEIGHT_MM = 2100;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// Coerce a numeric input to a valid value: empty/NaN -> min (or 0 when the
// minimum is unbounded), then clamp.
function numberInput(raw: string, min: number, max = Number.POSITIVE_INFINITY): number {
  const value = Number(raw);
  if (!Number.isFinite(value)) return Number.isFinite(min) ? min : 0;
  return clamp(value, min, max);
}

function wallLength(wall: PlanWall): number {
  return Math.hypot(wall.x2 - wall.x1, wall.y2 - wall.y1);
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function cloneDraft(draft: PlanDraft): PlanDraft {
  return JSON.parse(JSON.stringify(draft)) as PlanDraft;
}

function roomCentroid(room: PlanRoom, wallsById: Record<string, PlanWall>): { x: number; y: number } {
  const xs: number[] = [];
  const ys: number[] = [];
  for (const wallId of room.wall_ids) {
    const wall = wallsById[wallId];
    if (!wall) continue;
    xs.push((wall.x1 + wall.x2) / 2);
    ys.push((wall.y1 + wall.y2) / 2);
  }
  if (xs.length === 0) return { x: 0, y: 0 };
  return { x: xs.reduce((a, b) => a + b, 0) / xs.length, y: ys.reduce((a, b) => a + b, 0) / ys.length };
}

function parseApiError(reason: unknown): { status: number | null; code: string | null; message: string } {
  const text = reason instanceof Error ? reason.message : String(reason);
  const match = /^(\d{3}):\s*([\s\S]*)$/.exec(text);
  if (!match) return { status: null, code: null, message: text };
  const status = Number(match[1]);
  let code: string | null = null;
  let message = match[2];
  try {
    const body = JSON.parse(match[2]) as { detail?: unknown };
    const detail = body.detail;
    if (Array.isArray(detail)) {
      message = detail
        .map((entry) => {
          if (!entry || typeof entry !== "object") return String(entry);
          const record = entry as Record<string, unknown>;
          const loc = Array.isArray(record.loc) ? record.loc.join(".") : "";
          const msg = typeof record.msg === "string" ? record.msg : JSON.stringify(entry);
          return loc ? `${loc}: ${msg}` : msg;
        })
        .join("; ");
    } else if (detail && typeof detail === "object") {
      const record = detail as Record<string, unknown>;
      code = typeof record.code === "string" ? record.code : null;
      message = typeof record.detail === "string" ? record.detail : JSON.stringify(detail);
    } else if (typeof detail === "string") {
      message = detail;
    }
  } catch {
    /* keep raw body text */
  }
  return { status, code, message };
}

function jobError(job: Job | null): string | null {
  if (!job?.error) return null;
  const code = typeof job.error["code"] === "string" ? (job.error["code"] as string) : "job_failed";
  const detail =
    typeof job.error["detail"] === "string" ? (job.error["detail"] as string) : JSON.stringify(job.error);
  return `${code}: ${detail}`;
}

export function PlanEditor({
  projectId,
  assets,
  jobs,
  onChanged
}: {
  projectId: string;
  assets: Asset[];
  jobs: Job[];
  onChanged: () => Promise<void>;
}) {
  const apartmentImages = useMemo(
    () => assets.filter((asset) => asset.role === "apartment" && asset.media_type.startsWith("image/")),
    [assets]
  );

  const [phase, setPhase] = useState<"entry" | "editor">("entry");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [analyzeJobId, setAnalyzeJobId] = useState<string | null>(null);
  const [analyzeStartedAt, setAnalyzeStartedAt] = useState<number | null>(null);
  const [draft, setDraft] = useState<PlanDraft | null>(null);
  const [draftVersion, setDraftVersion] = useState<number | null>(null);
  const [draftStatus, setDraftStatus] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [mode, setMode] = useState<Mode>("select");
  const [pendingWallStart, setPendingWallStart] = useState<{ x: number; y: number } | null>(null);
  const [scalePoints, setScalePoints] = useState<{ x: number; y: number }[]>([]);
  const [scaleInput, setScaleInput] = useState("");
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [scaleBannerFocus, setScaleBannerFocus] = useState(false);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<Drag>(null);
  const scaleBannerRef = useRef<HTMLDivElement | null>(null);

  const planAssetId = selectedIds[0] ?? apartmentImages[0]?.id ?? "";
  const mmPerPx = draft?.scale.mm_per_px ?? null;
  const scaleKnown = draft?.scale.source !== "unknown" && mmPerPx !== null;
  const unitLabel = scaleKnown ? "mm" : "px";

  const toImage = (value: number) => (mmPerPx ? value / mmPerPx : value);
  const fromImage = (value: number) => (mmPerPx ? value * mmPerPx : value);

  // Reset per project.
  useEffect(() => {
    setPhase("entry");
    setSelectedIds([]);
    setAnalyzeJobId(null);
    setAnalyzeStartedAt(null);
    setDraft(null);
    setDraftVersion(null);
    setDraftStatus(null);
    setSelection(null);
    setMode("select");
    setPendingWallStart(null);
    setScalePoints([]);
    setScaleInput("");
    setError("");
    setInfo("");
  }, [projectId]);

  // Resume an existing draft (e.g. after a reload).
  useEffect(() => {
    let cancelled = false;
    api
      .getPlanDraft(projectId)
      .then((response) => {
        if (cancelled || !response) return;
        setDraft(response.draft);
        setDraftVersion(response.version);
        setDraftStatus(response.status);
        setPhase("editor");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const activeJob = useMemo(
    () => (analyzeJobId ? jobs.find((job) => job.id === analyzeJobId) ?? null : null),
    [jobs, analyzeJobId]
  );

  // Elapsed-time ticker for the analyzing banner (job payloads carry no progress).
  const [elapsedTick, setElapsedTick] = useState(0);
  useEffect(() => {
    if (!analyzeJobId) return;
    const timer = window.setInterval(() => setElapsedTick((t) => t + 1), 1000);
    return () => window.clearInterval(timer);
  }, [analyzeJobId]);
  const analyzeElapsed = useMemo(() => {
    if (!analyzeJobId || !analyzeStartedAt) return null;
    void elapsedTick;
    const seconds = Math.max(0, Math.floor((Date.now() - analyzeStartedAt) / 1000));
    const mm = Math.floor(seconds / 60);
    const ss = String(seconds % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  }, [analyzeJobId, analyzeStartedAt, elapsedTick]);

  // When the analyze job completes, the draft is persisted server-side.
  useEffect(() => {
    if (!analyzeJobId || activeJob?.status !== "succeeded") return;
    let cancelled = false;
    setAnalyzeJobId(null);
    setAnalyzeStartedAt(null);
    api
      .getPlanDraft(projectId)
      .then((response) => {
        if (cancelled) return;
        if (!response) {
          setError("Analysis finished but no draft was found.");
          return;
        }
        setDraft(response.draft);
        setDraftVersion(response.version);
        setDraftStatus(response.status);
        setPhase("editor");
        setInfo(`Draft v${response.version} ready.`);
      })
      .catch((reason) => {
        if (!cancelled) setError(parseApiError(reason).message);
      });
    return () => {
      cancelled = true;
    };
  }, [activeJob, analyzeJobId, projectId]);

  function toggleAsset(assetId: string) {
    setSelectedIds((current) =>
      current.includes(assetId) ? current.filter((id) => id !== assetId) : [...current, assetId]
    );
  }

  async function analyze() {
    if (selectedIds.length === 0 || busy) return;
    setBusy(true);
    setError("");
    setInfo("");
    try {
      const response = await api.analyzePlan(projectId, selectedIds);
      setAnalyzeJobId(response.job_id);
      setAnalyzeStartedAt(Date.now());
      setInfo("Analyzing…");
    } catch (reason) {
      setError(parseApiError(reason).message);
    } finally {
      setBusy(false);
    }
  }

  function clientToImage(event: ReactPointerEvent<SVGSVGElement>): { x: number; y: number } | null {
    const svg = svgRef.current;
    if (!svg) return null;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(ctm.inverse());
    return { x: point.x, y: point.y };
  }

  function imagePerDisplay(): number {
    const svg = svgRef.current;
    if (!svg || !natural) return 1;
    const width = svg.getBoundingClientRect().width;
    return width > 0 ? natural.w / width : 1;
  }

  function snapImage(value: number): number {
    const step = SNAP_DISPLAY_PX * imagePerDisplay();
    return step > 0 ? Math.round(value / step) * step : value;
  }

  function updateDraft(mutator: (next: PlanDraft) => void) {
    setDraft((current) => {
      if (!current) return current;
      const next = cloneDraft(current);
      mutator(next);
      return next;
    });
  }

  function updateWall(wallId: string, patch: Partial<PlanWall>) {
    updateDraft((next) => {
      const wall = next.floors[0]?.walls.find((item) => item.id === wallId);
      if (!wall) return;
      Object.assign(wall, patch);
      if (wall.thickness_mm > wallLength(wall)) wall.thickness_mm = Math.max(1, wallLength(wall));
      for (const opening of wall.openings) {
        if (opening.width_mm > wallLength(wall)) opening.width_mm = Math.max(1, wallLength(wall));
      }
    });
  }

  function updateOpening(wallId: string, openingId: string, patch: Partial<PlanOpening>) {
    updateDraft((next) => {
      const wall = next.floors[0]?.walls.find((item) => item.id === wallId);
      const opening = wall?.openings.find((item) => item.id === openingId);
      if (!wall || !opening) return;
      Object.assign(opening, patch);
      opening.t = clamp(opening.t, 0, 1);
      if (opening.width_mm > wallLength(wall)) opening.width_mm = Math.max(1, wallLength(wall));
    });
  }

  function updateRoom(roomId: string, patch: Partial<PlanRoom>) {
    updateDraft((next) => {
      const room = next.floors[0]?.rooms.find((item) => item.id === roomId);
      if (room) Object.assign(room, patch);
    });
  }

  function uniqueId(prefix: string, existing: Set<string>): string {
    let index = 1;
    while (existing.has(`${prefix}.${index}`)) index += 1;
    return `${prefix}.${index}`;
  }

  function addWall(a: { x: number; y: number }, b: { x: number; y: number }) {
    if (!draft) return;
    const floor = draft.floors[0];
    if (!floor) return;
    const displayLength = Math.hypot(b.x - a.x, b.y - a.y) / imagePerDisplay();
    if (displayLength < 1) {
      setError("Wall is too short — pick two distinct points.");
      return;
    }
    const existing = new Set<string>([
      ...floor.walls.map((wall) => wall.id),
      ...floor.walls.flatMap((wall) => wall.openings.map((opening) => opening.id)),
      ...floor.rooms.map((room) => room.id)
    ]);
    const id = uniqueId("wall", existing);
    const thickness = mmPerPx ? DEFAULT_WALL_THICKNESS_MM : DEFAULT_WALL_THICKNESS_PX;
    const wall: PlanWall = {
      id,
      x1: fromImage(a.x),
      y1: fromImage(a.y),
      x2: fromImage(b.x),
      y2: fromImage(b.y),
      thickness_mm: thickness,
      openings: []
    };
    if (wallLength(wall) < wall.thickness_mm) wall.thickness_mm = Math.max(1, wallLength(wall));
    updateDraft((next) => {
      next.floors[0]?.walls.push(wall);
    });
    setSelection({ kind: "wall", wallId: id });
  }

  function addOpening() {
    if (selection?.kind !== "wall" || !draft) return;
    const wallId = selection.wallId;
    const floor = draft.floors[0];
    const wall = floor?.walls.find((item) => item.id === wallId);
    if (!floor || !wall) return;
    const existing = new Set<string>([
      ...floor.walls.map((item) => item.id),
      ...floor.walls.flatMap((item) => item.openings.map((opening) => opening.id)),
      ...floor.rooms.map((room) => room.id)
    ]);
    const id = uniqueId("opening", existing);
    const length = wallLength(wall);
    const width = mmPerPx ? DEFAULT_OPENING_WIDTH_MM : Math.min(200, length * 0.5);
    const opening: PlanOpening = {
      id,
      kind: "door",
      t: 0.5,
      width_mm: Math.max(1, Math.min(width, length)),
      height_mm: mmPerPx ? DEFAULT_OPENING_HEIGHT_MM : 100,
      sill_mm: 0
    };
    updateDraft((next) => {
      next.floors[0]?.walls.find((item) => item.id === wallId)?.openings.push(opening);
    });
    setSelection({ kind: "opening", wallId, openingId: id });
  }

  function removeSelected() {
    if (!selection) return;
    const prunedRooms =
      selection.kind === "wall"
        ? (draft?.floors[0]?.rooms ?? []).filter(
            (room) => room.wall_ids.filter((id) => id !== selection.wallId).length < 3
          ).length
        : 0;
    updateDraft((next) => {
      const floor = next.floors[0];
      if (!floor) return;
      if (selection.kind === "wall") {
        floor.walls = floor.walls.filter((wall) => wall.id !== selection.wallId);
        const kept: PlanRoom[] = [];
        for (const room of floor.rooms) {
          const wallIds = room.wall_ids.filter((id) => id !== selection.wallId);
          if (wallIds.length < 3) continue;
          kept.push({ ...room, wall_ids: wallIds });
        }
        floor.rooms = kept;
      } else if (selection.kind === "opening") {
        const wall = floor.walls.find((item) => item.id === selection.wallId);
        if (wall) wall.openings = wall.openings.filter((opening) => opening.id !== selection.openingId);
      } else {
        floor.rooms = floor.rooms.filter((room) => room.id !== selection.roomId);
      }
    });
    if (prunedRooms > 0) {
      setInfo(`Removed ${prunedRooms} room(s) that lost their boundary.`);
    }
    setSelection(null);
  }

  function applyScale(realMm: number) {
    if (!draft || scalePoints.length !== 2 || realMm <= 0) return;
    const distance = Math.hypot(scalePoints[1].x - scalePoints[0].x, scalePoints[1].y - scalePoints[0].y);
    if (distance <= 0) return;
    const newMmPerPx = realMm / distance;
    const oldMmPerPx = mmPerPx ?? 1;
    const factor = newMmPerPx / oldMmPerPx;
    updateDraft((next) => {
      next.scale = { source: "manual", mm_per_px: newMmPerPx };
      for (const floor of next.floors) {
        floor.level_mm *= factor;
        for (const wall of floor.walls) {
          wall.x1 *= factor;
          wall.y1 *= factor;
          wall.x2 *= factor;
          wall.y2 *= factor;
          wall.thickness_mm *= factor;
          for (const opening of wall.openings) {
            opening.width_mm *= factor;
            opening.height_mm *= factor;
            if (opening.sill_mm != null) opening.sill_mm *= factor;
          }
        }
      }
    });
    setScalePoints([]);
    setScaleInput("");
    setMode("select");
    setScaleBannerFocus(false);
    setInfo(`Scale set: ${newMmPerPx.toFixed(3)} mm/px.`);
  }

  function handleSvgPointerDown(event: ReactPointerEvent<SVGSVGElement>) {
    const point = clientToImage(event);
    if (!point) return;
    if (mode === "add-wall") {
      if (!pendingWallStart) {
        setPendingWallStart(point);
      } else {
        addWall(pendingWallStart, point);
        setPendingWallStart(null);
        setMode("select");
      }
      return;
    }
    if (mode === "set-scale") {
      const next = [...scalePoints, point].slice(-2);
      setScalePoints(next);
      if (next.length === 2) setScaleInput("");
      return;
    }
    setSelection(null);
  }

  function handleSvgPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const point = clientToImage(event);
    if (!point) return;
    const x = snapImage(point.x);
    const y = snapImage(point.y);
    if (drag.type === "endpoint") {
      const wall = draft?.floors[0]?.walls.find((item) => item.id === drag.wallId);
      if (!wall) return;
      const otherX = drag.end === 1 ? toImage(wall.x2) : toImage(wall.x1);
      const otherY = drag.end === 1 ? toImage(wall.y2) : toImage(wall.y1);
      if (Math.hypot(x - otherX, y - otherY) < 1) return;
      updateWall(drag.wallId, drag.end === 1 ? { x1: fromImage(x), y1: fromImage(y) } : { x2: fromImage(x), y2: fromImage(y) });
      return;
    }
    const wall = draft?.floors[0]?.walls.find((item) => item.id === drag.wallId);
    if (!wall) return;
    const ax = toImage(wall.x1);
    const ay = toImage(wall.y1);
    const bx = toImage(wall.x2);
    const by = toImage(wall.y2);
    const dx = bx - ax;
    const dy = by - ay;
    const lengthSq = dx * dx + dy * dy;
    if (lengthSq <= 0) return;
    const t = clamp(((x - ax) * dx + (y - ay) * dy) / lengthSq, 0, 1);
    updateOpening(drag.wallId, drag.openingId, { t });
  }

  function endDrag() {
    dragRef.current = null;
  }

  function startEndpointDrag(event: ReactPointerEvent<SVGCircleElement>, wallId: string, end: 1 | 2) {
    if (mode !== "select") return;
    event.stopPropagation();
    svgRef.current?.setPointerCapture(event.pointerId);
    dragRef.current = { type: "endpoint", wallId, end };
    setSelection({ kind: "wall", wallId });
  }

  function startOpeningDrag(event: ReactPointerEvent<SVGRectElement>, wallId: string, openingId: string) {
    if (mode !== "select") return;
    event.stopPropagation();
    svgRef.current?.setPointerCapture(event.pointerId);
    dragRef.current = { type: "opening", wallId, openingId };
    setSelection({ kind: "opening", wallId, openingId });
  }

  async function saveDraft() {
    if (!draft || busy) return;
    setBusy(true);
    setError("");
    setInfo("");
    try {
      const response = await api.savePlanDraft(projectId, draft);
      setDraftVersion(response.version);
      setDraftStatus(response.status);
      setInfo(`Saved draft v${response.version}.`);
    } catch (reason) {
      setError(parseApiError(reason).message);
    } finally {
      setBusy(false);
    }
  }

  async function build3d() {
    if (!draft || busy) return;
    setBusy(true);
    setError("");
    setInfo("");
    try {
      await api.commitPlanDraft(projectId);
      setDraftStatus("committed");
      await onChanged();
      setPhase("entry");
      setInfo("Scene revision created.");
    } catch (reason) {
      const parsed = parseApiError(reason);
      if (parsed.code === "scale_unknown") {
        setError("Set the plan scale before building 3D.");
        setScaleBannerFocus(true);
        scaleBannerRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      } else if (parsed.code === "draft_already_committed") {
        setInfo("This draft was already committed.");
      } else {
        setError(parsed.message);
      }
    } finally {
      setBusy(false);
    }
  }

  const floor = draft?.floors[0] ?? null;
  const wallsById = useMemo(() => {
    const map: Record<string, PlanWall> = {};
    for (const wall of floor?.walls ?? []) map[wall.id] = wall;
    return map;
  }, [floor]);

  const selectedWall =
    selection?.kind === "wall" || selection?.kind === "opening"
      ? wallsById[selection.wallId] ?? null
      : null;
  const selectedOpening =
    selection?.kind === "opening" && selectedWall
      ? selectedWall.openings.find((opening) => opening.id === selection.openingId) ?? null
      : null;
  const selectedRoom =
    selection?.kind === "room" ? floor?.rooms.find((room) => room.id === selection.roomId) ?? null : null;

  const progressText = activeJob
    ? [
        typeof activeJob.progress["phase"] === "string" ? (activeJob.progress["phase"] as string) : null,
        typeof activeJob.progress["fraction"] === "number"
          ? `${Math.round((activeJob.progress["fraction"] as number) * 100)}%`
          : null
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  if (phase === "entry") {
    return (
      <article className="panel pl-panel">
        <div className="panel-heading">
          <h2>Plan → 3D</h2>
          {draftVersion !== null && <span className="muted">draft v{draftVersion}</span>}
        </div>
        <p className="hint">
          Upload plan images, let the AI read the geometry, then correct it before building the 3D scene.
        </p>

        {apartmentImages.length === 0 && (
          <p className="muted">Upload an apartment plan image (role «apartment») to start.</p>
        )}

        {apartmentImages.length > 0 && (
          <div className="pl-asset-list">
            {apartmentImages.map((asset) => {
              const index = selectedIds.indexOf(asset.id);
              return (
                <button
                  key={asset.id}
                  type="button"
                  className={index >= 0 ? "pl-asset selected" : "pl-asset"}
                  onClick={() => toggleAsset(asset.id)}
                >
                  <span className="pl-asset-order">{index >= 0 ? index + 1 : "·"}</span>
                  <span>{asset.original_name ?? asset.id.slice(0, 8)}</span>
                </button>
              );
            })}
          </div>
        )}

        <div className="pl-actions">
          <button type="button" onClick={() => void analyze()} disabled={busy || selectedIds.length === 0}>
            Analyze with AI
          </button>
          {draft && (
            <button type="button" className="secondary" onClick={() => setPhase("editor")}>
              Open draft v{draftVersion}
            </button>
          )}
        </div>

        {analyzeJobId && (
          <p className={activeJob?.status === "failed" ? "error" : "muted"}>
            Analyzing… {activeJob?.status ?? "queued"}
            {progressText ? ` · ${progressText}` : ""}
            {analyzeElapsed ? ` · ${analyzeElapsed} elapsed` : ""} · plan parsing on
            the GPU box typically takes 1–4 minutes (hard cap 15)
          </p>
        )}
        {jobError(activeJob) && <div className="error">{jobError(activeJob)}</div>}
        {error && <div className="error">{error}</div>}
        {info && <p className="muted">{info}</p>}
      </article>
    );
  }

  return (
    <article className="panel pl-panel">
      <div className="panel-heading">
        <h2>Plan editor</h2>
        <span className="muted">
          {draftVersion !== null ? `draft v${draftVersion}` : "unsaved"}
          {draftStatus ? ` · ${draftStatus}` : ""}
        </span>
      </div>

      {!scaleKnown && (
        <div
          ref={scaleBannerRef}
          className={scaleBannerFocus ? "pl-scale-banner focus" : "pl-scale-banner"}
        >
          <strong>Scale unknown.</strong> Real dimensions are disabled until you set the scale.
          <button type="button" className="secondary" onClick={() => { setMode("set-scale"); setScalePoints([]); setSelection(null); }}>
            Set scale
          </button>
        </div>
      )}

      {scaleKnown && draft?.scale.source === "plan_label" && (
        <div className="pl-scale-banner">
          Parsed scale: {mmPerPx?.toFixed(3)} mm/px.
          <button type="button" className="secondary" onClick={() => { setMode("set-scale"); setScalePoints([]); setSelection(null); }}>
            Override
          </button>
        </div>
      )}

      {mode === "set-scale" && (
        <div className="pl-scale-tool">
          <p className="hint">
            Click two points on the plan ({scalePoints.length}/2), then enter the real distance between them.
          </p>
          {scalePoints.length === 2 && (
            <div className="pl-scale-input">
              <label>
                Real length (mm)
                <input
                  type="number"
                  min={1}
                  value={scaleInput}
                  onChange={(event) => setScaleInput(event.target.value)}
                />
              </label>
              <button
                type="button"
                onClick={() => applyScale(Number(scaleInput))}
                disabled={!scaleInput || Number(scaleInput) <= 0}
              >
                Apply scale
              </button>
              <button type="button" className="secondary" onClick={() => { setScalePoints([]); setScaleInput(""); }}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {planAssetId && (
        <div className="pl-stage">
          <div className="pl-image-wrap">
            <img
              src={api.assetUrl(planAssetId)}
              alt="Plan underlay"
              draggable={false}
              onLoad={(event) =>
                setNatural({ w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight })
              }
            />
            {natural && (
              <svg
                ref={svgRef}
                className="pl-overlay"
                viewBox={`0 0 ${natural.w} ${natural.h}`}
                preserveAspectRatio="xMidYMid meet"
                onPointerDown={handleSvgPointerDown}
                onPointerMove={handleSvgPointerMove}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
              >
                {floor?.walls.map((wall) => {
                  const ax = toImage(wall.x1);
                  const ay = toImage(wall.y1);
                  const bx = toImage(wall.x2);
                  const by = toImage(wall.y2);
                  const thickness = toImage(wall.thickness_mm);
                  const isSelected = selection?.kind === "wall" && selection.wallId === wall.id;
                  return (
                    <g key={wall.id}>
                      <line
                        x1={ax}
                        y1={ay}
                        x2={bx}
                        y2={by}
                        stroke="transparent"
                        strokeWidth={Math.max(thickness, 12)}
                        onPointerDown={(event) => {
                          if (mode !== "select") return;
                          event.stopPropagation();
                          setSelection({ kind: "wall", wallId: wall.id });
                        }}
                      />
                      <line
                        x1={ax}
                        y1={ay}
                        x2={bx}
                        y2={by}
                        stroke={isSelected ? "#1f6feb" : "#242424"}
                        strokeWidth={Math.max(thickness, 2)}
                        strokeLinecap="round"
                        pointerEvents="none"
                      />
                      {wall.openings.map((opening) => {
                        const cx = lerp(ax, bx, opening.t);
                        const cy = lerp(ay, by, opening.t);
                        const angle = (Math.atan2(by - ay, bx - ax) * 180) / Math.PI;
                        const width = toImage(opening.width_mm);
                        const isOpeningSelected =
                          selection?.kind === "opening" && selection.openingId === opening.id;
                        return (
                          <rect
                            key={opening.id}
                            x={cx - width / 2}
                            y={cy - Math.max(thickness, 4) / 2}
                            width={width}
                            height={Math.max(thickness, 4)}
                            transform={`rotate(${angle} ${cx} ${cy})`}
                            fill={isOpeningSelected ? "#1f6feb" : "#e8a33d"}
                            opacity={0.85}
                            onPointerDown={(event) => startOpeningDrag(event, wall.id, opening.id)}
                          />
                        );
                      })}
                      <circle
                        cx={ax}
                        cy={ay}
                        r={isSelected ? 7 : 5}
                        fill="white"
                        stroke="#1f6feb"
                        strokeWidth={2}
                        onPointerDown={(event) => startEndpointDrag(event, wall.id, 1)}
                      />
                      <circle
                        cx={bx}
                        cy={by}
                        r={isSelected ? 7 : 5}
                        fill="white"
                        stroke="#1f6feb"
                        strokeWidth={2}
                        onPointerDown={(event) => startEndpointDrag(event, wall.id, 2)}
                      />
                    </g>
                  );
                })}

                {floor?.rooms.map((room) => {
                  const centroid = roomCentroid(room, wallsById);
                  return (
                    <text
                      key={room.id}
                      x={toImage(centroid.x)}
                      y={toImage(centroid.y)}
                      textAnchor="middle"
                      className="pl-room-label"
                      onPointerDown={(event) => {
                        if (mode !== "select") return;
                        event.stopPropagation();
                        setSelection({ kind: "room", roomId: room.id });
                      }}
                    >
                      {room.name}
                    </text>
                  );
                })}

                {pendingWallStart && (
                  <circle cx={pendingWallStart.x} cy={pendingWallStart.y} r={6} fill="#1f6feb" />
                )}
                {scalePoints.map((point, index) => (
                  <circle key={index} cx={point.x} cy={point.y} r={6} fill="#d64545" />
                ))}
                {scalePoints.length === 2 && (
                  <line
                    x1={scalePoints[0].x}
                    y1={scalePoints[0].y}
                    x2={scalePoints[1].x}
                    y2={scalePoints[1].y}
                    stroke="#d64545"
                    strokeWidth={2}
                    strokeDasharray="6 4"
                  />
                )}
              </svg>
            )}
          </div>
        </div>
      )}

      <div className="pl-toolbar">
        <button
          type="button"
          className={mode === "add-wall" ? "secondary active" : "secondary"}
          onClick={() => { setMode(mode === "add-wall" ? "select" : "add-wall"); setPendingWallStart(null); }}
        >
          Add wall
        </button>
        <button
          type="button"
          className="secondary"
          onClick={addOpening}
          disabled={selection?.kind !== "wall"}
        >
          Add opening
        </button>
        <button type="button" className="secondary" onClick={removeSelected} disabled={!selection}>
          Remove selected
        </button>
        <button
          type="button"
          className={mode === "set-scale" ? "secondary active" : "secondary"}
          onClick={() => {
            setMode(mode === "set-scale" ? "select" : "set-scale");
            setScalePoints([]);
            setSelection(null);
          }}
        >
          Set scale
        </button>
      </div>

      {selection && (
        <div className="pl-inspector">
          {selectedWall && selection?.kind === "wall" && (
            <>
              <h3>Wall {selectedWall.id}</h3>
              <label>
                Thickness ({unitLabel})
                <input
                  type="number"
                  min={1}
                  disabled={!scaleKnown}
                  value={selectedWall.thickness_mm}
                  onChange={(event) => updateWall(selectedWall.id, { thickness_mm: numberInput(event.target.value, 1) })}
                />
              </label>
              <div className="pl-grid-2">
                {(["x1", "y1", "x2", "y2"] as const).map((key) => (
                  <label key={key}>
                    {key} ({unitLabel})
                    <input
                      type="number"
                      disabled={!scaleKnown}
                      value={selectedWall[key]}
                      onChange={(event) => updateWall(selectedWall.id, { [key]: numberInput(event.target.value, Number.NEGATIVE_INFINITY) })}
                    />
                  </label>
                ))}
              </div>
            </>
          )}

          {selectedOpening && selection?.kind === "opening" && (
            <>
              <h3>Opening {selectedOpening.id}</h3>
              <label>
                Kind
                <select
                  value={selectedOpening.kind}
                  onChange={(event) =>
                    updateOpening(selection.wallId, selectedOpening.id, {
                      kind: event.target.value as PlanOpeningKind
                    })
                  }
                >
                  <option value="door">door</option>
                  <option value="window">window</option>
                  <option value="arch">arch</option>
                </select>
              </label>
              <label>
                Position t · {selectedOpening.t.toFixed(2)}
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={selectedOpening.t}
                  onChange={(event) =>
                    updateOpening(selection.wallId, selectedOpening.id, { t: Number(event.target.value) })
                  }
                />
              </label>
              <div className="pl-grid-2">
                <label>
                  Width ({unitLabel})
                  <input
                    type="number"
                    min={1}
                    disabled={!scaleKnown}
                    value={selectedOpening.width_mm}
                    onChange={(event) =>
                      updateOpening(selection.wallId, selectedOpening.id, { width_mm: numberInput(event.target.value, 1) })
                    }
                  />
                </label>
                <label>
                  Height ({unitLabel})
                  <input
                    type="number"
                    min={1}
                    disabled={!scaleKnown}
                    value={selectedOpening.height_mm}
                    onChange={(event) =>
                      updateOpening(selection.wallId, selectedOpening.id, { height_mm: numberInput(event.target.value, 1) })
                    }
                  />
                </label>
                <label>
                  Sill ({unitLabel})
                  <input
                    type="number"
                    min={0}
                    disabled={!scaleKnown}
                    value={selectedOpening.sill_mm ?? 0}
                    onChange={(event) =>
                      updateOpening(selection.wallId, selectedOpening.id, {
                        sill_mm: numberInput(event.target.value, 0, Math.max(0, selectedOpening.height_mm - 1))
                      })
                    }
                  />
                </label>
              </div>
            </>
          )}

          {selectedRoom && selection?.kind === "room" && (
            <>
              <h3>Room {selectedRoom.id}</h3>
              <label>
                Name
                <input
                  value={selectedRoom.name}
                  onChange={(event) => updateRoom(selectedRoom.id, { name: event.target.value })}
                />
              </label>
            </>
          )}
        </div>
      )}

      <div className="pl-actions">
        <button type="button" onClick={() => void saveDraft()} disabled={busy || !draft}>
          Save draft
        </button>
        <button type="button" onClick={() => void build3d()} disabled={busy || !draft}>
          Build 3D
        </button>
        <button type="button" className="secondary" onClick={() => setPhase("entry")} disabled={busy}>
          Back
        </button>
      </div>

      {error && <div className="error">{error}</div>}
      {info && <p className="muted">{info}</p>}
    </article>
  );
}
