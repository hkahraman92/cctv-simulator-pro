import { describe, expect, it } from "vitest";
import {
  API_TILT_MAX, API_TILT_MIN, DISPLAY_TILT_MAX, DISPLAY_TILT_MIN, fromDisplayTilt, toDisplayTilt,
} from "./terrainTilt";

describe("terrain tilt display convention", () => {
  it("shows a downward (negative) core tilt as a positive 'down' value", () => {
    expect(toDisplayTilt(-5)).toBe(5);
    expect(toDisplayTilt(-45)).toBe(45);
    expect(toDisplayTilt(10)).toBe(-10);
    expect(fromDisplayTilt(5)).toBe(-5);
  });

  it("round-trips every step of the slider exactly", () => {
    for (let t = API_TILT_MIN; t <= API_TILT_MAX; t += 0.5) {
      expect(fromDisplayTilt(toDisplayTilt(t))).toBe(t);
      expect(toDisplayTilt(fromDisplayTilt(t))).toBe(t);
    }
  });

  it("maps the range ends to the same physical range", () => {
    expect(DISPLAY_TILT_MIN).toBe(-10);
    expect(DISPLAY_TILT_MAX).toBe(45);
    expect(fromDisplayTilt(DISPLAY_TILT_MAX)).toBe(API_TILT_MIN);
    expect(fromDisplayTilt(DISPLAY_TILT_MIN)).toBe(API_TILT_MAX);
  });

  it("never produces negative zero", () => {
    expect(Object.is(toDisplayTilt(0), 0)).toBe(true);
    expect(Object.is(fromDisplayTilt(0), 0)).toBe(true);
  });
});
