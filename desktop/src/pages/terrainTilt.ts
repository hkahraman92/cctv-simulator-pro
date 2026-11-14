/**
 * The core takes tilt as negative-down (-45 = looking 45 degrees below the horizon); the Optics and 3D pages
 * show "Eğim (aşağı)" as positive-down. This page converts ONLY at the display boundary: state and every API
 * call keep the core's convention.
 */
export const toDisplayTilt = (apiTilt: number): number => (apiTilt === 0 ? 0 : -apiTilt);
export const fromDisplayTilt = (shown: number): number => (shown === 0 ? 0 : -shown);

/** The slider's physical range (core convention -45 .. +10), as shown: -10 .. 45 down. */
export const API_TILT_MIN = -45;
export const API_TILT_MAX = 10;
export const DISPLAY_TILT_MIN = toDisplayTilt(API_TILT_MAX);
export const DISPLAY_TILT_MAX = toDisplayTilt(API_TILT_MIN);
