# CCTV Simulator — Performance Pass (applied in place)

Changed: `calculations.py`, `perspective_3d.py`, `ui/main_window.py`,
`ui/canvas_drawer.py`, `ui/view_3d_window.py`.

## What the project does

Tkinter desktop app. You place CCTV cameras on a site plan; it computes optics
(FOV, dead zone, geometric range, pixels-per-metre at distance) against
EN 62676-4 DORI and Johnson/NATO thermal thresholds, draws a side profile, a
top-down plan and a 3D camera-eye view, checks a spec document for compliance
(Gemini API or a rule engine), and exports CSV / XLSX / PDF / PNG.

Flow: `cctv_dual_view_simulator.py` → `ui/main_window.DualViewCCTVDesignApp`
→ `calculations.calculate_for_camera` (per camera × lens mode) → panels +
`ui/canvas_drawer.CanvasDrawer.draw_all` → optional
`ui/view_3d_window.Camera3DViewWindow.render_3d_view` → `exporters`.

## The bottleneck

`calculate` is the call-graph hotspot (fan-in 24). It was reached from
`<B1-Motion>` and `<B3-Motion>` through `_set_selected_camera_position`,
`_set_selected_camera_heading` and `_set_target_point`, so **the whole pipeline
ran once per mouse pixel** — 60-120 times a second while dragging a camera —
while Tk paints only once per frame. Nearly all of that work was discarded
before anyone saw it.

The maths was never the problem: the pure-Python part of a drag frame
(4 cameras, "compare" mode) is 0.43 ms. The other ~18 ms was Tk item churn —
`canvas.delete("all")` plus 250-530 `create_*` calls, a Treeview rebuilt row by
row, and a full 3D repaint.

## Measured result

Headless Tk (Xvfb, CPython 3.12), 4 cameras in "compare" mode, 120 simulated
`<B1-Motion>` events:

| | before | after |
|---|---|---|
| cost per drag event | 18.93 ms | **0.69 ms** (27.2x) |
| canvas items per redraw (4 cam) | 530 | **348** (−34%) |
| canvas items per redraw (1 cam) | 247 | 156 |
| full pass (calculate + paint) | 20.5 ms | 21.0 ms (unchanged, as expected) |

The full pass is deliberately not faster — it now just runs ~30x less often.

## Changes

### `ui/main_window.py` — event coalescing (the big one)
* `schedule_calculate()` / `_run_scheduled_calculate()` / `flush_calculate()`:
  a burst of requests collapses into one `after(33ms)` run.
* `calculate(light=…)`: a **light** pass does geometry + canvas only; a **full**
  pass adds `_populate_table`, `_populate_recommendations`,
  `update_lens_suggestion`, `update_alternative_models` and the 3D window.
* Drag setters queue a light pass; `<ButtonRelease>` calls `flush_calculate()`
  so the panels catch up exactly once per drag.
* `<Configure>` (resize bursts) debounced at 80 ms.
* **Fix:** `<B3-Motion>` rotated the camera but had no `<ButtonRelease-3>`
  handler, so a right-drag never got its closing full pass. Now bound.
* `tree.delete(*children)` — one Tcl round trip instead of one per row.
* `_selected_design_level()` (called 7× per pass) is now an O(1) dict lookup;
  `_levels_desc()` caches the descending PPM sort that `_level_name_for_ppm`
  used to redo on every `<Motion>` event. Both invalidated in
  `_refresh_level_tree()`, which runs after every `ppm_levels` mutation.
* Queued jobs check `winfo_exists()` so a pending pass can't fire into a
  destroyed window.

### `ui/view_3d_window.py` — same treatment
`ttk.Scale` fires per pixel of travel and each call built a fresh
`Perspective3DEngine` + repainted everything. `schedule_render()` coalesces the
distance slider, the lateral slider, the canvas drag and `<Configure>`.
`close()` cancels any pending job.

### `ui/canvas_drawer.py`
* `_draw_side_lane` drew one ruler line per metre (up to 152 `create_line` +
  31 `create_text` per redraw), overlapping into a grey smear at low zoom.
  Tick now snaps to a 1/2/5/10/20/25/50/100 ladder keeping ticks ≥ 6 px apart.
  Verified: the surviving lines are a strict subset of the old positions.
* `_get_max_draw_distance` keeps a running max instead of building a list of
  every ground distance of every camera per redraw, and returns early once the
  150 m clamp is reached.

### `calculations.py`
| function | speedup | change |
|---|---|---|
| `optimize_tilt_calc` | **2.35x** | `dataclasses.replace` instead of `CameraConfig(**asdict(camera))` (`asdict` deep-copies recursively, 149× per call); `with_recommendations=False` in the sweep, so 149 recommendation lists are no longer built and thrown away |
| `analyze_dead_zone_coverage` | **2.66x** | 3 direction vectors instead of 9 `sin`/`cos`; coverers flattened to tuples once; a camera already known to cover is skipped |
| `calculate_for_camera` | **1.57x** | the 17-level sort was redone on every call — now cached; `mode_label`, `res_w*focal` and `drop²` hoisted out of the loop; `x*x` instead of `x**2` |

### `perspective_3d.py`
| | speedup | change |
|---|---|---|
| `project_point` | **1.52x** | `focal/(sensor/2)` and the half-viewport precomputed in `__init__`; one reciprocal instead of two divisions; `__slots__` on `Point3D`/`ProjectedPoint` |
| `is_thermal` | **5.09x** | was a property doing 3 `str.upper()` + 5 substring scans per read, inside the grid/DORI loops — now computed once |

`project_many()` added for batch projection (grid, DORI bands, mesh): localises
attribute loads once instead of ~10 per point. Not wired in yet — drop-in for
`generate_ground_grid_lines` / `generate_dori_ground_polygons` when convenient.

## Bug fixed

`build_recommendations` looked up the face level with
`"yuz" in lvl.name.lower()`. The level is named **"Optik: Yüz Tespit"** — ASCII
`u` never matches `ü`, so `face_level` was always `None` and the
"Yüz tespit menzili çok kısa" warning had never fired. Marked `# BUGFIX`.

## Verification

Output of the patched code was diffed against the original, headless, on a
4-camera "compare" project with an active target point:

* analysis table rows — identical
* info label, dead-zone panel, target panel, canvas context — identical
* 3D canvas — 45 items, byte-identical (type, coordinates, fill, font, dash)
* 2D canvas non-ruler items — 164, byte-identical
* 2D ruler items — 366 → 184, surviving lines a strict subset of the old
* recommendations — one added line, exactly the `Yüz` bugfix above
* `perspective_3d`: 12 000 random projections across 40 random camera/viewport
  configurations — 0 mismatches
* `calculations`: 400 random camera configurations — 0 mismatches outside the
  bugfix; `optimize_tilt_calc` returns the identical tilt and PPM

## Requirements note

`@dataclass(slots=True)` on `Point3D` / `ProjectedPoint` needs **Python ≥ 3.10**.
On 3.9, drop `slots=True`; the other `perspective_3d` gains are independent.
