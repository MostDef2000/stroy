import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  api,
  type BudgetReport,
  type Generation,
  type Job,
  type PricedBudgetItem,
  type RenderRecord,
  type RevisionSummary,
  type SceneVariant,
  type SceneVariantStatus,
  type ValidationReport,
  type VariantDiff
} from "./api";
import { ImageLightbox } from "./ImageLightbox";
import { ImagePreview } from "./ImagePreview";
import { BRIEF_SHARE_HINT, statusLabel } from "./copy";
import {
  CHECK_SEVERITIES,
  groupResults,
  ruleLabel,
  summaryLine
} from "./sceneValidation";
import { apiErrorText, renderRgbAssetId } from "./twinDesign";
import {
  BUDGET_KIND_LABELS,
  budgetDeltaLabel,
  budgetFormat,
  budgetKindLabel,
  canTransition,
  DEFAULT_BUDGET_CURRENCY,
  diffSummary,
  formatMoney,
  incompleteLabel,
  lineageRevisions,
  STATUS_LABELS,
  visibleVariants
} from "./sceneVariants";
import {
  buildTimelineEntries,
  comparePairFor,
  groupEntriesByDay
} from "./resultsTimeline";
import type { TimelineAction, TimelineResult } from "./resultsTimeline";
import {
  defaultSelectionId,
  resolveSelection,
  revisionSelectionId
} from "./resultsSelection";
import type { SelectionDetail } from "./resultsSelection";

// Results master-detail workspace (#152): the history list lives on the left
// (internally scrollable), the selected entry's preview/details/compare and
// the restore/re-render actions live on the right. #101 semantics unchanged.

// Selection resolved against the app's full api types (camera_id etc.).
type ResultsSelection = SelectionDetail<Generation, RevisionSummary>;

// Group titles for the full validation list (severity order fixed by
// CHECK_SEVERITIES).
const CHECK_GROUP_TITLES: Record<string, string> = {
  error: "Ошибки",
  warning: "Предупреждения",
  info: "Замечания"
};

