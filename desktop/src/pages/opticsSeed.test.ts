import { describe, expect, it } from "vitest";
import { clampTask, seedTask } from "./opticsSeed";

const EN: [string, number][] = [["Teşhis", 250], ["Tanıma", 125], ["Gözlem", 62.5], ["Algılama", 25]];
const v = (pass: boolean, identify: number, recognize: number, observe: number, detect: number) =>
  ({ pass, dori_reach_m: { identify, recognize, observe, detect } });

describe("optics page seeding", () => {
  it("keeps the page when the verdict already passes", () => expect(seedTask(v(true, 5, 10, 20, 40), EN, 20)).toBeNull());
  it("picks the strongest task reached at the distance", () => expect(seedTask(v(false, 8, 16, 32, 64), EN, 20)).toEqual({ task: 2 }));
  it("else the weakest task at 80 % of its reach", () => expect(seedTask(v(false, 1, 2, 5, 10), EN, 20)).toEqual({ task: 3, dist: 8 }));
  it("a project's own non-EN levels have no reach: nothing to seed from", () => {
    expect(seedTask(v(false, 8, 16, 32, 64), [["Doğrulama 500 px/m", 500]], 20)).toBeNull();
  });
  it("a task index from the previous project is clamped into the new list", () => {
    expect(clampTask(4, 2)).toBe(1);
    expect(clampTask(-1, 4)).toBe(0);
    expect(clampTask(0, 1)).toBe(0);
  });
});
