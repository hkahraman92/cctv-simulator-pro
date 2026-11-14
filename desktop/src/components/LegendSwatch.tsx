/** Level keys whose swatch colour is too close to a neighbour's, so the swatch also gets a diagonal hatch. */
const HATCHED = new Set(["monitor"]);
export const isHatched = (levelKey?: string) => !!levelKey && HATCHED.has(levelKey);

/**
 * One legend colour chip. Monitoring (12,5 px/m) and Detection (25 px/m) are nearly the same colour, so the Monitoring
 * chip carries a hatch in the text colour. The row is identified by the level key the core sends with it, never by its
 * Turkish label; a row without a key is drawn plain.
 */
export function LegendSwatch({ rgba, levelKey }: { rgba: readonly [number, number, number, number]; levelKey?: string }) {
  const [r, g, b, a] = rgba;
  const fill = `rgba(${r},${g},${b},${a / 255})`;
  const hatch = isHatched(levelKey);
  return (
    <span className="h-3 w-3 shrink-0 rounded-sm border border-line text-text"
      style={{ background: hatch ? `repeating-linear-gradient(135deg, transparent 0 2px, currentColor 2px 3px), ${fill}` : fill }} />
  );
}