// R2 design check for the selected revision: the latest stored report is
// fetched on selection (404 → «Отчёта нет» + manual compute button); when a
// report exists the summary plus the FULL grouped list is shown (not the
// rail's top-3 cut).
function ValidationSection({
  projectId,
  revisionId
}: {
  projectId: string;
  revisionId: string;
}) {
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(true);
  const [computing, setComputing] = useState(false);
  const [error, setError] = useState("");

  // Refetch when the selection moves to another revision. A cancelled flag
  // guards against out-of-order resolutions while switching entries.
  useEffect(() => {
    let cancelled = false;
    setReport(null);
    setLoaded(false);
    setError("");
    setBusy(true);
    api
      .latestValidation(projectId, revisionId)
      .then((latest) => {
        if (cancelled) return;
        setReport(latest);
        setLoaded(true);
      })
      .catch((reason) => {
        if (cancelled) return;
        setError(apiErrorText(reason));
        setLoaded(true);
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, revisionId]);

  async function runCheck() {
    if (computing) return;
    setComputing(true);
    setError("");
    try {
      setReport(
        await api.validateScene(projectId, { scene_revision_id: revisionId })
      );
    } catch (reason) {
      setError(apiErrorText(reason));
    } finally {
      setComputing(false);
    }
  }

  const grouped = report ? groupResults(report.results) : null;

  return (
    <details className="design-check design-check--detail">
      <summary>
        <span className="design-check-title">Проверка дизайна</span>
        {report && (
          <span className="design-check-summary">{summaryLine(report)}</span>
        )}
        {report && (
          <span className="design-check-counts">
            {(["error", "warning", "info"] as const).map((severity) => (
              <span
                key={severity}
                className={`design-check-count design-check-count--${severity}`}
              >
                {report.summary[severity]}
              </span>
            ))}
          </span>
        )}
      </summary>
      {busy && <p className="muted">Загрузка отчёта…</p>}
      {error && <div className="error">{error}</div>}
      {loaded && !busy && !report && !error && (
        <div className="design-check-empty">
          <p className="muted">Отчёта нет</p>
          <button
            type="button"
            className="secondary"
            disabled={computing}
            onClick={() => void runCheck()}
          >
            {computing ? "Проверяем…" : "Запустить проверку"}
          </button>
        </div>
      )}
      {report && grouped && (
        <div className="design-check-groups">
          {report.results.length === 0 && (
            <p className="muted">Проблем не найдено.</p>
          )}
          {CHECK_SEVERITIES.map((severity) =>
            grouped[severity].length > 0 ? (
              <section
                key={severity}
                className={`design-check-group design-check-group--${severity}`}
              >
                <h4>
                  {CHECK_GROUP_TITLES[severity]} · {grouped[severity].length}
                </h4>
                {grouped[severity].map((result, index) => (
                  <div
                    key={`${result.rule_id}-${index}`}
                    className={`design-check-item design-check-item--${result.severity}`}
                  >
                    <span className="design-check-rule">
                      {ruleLabel(result.rule_id)}
                    </span>
                    <p className="design-check-explanation">
                      {result.explanation}
                    </p>
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
                  </div>
                ))}
              </section>
            ) : null
          )}
        </div>
      )}
    </details>
  );
}

function outputAsset(generation: Generation | undefined) {
  return generation?.manifest.output_asset_ids?.[0] ?? null;
}

// ---------------------------------------------------------------------------
// R4 scene variants (#feat/r4-variants-budget)
//
// MINIMUM-VIABLE boundary (decision reported to the owner): the variant detail
// pane supports restore-to-revision, budget, validation and renders only.
// Variant-scoped entity EDITS (POST /variants/{id}/revisions with a
// DesignCommand envelope) stay canonical-only in R4 FE — api.applyVariantCommand
// and sceneVariants.buildVariantCommandEnvelope ship unused-but-ready for a
// later pass. The canonical /scene head is never redirected from here (owner
// decision: the canonical scene stays separate from variant heads).
// ---------------------------------------------------------------------------

/** Chip letters A, B, C… by creation order (wraps past Z for long lists). */
function variantLetter(index: number): string {
  return String.fromCharCode(65 + (index % 26));
}

function VariantStatusDot({ status }: { status: SceneVariantStatus }) {
  return (
    <span
      className={`variant-dot variant-dot--${status}`}
      title={STATUS_LABELS[status]}
      aria-label={STATUS_LABELS[status]}
    />
  );
}

// Budget card for one variant: totals (known / contingency / grand), the
// «неполные данные» list when the report is incomplete, items grouped by kind
// and an inline «+ статья» add form (label / amount / currency defaulting to
// RUB / kind select — owner decision: RUB default).
function VariantBudgetCard({
  projectId,
  variantId
}: {
  projectId: string;
  variantId: string;
}) {
  const [report, setReport] = useState<BudgetReport | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState("");
  const [label, setLabel] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(DEFAULT_BUDGET_CURRENCY);
  // v1 boundary (peer review): manual expense items only. "material" items
  // require a takeoff in metadata and "candidate" items require a
  // product_candidate_id — both arrive through the design/import flow, so
  // sending them from this form would 422. The form therefore always sends
  // kind="manual".
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);

  // Two-step delete confirmation times out after 5s, consistent with the
  // pane's other confirmable actions.
  useEffect(() => {
    if (!confirmRemoveId) return;
    const timer = setTimeout(() => setConfirmRemoveId(null), 5000);
    return () => clearTimeout(timer);
  }, [confirmRemoveId]);

  useEffect(() => {
    let cancelled = false;
    setReport(null);
    setLoaded(false);
    setError("");
    api
      .getBudgetReport(projectId, variantId)
      .then((result) => {
        if (cancelled) return;
        setReport(result);
        setLoaded(true);
      })
      .catch((reason) => {
        if (cancelled) return;
        setError(apiErrorText(reason));
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, variantId]);

  async function addItem(event: FormEvent) {
    event.preventDefault();
    if (addBusy || !label.trim()) return;
    setAddBusy(true);
    setAddError("");
    try {
      const trimmedAmount = amount.trim();
      let parsedAmount: number | undefined;
      if (trimmedAmount !== "") {
        parsedAmount = Number(trimmedAmount.replace(",", "."));
        if (!Number.isFinite(parsedAmount) || parsedAmount < 0) {
          setAddError("Сумма должна быть неотрицательным числом.");
          return;
        }
      }
      await api.addBudgetItem(projectId, variantId, {
        kind: "manual",
        label: label.trim(),
        amount: parsedAmount,
        currency: currency.trim() || DEFAULT_BUDGET_CURRENCY
      });
      setLabel("");
      setAmount("");
      setReport(await api.getBudgetReport(projectId, variantId));
    } catch (reason) {
      setAddError(apiErrorText(reason));
    } finally {
      setAddBusy(false);
    }
  }

  async function removeItem(itemId: string) {
    if (removeBusy) return;
    setRemoveBusy(true);
    try {
      await api.deleteBudgetItem(projectId, variantId, itemId);
      setConfirmRemoveId(null);
      setReport(await api.getBudgetReport(projectId, variantId));
    } catch (reason) {
      setError(apiErrorText(reason));
    } finally {
      setRemoveBusy(false);
    }
  }

  // Items grouped by kind in the shipped label order first, then any custom
  // kind the backend knows that the FE label map does not.
  const groups = useMemo(() => {
    if (!report) return [];
    const map = new Map<string, PricedBudgetItem[]>();
    for (const item of report.items) {
      const list = map.get(item.kind) ?? [];
      list.push(item);
      map.set(item.kind, list);
    }
    const orderedKinds = [...Object.keys(BUDGET_KIND_LABELS), ...map.keys()];
    const kinds = [...new Set(orderedKinds)].filter((key) => map.has(key));
    return kinds.map((key) => ({ kind: key, items: map.get(key) ?? [] }));
  }, [report]);

  return (
    <details className="budget-card" open>
      <summary>
        <span className="design-check-title">Смета варианта</span>
        {report && (
          <span className="design-check-summary">{budgetFormat(report)}</span>
        )}
        {report?.incomplete && (
          <span className="budget-incomplete-tag">неполные данные</span>
        )}
      </summary>
      {!loaded && <p className="muted">Загрузка сметы…</p>}
      {error && <div className="error">{error}</div>}
      {report && (
        <>
          <div className="budget-totals">
            <span>
              Известно:{" "}
              <strong>
                {formatMoney(
                  report.totals.known,
                  report.currency ?? DEFAULT_BUDGET_CURRENCY
                )}
              </strong>
            </span>
            <span>
              Резерв:{" "}
              <strong>
                {formatMoney(
                  report.totals.contingency,
                  report.currency ?? DEFAULT_BUDGET_CURRENCY
                )}
              </strong>
            </span>
            <span>
              Итого: <strong>{budgetFormat(report)}</strong>
            </span>
          </div>
          {report.incomplete && (
            <p className="budget-unknowns">
              Неполные данные: {incompleteLabel(report.unknowns)}
            </p>
          )}
          {groups.map((group) => (
            <section className="budget-group" key={group.kind}>
              <h4>
                {budgetKindLabel(group.kind)} · {group.items.length}
              </h4>
              {group.items.map((item) => (
                <div className="budget-item" key={item.id}>
                  <span className="budget-item-label">{item.label}</span>
                  {item.unknowns.length > 0 && (
                    <span className="budget-item-unknowns">
                      {incompleteLabel(item.unknowns)}
                    </span>
                  )}
                  <span className="budget-item-amount">
                    {item.contribution != null
                      ? formatMoney(
                          item.contribution,
                          item.effective_currency ??
                            report.currency ??
                            DEFAULT_BUDGET_CURRENCY
                        )
                      : "—"}
                  </span>
                  <button
                    type="button"
                    className={`budget-item-remove${
                      confirmRemoveId === item.id ? " confirming" : ""
                    }`}
                    aria-label={
                      confirmRemoveId === item.id
                        ? `Подтвердить удаление: ${item.label}`
                        : `Удалить статью: ${item.label}`
                    }
                    disabled={removeBusy}
                    title={confirmRemoveId === item.id ? "Подтвердить удаление?" : "Удалить"}
                    onClick={() =>
                      confirmRemoveId === item.id
                        ? void removeItem(item.id)
                        : setConfirmRemoveId(item.id)
                    }
                  >
                    {confirmRemoveId === item.id ? "?" : "×"}
                  </button>
                </div>
              ))}
            </section>
          ))}
          {report.items.length === 0 && (
            <p className="muted">Статей пока нет.</p>
          )}
          {adding ? (
            <form className="budget-add" onSubmit={(event) => void addItem(event)}>
              <label>
                Статья
                <input
                  value={label}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="Например: Диван"
                />
              </label>
              <label>
                Сумма
                <input
                  value={amount}
                  onChange={(event) => setAmount(event.target.value)}
                  inputMode="decimal"
                  placeholder="0"
                />
              </label>
              <label>
                Валюта
                <input
                  value={currency}
                  onChange={(event) => setCurrency(event.target.value)}
                />
              </label>
              <div className="budget-add-actions">
                <button type="submit" disabled={addBusy || !label.trim()}>
                  {addBusy ? "Добавляем…" : "Добавить"}
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setAdding(false)}
                >
                  Отмена
                </button>
              </div>
              <p className="hint budget-add-hint">
                Тип: {budgetKindLabel("manual")}. Материалы и товары приходят из
                дизайн-потока и импорта — вручную добавляются только статьи
                произвольных расходов.
              </p>
              {addError && <div className="error">{addError}</div>}
            </form>
          ) : (
            <button
              type="button"
              className="secondary budget-add-trigger"
              onClick={() => setAdding(true)}
            >
              + статья
            </button>
          )}
        </>
      )}
    </details>
  );
}

// Variant detail pane: head revision info, the design-check report for the
// variant head (ValidationSection reuse), the budget card, the variant-linked
// renders and the per-revision restore actions along the variant lineage.
function VariantDetail({
  projectId,
  variant,
  revisions,
  onVariantsChanged,
  onForget,
  openLightbox
}: {
  projectId: string;
  variant: SceneVariant;
  revisions: RevisionSummary[];
  onVariantsChanged: () => Promise<void>;
  onForget: () => void;
  openLightbox: (src: string, alt: string, title: string, trigger: HTMLElement) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmRestoreId, setConfirmRestoreId] = useState<string | null>(null);

  // Two-step confirmations (delete + per-revision restore) time out after 5s,
  // same as the canonical timeline's restore confirm.
  useEffect(() => {
    if (!confirmDelete && !confirmRestoreId) return;
    const timer = setTimeout(() => {
      setConfirmDelete(false);
      setConfirmRestoreId(null);
    }, 5000);
    return () => clearTimeout(timer);
  }, [confirmDelete, confirmRestoreId]);

  // Post-restore bridge: the restored head revision reaches the revisions
  // prop only with the next poll (≤5s), which would flash the versions list
  // empty. The restore response carries the new revision, so it is held here
  // and synthesized into the lineage until the poll delivers it.
  const [pendingRestore, setPendingRestore] = useState<{
    revisionId: string;
    parentId: string | null;
    at: number;
  } | null>(null);

  useEffect(() => {
    if (
      pendingRestore &&
      revisions.some((node) => node.revision_id === pendingRestore.revisionId)
    ) {
      setPendingRestore(null);
    }
  }, [revisions, pendingRestore]);

  // Variant lineage: the project revisions list carries variant revisions too,
  // so walk the parent chain head → base client-side (no lineage endpoint).
  const lineage = useMemo(() => {
    const walked = lineageRevisions(
      variant.head_scene_revision_id,
      variant.base_scene_revision_id,
      revisions
    );
    if (
      pendingRestore &&
      !revisions.some((node) => node.revision_id === pendingRestore.revisionId)
    ) {
      const synthesized: RevisionSummary = {
        revision_id: pendingRestore.revisionId,
        parent_revision_id: pendingRestore.parentId,
        command_id: null,
        content_hash: "",
        created_at: new Date(pendingRestore.at).toISOString()
      };
      return [
        synthesized,
        ...lineageRevisions(
          pendingRestore.parentId ?? variant.base_scene_revision_id,
          variant.base_scene_revision_id,
          revisions
        )
      ];
    }
    return walked;
  }, [
    variant.head_scene_revision_id,
    variant.base_scene_revision_id,
    revisions,
    pendingRestore
  ]);

  // Variant-linked renders only; legacy NULL-variant renders stay in the
  // canonical Results section as today.
  const [renders, setRenders] = useState<RenderRecord[] | null>(null);
  const [rendersError, setRendersError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setRenders(null);
    setRendersError("");
    api
      .listRenders(projectId)
      .then((all) => {
        if (cancelled) return;
        setRenders(all.filter((render) => render.variant_id === variant.id));
      })
      .catch((reason) => {
        if (cancelled) return;
        setRendersError(apiErrorText(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, variant.id]);

  async function patchStatus(next: SceneVariantStatus) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.patchVariant(projectId, variant.id, { status: next });
      await onVariantsChanged();
    } catch (reason) {
      setError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function fork() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.forkVariant(projectId, variant.id, {});
      await onVariantsChanged();
    } catch (reason) {
      setError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteVariant(projectId, variant.id);
      onForget();
      await onVariantsChanged();
    } catch (reason) {
      setError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function restoreTo(revisionId: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.restoreVariantRevision(projectId, variant.id, {
        target_revision_id: revisionId,
        expected_head_revision_id: variant.head_scene_revision_id
      });
      // Seed the new head locally so the versions list does not flash empty
      // until the next poll delivers the restored revision (≤5s).
      setPendingRestore({
        revisionId: response.revision_id,
        parentId: response.parent_revision_id,
        at: Date.now()
      });
      setConfirmRestoreId(null);
      await onVariantsChanged();
    } catch (reason) {
      setError(apiErrorText(reason));
    } finally {
      setBusy(false);
    }
  }

  function shortId(revisionId: string): string {
    return revisionId.length > 8 ? revisionId.slice(0, 8) : revisionId;
  }

  return (
    <>
      <div className="rt-detail-head">
        <div className="rt-detail-title">
          <strong>{variant.title}</strong>
          <VariantStatusDot status={variant.status} />
          <span className="rt-time">
            {new Date(variant.updated_at).toLocaleString("ru-RU")}
          </span>
        </div>
        <details className="disclose-inline">
          <summary>Дополнительно</summary>
          <p className="hint">
            Голова варианта: {shortId(variant.head_scene_revision_id)} · база:{" "}
            {shortId(variant.base_scene_revision_id)}
          </p>
        </details>
        {error && <div className="error">{error}</div>}
        <div className="variant-actions">
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => void fork()}
          >
            Форк
          </button>
          {canTransition(variant.status, "shortlisted") && (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => void patchStatus("shortlisted")}
            >
              Шорт-лист
            </button>
          )}
          {canTransition(variant.status, "approved") && (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => void patchStatus("approved")}
            >
              Утвердить
            </button>
          )}
          {canTransition(variant.status, "archived") && (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => void patchStatus("archived")}
            >
              В архив
            </button>
          )}
          <button
            type="button"
            className={confirmDelete ? "rt-confirm" : "secondary"}
            disabled={busy}
            onClick={() =>
              confirmDelete ? void remove() : setConfirmDelete(true)
            }
          >
            {confirmDelete ? "Подтвердить удаление?" : "Удалить"}
          </button>
        </div>
        {variant.status === "shortlisted" && (
          <p className="hint variant-demote-hint">
            Утверждение автоматически понижит текущий утверждённый вариант до
            шорт-листа (один утверждённый вариант на проект).
          </p>
        )}
      </div>

      {/* R2 reuse: the design-check report bound to the variant HEAD revision. */}
      <ValidationSection
        projectId={projectId}
        revisionId={variant.head_scene_revision_id}
      />

      <VariantBudgetCard projectId={projectId} variantId={variant.id} />

      <details className="variant-renders" open>
        <summary>
          Рендеры варианта{renders ? ` · ${renders.length}` : ""}
        </summary>
        {rendersError && <div className="error">{rendersError}</div>}
        {renders && renders.length === 0 && (
          <p className="muted">Для варианта пока нет рендеров.</p>
        )}
        {renders &&
          renders.map((render) => {
            const assetId = renderRgbAssetId(render.manifest);
            const title = "Рендер вида";
            return (
              <div className="variant-render-row" key={render.id}>
                {assetId ? (
                  <ImagePreview
                    variant="thumbnail"
                    aspectRatio="4/3"
                    src={api.assetUrl(assetId)}
                    alt={title}
                    expandable
                    onExpand={(trigger) =>
                      openLightbox(api.assetUrl(assetId), title, title, trigger)
                    }
                  />
                ) : (
                  <div className="compare-empty">нет изображения</div>
                )}
                <div className="rt-row-meta">
                  <span className="rt-row-label">{title}</span>
                  <span className="rt-time">
                    {new Date(render.created_at).toLocaleString("ru-RU")}
                  </span>
                </div>
                <details className="disclose-inline">
                  <summary>Дополнительно</summary>
                  <span className="hint">Камера: {render.camera_id}</span>
                </details>
              </div>
            );
          })}
      </details>

      <details className="variant-lineage" open>
        <summary>Версии варианта · {lineage.length}</summary>
        {lineage.map((node, index) => {
          const isHead = node.revision_id === variant.head_scene_revision_id;
          const isBase = index === lineage.length - 1;
          return (
            <div className="variant-lineage-row" key={node.revision_id}>
              <span className="rt-row-meta">
                <span className="rt-row-label">
                  {new Date(node.created_at).toLocaleString("ru-RU")}
                </span>
                {isHead && <span className="tag">голова</span>}
                {isBase && !isHead && <span className="tag">база</span>}
              </span>
              <details className="disclose-inline">
                <summary>Дополнительно</summary>
                <code>{node.revision_id}</code>
              </details>
              {!isHead && (
                <button
                  type="button"
                  className={
                    confirmRestoreId === node.revision_id
                      ? "rt-confirm"
                      : "secondary"
                  }
                  disabled={busy}
                  onClick={() =>
                    confirmRestoreId === node.revision_id
                      ? void restoreTo(node.revision_id)
                      : setConfirmRestoreId(node.revision_id)
                  }
                >
                  {confirmRestoreId === node.revision_id
                    ? "Подтвердить возврат?"
                    : "Вернуть"}
                </button>
              )}
            </div>
          );
        })}
      </details>
    </>
  );
}

