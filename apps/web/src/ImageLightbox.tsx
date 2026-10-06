import {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useId,
  useRef,
  useState
} from "react";
import { createPortal } from "react-dom";
import {
  applyZoomAtPoint,
  clampPan,
  clampZoom,
  commandFromKeyboardEvent,
  fitContain,
  IMAGE_PREVIEW_ZOOM,
  Point,
  reduceZoomState,
  renderedSizeAtZoom,
  Size,
  ZoomCommand,
  ZoomState
} from "./imagePreview";

// Fullscreen lightbox (#150). CONTROLLED component: the host passes `open` and
// primitive props only (src/alt/title strings) — App.tsx polls every 5s and
// replaces object identities, so nothing here may depend on object identity.
// Zoom/pan state lives inside; it resets when the dialog opens or src changes.

export type ImageLightboxProps = {
  open: boolean;
  src: string | null;
  alt: string;
  title?: string;
  onClose: () => void;
  /** Element that invoked the lightbox; focus returns to it on close. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
  portalTarget?: HTMLElement;
  initialZoom?: number;
  maxZoom?: number;
  className?: string;
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ImageLightbox({
  open,
  src,
  alt,
  title,
  onClose,
  returnFocusRef,
  portalTarget,
  initialZoom = 1,
  maxZoom = IMAGE_PREVIEW_ZOOM.max,
  className
}: ImageLightboxProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  const [zoomState, setZoomState] = useState<ZoomState>({
    zoom: initialZoom,
    pan: { x: 0, y: 0 }
  });
  const [natural, setNatural] = useState<Size>({ x: 0, y: 0 });
  const [viewport, setViewport] = useState<Size>({ x: 0, y: 0 });

  const backdropDownRef = useRef(false);
  const pointerPanRef = useRef<{ pointerId: number; last: Point } | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const wasActiveRef = useRef(false);

  const effectiveMaxZoom = Math.max(IMAGE_PREVIEW_ZOOM.min, maxZoom);
  const active = open === true && typeof src === "string" && src.length > 0;

  // Layout values for event handlers that must not depend on object identity.
  const layoutRef = useRef({ viewport, natural, effectiveMaxZoom });
  useEffect(() => {
    layoutRef.current = { viewport, natural, effectiveMaxZoom };
  }, [viewport, natural, effectiveMaxZoom]);

  // Zoom/pan reset on open and on src change (primitive deps only: the app
  // polls every 5s and replaces prop object identities, never primitives).
  useEffect(() => {
    if (!active) return;
    setNatural({ x: 0, y: 0 });
    setZoomState({ zoom: initialZoom, pan: { x: 0, y: 0 } });
  }, [active, src, initialZoom]);

  // Focus management: move focus into the dialog on open, restore it on close.
  useEffect(() => {
    if (active) {
      const current =
        returnFocusRef?.current ??
        (document.activeElement instanceof HTMLElement ? document.activeElement : null);
      restoreFocusRef.current = current;
      closeButtonRef.current?.focus();
      wasActiveRef.current = true;
      return;
    }
    if (wasActiveRef.current) {
      wasActiveRef.current = false;
      const target = restoreFocusRef.current;
      restoreFocusRef.current = null;
      if (target && target.isConnected) target.focus();
    }
  }, [active, returnFocusRef]);

  // Track the stage size so fit/pan recompute on resize (and on first open).
  useEffect(() => {
    const stage = stageRef.current;
    if (!active || !stage) return;
    const measure = () => {
      const width = stage.clientWidth;
      const height = stage.clientHeight;
      setViewport((previous) =>
        previous.x === width && previous.y === height ? previous : { x: width, y: height }
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [active, src]);

  // Ctrl+wheel / pinch zooms at the pointer and must preventDefault, which
  // React's passive wheel listener cannot do — attach a native listener.
  useEffect(() => {
    const stage = stageRef.current;
    if (!active || !stage) return;
    function handleWheel(event: WheelEvent) {
      if (!event.ctrlKey) return; // plain wheel must NOT zoom
      event.preventDefault();
      const rect = stage!.getBoundingClientRect();
      const layout = layoutRef.current;
      const factor =
        event.deltaY < 0 ? IMAGE_PREVIEW_ZOOM.step : 1 / IMAGE_PREVIEW_ZOOM.step;
      const focal = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      setZoomState((state) =>
        applyZoomAtPoint(
          state,
          clampZoom(state.zoom * factor, IMAGE_PREVIEW_ZOOM.min, layout.effectiveMaxZoom),
          focal,
          layout.viewport,
          layout.natural
        )
      );
    }
    stage.addEventListener("wheel", handleWheel, { passive: false });
    return () => stage.removeEventListener("wheel", handleWheel);
  }, [active, src]);

  if (!active || !src) return null;

  function close() {
    onClose();
  }

  function applyCommand(command: ZoomCommand) {
    if (command === "zoomIn" || command === "zoomOut") {
      const base =
        command === "zoomIn"
          ? zoomState.zoom * IMAGE_PREVIEW_ZOOM.step
          : zoomState.zoom / IMAGE_PREVIEW_ZOOM.step;
      const nextZoom = clampZoom(base, IMAGE_PREVIEW_ZOOM.min, effectiveMaxZoom);
      setZoomState(applyZoomAtPoint(zoomState, nextZoom, null, viewport, natural));
      return;
    }
    setZoomState(reduceZoomState(zoomState, command, viewport, natural));
  }

  function trapTab(event: ReactKeyboardEvent<HTMLDivElement>) {
    const container = dialogRef.current;
    if (!container) return;
    const focusables = Array.from(
      container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
    ).filter((element) => element.offsetParent !== null);
    const activeElement = document.activeElement;
    if (!container.contains(activeElement)) {
      event.preventDefault();
      (focusables[0] ?? container).focus();
      return;
    }
    if (focusables.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && (activeElement === first || activeElement === container)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (activeElement === last || activeElement === container)) {
      event.preventDefault();
      first.focus();
    }
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      // Dialog-scoped: keep ProjectHeader's document-level Escape menu
      // listener from double-reacting.
      event.stopPropagation();
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "Tab") {
      trapTab(event);
      return;
    }
    const command = commandFromKeyboardEvent(event);
    if (!command) return;
    event.preventDefault();
    event.stopPropagation();
    applyCommand(command);
  }

  function handleBackdropMouseDown(event: ReactMouseEvent<HTMLDivElement>) {
    // Close only when the whole gesture stays on the backdrop itself, so a
    // drag that starts on the image cannot dismiss the dialog on mouseup.
    backdropDownRef.current = event.target === event.currentTarget;
  }

  function handleBackdropMouseUp(event: ReactMouseEvent<HTMLDivElement>) {
    const startedOnBackdrop = backdropDownRef.current;
    backdropDownRef.current = false;
    if (startedOnBackdrop && event.target === event.currentTarget) close();
  }

  function stagePoint(event: { clientX: number; clientY: number }): Point {
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function handleDoubleClick(event: ReactMouseEvent<HTMLDivElement>) {
    // Toggle 1 ↔ 2 zoomed at the click point.
    const nextZoom = zoomState.zoom === 1 ? 2 : 1;
    const focal = stagePoint(event);
    setZoomState(
      applyZoomAtPoint(zoomState, nextZoom, focal, viewport, natural)
    );
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (zoomState.zoom <= 1) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    pointerPanRef.current = {
      pointerId: event.pointerId,
      last: { x: event.clientX, y: event.clientY }
    };
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const pan = pointerPanRef.current;
    if (!pan || pan.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - pan.last.x;
    const deltaY = event.clientY - pan.last.y;
    pan.last = { x: event.clientX, y: event.clientY };
    // Zoom does not change while panning, so the rendered size from the
    // current render scope stays valid inside the functional update.
    const { fitScale } = fitContain(natural, viewport);
    const rendered = renderedSizeAtZoom(natural, fitScale, zoomState.zoom);
    setZoomState((state) => ({
      ...state,
      pan: clampPan(
        { x: state.pan.x + deltaX, y: state.pan.y + deltaY },
        rendered,
        viewport
      )
    }));
  }

  function handlePointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    if (pointerPanRef.current?.pointerId === event.pointerId) {
      pointerPanRef.current = null;
    }
  }

  const fit = fitContain(natural, viewport);
  const rendered = renderedSizeAtZoom(natural, fit.fitScale, zoomState.zoom);
  const displayPan = clampPan(zoomState.pan, rendered, viewport);
  const transform = `translate(${displayPan.x}px, ${displayPan.y}px) scale(${fit.fitScale * zoomState.zoom})`;
  const pannable = zoomState.zoom > 1;

  return createPortal(
    <div
      className="lb-backdrop"
      onMouseDown={handleBackdropMouseDown}
      onMouseUp={handleBackdropMouseUp}
    >
      <div
        ref={dialogRef}
        className={className ? `lb-dialog ${className}` : "lb-dialog"}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <div className="lb-toolbar">
          <h2 className="lb-title" id={titleId}>
            {title || alt || "Просмотр изображения"}
          </h2>
          <div className="lb-controls">
            <button
              type="button"
              className="lb-button"
              aria-label="Уменьшить масштаб"
              onClick={() => applyCommand("zoomOut")}
            >
              −
            </button>
            <button
              type="button"
              className="lb-button"
              aria-label="Масштаб 100%"
              onClick={() => applyCommand("reset")}
            >
              100%
            </button>
            <button
              type="button"
              className="lb-button"
              aria-label="Увеличить масштаб"
              onClick={() => applyCommand("zoomIn")}
            >
              +
            </button>
            <button
              ref={closeButtonRef}
              type="button"
              className="lb-button lb-button--close"
              onClick={close}
            >
              Закрыть
            </button>
          </div>
        </div>
        <div
          ref={stageRef}
          className={pannable ? "lb-stage lb-stage--pannable" : "lb-stage"}
          onDoubleClick={handleDoubleClick}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          <img
            className="lb-image"
            src={src}
            alt={alt}
            draggable={false}
            style={{ transform, transformOrigin: "center" }}
            onLoad={(event) => {
              const image = event.currentTarget;
              setNatural((previous) =>
                previous.x === image.naturalWidth && previous.y === image.naturalHeight
                  ? previous
                  : { x: image.naturalWidth, y: image.naturalHeight }
              );
            }}
          />
        </div>
      </div>
    </div>,
    portalTarget ?? document.body
  );
}
