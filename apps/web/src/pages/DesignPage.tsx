import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, type Asset, type AssetRole, type Generation, type ImportUrlResponse, type Job, type ProductCandidate, type RenderRecord, type SceneDocument, type SceneRevision, type ValidationReport } from "../api";
import { AttachmentSection } from "../AttachmentSection";
import { ImagePreview } from "../ImagePreview";
import { LayerBadge } from "../LayerBadge";
import { CONCEPT_GENERATED_LABEL, CONCEPT_NEEDS_RENDER_HINT, DESIGN_CONFLICT_HINT, MOOD_SCOPE_HINT, WORKER_OFFLINE_NOTICE, renderStageLabel, statusLabel } from "../copy";
import { computeCameraReadiness, computeRenderReadiness } from "../cameraReadiness";
import { CameraPanel } from "../CameraPanel";
import { PhotoEditPanel } from "../PhotoEditPanel";
import { ReplacementPanel } from "../ReplacementPanel";
import { SceneViewer } from "../SceneViewer";
import { SceneViewerErrorBoundary } from "../SceneViewerErrorBoundary";
import { TwinDesignPanel } from "../TwinDesignPanel";
import {
  buildPatchPayload,
  buildPlaceCommand,
  dimsValid,
  missingFields,
  rejectReasonLabel,
  type ProductCandidateLike
} from "../productImport";
import {
  buildSetStateCommand,
  ENTITY_STATES,
  ENTITY_STATE_LABELS,
  entityState,
  filterEntitiesByState,
  type EntityState
} from "../sceneLayers";
import {
  buildSetIntentCommand,
  buildSetLocksCommand,
  entityIntent,
  intentActionBlocked,
  INTENT_LABELS,
  type DesignIntent,
  type EntityLocksPatch
} from "../sceneIntent";
import { ruleLabel, summaryLine, topResults } from "../sceneValidation";
import {
  buildSelectionContext,
  buildSetColorCommand,
  isAppearanceEditableSurface,
  SURFACE_COLOR_PRESETS,
  SURFACE_FINISH_PRESETS,
  type SelectionContext,
  type SurfaceFinishPreset
} from "../surfaceEdits";
import { apiErrorText, redesignResultAssetId, renderRgbAssetId, sortedRendersNewestFirst, uniqueId } from "../twinDesign";
import {
  buildConceptRedesignInput,
  captionMoodTitle,
  DEFAULT_MOOD_ID,
  loadMoodId,
  MOOD_PRESETS,
  saveMoodId,
  type ConceptGenerationLike
} from "../moodSelect";
import type { PageId } from "../nav";

const KIND_LABELS: Record<string, string> = {
  room: "Комната",
  floor: "Пол",
  wall: "Стена",
  furniture: "Мебель"
};

// Rail display order for the intent toggles (keep first: it is the neutral
// "leave as is" choice; clear is rendered separately when an intent is set).
const INTENT_ORDER: readonly DesignIntent[] = ["keep", "replace", "remove"];

function isConflict(reason: unknown): boolean {
  const message = reason instanceof Error ? reason.message : String(reason);
  return /^409:/.test(message);
}

// The structural shell (floors/walls/rooms) always reaches SceneViewer even
// when its layer is hidden: planOverview() frames the overview camera from
// wall entities and the shell is the context every layer sits in.
function isShellEntity(entity: SceneDocument["entities"][number]): boolean {
  return entity.kind === "floor" || entity.kind === "wall" || entity.kind === "room";
}

// Design page (#154): a persistent design-tool workspace — command bar on top
// (3D/Фото mode switch + reference/analyze actions), a dominant center surface
// shared by both modes (SceneViewer in 3D, the photo stage in Фото), a
// contextual right rail, the collapsed advanced strip and the persistent
// bottom AI composer. Scene manipulation, photo-first editing, calibration,
// twin design, replacement and style analysis. Requires a scene revision (#101).

// R3 product import: the review form is a flat record of editable strings
// ("" = not entered); buildPatchPayload/buildPlaceCommand parse the raw values.
type ImportForm = {
  title: string;
  brand: string;
  model: string;
  price: string;
  currency: string;
  material: string;
  color: string;
  width_mm: string;
  depth_mm: string;
  height_mm: string;
};

const EMPTY_IMPORT_FORM: ImportForm = {
  title: "",
  brand: "",
  model: "",
  price: "",
  currency: "",
  material: "",
  color: "",
  width_mm: "",
  depth_mm: "",
  height_mm: ""
};

// Field keys → ru labels for the «не хватает данных» hint. Keys are the
// backend fact names (products.py _FACT_COLUMNS → missing_fields), which the
// review form uses 1:1 (descriptor facts are stored under material/color).
const IMPORT_FIELD_LABELS: Record<string, string> = {
  title: "название",
  brand: "бренд",
  model: "модель",
  price: "цена",
  currency: "валюта",
  width_mm: "ширина",
  depth_mm: "глубина",
  height_mm: "высота",
  material: "материал",
  color: "цвет"
};

// Backend missing_fields fact key → review-form field key. Both descriptor
// facts are named material/color on the wire and in the form; the map keeps
// the normalization point explicit if the backend ever renames a fact.
const SERVER_FACT_TO_FORM_KEY: Record<string, keyof ImportForm> = {
  material: "material",
  color: "color"
};

function descriptorsText(value: string[] | string | null | undefined): string {
  if (Array.isArray(value)) return value.join(", ");
  return typeof value === "string" ? value : "";
}

