/** True when a level name already states its own px/m ("... (250 px/m)", "125 ppm", "62,5 PPM"). */
export function nameStatesPpm(name: string, ppm: number): boolean {
  const num = String(+ppm.toFixed(2)).replace(/\./g, "[.,]");
  return new RegExp(`(?<![\\d.,])${num}(?:[.,]0+)?\\s*(?:px\\s*/\\s*m|ppm)(?![\\p{L}\\d])`, "iu").test(name);
}

/**
 * Level names commonly read "<group>: <TR name> / <EN name>". For a narrow list the distinguishing
 * part is the Turkish name; the group becomes a muted prefix. Stored names are never changed.
 */
export function splitLevelName(name: string): { group: string; short: string } {
  const m = /^\s*([^:/]{1,24}?)\s*:\s*(.+)$/.exec(name);
  const rest = m ? m[2] : name;
  const group = m ? m[1].trim() : "";
  const short = rest.split("/")[0].trim();
  return short ? { group, short } : { group: "", short: name };
}

/** "Name (N px/m)", without repeating the number when the name carries it. */
export function levelLabel(name: string, ppm: number, formatted: string): string {
  return nameStatesPpm(name, ppm) ? name : `${name} (${formatted} px/m)`;
}
