import { describe, expect, it } from "vitest";
import src from "./Library.tsx?raw";

// The table must fit the panel at a 1280 px window (about 620 px): no forced wide Model column, a low table
// minimum (horizontal scroll stays only as the narrow-window fallback), the full name in the title attribute.

describe("library table layout", () => {
  it("the table minimum fits a 1280 px panel and the Model column no longer forces 240 px", () => {
    const min = Number(/<table className="[^"]*min-w-\[(\d+)px\]/.exec(src)?.[1]);
    expect(min).toBeGreaterThan(0);
    expect(min).toBeLessThanOrEqual(620);
    expect(src).not.toContain("min-w-[240px]");
  });
  it("the Model cell shrinks, truncates and keeps its full name as the title", () => {
    const cell = /<td className="([^"]*)" title=\{m\.name\}>\{m\.name\}<\/td>/.exec(src)?.[1] ?? "";
    expect(cell).toContain("truncate");
    expect(cell).toContain("max-w-0");
  });
  it("sorting and keyboard navigation hooks are still there", () => {
    expect(src).toContain("onKeyDown={onTableKey}");
    expect(src).toContain("aria-sort");
    expect(src).toContain("aria-activedescendant");
  });
});