function importFormFromCandidate(candidate: ProductCandidate): ImportForm {
  return {
    title: candidate.title ?? "",
    brand: candidate.brand ?? "",
    model: candidate.model ?? "",
    price: candidate.price == null ? "" : String(candidate.price),
    currency: candidate.currency ?? "",
    material: descriptorsText(candidate.material_descriptors),
    color: descriptorsText(candidate.color_descriptors),
    width_mm: candidate.width_mm == null ? "" : String(candidate.width_mm),
    depth_mm: candidate.depth_mm == null ? "" : String(candidate.depth_mm),
    height_mm: candidate.height_mm == null ? "" : String(candidate.height_mm)
  };
}
export function DesignPage({
  projectId,
  revision,
  assets,
  jobs,
  generations,
  workersOnline,
  onChanged,
  onUpload,
  uploadProgress,
  instruction,
  onInstructionChange,
  onSubmitInstruction,
  onAnalyzeStyle,
  onNavigate
}: {
  projectId: string;
  revision: SceneRevision | null;
  assets: Asset[];
  jobs: Job[];
  generations: Generation[];
  workersOnline: boolean;
  onChanged: () => Promise<void>;
  onUpload: (file: File | null, role: AssetRole) => void;
  uploadProgress: number | null;
  instruction: string;
  onInstructionChange: (value: string) => void;
  // R10 (#186): the page builds the selection context from the entity
  // selected at submit time (undefined = nothing selected → the payload
  // stays {text}); App still owns the actual submit.
  onSubmitInstruction: (event: FormEvent, selectionContext?: SelectionContext) => void;
  onAnalyzeStyle: () => void;
  onNavigate: (page: PageId) => void;
}) {
  const [mode, setMode] = useState<"3d" | "photo">("3d");
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [replaceTargetId, setReplaceTargetId] = useState<string | null>(null);
  // R1 layers: which entity states the 3D view shows. All visible by default
  // (byte-equal scene passes through), at least one layer stays on.
  const [visibleStates, setVisibleStates] = useState<EntityState[]>([...ENTITY_STATES]);
  const [stateBusy, setStateBusy] = useState(false);
  const [stateError, setStateError] = useState("");
  // R2 intents/locks: one command channel shared by the intent toggles and
  // the locks checkboxes (same freshness rule and error surface as set_state).
  const [intentBusy, setIntentBusy] = useState(false);
  const [intentError, setIntentError] = useState("");
  const [intentConflict, setIntentConflict] = useState(false);
  // R10 (#186): surface appearance panel (wall/floor/ceiling). One command
  // channel with the same freshness rule and error surface as the intent
  // senders above; customColorRef backs the native color input (uncontrolled
  // — the send is an explicit «Применить» click, one click = one command).
  const [surfaceBusy, setSurfaceBusy] = useState(false);
  const [surfaceError, setSurfaceError] = useState("");
  const [surfaceConflict, setSurfaceConflict] = useState(false);
  const [surfaceInfo, setSurfaceInfo] = useState("");
  const customColorRef = useRef<HTMLInputElement | null>(null);
  // R10 (#186): switching the selection clears the surface panel's stale
  // hints, so the card for the next entity never shows the previous entity's
  // error/conflict/success (the intent/locks channel resets its own hints on
  // send; the panel persists between picks, so it needs this sweep).
  useEffect(() => {
    setSurfaceError("");
    setSurfaceConflict(false);
    setSurfaceInfo("");
  }, [selectedEntityId]);
  // R2 design check: the report is fetched only on demand (manual button);
  // drags and commits never re-run it.
  const [checkReport, setCheckReport] = useState<ValidationReport | null>(null);
  const [checkBusy, setCheckBusy] = useState(false);
  const [checkError, setCheckError] = useState("");
  // R3 product import: inline panel state (URL/image step → review → place).
  const [importOpen, setImportOpen] = useState(false);
  const [importUrl, setImportUrl] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [imageBusy, setImageBusy] = useState(false);
  const [importError, setImportError] = useState("");
  const [importCandidate, setImportCandidate] = useState<ProductCandidate | null>(null);
  // missing_fields as reported by the import response (dims are re-derived
  // live from the form; see missingHighlight below).
  const [importMissing, setImportMissing] = useState<string[]>([]);
  const [importForm, setImportForm] = useState<ImportForm>({ ...EMPTY_IMPORT_FORM });
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [placeBusy, setPlaceBusy] = useState(false);
  const [placeError, setPlaceError] = useState("");
  // R4 variants: small command-bar action that seeds a variant from the
  // current canonical head, then jumps to Results where the chip row lives.
  const [variantOpen, setVariantOpen] = useState(false);
  const [variantTitle, setVariantTitle] = useState("");
  const [variantBusy, setVariantBusy] = useState(false);
  const [variantError, setVariantError] = useState("");
  const advancedRef = useRef<HTMLDetailsElement | null>(null);
  const canvasRef = useRef<HTMLElement | null>(null);
  // R7 (#187): two-stage manual render flow in the contextual rail — a draft
  // render (eevee BE-side) first, the final photoreal render unlocks after
  // the draft job succeeded. The render list shows stage chips; jobs arrive
  // via the shared App polling (jobs prop), renders are re-read on demand.
  const [renderBusy, setRenderBusy] = useState(false);
  const [pendingRenderJobId, setPendingRenderJobId] = useState<string | null>(null);
  const [pendingRenderStage, setPendingRenderStage] = useState<"draft" | "final" | null>(null);
  const [draftReady, setDraftReady] = useState(false);
  const [renderInfo, setRenderInfo] = useState("");
  const [renderError, setRenderError] = useState("");
  const [renders, setRenders] = useState<RenderRecord[]>([]);
  // R7: bumped by the viewer error boundary retry — remounts SceneViewer so a
  // crashed WebGL subtree is recreated from scratch, not merely re-rendered.
  const [viewEpoch, setViewEpoch] = useState(0);

  // ---------------------------------------------------------------------------
  // Rules of hooks: every hook below runs UNCONDITIONALLY, before the
  // `!revision` early return. Revision flips to null when the owner switches
  // to a project without a scene revision while this page is mounted; a hook
  // count change between renders crashes React («Rendered fewer hooks than
  // expected») and white-screens the app. The hooks therefore work with
  // nullable values and only the JSX return is gated.
  // ---------------------------------------------------------------------------

  // R1 layer filter: hide the entities of the unchecked layers before the
  // scene reaches SceneViewer. With every layer on, the original scene object
  // passes through unchanged. Cameras are never filtered, so the camera
  // toolbar and the calibrated-pose overlay are unaffected by the toggles.
  // Null when no revision exists yet (the empty state never renders the
  // viewer, so the null never reaches SceneViewer).
  const sceneForViewer = useMemo(() => {
    if (!revision) return null;
    const scene = revision.scene;
    if (visibleStates.length === ENTITY_STATES.length) return scene;
    const shell = scene.entities.filter(isShellEntity);
    const content = filterEntitiesByState(
      { ...scene, entities: scene.entities.filter((entity) => !isShellEntity(entity)) },
      visibleStates
    );
    return { ...scene, entities: [...shell, ...content.entities] };
  }, [revision, visibleStates]);

  const latestRenders = useMemo(
    () => renders.slice(0, 5),
    [renders]
  );

  // R7 (#187): render history for the rail list (latest five, stage chips).
  useEffect(() => {
    let cancelled = false;
    api.listRenders(projectId)
      .then((records) => {
        if (!cancelled) setRenders(records);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // R7 (#187): the render job arrives via the shared App poll; when it leaves
  // the queued/running set we resolve the draft→final gate and refresh the
  // list. Mirrors the TwinDesignPanel completion pattern.
  useEffect(() => {
    if (!pendingRenderJobId) return;
    const job = jobs.find((item) => item.id === pendingRenderJobId);
    if (!job) return;
    if (job.status === "queued" || job.status === "running") return;
    if (job.status === "succeeded") {
      if (pendingRenderStage === "final") {
        setRenderInfo("Финальный рендер готов — смотрите страницу «Результаты».");
      } else {
        setDraftReady(true);
        setRenderInfo("Черновик готов. Проверьте ракурс и сделайте финальный рендер.");
      }
    } else {
      setRenderError(`Рендер не завершён: ${statusLabel(job.status)}.`);
    }
    setPendingRenderJobId(null);
    setPendingRenderStage(null);
    api.listRenders(projectId)
      .then(setRenders)
      .catch(() => undefined);
  }, [jobs, pendingRenderJobId, pendingRenderStage, projectId]);

  // ---------------------------------------------------------------------------
  // R9 (#197): editorial mood for concept sketches. Presets mirror the BE
  // registry (services/moods.py); the choice is persisted per project in
  // localStorage and only feeds the concept CTA near the composer — it never
  // alters the scene structure.
  // ---------------------------------------------------------------------------
  const [moodId, setMoodId] = useState<string>(DEFAULT_MOOD_ID);
  const [conceptBusy, setConceptBusy] = useState(false);
  const [conceptJobId, setConceptJobId] = useState<string | null>(null);
  const [conceptError, setConceptError] = useState("");
  const [conceptInfo, setConceptInfo] = useState("");
  const [conceptResultAssetId, setConceptResultAssetId] = useState<string | null>(
    null
  );
  // Mood title snapshot taken from the finished generation itself (see the
  // success effect below) — never re-derived from the live selector, so
  // switching the mood cannot relabel an already-generated sketch.
  const [conceptMoodTitle, setConceptMoodTitle] = useState<string | null>(null);

  // Restore the per-project mood on project switch and reset the concept job
  // bookkeeping (jobs are project-scoped, so a pending id from the previous
  // project could never resolve). Persistence itself is written imperatively
  // in selectMood — never in an effect — so switching projects cannot
  // flash-write the previous project's mood under the new key.
  useEffect(() => {
    setMoodId(loadMoodId(projectId));
    setConceptJobId(null);
    setConceptError("");
    setConceptInfo("");
    setConceptResultAssetId(null);
    setConceptMoodTitle(null);
  }, [projectId]);

  // Ruling: the concept CTA bases on the newest successful render's rgb pass
  // (renderRgbAssetId over the newest-first render list); without one the CTA
  // stays disabled with the «Сначала сделайте черновой рендер» hint.
  const conceptBaseAssetId = useMemo(() => {
    for (const render of sortedRendersNewestFirst(renders)) {
      const baseAssetId = renderRgbAssetId(render.manifest);
      if (baseAssetId) return baseAssetId;
    }
    return null;
  }, [renders]);

  const activeConceptJob = useMemo(
    () => (conceptJobId ? jobs.find((job) => job.id === conceptJobId) ?? null : null),
    [jobs, conceptJobId]
  );

  useEffect(() => {
    if (!conceptJobId || activeConceptJob?.status !== "succeeded") return;
    setConceptJobId(null);
    const resultAssetId = redesignResultAssetId(activeConceptJob);
    if (resultAssetId) {
      setConceptResultAssetId(resultAssetId);
      // Snapshot the mood the sketch was actually generated with: the BE
      // stamps mood_id/mood_title into the generation's structured_conditioning
      // (services/generations.py) and mirrors the full manifest into
      // job.result.generation_manifest (worker/runtime.py). The live selector
      // is only the fallback for records without a stamped mood_title.
      const rawManifest = activeConceptJob.result?.["generation_manifest"];
      const generation: ConceptGenerationLike | null =
        rawManifest && typeof rawManifest === "object"
          ? { manifest: rawManifest }
          : null;
      setConceptMoodTitle(captionMoodTitle(generation, moodId));
      setConceptInfo("Эскиз концепта готов — смотрите превью ниже.");
    } else {
      setConceptError("Задача завершилась без выходного изображения.");
    }
  }, [activeConceptJob, conceptJobId, moodId]);

  useEffect(() => {
    if (!conceptJobId) return;
    if (activeConceptJob?.status !== "failed" && activeConceptJob?.status !== "cancelled") {
      return;
    }
    setConceptJobId(null);
    setConceptError(`Эскиз концепта: ${statusLabel(activeConceptJob.status)}.`);
  }, [activeConceptJob, conceptJobId]);

  const conceptStatus = useMemo(() => {
    if (!conceptJobId) return null;
    if (!activeConceptJob) return statusLabel("queued");
    const fraction =
      typeof activeConceptJob.progress["fraction"] === "number"
        ? Math.round((activeConceptJob.progress["fraction"] as number) * 100)
        : null;
    const label = statusLabel(activeConceptJob.status);
    return fraction === null ? label : `${label} · ${fraction}%`;
  }, [activeConceptJob, conceptJobId]);

  function selectMood(next: string) {
    setMoodId(next);
    saveMoodId(projectId, next);
  }

  // Same freshness rule as every other sender: base_revision_id must equal
  // the current latest revision, read fresh, never from the stale prop. The
  // mood id travels alongside the fixed short prompt; the BE composes the
  // editorial direction (mood_id only — no prompt text from the preset).
  async function runConceptSketch() {
    if (conceptBusy || conceptJobId || !conceptBaseAssetId) return;
    setConceptError("");
    setConceptInfo("");
    setConceptResultAssetId(null);
    setConceptMoodTitle(null);
    setConceptBusy(true);
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) throw new Error("Сцена ещё не инициализирована.");
      const response = await api.createRedesign(
        projectId,
        buildConceptRedesignInput({
          baseRevisionId: fresh.revision_id,
          baseAssetId: conceptBaseAssetId,
          moodId
        })
      );
      setConceptJobId(response.job.id);
      await onChanged();
    } catch (reason) {
      setConceptError(apiErrorText(reason));
    } finally {
      setConceptBusy(false);
    }
  }

  // R3: which review-form fields stay highlighted as missing —
  // server-reported gaps until the user fills them in, plus the dimensions
  // re-derived live from the form (they gate placement).
  const missingHighlight = useMemo(() => {
    const keys = new Set<string>();
    for (const key of importMissing) {
      const formKey = SERVER_FACT_TO_FORM_KEY[key] ?? key;
      const value = importForm[formKey as keyof ImportForm];
      if (value == null || value.trim() === "") keys.add(formKey);
    }
    for (const key of missingFields(importForm)) keys.add(key);
    return keys;
  }, [importMissing, importForm]);

  if (!revision) {
    return (
      <section className="empty-state">
        <h2>Сцена ещё не создана</h2>
        <p>Сцена появится после того, как вы создадите 3D из плана. Начните на странице «План».</p>
        <button className="secondary" onClick={() => onNavigate("plan")}>
          Перейти к плану
        </button>
      </section>
    );
  }

  const referenceImages = assets.filter(
    (asset) => asset.role === "reference" && asset.media_type.startsWith("image/")
  ).length;

  const readiness = computeCameraReadiness(revision.scene.cameras);

  // R7 (#187): render-readiness gate for the rail — calibrated camera → full
  // draft→final flow; estimated camera → draft only with the «приблизительный
  // ракурс» caveat; no camera → disabled with the warning copy.
  const renderReadiness = computeRenderReadiness(revision.scene.cameras);
  const renderPending = pendingRenderJobId !== null;
  const renderButtonLabel = renderPending
    ? pendingRenderStage === "final"
      ? "Готовим финальный рендер…"
      : "Готовим черновик…"
    : draftReady
      ? "Сделать финальный рендер"
      : "Сделать рендер";
  const renderButtonDisabled =
    renderBusy ||
    renderPending ||
    !renderReadiness.cameraId ||
    (draftReady ? !renderReadiness.allowFinal : false);

  // Freshness rule shared with the command senders: the CAS-ish revision id
  // and the readiness verdict come from a fresh scene read, never from the
  // possibly-stale prop. The stage is sent explicitly and never overrides
  // renderer_profile (BE maps draft→eevee itself); the idempotency key
  // includes the stage so a retried draft never collides with a final.
  const startRender = async (stage: "draft" | "final") => {
    if (renderBusy || renderPending) return;
    setRenderInfo("");
    setRenderError("");
    setRenderBusy(true);
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) throw new Error("Сцена ещё не инициализирована.");
      const freshReadiness = computeRenderReadiness(fresh.scene.cameras);
      if (!freshReadiness.cameraId) {
        throw new Error("Недостаточно точности для рендера.");
      }
      if (stage === "final" && !freshReadiness.allowFinal) {
        throw new Error("Недостаточно точности для финального рендера.");
      }
      const job = await api.createRender(projectId, {
        camera_id: freshReadiness.cameraId,
        scene_revision_id: fresh.revision_id,
        stage,
        idempotency_key: `manual:${fresh.revision_id}:${freshReadiness.cameraId}:${stage}`
      });
      setPendingRenderJobId(job.id);
      setPendingRenderStage(stage);
    } catch (reason) {
      setRenderError(apiErrorText(reason));
    } finally {
      setRenderBusy(false);
    }
  };

  function toggleState(state: EntityState) {
    setVisibleStates((current) => {
      if (current.includes(state)) {
        // Keep at least one layer visible so the viewer never goes blank.
        return current.length > 1
          ? current.filter((item) => item !== state)
          : current;
      }
      return ENTITY_STATES.filter(
        (item) => item === state || current.includes(item)
      );
    });
  }

  // R1 set_state: same freshness rule as the other scene-command senders —
  // the base_revision_id must equal the current latest revision.
  async function applyState(entityId: string, state: EntityState) {
    if (stateBusy) return;
    setStateBusy(true);
    setStateError("");
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setStateError("Сцена ещё не инициализирована.");
        return;
      }
      await api.applySceneCommand(
        projectId,
        buildSetStateCommand({
          commandId: uniqueId(),
          baseRevisionId: fresh.revision_id,
          targetId: entityId,
          state
        })
      );
      await onChanged();
    } catch (reason) {
      setStateError(apiErrorText(reason));
    } finally {
      setStateBusy(false);
    }
  }

  // R2 set_intent / set_locks: the builders return the exact command core
  // (operation/target_id/parameters); this dispatcher wraps it into the full
  // DesignCommand envelope — schema_version, fresh base_revision_id, user
  // origin — mirroring buildSetStateCommand's field-for-field shape.
  async function applyEntityCommand(command: {
    operation: "set_intent" | "set_locks";
    target_id: string;
    parameters: Record<string, unknown>;
  }) {
    if (intentBusy) return;
    setIntentBusy(true);
    setIntentError("");
    setIntentConflict(false);
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setIntentError("Сцена ещё не инициализирована.");
        return;
      }
      await api.applySceneCommand(projectId, {
        schema_version: "0.1.0",
        command_id: uniqueId(),
        base_revision_id: fresh.revision_id,
        operation: command.operation,
        target_id: command.target_id,
        parameters: command.parameters,
        reference_asset_ids: [],
        origin: "user",
        request_text: null
      });
      await onChanged();
    } catch (reason) {
      setIntentError(apiErrorText(reason));
      setIntentConflict(isConflict(reason));
    } finally {
      setIntentBusy(false);
    }
  }

  function applyIntent(intent: DesignIntent | null) {
    if (!selectedEntity) return;
    void applyEntityCommand(buildSetIntentCommand(selectedEntity.id, intent));
  }

  // Partial lock merge: send only the single toggled key, never the whole
  // locks object (the backend merges the patch into the entity's locks).
  function applyLock(
    key: keyof EntityLocksPatch,
    value: boolean
  ) {
    if (!selectedEntity) return;
    const patch: EntityLocksPatch = {};
    patch[key] = value;
    void applyEntityCommand(buildSetLocksCommand(selectedEntity.id, patch));
  }

  // R10 (#186) surface appearance: one command channel for the panel, with
  // the same freshness rule as applyEntityCommand — a fresh scene read gives
  // base_revision_id, uniqueId() gives command_id, the builder's command core
  // is wrapped into the full DesignCommand envelope (origin "user"). A 409
  // (stale revision or the material lock) surfaces the shared conflict hint;
  // success points at «Результаты», where the scene version can be undone.
  async function applySurfaceCommand(command: {
    operation: "set_color" | "set_material";
    target_id: string;
    parameters: Record<string, unknown>;
  }) {
    if (surfaceBusy) return;
    setSurfaceBusy(true);
    setSurfaceError("");
    setSurfaceConflict(false);
    setSurfaceInfo("");
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setSurfaceError("Сцена ещё не инициализирована.");
        return;
      }
      await api.applySceneCommand(projectId, {
        schema_version: "0.1.0",
        command_id: uniqueId(),
        base_revision_id: fresh.revision_id,
        operation: command.operation,
        target_id: command.target_id,
        parameters: command.parameters,
        reference_asset_ids: [],
        origin: "user",
        request_text: null
      });
      setSurfaceInfo(
        "Изменение сохранено как версия. Отменить можно на странице «Результаты»."
      );
      await onChanged();
    } catch (reason) {
      setSurfaceError(apiErrorText(reason));
      setSurfaceConflict(isConflict(reason));
    } finally {
      setSurfaceBusy(false);
    }
  }

  // Wall accent: one click on a swatch (or «Применить» for the custom color)
  // = one set_color command = one scene revision.
  function applySurfaceColor(color: string) {
    if (!selectedEntity) return;
    const trimmed = color.trim();
    if (!trimmed) return;
    void applySurfaceCommand(buildSetColorCommand(selectedEntity.id, trimmed));
  }

  // Floor/ceiling finish: the visible effect is the color (the FE viewer and
  // the BE render both key off metadata.color), so the panel sends set_color
  // with the preset's hex — one click stays one scene revision.
  function applySurfaceFinish(preset: SurfaceFinishPreset) {
    if (!selectedEntity) return;
    void applySurfaceCommand(buildSetColorCommand(selectedEntity.id, preset.hex));
  }

  // R2 design check: manual run against the current latest revision.
  async function runDesignCheck() {
    if (checkBusy) return;
    setCheckBusy(true);
    setCheckError("");
    try {
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setCheckError("Сцена ещё не инициализирована.");
        return;
      }
      setCheckReport(
        await api.validateScene(projectId, { scene_revision_id: fresh.revision_id })
      );
    } catch (reason) {
      setCheckError(apiErrorText(reason));
    } finally {
      setCheckBusy(false);
    }
  }

  // ---- R3 product import flow -------------------------------------------
  //
  // Step 1 extracts a candidate from a product URL (guard rejections surface
  // as 422 {code:"url_rejected", reason} — shown inline via
  // rejectReasonLabel) or from a reference image (same api.upload channel as
  // the apartment/reference uploads, immediately wrapped into a manual
  // candidate). Step 2 lets the user correct the extracted fields (patched on
  // demand via «Сохранить»). Step 3 places the candidate with the same
  // command envelope as applyEntityCommand, gated on dimsValid.

  function openReview(response: ImportUrlResponse) {
    setImportCandidate(response.candidate);
    setImportMissing(response.missing_fields);
    setImportForm(importFormFromCandidate(response.candidate));
    setSaveError("");
    setPlaceError("");
  }

  async function runImportUrl() {
    if (importBusy || imageBusy) return;
    const url = importUrl.trim();
    if (!url) return;
    setImportBusy(true);
    setImportError("");
    try {
      openReview(await api.importProductUrl(projectId, url));
    } catch (reason) {
      setImportError(rejectReasonLabel(reason));
    } finally {
      setImportBusy(false);
    }
  }

  async function importFromImage(file: File) {
    if (imageBusy || importBusy) return;
    setImageBusy(true);
    setImportError("");
    try {
      const uploaded = await api.upload(projectId, file, "reference", () => {});
      openReview({
        candidate: await api.createProduct(projectId, { source_asset_id: uploaded.id }),
        missing_fields: [],
        extraction: { status: "manual" }
      });
    } catch (reason) {
      setImportError(apiErrorText(reason));
    } finally {
      setImageBusy(false);
    }
  }

  async function saveImportCandidate() {
    if (!importCandidate || saveBusy) return;
    setSaveBusy(true);
    setSaveError("");
    try {
      setImportCandidate(
        await api.patchProduct(projectId, importCandidate.id, buildPatchPayload(importForm))
      );
    } catch (reason) {
      setSaveError(apiErrorText(reason));
    } finally {
      setSaveBusy(false);
    }
  }

  async function placeImportedProduct() {
    if (!importCandidate || placeBusy) return;
    if (!dimsValid(importForm)) return;
    setPlaceBusy(true);
    setPlaceError("");
    try {
      // The command base_revision_id must match the current latest revision,
      // so never trust the prop's revision id here (same freshness rule as
      // applyState/applyEntityCommand).
      const fresh = await api.scene(projectId);
      if (!fresh) {
        setPlaceError("Сцена ещё не инициализирована.");
        return;
      }
      // Form corrections win over the stored candidate so placing right
      // after an unsaved fix still places what the user sees.
      const placeCandidate: ProductCandidateLike = {
        id: importCandidate.id,
        source_url: importCandidate.source_url,
        source_asset_id: importCandidate.source_asset_id,
        title: importForm.title,
        brand: importForm.brand,
        model: importForm.model,
        price: importForm.price,
        currency: importForm.currency,
        width_mm: importForm.width_mm,
        depth_mm: importForm.depth_mm,
        height_mm: importForm.height_mm,
        preview_asset_id: importCandidate.preview_asset_id
      };
      const command = buildPlaceCommand(placeCandidate);
      await api.applySceneCommand(projectId, {
        schema_version: "0.1.0",
        command_id: uniqueId(),
        base_revision_id: fresh.revision_id,
        operation: command.operation,
        target_id: command.target_id,
        parameters: command.parameters,
        reference_asset_ids: [
          importCandidate.source_asset_id,
          importCandidate.preview_asset_id
        ].filter((id): id is string => typeof id === "string" && id.length > 0),
        origin: "user",
        request_text: null
      });
      // Select the placed entity and land in the 3D scene so the existing
      // drag flow takes over (same hand-off as the twin panel).
      setMode("3d");
      setSelectedEntityId(command.target_id);
      closeImportPanel();
      await onChanged();
    } catch (reason) {
      setPlaceError(apiErrorText(reason));
    } finally {
      setPlaceBusy(false);
    }
  }

  function closeImportPanel() {
    setImportOpen(false);
    setImportUrl("");
    setImportError("");
    setImportCandidate(null);
    setImportMissing([]);
    setImportForm({ ...EMPTY_IMPORT_FORM });
    setSaveError("");
    setPlaceError("");
  }

  // R4: create a variant from the current canonical scene head and land on
  // Results, where the variant chip row and detail pane live (SPA navigation,
  // same onNavigate channel as every other page switch).
  async function createVariantFromCurrent() {
    const title = variantTitle.trim();
    if (!title || variantBusy) return;
    setVariantBusy(true);
    setVariantError("");
    try {
      await api.createVariantFromCurrent(projectId, title);
      setVariantTitle("");
      setVariantOpen(false);
      onNavigate("results");
    } catch (reason) {
      setVariantError(apiErrorText(reason));
    } finally {
      setVariantBusy(false);
    }
  }

  // Contextual rail (#154): the entity selected in the 3D scene, with its
  // actions one glance away instead of far below the canvas.
  const selectedEntity = selectedEntityId
    ? revision.scene.entities.find((entity) => entity.id === selectedEntityId) ?? null
    : null;

  // R10 (#186): the material lock freezes the appearance panel (the backend
  // rejects set_color/set_material with 409 while it is set).
  const surfaceLocked = selectedEntity?.locks?.material === true;

  // R10 (#186): the entity's current color (metadata.color — the same key the
  // SceneViewer paints and the render pipeline prefers), used to highlight
  // the active swatch and to seed the custom color picker.
  const surfaceColor =
    (selectedEntity?.metadata?.["color"] as string | undefined) ?? null;

  function openAdvanced() {
    const node = advancedRef.current;
    if (!node) return;
    node.open = true;
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function closeAdvanced() {
    const node = advancedRef.current;
    if (node) node.open = false;
    canvasRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Compact rail card list: top-3 results in severity order (computed once).
  const checkTopResults = checkReport ? topResults(checkReport) : [];

  // Review-form input class: fields the import reported as missing (and that
  // are still empty) get the warning highlight. (missingHighlight memo lives
  // above the !revision early return — rules of hooks.)
  function fieldClass(key: string): string {
    return missingHighlight.has(key)
      ? "product-import-field product-import-field--missing"
      : "product-import-field";
  }

  return (
    <>
      {!workersOnline && (
        <p className="worker-notice muted">
          {WORKER_OFFLINE_NOTICE}
        </p>
      )}

      <div className="design-workspace">
        {/* Command bar (#154): mode switch + the reference/analyze actions. */}
        <section className="page-upload-row design-command-bar" aria-label="Панель управления дизайном">
          <div className="mode-switch" role="group" aria-label="Режим редактирования">
            <button
              type="button"
              className={mode === "3d" ? "active" : ""}
              aria-pressed={mode === "3d"}
              onClick={() => setMode("3d")}
            >
              3D
            </button>
            <button
              type="button"
              className={mode === "photo" ? "active" : ""}
              aria-pressed={mode === "photo"}
              onClick={() => setMode("photo")}
            >
              Фото
            </button>
          </div>
          {/* R1 layer toggles: filter the entities SceneViewer renders. The
              structural shell (floors/walls/rooms) always stays visible. */}
          <div className="mode-switch" role="group" aria-label="Слои сцены">
            {ENTITY_STATES.map((state) => {
              const active = visibleStates.includes(state);
              return (
                <button
                  key={state}
                  type="button"
                  className={active ? "active" : ""}
                  aria-pressed={active}
                  onClick={() => toggleState(state)}
                >
                  {ENTITY_STATE_LABELS[state]}
                </button>
              );
            })}
          </div>
          {/* R3 product import: toggles the inline add-product flow panel. */}
          <button
            type="button"
            className={importOpen ? "product-import-toggle active" : "product-import-toggle"}
            aria-expanded={importOpen}
            onClick={() => setImportOpen((open) => !open)}
          >
            Добавить товар
          </button>
          {/* R4 variants: seed a variant from the current canonical head. */}
          <button
            type="button"
            className={variantOpen ? "variant-create-toggle active" : "variant-create-toggle"}
            aria-expanded={variantOpen}
            onClick={() => setVariantOpen((open) => !open)}
          >
            Вариант из текущего
          </button>
          <label className="upload">
            Загрузить референс
            <input
              type="file"
              onChange={(event) => {
                onUpload(event.target.files?.[0] ?? null, "reference");
                event.target.value = "";
              }}
            />
          </label>
          {uploadProgress !== null && (
            <div className="upload-progress" aria-label="Upload progress">
              <div style={{ width: `${uploadProgress}%` }} />
              <span>{uploadProgress}%</span>
            </div>
          )}
          <div className="style-analyze-group">
            <button onClick={onAnalyzeStyle}>Анализ стиля</button>
            <p className="hint" id="style-hint">
              Анализ стиля требует 3–5 изображений с ролью «референс». Сейчас: {referenceImages}.
            </p>
          </div>
        </section>

        {/* R3 product import: inline collapsible flow panel (never a modal,
            never on canvas) — step 1 URL/image, step 2 review, step 3 place. */}
        {importOpen && (
          <section className="product-import" aria-label="Добавление товара">
            <div className="product-import-head">
              <h3>Добавить товар</h3>
              <p className="hint">
                Ссылка или изображение → проверьте данные → разместите в квартире.
              </p>
            </div>
            <div className="product-import-step">
              <form
                className="product-import-url-row"
                onSubmit={(event) => {
                  event.preventDefault();
                  void runImportUrl();
                }}
              >
                <label className="product-import-url">
                  Ссылка на товар
                  <input
                    value={importUrl}
                    onChange={(event) => setImportUrl(event.target.value)}
                    placeholder="https://shop.example.com/product/…"
                    inputMode="url"
                  />
                </label>
                <button type="submit" disabled={importBusy || imageBusy || !importUrl.trim()}>
                  {importBusy ? "Импортируем…" : "Импортировать"}
                </button>
              </form>
              <label className="product-import-image">
                Из изображения
                <input
                  type="file"
                  accept="image/*"
                  disabled={imageBusy || importBusy}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void importFromImage(file);
                    event.target.value = "";
                  }}
                />
              </label>
            </div>
            {importError && <div className="error">{importError}</div>}

            {importCandidate && (
              <form
                className="product-import-review"
                onSubmit={(event) => {
                  event.preventDefault();
                  void placeImportedProduct();
                }}
              >
                <div className="product-import-review-head">
                  <div>
                    <h4>Проверьте данные товара</h4>
                    {importCandidate.source_url && (
                      <p className="hint">
                        Источник:{" "}
                        <a
                          href={importCandidate.source_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {importCandidate.source_url}
                        </a>
                      </p>
                    )}
                  </div>
                  {(importCandidate.preview_asset_id ?? importCandidate.source_asset_id) && (
                    <div className="product-import-preview">
                      <ImagePreview
                        variant="bounded"
                        maxHeight="140px"
                        src={api.assetUrl(
                          importCandidate.preview_asset_id ?? importCandidate.source_asset_id ?? ""
                        )}
                        alt="Превью товара"
                      />
                    </div>
                  )}
                </div>
                <div className="product-import-grid">
                  <label>
                    Название
                    <input
                      value={importForm.title}
                      onChange={(event) =>
                        setImportForm({ ...importForm, title: event.target.value })
                      }
                      className={fieldClass("title")}
                    />
                  </label>
                  <label>
                    Бренд
                    <input
                      value={importForm.brand}
                      onChange={(event) =>
                        setImportForm({ ...importForm, brand: event.target.value })
                      }
                      className={fieldClass("brand")}
                    />
                  </label>
                  <label>
                    Модель
                    <input
                      value={importForm.model}
                      onChange={(event) =>
                        setImportForm({ ...importForm, model: event.target.value })
                      }
                      className={fieldClass("model")}
                    />
                  </label>
                  <label>
                    Цена
                    <input
                      value={importForm.price}
                      onChange={(event) =>
                        setImportForm({ ...importForm, price: event.target.value })
                      }
                      inputMode="decimal"
                      className={fieldClass("price")}
                    />
                  </label>
                  <label>
                    Валюта
                    <input
                      value={importForm.currency}
                      onChange={(event) =>
                        setImportForm({ ...importForm, currency: event.target.value })
                      }
                      placeholder="RUB"
                      className={fieldClass("currency")}
                    />
                  </label>
                  <label>
                    Материал
                    <input
                      value={importForm.material}
                      onChange={(event) =>
                        setImportForm({ ...importForm, material: event.target.value })
                      }
                      className={fieldClass("material")}
                    />
                  </label>
                  <label>
                    Цвет
                    <input
                      value={importForm.color}
                      onChange={(event) =>
                        setImportForm({ ...importForm, color: event.target.value })
                      }
                      className={fieldClass("color")}
                    />
                  </label>
                </div>
                <div className="product-import-grid">
                  <label>
                    Ширина (мм)
                    <input
                      type="number"
                      value={importForm.width_mm}
                      onChange={(event) =>
                        setImportForm({ ...importForm, width_mm: event.target.value })
                      }
                      className={fieldClass("width_mm")}
                    />
                  </label>
                  <label>
                    Глубина (мм)
                    <input
                      type="number"
                      value={importForm.depth_mm}
                      onChange={(event) =>
                        setImportForm({ ...importForm, depth_mm: event.target.value })
                      }
                      className={fieldClass("depth_mm")}
                    />
                  </label>
                  <label>
                    Высота (мм)
                    <input
                      type="number"
                      value={importForm.height_mm}
                      onChange={(event) =>
                        setImportForm({ ...importForm, height_mm: event.target.value })
                      }
                      className={fieldClass("height_mm")}
                    />
                  </label>
                </div>
                {missingHighlight.size > 0 && (
                  <p className="hint product-import-missing-hint">
                    Не хватает данных:{" "}
                    {[...missingHighlight]
                      .map((key) => IMPORT_FIELD_LABELS[key] ?? key)
                      .join(", ")}
                    . Габариты обязательны для размещения.
                  </p>
                )}
                <div className="product-import-actions">
                  <button
                    type="button"
                    className="secondary"
                    disabled={saveBusy}
                    onClick={() => void saveImportCandidate()}
                  >
                    {saveBusy ? "Сохраняем…" : "Сохранить"}
                  </button>
                  <button type="submit" disabled={placeBusy || !dimsValid(importForm)}>
                    {placeBusy ? "Размещаем…" : "Разместить в квартире"}
                  </button>
                </div>
                {saveError && <div className="error">{saveError}</div>}
                {placeError && <div className="error">{placeError}</div>}
              </form>
            )}
          </section>
        )}

        {/* R4 variants: inline title input for the create-from-current action
            (never a modal, never window.prompt). Success navigates to Results
            where the variant chip row lives. */}
        {variantOpen && (
          <form
            className="variant-create variant-create--page"
            onSubmit={(event) => {
              event.preventDefault();
              void createVariantFromCurrent();
            }}
          >
            <label className="variant-create-title">
              Название варианта
              <input
                value={variantTitle}
                onChange={(event) => setVariantTitle(event.target.value)}
                placeholder="Например: Вариант с тёмным полом"
                autoFocus
              />
            </label>
            <button type="submit" disabled={variantBusy || !variantTitle.trim()}>
              {variantBusy ? "Создаём…" : "Создать вариант"}
            </button>
            <p className="hint">
              Вариант скопирует текущую версию сцены. Управление вариантами — на
              странице «Результаты».
            </p>
            {variantError && <div className="error">{variantError}</div>}
          </form>
        )}

        <div className="design-main">
          {/* In 3D mode the SceneViewer owns the shared center surface. It may
              remount on mode switches — same semantics as the base layout. */}
          {mode === "3d" && (
            <section className="canvas-panel" ref={canvasRef}>
              {/* R7: a WebGL/context failure must not take the whole page
                  down — the boundary keeps plan/photo tooling usable. */}
              <SceneViewerErrorBoundary
                onRetry={() => setViewEpoch((value) => value + 1)}
              >
                <SceneViewer
                  key={viewEpoch}
                  scene={sceneForViewer}
                  projectId={projectId}
                  onChanged={onChanged}
                  selectedId={selectedEntityId}
                  onSelectEntity={setSelectedEntityId}
                  onRequestReplace={(id) => {
                    setReplaceTargetId(id);
                    openAdvanced();
                  }}
                />
              </SceneViewerErrorBoundary>
            </section>
          )}

          {/* PhotoEditPanel stays mounted across mode switches so its local edit
              state (photo, prompt, region, pending job, iteration, base override)
              survives toggling to 3D and back. Hidden via CSS in 3D mode. In
              photo mode it spans the whole main row and its internal CSS grid
              puts the stage in the shared center surface position and the
              controls into the rail position. */}
          <div className={mode === "photo" ? "design-pane" : "design-pane pane-hidden"}>
            <PhotoEditPanel
              projectId={projectId}
              revision={revision}
              assets={assets}
              jobs={jobs}
              generations={generations}
              onChanged={onChanged}
            />
          </div>

          {/* Contextual rail (#154), 3D mode only — the photo mode fills this
              region with the photo controls inside the design pane. */}
          {mode === "3d" && (
            <aside className="design-rail" aria-label="Контекстные инструменты сцены">
              <div className={"camera-readiness " + readiness.state}>
                <span>
                  {readiness.label}
                  {readiness.detail ? ` · ${readiness.detail}` : ""}
                </span>
                <button type="button" className="secondary" onClick={openAdvanced}>
                  Настроить камеру
                </button>
              </div>

              {/* R7 (#187): two-stage render flow — a draft (eevee) render
                  first; the final photoreal render unlocks once the draft
                  succeeded. Estimated cameras render drafts only, with the
                  «приблизительный ракурс» caveat. */}
              <section className="design-render" aria-label="Рендер сцены">
                <div className="design-render-actions">
                  {renderReadiness.state === "estimated" && (
                    <span
                      className="mapping-chip approx"
                      title={renderReadiness.warning ?? undefined}
                    >
                      приблизительный ракурс
                    </span>
                  )}
                  <button
                    type="button"
                    className="primary"
                    disabled={renderButtonDisabled}
                    onClick={() => void startRender(draftReady ? "final" : "draft")}
                  >
                    {renderButtonLabel}
                  </button>
                </div>
                {!renderReadiness.cameraId && (
                  <p className="hint">{renderReadiness.warning}</p>
                )}
                {renderReadiness.state === "estimated" && draftReady && (
                  <p className="hint">{renderReadiness.warning}</p>
                )}
                {renderInfo && <p className="hint">{renderInfo}</p>}
                 {renderError && <div className="error">{renderError}</div>}
                 {latestRenders.length > 0 && (
                   <ul className="design-render-list">
                     {latestRenders.map((item) => {
                       // Stage lives in the persisted manifest_json — the
                       // RenderRecord wire shape has no top-level stage
                       // (acceptance finding: every chip showed «Финальный»).
                       const stage = item.manifest?.stage;
                       return (
                         <li key={item.id}>
                           <span
                             className={
                               "render-stage-chip " +
                               (stage === "draft" ? "draft" : "final")
                             }
                           >
                             {renderStageLabel(stage)}
                           </span>
                           <span className="muted">
                             {new Date(item.created_at).toLocaleString("ru-RU")}
                           </span>
                         </li>
                       );
                     })}
                   </ul>
                 )}
              </section>

              {/* R10 (#186): appearance of the shell surfaces — placed BEFORE
                  the selected-object card so it is visible on selection at
                  1280×800 without rail scrolling. Walls get the «Акцент» color
                  presets plus a custom color; floor/ceiling get the «Отделка»
                  finishes. One click = one set_color command = one scene
                  revision, undoable on «Результаты». The material lock
                  disables every control (the backend would reject the command
                  with 409). */}
              {selectedEntity && isAppearanceEditableSurface(selectedEntity) && (
                <section className="design-surface" aria-label="Внешний вид поверхности">
                  <h3>Внешний вид</h3>
                  {selectedEntity.kind === "wall" && (
                    <>
                      <div className="design-surface-group">
                        <p className="design-surface-title">Акцент</p>
                        <div
                          className="design-surface-swatches"
                          role="group"
                          aria-label="Цвет стены"
                        >
                          {SURFACE_COLOR_PRESETS.map((preset) => (
                            <button
                              key={preset.hex}
                              type="button"
                              className={
                                "design-surface-swatch" +
                                (surfaceColor === preset.hex ? " active" : "")
                              }
                              style={{ backgroundColor: preset.hex }}
                              aria-label={preset.label}
                              aria-pressed={surfaceColor === preset.hex}
                              title={preset.label}
                              disabled={surfaceBusy || surfaceLocked}
                              onClick={() => void applySurfaceColor(preset.hex)}
                            />
                          ))}
                        </div>
                      </div>
                      <div className="design-surface-group">
                        <p className="design-surface-title">Свой цвет</p>
                        <div className="design-surface-custom">
                          <input
                            key={selectedEntity.id}
                            ref={customColorRef}
                            type="color"
                            defaultValue={surfaceColor ?? SURFACE_COLOR_PRESETS[0].hex}
                            aria-label="Свой цвет для стены"
                            disabled={surfaceBusy || surfaceLocked}
                          />
                          <button
                            type="button"
                            className="secondary design-surface-apply"
                            disabled={surfaceBusy || surfaceLocked}
                            onClick={() =>
                              void applySurfaceColor(customColorRef.current?.value ?? "")
                            }
                          >
                            {surfaceBusy ? "Применяём…" : "Применить"}
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                  {(selectedEntity.kind === "floor" ||
                    selectedEntity.kind === "ceiling") && (
                    <div className="design-surface-group">
                      <p className="design-surface-title">Отделка</p>
                      <div
                        className="design-surface-swatches"
                        role="group"
                        aria-label={
                          selectedEntity.kind === "floor"
                            ? "Отделка пола"
                            : "Отделка потолка"
                        }
                      >
                        {SURFACE_FINISH_PRESETS.map((preset) => (
                          <button
                            key={preset.material_ref}
                            type="button"
                            className={
                              "design-surface-swatch" +
                              (surfaceColor === preset.hex ? " active" : "")
                            }
                            style={{ backgroundColor: preset.hex }}
                            aria-label={preset.label}
                            aria-pressed={surfaceColor === preset.hex}
                            title={preset.label}
                            disabled={surfaceBusy || surfaceLocked}
                            onClick={() => void applySurfaceFinish(preset)}
                          />
                        ))}
                      </div>
                    </div>
                  )}
                  {surfaceLocked && (
                    <p className="hint design-surface-lock-hint">
                      Цвет закреплён блокировкой «Материал/цвет» — снимите её
                      в карточке выбранного объекта ниже.
                    </p>
                  )}
                  {surfaceConflict && (
                    <p className="hint design-surface-conflict">
                      {DESIGN_CONFLICT_HINT}
                    </p>
                  )}
                  {surfaceError && <div className="error">{surfaceError}</div>}
                  {surfaceInfo && (
                    <p className="hint design-surface-info">
                      {surfaceInfo}{" "}
                      <button
                        type="button"
                        className="design-surface-link"
                        onClick={() => onNavigate("results")}
                      >
                        Результаты
                      </button>
                    </p>
                  )}
                </section>
              )}

              {selectedEntity ? (
                <section className="design-selected" aria-label="Выбранный объект">
                  <h3>Выбранный объект</h3>
                  <p className="design-selected-name">
                    {selectedEntity.display_name ?? selectedEntity.id}
                  </p>
                  <p className="hint">
                    <LayerBadge state={entityState(selectedEntity)} />{" "}
                    {KIND_LABELS[selectedEntity.kind] ?? selectedEntity.kind}
                  </p>
                  {/* R1: move the selected object between the as-is /
                      structure / design layers via the set_state command. */}
                  <label className="design-state-select">
                    Слой
                    <select
                      value={entityState(selectedEntity)}
                      disabled={stateBusy}
                      onChange={(event) =>
                        void applyState(selectedEntity.id, event.target.value as EntityState)
                      }
                    >
                      {ENTITY_STATES.map((state) => (
                        <option key={state} value={state}>
                          {ENTITY_STATE_LABELS[state]}
                        </option>
                      ))}
                    </select>
                  </label>
                  {stateError && <div className="error">{stateError}</div>}
                  {/* R2: design intent for the selected object — the UI
                      mirror of the backend set_intent command and its guard
                      matrix. Structural entities (Слой: Structure) may only
                      be kept. */}
                  <div className="design-intent-row" role="group" aria-label="Намерение по объекту">
                    {INTENT_ORDER.map((intent) => {
                      const active = entityIntent(selectedEntity) === intent;
                      return (
                        <button
                          key={intent}
                          type="button"
                          className={"design-intent-toggle" + (active ? " active" : "")}
                          aria-pressed={active}
                          disabled={
                            intentBusy ||
                            (intent !== "keep" &&
                              intentActionBlocked(selectedEntity, intent))
                          }
                          onClick={() => void applyIntent(intent)}
                        >
                          {INTENT_LABELS[intent]}
                        </button>
                      );
                    })}
                  </div>
                  {entityIntent(selectedEntity) !== null && (
                    <button
                      type="button"
                      className="secondary design-intent-clear"
                      disabled={intentBusy}
                      onClick={() => void applyIntent(null)}
                    >
                      Сбросить
                    </button>
                  )}
                  {intentError && <div className="error">{intentError}</div>}
                  {intentConflict && (
                    <p className="hint design-intent-conflict">
                      {DESIGN_CONFLICT_HINT}
                    </p>
                  )}
                  {/* R2: per-entity locks via set_locks; each checkbox sends
                      only its own key (partial merge on the backend). */}
                  <details className="design-intent-locks">
                    <summary>Блокировки</summary>
                    <label className="design-intent-lock">
                      <input
                        type="checkbox"
                        checked={selectedEntity.locks?.existence ?? false}
                        disabled={intentBusy}
                        onChange={(event) =>
                          void applyLock("existence", event.target.checked)
                        }
                      />
                      Должен остаться
                    </label>
                    <label className="design-intent-lock">
                      <input
                        type="checkbox"
                        checked={selectedEntity.locks?.transform ?? false}
                        disabled={intentBusy}
                        onChange={(event) =>
                          void applyLock("transform", event.target.checked)
                        }
                      />
                      Положение
                    </label>
                    <label className="design-intent-lock">
                      <input
                        type="checkbox"
                        checked={selectedEntity.locks?.geometry ?? false}
                        disabled={intentBusy}
                        onChange={(event) =>
                          void applyLock("geometry", event.target.checked)
                        }
                      />
                      Геометрия/размеры
                    </label>
                    <label className="design-intent-lock">
                      <input
                        type="checkbox"
                        checked={selectedEntity.locks?.material ?? false}
                        disabled={intentBusy}
                        onChange={(event) =>
                          void applyLock("material", event.target.checked)
                        }
                      />
                      Материал/цвет
                    </label>
                  </details>
                  <div className="design-selected-actions">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        setReplaceTargetId(selectedEntity.id);
                        openAdvanced();
                      }}
                    >
                      Заменить по референсу
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setSelectedEntityId(null)}
                    >
                      Снять выделение
                    </button>
                  </div>
                  {/* R1: attachments pinned to the selected entity. */}
                  <AttachmentSection
                    projectId={projectId}
                    targetType="entity"
                    targetId={selectedEntity.id}
                    scene={revision.scene}
                  />
                  <p className="hint">Перетащите объект в сцене, чтобы разместить.</p>
                </section>
              ) : (
                <p className="hint">
                  Выберите объект в сцене — действия появятся здесь.
                </p>
              )}

              {/* R2 design check: compact scene-level card in the rail (never
                  on canvas). Manual run only — drags and commits never
                  refresh the report. */}
              <details className="design-check">
                <summary>
                  <span className="design-check-title">Проверка дизайна</span>
                  {checkReport && (
                    <span className="design-check-summary">
                      {summaryLine(checkReport)}
                    </span>
                  )}
                  {checkReport && (
                    <span className="design-check-counts">
                      {(["error", "warning", "info"] as const).map((severity) => (
                        <span
                          key={severity}
                          className={`design-check-count design-check-count--${severity}`}
                        >
                          {checkReport.summary[severity]}
                        </span>
                      ))}
                    </span>
                  )}
                </summary>
                {checkTopResults.length > 0 && (
                  <ul className="design-check-list">
                    {checkTopResults.map((result, index) => (
                      <li
                        key={`${result.rule_id}-${index}`}
                        className={`design-check-item design-check-item--${result.severity}`}
                      >
                        <span className="design-check-rule">
                          {ruleLabel(result.rule_id)}
                        </span>
                        {(result.measured_mm != null ||
                          result.expected_min_mm != null) && (
                          <span className="design-check-mm">
                            {result.measured_mm != null
                              ? `${result.measured_mm} мм`
                              : "—"}
                            {result.expected_min_mm != null
                              ? ` · мин. ${result.expected_min_mm} мм`
                              : ""}
                          </span>
                        )}
                        {result.entity_ids.length > 0 && (
                          <details className="disclose-inline">
                            <summary>Дополнительно</summary>
                            <span className="design-check-ids">
                              {result.entity_ids.map((id) => (
                                <code key={id} className="design-check-id">
                                  {id}
                                </code>
                              ))}
                            </span>
                          </details>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {checkError && <div className="error">{checkError}</div>}
                <button
                  type="button"
                  className="secondary design-check-run"
                  disabled={checkBusy}
                  onClick={() => void runDesignCheck()}
                >
                  {checkBusy ? "Проверяем…" : "Запустить проверку"}
                </button>
              </details>
            </aside>
          )}
        </div>

        {/* Advanced strip (#154): collapsed by default in both modes, so the
            camera/twin/replacement panels keep their state across mode
            switches (they are never unmounted). */}
        <details className="design-advanced" ref={advancedRef}>
          <summary>Дополнительно</summary>
          <section className="dashboard-grid">
            <CameraPanel
              projectId={projectId}
              revision={revision}
              assets={assets}
              onChanged={onChanged}
            />

            <TwinDesignPanel
              projectId={projectId}
              revision={revision}
              jobs={jobs}
              onChanged={onChanged}
              onEntityAdded={(id) => {
                setSelectedEntityId(id);
                closeAdvanced();
              }}
            />

            <ReplacementPanel
              projectId={projectId}
              revision={revision}
              assets={assets}
              onChanged={onChanged}
              initialTargetId={replaceTargetId}
            />
          </section>
        </details>

        {/* R9 (#197): mood bar — small editorial selector near the composer.
            Presets mirror the BE registry (moodSelect.ts); the choice persists
            per project and feeds only the concept CTA (label above), never
            the scene structure. */}
        <section className="mood-bar" aria-label="Настроение эскизов концепта">
          <div className="mood-bar-row">
            <label className="mood-bar-label">
              Настроение
              <select
                value={moodId}
                onChange={(event) => selectMood(event.target.value)}
              >
                {MOOD_PRESETS.map((preset) => (
                  <option key={preset.id} value={preset.id} title={preset.hint}>
                    {preset.title}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="secondary"
              disabled={conceptBusy || conceptJobId !== null || !conceptBaseAssetId}
              onClick={() => void runConceptSketch()}
            >
              {conceptBusy || conceptJobId
                ? "Готовим эскиз…"
                : "Сделать эскиз концепта"}
            </button>
            <p className="hint mood-bar-scope">{MOOD_SCOPE_HINT}</p>
          </div>
          {!conceptBaseAssetId && <p className="hint">{CONCEPT_NEEDS_RENDER_HINT}</p>}
          {conceptStatus && <p className="muted">Эскиз концепта: {conceptStatus}</p>}
          {conceptError && <div className="error">{conceptError}</div>}
          {conceptInfo && <p className="hint">{conceptInfo}</p>}
          {conceptResultAssetId && (
            <figure className="mood-result">
              <ImagePreview
                variant="bounded"
                maxHeight="320px"
                caption={
                  conceptMoodTitle
                    ? `${CONCEPT_GENERATED_LABEL} · ${conceptMoodTitle}`
                    : CONCEPT_GENERATED_LABEL
                }
                src={api.assetUrl(conceptResultAssetId)}
                alt={
                  conceptMoodTitle
                    ? `${CONCEPT_GENERATED_LABEL}: ${conceptMoodTitle}`
                    : CONCEPT_GENERATED_LABEL
                }
                skeleton
              />
            </figure>
          )}
        </section>

        {/* Persistent composer (#154): reachable in both modes; instruction
            state stays app-level (App.tsx props are unchanged). R10 (#186):
            the submit carries the selection context of the entity picked at
            submit time (undefined = nothing selected → body stays {text}). */}
        <form
          className="instruction-bar design-composer"
          aria-label="AI-инструкция к сцене"
          onSubmit={(event) =>
            onSubmitInstruction(event, buildSelectionContext(selectedEntity))
          }
        >
          <input
            value={instruction}
            onChange={(event) => onInstructionChange(event.target.value)}
            placeholder="Например: сделай диван бежевым и убери стол"
            aria-label="Описание изменения через AI"
            disabled={!revision}
          />
          <button disabled={!revision || !instruction.trim()}>Применить через AI</button>
        </form>
      </div>
    </>
  );
}
