import { describe, expect, it } from "vitest";
import { parseMaxRevisit, ptzSpeed, speedCandidates } from "./patrolSpeeds";

describe("speedCandidates", () => {
  it("is x0.25, x0.5, x1, x2, x4 of the current speed", () => {
    expect(speedCandidates(20)).toEqual([5, 10, 20, 40, 80]);
  });
  it("clamps to 0.5-400 and drops the duplicates the clamp creates", () => {
    expect(speedCandidates(1)).toEqual([0.5, 1, 2, 4]);          // 0.25 and 0.5 both clamp to 0.5
    expect(speedCandidates(0.5)).toEqual([0.5, 1, 2]);
    expect(speedCandidates(300)).toEqual([75, 150, 300, 400]);   // 600 and 1200 both clamp to 400
    expect(speedCandidates(1000)).toEqual([250, 400]);
  });
  it("always stays inside what the core accepts", () => {
    for (const base of [0.1, 0.5, 2, 7.5, 120, 400, 5000]) {
      const c = speedCandidates(base);
      expect(c.length).toBeGreaterThanOrEqual(1);
      expect(c.length).toBeLessThanOrEqual(12);
      expect(new Set(c).size).toBe(c.length);
      for (const v of c) { expect(v).toBeGreaterThanOrEqual(0.5); expect(v).toBeLessThanOrEqual(400); }
    }
  });
});

describe("parseMaxRevisit", () => {
  it("empty means no limit", () => {
    expect(parseMaxRevisit("")).toEqual({});
    expect(parseMaxRevisit("  ")).toEqual({});
  });
  it("accepts 1-3600 with a decimal point or comma", () => {
    expect(parseMaxRevisit("30")).toEqual({ value: 30 });
    expect(parseMaxRevisit("12,5")).toEqual({ value: 12.5 });
    expect(parseMaxRevisit("1")).toEqual({ value: 1 });
    expect(parseMaxRevisit("3600")).toEqual({ value: 3600 });
  });
  it("rejects outside the range or not a number", () => {
    for (const bad of ["0", "0.5", "3601", "-4", "abc", "Infinity"]) expect(parseMaxRevisit(bad).error).toBeTruthy();
  });
});

describe("ptzSpeed", () => {
  it("a PTZ's own speed wins, null / absent follows the global one", () => {
    expect(ptzSpeed(35, 20)).toEqual({ speed: 35, own: true });
    expect(ptzSpeed(null, 20)).toEqual({ speed: 20, own: false });
    expect(ptzSpeed(undefined, 20)).toEqual({ speed: 20, own: false });
  });
});
