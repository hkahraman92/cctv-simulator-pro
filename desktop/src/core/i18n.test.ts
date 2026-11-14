import { afterEach, describe, expect, it } from "vitest";
import { EN, LOCALE_FILES, getLang, mergeTables, setLang, t, tn } from "./i18n";
import { fmt } from "../components/ui";

afterEach(() => setLang("tr"));

describe("t", () => {
  it("Turkish returns the key itself, English translates it", () => {
    expect(t("Kaydet")).toBe("Kaydet");
    setLang("en");
    expect(getLang()).toBe("en");
    expect(t("Kaydet")).toBe("Save");
    setLang("tr");
    expect(t("Kaydet")).toBe("Kaydet");
  });

  it("falls back to the Turkish key when there is no translation", () => {
    setLang("en");
    expect(t("Bu metnin çevirisi yok")).toBe("Bu metnin çevirisi yok");
  });

  it("substitutes {name} placeholders in both languages", () => {
    expect(t("Proje adını değiştir: {name}", { name: "A" })).toBe("Proje adını değiştir: A");
    setLang("en");
    expect(t("Proje adını değiştir: {name}", { name: "A" })).toBe("Rename project: A");
    expect(t("{x} ve {y}", { x: 1 })).toBe("1 ve {y}");          // unknown placeholder stays visible
    expect(t("%{n}", { n: "50" })).toBe("50%");
  });

  it("tn uses the #1 singular form in English only when n is 1", () => {
    expect(tn(3, "{n} direk")).toBe("3 direk");
    expect(tn(1, "{n} direk")).toBe("1 direk");
    setLang("en");
    expect(tn(1, "{n} direk")).toBe("1 pole");
    expect(tn(0, "{n} direk")).toBe("0 poles");
    expect(tn(5, "{n} direk")).toBe("5 poles");
    expect(tn(2, "{n} kamera", { x: 1 })).toBe("2 cameras");
  });

  it("every English entry is non-empty and keeps the placeholders of its key", () => {
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
    for (const [k, v] of Object.entries(EN)) {
      expect(v, `empty translation for "${k}"`).not.toBe("");
      expect(names(v), `placeholders differ for "${k}"`).toBe(names(k.replace(/#1$/, "")));
    }
  });
});

describe("number format follows the language", () => {
  it("decimal comma in Turkish, point in English", () => {
    expect(fmt(12.5)).toBe("12,5");
    setLang("en");
    expect(fmt(12.5)).toBe("12.5");
    expect(fmt(null)).toBe("∞");
  });
});

describe("locale files", () => {
  it("are found and have no conflicting duplicate keys", () => {
    expect(Object.keys(LOCALE_FILES).length).toBeGreaterThanOrEqual(2);
    const { conflicts } = mergeTables(LOCALE_FILES);
    expect(conflicts.map((c) => `"${c.key}": ${c.fileA} = "${c.a}" / ${c.fileB} = "${c.b}"`)).toEqual([]);
  });

  it("mergeTables reports a key translated differently in two files", () => {
    const { conflicts, table } = mergeTables({ a: { Kaydet: "Save", Sil: "Delete" }, b: { Kaydet: "Store", Sil: "Delete" } });
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].key).toBe("Kaydet");
    expect(table.Kaydet).toBe("Save");
  });
});
