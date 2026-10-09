import {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  useRef,
  useState
} from "react";

// Shared bounded image preview (#150). Thumbnail variant: stable aspect-ratio
// box with a selection affordance. Bounded variant: the image is clamped by
// its container (max-height policy) so source image dimensions can never grow
// the document, plus a VISIBLE «Развернуть» affordance that opens the shared
// lightbox (double-click is a secondary path, never the only one).

export type ImagePreviewVariant = "thumbnail" | "bounded";

export type ImagePreviewProps = {
  src: string;
  alt: string;
  variant?: ImagePreviewVariant;
  fit?: "cover" | "contain";
  selected?: boolean;
  disabled?: boolean;
  /** CSS aspect-ratio string for the thumbnail box, e.g. "4/3". */
  aspectRatio?: string;
  /** Max-height policy for the bounded variant (CSS length), e.g. "320px". */
  maxHeight?: string;
  caption?: string;
  onSelect?: () => void;
  expandable?: boolean;
  expandLabel?: string;
  /** Called with the trigger element so the host can restore focus to it. */
  onExpand?: (trigger: HTMLElement) => void;
  className?: string;
  /**
   * R9 (#197): paint the shared .skeleton shimmer until the image fires
   * load (or error, so a broken src never shimmers forever). Opt-in — the
   * default flow stays byte-identical for every other call site.
   */
  skeleton?: boolean;
};

export function ImagePreview({
  src,
  alt,
  variant = "thumbnail",
  fit,
  selected = false,
  disabled = false,
  aspectRatio,
  maxHeight,
  caption,
  onSelect,
  expandable = false,
  expandLabel = "Развернуть",
  onExpand,
  className,
  skeleton = false
}: ImagePreviewProps) {
  const expandButtonRef = useRef<HTMLButtonElement | null>(null);
  // Track which src has settled (load OR error) so a src switch re-arms the
  // shimmer without an effect: loaded === (the current src has finished).
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const loaded = loadedSrc === src;

  const resolvedFit = fit ?? (variant === "thumbnail" ? "cover" : "contain");
  // CSS custom properties drive the component's css (aspect, fit, max-height).
  const style = {} as CSSProperties & Record<`--imgpv-${string}`, string>;
  if (aspectRatio) style["--imgpv-aspect"] = aspectRatio;
  if (maxHeight) style["--imgpv-max-height"] = maxHeight;
  style["--imgpv-fit"] = resolvedFit;

  const classes = [
    "imgpv",
    variant === "thumbnail" ? "imgpv--thumbnail" : "imgpv--bounded",
    skeleton && !loaded ? "imgpv--loading" : "",
    selected ? "imgpv--selected" : "",
    className ?? ""
  ]
    .filter(Boolean)
    .join(" ");

  function expand(fallbackTrigger: HTMLElement | null) {
    if (!expandable || disabled) return;
    const trigger = fallbackTrigger ?? expandButtonRef.current;
    if (!trigger) return;
    onExpand?.(trigger);
  }

  function handleExpandClick(event: ReactMouseEvent<HTMLButtonElement>) {
    // The button is the primary trigger and the focus-return target.
    event.stopPropagation();
    expand(event.currentTarget);
  }

  function handleDoubleClick(event: ReactMouseEvent<HTMLDivElement>) {
    // Secondary path only — never the only way to expand.
    if (disabled) return;
    event.preventDefault();
    expand(expandButtonRef.current);
  }

  function handleSelect() {
    if (disabled || !onSelect) return;
    onSelect();
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (disabled || !onSelect) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  }

  const selectable = Boolean(onSelect) && !disabled;

  return (
    <div
      className={classes}
      style={style}
      {...(selectable
        ? {
            role: "button",
            tabIndex: 0,
            "aria-pressed": selected,
            onClick: handleSelect,
            onKeyDown: handleKeyDown
          }
        : {})}
      onDoubleClick={expandable ? handleDoubleClick : undefined}
    >
      {skeleton && !loaded && (
        <div className="skeleton imgpv__skeleton" aria-hidden="true" />
      )}
      <img
        className="imgpv__image"
        src={src}
        alt={alt}
        draggable={false}
        onLoad={skeleton ? () => setLoadedSrc(src) : undefined}
        onError={skeleton ? () => setLoadedSrc(src) : undefined}
      />
      {caption && <div className="imgpv__caption">{caption}</div>}
      {expandable && (
        <button
          ref={expandButtonRef}
          type="button"
          className="imgpv__expand"
          aria-haspopup="dialog"
          aria-label={alt ? `${expandLabel}: ${alt}` : expandLabel}
          disabled={disabled}
          onClick={handleExpandClick}
        >
          {expandLabel}
        </button>
      )}
    </div>
  );
}