// Comparison of two selected variants: structured collapsible diff sections
// (entities / materials / validation / budget Δ or «неполные данные» /
// render counts). Money is only compared when both sides are complete — the
// backend nulls the delta otherwise.
function VariantCompareView({
  projectId,
  leftId,
  rightId
}: {
  projectId: string;
  leftId: string;
  rightId: string;
}) {
  const [diff, setDiff] = useState<VariantDiff | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setDiff(null);
    setLoaded(false);
    setError("");
    api
      .compareVariants(projectId, leftId, rightId)
      .then((result) => {
        if (cancelled) return;
        setDiff(result);
        setLoaded(true);
      })
      .catch((reason) => {
        if (cancelled) return;
        setError(apiErrorText(reason));
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, leftId, rightId]);

  if (error) return <div className="error">{error}</div>;
  if (!loaded || !diff) return <p className="muted">Считаем различия…</p>;

  return (
    <div className="variant-compare">
      <div className="rt-detail-title">
        <strong>
          {diff.left.title} → {diff.right.title}
        </strong>
      </div>
      <p className="variant-compare-summary">{diffSummary(diff)}</p>

      <details className="variant-compare-section" open>
        <summary>
          Объекты · добавлено {diff.entities.added.length}, удалено{" "}
          {diff.entities.removed.length}, изменено {diff.entities.modified.length}
        </summary>
        <div className="variant-compare-list">
          {diff.entities.added.length > 0 && (
            <details className="disclose-inline">
              <summary>Дополнительно</summary>
              {diff.entities.added.map((id) => (
                <div key={id}>
                  <span className="tag">+ добавлен</span> <code>{id}</code>
                </div>
              ))}
            </details>
          )}
          {diff.entities.removed.length > 0 && (
            <details className="disclose-inline">
              <summary>Дополнительно</summary>
              {diff.entities.removed.map((id) => (
                <div key={id}>
                  <span className="tag">− удалён</span> <code>{id}</code>
                </div>
              ))}
            </details>
          )}
          {diff.entities.modified.length > 0 && (
            <details className="disclose-inline">
              <summary>Дополнительно</summary>
              {diff.entities.modified.map((change) => (
                <div key={change.id}>
                  <span className="tag">~ изменён</span> <code>{change.id}</code>
                  {change.changes.length > 0 && (
                    <span className="variant-compare-changes">
                      {" "}
                      ({change.changes.join(", ")})
                    </span>
                  )}
                </div>
              ))}
            </details>
          )}
          {diff.entities.added.length === 0 &&
            diff.entities.removed.length === 0 &&
            diff.entities.modified.length === 0 && (
              <p className="muted">Объекты не различаются.</p>
            )}
        </div>
      </details>

      <details className="variant-compare-section">
        <summary>
          Материалы · +{diff.materials.added.length} −
          {diff.materials.removed.length}
        </summary>
        <div className="variant-compare-list">
          {diff.materials.added.map((ref) => (
            <div key={ref}>
              <span className="tag">+ добавлен</span> <code>{ref}</code>
            </div>
          ))}
          {diff.materials.removed.map((ref) => (
            <div key={ref}>
              <span className="tag">− удалён</span> <code>{ref}</code>
            </div>
          ))}
          {diff.materials.added.length === 0 &&
            diff.materials.removed.length === 0 && (
              <p className="muted">Материалы не различаются.</p>
            )}
        </div>
      </details>

      <details className="variant-compare-section">
        <summary>
          Проверки · +{diff.validation.added.length} −
          {diff.validation.resolved.length}
        </summary>
        <div className="variant-compare-list">
          {diff.validation.added.map((warning, index) => (
            <div key={`added-${warning.rule_id}-${index}`}>
              <span className="tag">+ новое</span>{" "}
              {ruleLabel(warning.rule_id)}
              {warning.entity_ids.length > 0 && (
                <span className="variant-compare-changes">
                  {" "}
                  ({warning.entity_ids.length} объект(ов))
                </span>
              )}
            </div>
          ))}
          {diff.validation.resolved.map((warning, index) => (
            <div key={`resolved-${warning.rule_id}-${index}`}>
              <span className="tag">− решено</span>{" "}
              {ruleLabel(warning.rule_id)}
            </div>
          ))}
          {diff.validation.added.length === 0 &&
            diff.validation.resolved.length === 0 && (
              <p className="muted">Предупреждения не изменились.</p>
            )}
        </div>
      </details>

      <details className="variant-compare-section">
        <summary>
          Бюджет ·{" "}
          {budgetDeltaLabel(
            diff.budget,
            diff.budget.right.currency ?? diff.budget.left.currency
          )}
        </summary>
        <div className="variant-compare-list">
          <div>
            {diff.left.title}:{" "}
            {formatMoney(
              diff.budget.left.totals.grand_total,
              diff.budget.left.currency ?? DEFAULT_BUDGET_CURRENCY
            )}
            {diff.budget.left.incomplete && (
              <span className="budget-incomplete-tag"> неполные данные</span>
            )}
          </div>
          <div>
            {diff.right.title}:{" "}
            {formatMoney(
              diff.budget.right.totals.grand_total,
              diff.budget.right.currency ?? DEFAULT_BUDGET_CURRENCY
            )}
            {diff.budget.right.incomplete && (
              <span className="budget-incomplete-tag"> неполные данные</span>
            )}
          </div>
          {diff.budget.delta === null && (
            <p className="budget-unknowns">
              Неполные данные: дельта считается только по полным сметам обеих
              сторон.
            </p>
          )}
        </div>
      </details>

      <details className="variant-compare-section">
        <summary>
          Рендеры · {diff.renders.left.length} / {diff.renders.right.length}
        </summary>
        <div className="variant-compare-list">
          <div>
            {diff.left.title}: {diff.renders.left.length}
          </div>
          <div>
            {diff.right.title}: {diff.renders.right.length}
          </div>
        </div>
      </details>
    </div>
  );
}

