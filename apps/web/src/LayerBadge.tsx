import { ENTITY_STATE_LABELS, type EntityState } from "./sceneLayers";

// R1 layer pill (#154 design rail + twin panel entity list): a small ghost
// pill with the layer's state color, mirroring the #169 badge language
// (transparent fill, hairline state border, muted state text — see the R1
// block at the end of styles.css).
export function LayerBadge({ state }: { state: EntityState }) {
  return (
    <span className={`layer-badge layer-badge-${state}`}>
      {ENTITY_STATE_LABELS[state]}
    </span>
  );
}
