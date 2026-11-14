import { describe, expect, it } from "vitest";
import { levelLabel, nameStatesPpm, splitLevelName } from "./levelLabel";

describe("splitLevelName", () => {
  it("puts the Turkish part first and the group aside", () => {
    expect(splitLevelName("Optik: İnceleme / Inspection")).toEqual({ group: "Optik", short: "İnceleme" });
    expect(splitLevelName("Teşhis / Identification")).toEqual({ group: "", short: "Teşhis" });
    expect(splitLevelName("Firma: Yüz")).toEqual({ group: "Firma", short: "Yüz" });
    expect(splitLevelName("Plaka")).toEqual({ group: "", short: "Plaka" });
  });
});

describe("levelLabel", () => {
  it("appends px/m when the name has no number", () => {
    expect(levelLabel("Teşhis", 250, "250")).toBe("Teşhis (250 px/m)");
  });
  it("does not repeat a number the name already carries", () => {
    expect(levelLabel("Optik: Teşhis / Identification (250 px/m)", 250, "250")).toBe("Optik: Teşhis / Identification (250 px/m)");
    expect(nameStatesPpm("Gözlem 62,5 px/m", 62.5)).toBe(true);
    expect(nameStatesPpm("Gözlem 62.5 PPM", 62.5)).toBe(true);
    expect(nameStatesPpm("Gözlem 125 ppm", 125)).toBe(true);
    expect(nameStatesPpm("Tanıma 125 px / m", 125)).toBe(true);
  });
  it("is not fooled by a different or partial number", () => {
    expect(nameStatesPpm("Algılama 125 px/m", 25)).toBe(false);
    expect(nameStatesPpm("Gözlem 62,5 px/m", 2.5)).toBe(false);
    expect(nameStatesPpm("Teşhis 250", 250)).toBe(false);
    expect(levelLabel("Gözlem 62,5", 62.5, "62,5")).toBe("Gözlem 62,5 (62,5 px/m)");
  });
});