function timeLabel(value: string) {
  return new Date(value).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit"
  });
}

function jobCommands(job: Job | undefined): Array<Record<string, unknown>> {
  const rawCommands = job?.result?.["commands"];
  return Array.isArray(rawCommands)
    ? (rawCommands as Array<Record<string, unknown>>)
    : [];
}

// Must match `--rt-compare-h` in styles.css: both before/after boxes get the
// same fixed bounded area, and the matching max-height keeps images contained
// (never cropped) inside it.
const COMPARE_MAX_HEIGHT = "min(48dvh, 440px)";

const RESTORE_WARNING =
  "Возврат откатит сцену к состоянию на выбранный момент; изменения после него будут отменены.";

export function DesignPanel({
  projectId,
  revisions,
  jobs,
  generations,
  currentRevisionId,
  cameraId,
  onRestore,
  onRerender,
  onShareWithDesigner
}: {
  projectId: string;
  revisions: RevisionSummary[];
  jobs: Job[];
  generations: Generation[];
  currentRevisionId: string | null;
  cameraId: string | null;
  onRestore: (revisionId: string) => Promise<void>;
  onRerender: (revisionId: string) => Promise<void>;
  /** R8 (#198): opens the designer brief for the given variant id. */
  onShareWithDesigner: (variantId: string) => void;
}) {
  // Selection is a stable string id (generation/job id, or `rev:`-prefixed
  // revision id) — the 5s poll replaces all objects, so selection must never
  // key by object identity.
  const [selectionId, setSelectionId] = useState<string | null>(null);
  const [detailMode, setDetailMode] = useState<"single" | "compare">("single");
  const [confirmRestoreId, setConfirmRestoreId] = useState<string | null>(null);
  // Shared lightbox (#150): primitives only — the app polls every 5s and
  // replaces object identities, so lightbox state must never hold objects.
  const [lightbox, setLightbox] = useState<{
    src: string;
    alt: string;
    title: string;
  } | null>(null);
  const lightboxTriggerRef = useRef<HTMLElement | null>(null);
  const selectedRowRef = useRef<HTMLButtonElement | null>(null);

  // R4 variants: fetched here (not via props) so the ResultsPage/App prop
  // contracts stay untouched; refreshed explicitly after every mutation.
  // include_archived=true fetches once — the «Показать архив» toggle filters
  // client-side via visibleVariants (archived are hidden by default).
  const [variants, setVariants] = useState<SceneVariant[]>([]);
  const [variantsLoaded, setVariantsLoaded] = useState(false);
  const [variantsError, setVariantsError] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [selectedVariantId, setSelectedVariantId] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<{
    left: string | null;
    right: string | null;
  }>({ left: null, right: null });
  const [variantCreateOpen, setVariantCreateOpen] = useState(false);
  const [variantTitle, setVariantTitle] = useState("");
  const [variantCreateBusy, setVariantCreateBusy] = useState(false);

  const refreshVariants = useCallback(async () => {
    try {
      setVariants(await api.listVariants(projectId, { include_archived: true }));
      setVariantsError("");
    } catch (reason) {
      setVariantsError(apiErrorText(reason));
    } finally {
      setVariantsLoaded(true);
    }
  }, [projectId]);

  useEffect(() => {
    void refreshVariants();
  }, [refreshVariants]);

  // A refresh (or a delete from another tab) prunes stale selection/compare
  // ids — state never holds objects, only ids (same rule as the timeline).
  useEffect(() => {
    setSelectedVariantId((id) =>
      id && variants.some((variant) => variant.id === id) ? id : null
    );
    setCompareIds((slots) => ({
      left:
        slots.left && variants.some((variant) => variant.id === slots.left)
          ? slots.left
          : null,
      right:
        slots.right && variants.some((variant) => variant.id === slots.right)
          ? slots.right
          : null
    }));
  }, [variants]);

  // Chip row: creation order (stable A/B/C letters by age), archived variants
  // excluded unless the toggle is on.
  const variantChips = useMemo(() => {
    const visible = visibleVariants(variants, showArchived);
    return [...visible].sort((a, b) => a.created_at.localeCompare(b.created_at));
  }, [variants, showArchived]);

  const selectedVariant =
    variants.find((variant) => variant.id === selectedVariantId) ?? null;
  const compareReady = compareIds.left !== null && compareIds.right !== null;

  // R8 (#198) «Поделиться с дизайнером»: the explicitly selected variant wins
  // (archived included — an archived variant stays exportable when the owner
  // points at it); otherwise the approved variant. The fallback matches ONLY
  // status==="approved": approval is the owner's act of sharing, and an
  // approved→archived variant must never be auto-picked (no auto-fallback).
  const approvedVariantId = useMemo(
    () => variants.find((variant) => variant.status === "approved")?.id ?? null,
    [variants]
  );
  const shareVariantId = selectedVariant?.id ?? approvedVariantId;

  // Chip click: select the variant and clear the compare selection (the
  // compare view replaces the detail pane only while both slots are filled).
  function handleChipClick(variantId: string) {
    setCompareIds({ left: null, right: null });
    setSelectedVariantId((current) => (current === variantId ? null : variantId));
  }

  // ⇄ toggles compare membership; with both slots full the oldest slot is
  // recycled (FIFO), so a third click always leaves exactly two selected.
  function handleCompareToggle(variantId: string) {
    setCompareIds((slots) => {
      if (slots.left === variantId || slots.right === variantId) {
        return {
          left: slots.left === variantId ? null : slots.left,
          right: slots.right === variantId ? null : slots.right
        };
      }
      if (!slots.left) return { ...slots, left: variantId };
      if (!slots.right) return { ...slots, right: variantId };
      return { left: slots.right, right: variantId };
    });
  }

  async function createVariant() {
    const title = variantTitle.trim();
    if (!title || variantCreateBusy) return;
    setVariantCreateBusy(true);
    setVariantsError("");
    try {
      const created = await api.createVariantFromCurrent(projectId, title);
      setVariantTitle("");
      setVariantCreateOpen(false);
      await refreshVariants();
      setSelectedVariantId(created.id);
    } catch (reason) {
      setVariantsError(apiErrorText(reason));
    } finally {
      setVariantCreateBusy(false);
    }
  }

  function openLightbox(src: string, alt: string, title: string, trigger: HTMLElement) {
    lightboxTriggerRef.current = trigger;
    setLightbox({ src, alt, title });
  }

  // Two-step restore confirmation times out after 5s of inactivity.
  useEffect(() => {
    if (!confirmRestoreId) return;
    const timer = setTimeout(() => setConfirmRestoreId(null), 5000);
    return () => clearTimeout(timer);
  }, [confirmRestoreId]);

  // Any click outside a restore button cancels the pending confirmation
  // (capture phase so it sees clicks before they bubble anywhere).
  useEffect(() => {
    if (!confirmRestoreId) return;
    function handleDocumentClick(event: MouseEvent) {
      if (!(event.target as HTMLElement).closest?.("[data-restore-button]")) {
        setConfirmRestoreId(null);
      }
    }
    document.addEventListener("click", handleDocumentClick, true);
    return () => document.removeEventListener("click", handleDocumentClick, true);
  }, [confirmRestoreId]);

  // A refreshed revision list or a changed current revision invalidates any
  // pending restore confirmation.
  useEffect(() => {
    setConfirmRestoreId(null);
  }, [revisions, currentRevisionId]);

  const currentRevision = revisions.find(
    (revision) => revision.revision_id === currentRevisionId
  );

  const entries = useMemo(
    () => buildTimelineEntries({ jobs, generations, currentRevisionId }),
    [jobs, generations, currentRevisionId]
  );
  const groups = useMemo(() => groupEntriesByDay(entries), [entries]);

  // Re-resolve the selection against the latest data on every change; fall
  // back to the newest entry (else newest revision) when the stored id is
  // unknown or nothing was selected yet.
  const selection = useMemo(() => {
    const resolved = resolveSelection({
      selectionId,
      entries,
      revisions,
      generations,
      currentRevisionId
    });
    if (resolved) return resolved;
    return resolveSelection({
      selectionId: defaultSelectionId(entries, revisions),
      entries,
      revisions,
      generations,
      currentRevisionId
    });
  }, [selectionId, entries, revisions, generations, currentRevisionId]);

  const selectedKey = selection
    ? selection.kind === "revision"
      ? revisionSelectionId(selection.revision.revision_id)
      : selection.entry.id
    : null;

  // Compare mode only makes sense for one selection; reset when it moves.
  useEffect(() => {
    setDetailMode("single");
  }, [selectedKey]);

  // Keep the selected master row visible when the selection moves
  // programmatically (fallback after a poll). Skip the initial mount.
  const lastScrolledKey = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedKey) return;
    if (lastScrolledKey.current === null) {
      lastScrolledKey.current = selectedKey;
      return;
    }
    if (lastScrolledKey.current === selectedKey) return;
    lastScrolledKey.current = selectedKey;
    selectedRowRef.current?.scrollIntoView({ block: "nearest" });
  }, [selectedKey]);

  function handleRestoreClick(revisionId: string) {
    if (confirmRestoreId !== revisionId) {
      setConfirmRestoreId(revisionId);
      return;
    }
    setConfirmRestoreId(null);
    void onRestore(revisionId);
  }

  function select(id: string) {
    // Timeline selection replaces any variant selection (the detail pane shows
    // what the user last clicked).
    setSelectedVariantId(null);
    setSelectionId(id);
  }

  // Before/after pair for the selected render — always derived, never stored
  // (object identities are replaced by the poll).
  const beforeGeneration = useMemo(() => {
    if (!selection || selection.kind !== "result") return undefined;
    const pair = comparePairFor(generations, selection.entry.id);
    return pair.beforeId
      ? generations.find((item) => item.id === pair.beforeId)
      : undefined;
  }, [selection, generations]);

  // The revision the restore/re-render actions target for this selection.
  function revisionTarget(selection: ResultsSelection): {
    revisionId: string;
    isCurrent: boolean;
  } | null {
    if (selection.kind === "revision") {
      return {
        revisionId: selection.revision.revision_id,
        isCurrent: selection.isCurrent
      };
    }
    if (selection.kind === "result") {
      return {
        revisionId: selection.entry.designRevisionId,
        isCurrent: selection.entry.designRevisionId === currentRevisionId
      };
    }
    return null; // action-only entries offer no restore/re-render
  }

  // Restore (two-step inline confirm) + re-render for the selected context.
  function renderRevisionActions(target: {
    revisionId: string;
    isCurrent: boolean;
  }) {
    const confirming = confirmRestoreId === target.revisionId;
    return (
      <>
        {!target.isCurrent && (
          <button
            className={confirming ? "rt-confirm" : "secondary"}
            data-restore-button="true"
            onClick={() => handleRestoreClick(target.revisionId)}
          >
            {confirming ? "Подтвердить возврат?" : "Вернуть"}
          </button>
        )}
        {cameraId && (
          <button
            className="secondary"
            onClick={() => void onRerender(target.revisionId)}
          >
            Сделать рендер
          </button>
        )}
      </>
    );
  }

  function renderActionCommands(commands: Array<Record<string, unknown>>, entryId: string) {
    return commands.map((command, index) => (
      <div key={`${entryId}-${index}`}>
        {String(command["operation"] ?? "command")}
        {command["target_id"] != null ? ` · ${String(command["target_id"])}` : ""}
      </div>
    ));
  }

  // --- Master rows -------------------------------------------------------

  function renderResultRow(entry: TimelineResult) {
    const selected = selectedKey === entry.id;
    const isCurrent = entry.designRevisionId === currentRevisionId;
    const assetId = entry.outputAssetId;
    return (
      <div className={`rt-row${selected ? " selected" : ""}`} key={entry.id}>
        <button
          type="button"
          className="rt-row-hit"
          aria-current={selected ? "true" : undefined}
          aria-label={`${entry.label}, ${timeLabel(entry.createdAt)}${
            isCurrent ? ", текущая" : ""
          }`}
          ref={selected ? selectedRowRef : undefined}
          onClick={() => select(entry.id)}
        />
        <div className="rt-row-content">
          {assetId && (
            <ImagePreview
              variant="thumbnail"
              aspectRatio="4/3"
              src={api.assetUrl(assetId)}
              alt={entry.label}
              expandable
              onExpand={(trigger) =>
                openLightbox(api.assetUrl(assetId), entry.label, entry.label, trigger)
              }
            />
          )}
          <div className="rt-row-meta">
            <span className="rt-row-label">{entry.label}</span>
            <span className="rt-time">{timeLabel(entry.createdAt)}</span>
            {isCurrent && <span className="tag">текущая</span>}
          </div>
        </div>
      </div>
    );
  }

  function renderActionRow(entry: TimelineAction) {
    const selected = selectedKey === entry.id;
    const commands = jobCommands(jobs.find((item) => item.id === entry.id));
    return (
      <div
        className={`rt-row rt-row--action${selected ? " selected" : ""}`}
        key={entry.id}
      >
        <button
          type="button"
          className="rt-row-hit"
          aria-current={selected ? "true" : undefined}
          aria-label={`${entry.label}, ${timeLabel(entry.createdAt)}`}
          ref={selected ? selectedRowRef : undefined}
          onClick={() => select(entry.id)}
        />
        <div className="rt-row-content">
          <div className="rt-row-meta">
            <span className="rt-row-label">{entry.label}</span>
            <span className="rt-time">{timeLabel(entry.createdAt)}</span>
            <span className="tag">{statusLabel(entry.status)}</span>
          </div>
          <details className="rt-details">
            <summary>Детали</summary>
            <div>Задание: {entry.id}</div>
            {entry.finalRevisionId && <div>Ревизия: {entry.finalRevisionId}</div>}
            {renderActionCommands(commands, entry.id)}
            {entry.errorText && <div className="error">{entry.errorText}</div>}
          </details>
        </div>
      </div>
    );
  }

  // --- Detail pane -------------------------------------------------------

  function renderResultDetail(selection: Extract<ResultsSelection, { kind: "result" }>) {
    const { entry } = selection;
    const generation = selection.generation;
    const assetId = entry.outputAssetId;
    const isCurrent = entry.designRevisionId === currentRevisionId;
    const target = revisionTarget(selection);
    const beforeAssetId = beforeGeneration
      ? outputAsset(beforeGeneration)
      : null;
    return (
      <>
        <div className="rt-detail-head">
          <div className="rt-detail-title">
            <strong>{entry.label}</strong>
            <span className="rt-time">
              {new Date(entry.createdAt).toLocaleString("ru-RU")}
            </span>
            {isCurrent && <span className="tag">текущая</span>}
          </div>
          {target && !target.isCurrent && <p className="rt-warning">{RESTORE_WARNING}</p>}
          {target && (
            <div className="rt-detail-actions">
              {assetId &&
                (detailMode === "single" ? (
                  <button className="secondary" onClick={() => setDetailMode("compare")}>
                    Сравнить
                  </button>
                ) : (
                  <button className="secondary" onClick={() => setDetailMode("single")}>
                    К рендеру
                  </button>
                ))}
              {renderRevisionActions(target)}
            </div>
          )}
        </div>

        {assetId && detailMode === "single" && (
          <figure className="rt-detail-media">
            <ImagePreview
              variant="bounded"
              src={api.assetUrl(assetId)}
              alt={entry.label}
              expandable
              onExpand={(trigger) =>
                openLightbox(api.assetUrl(assetId), entry.label, entry.label, trigger)
              }
            />
          </figure>
        )}

        {assetId && detailMode === "compare" && (
          <div className="compare-grid rt-compare">
            <figure>
              <figcaption>
                <strong>До</strong>
                <small>
                  {beforeGeneration
                    ? new Date(beforeGeneration.created_at).toLocaleString("ru-RU")
                    : "—"}
                </small>
              </figcaption>
              {beforeAssetId ? (
                <ImagePreview
                  variant="bounded"
                  maxHeight={COMPARE_MAX_HEIGHT}
                  src={api.assetUrl(beforeAssetId)}
                  alt="До"
                  expandable
                  onExpand={(trigger) =>
                    openLightbox(api.assetUrl(beforeAssetId), "До", "До", trigger)
                  }
                />
              ) : (
                <div className="compare-empty">нет предыдущего рендера</div>
              )}
            </figure>
            <figure>
              <figcaption>
                <strong>После</strong>
                <small>{new Date(entry.createdAt).toLocaleString("ru-RU")}</small>
              </figcaption>
              <ImagePreview
                variant="bounded"
                maxHeight={COMPARE_MAX_HEIGHT}
                src={api.assetUrl(assetId)}
                alt="После"
                expandable
                onExpand={(trigger) =>
                  openLightbox(api.assetUrl(assetId), "После", "После", trigger)
                }
              />
            </figure>
          </div>
        )}

        {!assetId && <p className="muted">У этого рендера нет изображения.</p>}

        {/* R2: design-check report for the revision this render belongs to. */}
        <ValidationSection projectId={projectId} revisionId={entry.designRevisionId} />

        <details className="rt-details">
          <summary>Детали</summary>
          <div>Рендер: {entry.id}</div>
          <div>Ревизия: {entry.designRevisionId}</div>
          {generation && <div>Камера: {generation.camera_id}</div>}
        </details>
      </>
    );
  }

  function renderActionDetail(entry: TimelineAction) {
    const commands = jobCommands(jobs.find((item) => item.id === entry.id));
    return (
      <>
        <div className="rt-detail-head">
          <div className="rt-detail-title">
            <strong>{entry.label}</strong>
            <span className="rt-time">
              {new Date(entry.createdAt).toLocaleString("ru-RU")}
            </span>
            <span className="tag">{statusLabel(entry.status)}</span>
          </div>
        </div>
        <details className="rt-details">
          <summary>Детали</summary>
          <div>Задание: {entry.id}</div>
          {entry.finalRevisionId && <div>Ревизия: {entry.finalRevisionId}</div>}
          {renderActionCommands(commands, entry.id)}
          {entry.errorText && <div className="error">{entry.errorText}</div>}
        </details>
      </>
    );
  }

  function renderRevisionDetail(
    selection: Extract<ResultsSelection, { kind: "revision" }>
  ) {
    const created = new Date(selection.revision.created_at).toLocaleString("ru-RU");
    const generation = selection.generation;
    const assetId = outputAsset(generation);
    return (
      <>
        <div className="rt-detail-head">
          <div className="rt-detail-title">
            <strong>Версия</strong>
            <span className="rt-time">{created}</span>
            {selection.isCurrent && <span className="tag">текущая</span>}
          </div>
          {!selection.isCurrent && <p className="rt-warning">{RESTORE_WARNING}</p>}
          <div className="rt-detail-actions">
            {renderRevisionActions({
              revisionId: selection.revision.revision_id,
              isCurrent: selection.isCurrent
            })}
          </div>
        </div>
        {assetId ? (
          <figure className="rt-detail-media">
            <ImagePreview
              variant="bounded"
              src={api.assetUrl(assetId)}
              alt={`Рендер версии от ${created}`}
              expandable
              onExpand={(trigger) =>
                openLightbox(
                  api.assetUrl(assetId),
                  `Рендер версии от ${created}`,
                  `Рендер версии от ${created}`,
                  trigger
                )
              }
            />
          </figure>
        ) : (
          <p className="muted">Для этой версии нет рендеров.</p>
        )}
        {/* R2: design-check report for the selected revision. */}
        <ValidationSection
          projectId={projectId}
          revisionId={selection.revision.revision_id}
        />
        {generation && (
          <details className="rt-details">
            <summary>Детали</summary>
            <div>Рендер: {generation.id}</div>
            <div>Ревизия: {selection.revision.revision_id}</div>
            <div>Камера: {generation.camera_id}</div>
          </details>
        )}
      </>
    );
  }

  return (
    <>
      {/* R4 variants bar: chip row (creation order) + inline create + archive
          toggle + compare selection. Full-width above the master-detail
          workspace so the chips breathe. */}
      <section className="panel variant-bar" aria-label="Варианты сцены">
        <div className="variant-chips">
          {!variantsLoaded ? (
            <p className="muted">Загрузка вариантов…</p>
          ) : (
            variantChips.map((variant, index) => {
              const selected = selectedVariantId === variant.id;
              const inCompare =
                compareIds.left === variant.id || compareIds.right === variant.id;
              return (
                <span
                  className={`variant-chip${selected ? " selected" : ""}`}
                  key={variant.id}
                >
                  <button
                    type="button"
                    className="variant-chip-hit"
                    aria-pressed={selected}
                    onClick={() => handleChipClick(variant.id)}
                  >
                    <span className="variant-chip-letter">
                      {variantLetter(index)}
                    </span>
                    <span className="variant-chip-title">{variant.title}</span>
                    <VariantStatusDot status={variant.status} />
                  </button>
                  <button
                    type="button"
                    className={`variant-chip-compare${inCompare ? " active" : ""}`}
                    aria-pressed={inCompare}
                    aria-label={`Выбрать для сравнения: ${variant.title}`}
                    title="Выбрать для сравнения"
                    onClick={() => handleCompareToggle(variant.id)}
                  >
                    ⇄
                  </button>
                </span>
              );
            })
          )}
          {variantCreateOpen ? (
            <form
              className="variant-create"
              onSubmit={(event) => {
                event.preventDefault();
                void createVariant();
              }}
            >
              <input
                value={variantTitle}
                onChange={(event) => setVariantTitle(event.target.value)}
                placeholder="Название варианта"
                aria-label="Название варианта"
                autoFocus
              />
              <button
                type="submit"
                disabled={variantCreateBusy || !variantTitle.trim()}
              >
                {variantCreateBusy ? "Создаём…" : "Создать"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setVariantCreateOpen(false);
                  setVariantTitle("");
                }}
              >
                Отмена
              </button>
            </form>
          ) : (
            <button
              type="button"
              className="secondary variant-create-trigger"
              onClick={() => setVariantCreateOpen(true)}
            >
              + Создать вариант
            </button>
          )}
        </div>
        <label className="variant-archive-toggle">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)}
          />
          Показать архив
        </label>
        {compareIds.left && !compareIds.right && (
          <p className="hint variant-compare-hint">
            Выберите второй вариант для сравнения.
          </p>
        )}
        {variantsError && <div className="error">{variantsError}</div>}
      </section>

      <div className="rt-workspace">
        <aside className="panel rt-master">
          <h2>История дизайна</h2>

          <div className="rt-header">
            <strong>Текущая версия</strong>
            {currentRevision ? (
              <span>
                {new Date(currentRevision.created_at).toLocaleString("ru-RU")}{" "}
                <span className="tag">текущая</span>
              </span>
            ) : (
              <span className="muted">нет данных</span>
            )}
          </div>

          <div className="rt-list">
            {entries.length === 0 ? (
              <p className="muted">Пока нет действий дизайнера</p>
            ) : (
              groups.map((group) => (
                <section className="rt-day" key={group.key}>
                  <h3>{group.label}</h3>
                  {group.entries.map((entry) =>
                    entry.kind === "action"
                      ? renderActionRow(entry)
                      : renderResultRow(entry)
                  )}
                </section>
              ))
            )}

            <details className="rt-versions" open>
              <summary>Версии</summary>
              {revisions.slice(0, 12).map((revision) => {
                const rowId = revisionSelectionId(revision.revision_id);
                const selected = selectedKey === rowId;
                const isCurrent = revision.revision_id === currentRevisionId;
                return (
                  <button
                    type="button"
                    className={`rt-row-hit${selected ? " selected" : ""}`}
                    key={revision.revision_id}
                    aria-current={selected ? "true" : undefined}
                    ref={selected ? selectedRowRef : undefined}
                    onClick={() => select(rowId)}
                  >
                    <span className="rt-row-meta">
                      <span className="rt-row-label">
                        {new Date(revision.created_at).toLocaleString("ru-RU")}
                      </span>
                      {isCurrent && <span className="tag">текущая</span>}
                    </span>
                  </button>
                );
              })}
            </details>
          </div>
        </aside>

        <section className="panel rt-detail" aria-label="Выбранный элемент истории">
          <h2>Просмотр</h2>
          {/* R8 (#198): hands the selected (or approved) variant to the designer
              brief page. Disabled until a variant is selected or approved. */}
          <div className="brief-share-row">
            <button
              type="button"
              className="secondary"
              disabled={!shareVariantId}
              title={shareVariantId ? undefined : BRIEF_SHARE_HINT}
              onClick={() => {
                if (shareVariantId) onShareWithDesigner(shareVariantId);
              }}
            >
              Поделиться с дизайнером
            </button>
          </div>
          {compareReady && compareIds.left && compareIds.right ? (
            <VariantCompareView
              projectId={projectId}
              leftId={compareIds.left}
              rightId={compareIds.right}
            />
          ) : selectedVariant ? (
            <VariantDetail
              key={selectedVariant.id}
              projectId={projectId}
              variant={selectedVariant}
              revisions={revisions}
              onVariantsChanged={refreshVariants}
              onForget={() => setSelectedVariantId(null)}
              openLightbox={openLightbox}
            />
          ) : !selection ? (
            <p className="muted">Выберите элемент истории слева.</p>
          ) : selection.kind === "result" ? (
            renderResultDetail(selection)
          ) : selection.kind === "action" ? (
            renderActionDetail(selection.entry)
          ) : (
            renderRevisionDetail(selection)
          )}
        </section>
      </div>

      <ImageLightbox
        open={lightbox !== null}
        src={lightbox?.src ?? null}
        alt={lightbox?.alt ?? ""}
        title={lightbox?.title}
        onClose={() => setLightbox(null)}
        returnFocusRef={lightboxTriggerRef}
      />
    </>
  );
}
