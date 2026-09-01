---
name: cctv-simulator
description: Working conventions for the CCTV Dual-View Simulator (Python/Tkinter). Architecture map, the optics engine contract, UI performance rules, the EN 62676-4 DORI colour system, and the PyInstaller/Tcl build pitfalls that have already cost real debugging time. Use when editing anything under cctv_simulator/, when touching the Tk UI, or when building the Windows executable.
---

# CCTV Dual-View Simulator - project conventions

Everything here was verified against the running code, not assumed.

## What the application is

Tkinter desktop tool. Cameras are placed on a site plan; the app computes optics
(FOV, dead zone, geometric range, pixels-per-metre at distance) against
EN 62676-4 DORI and Johnson/NATO thermal thresholds, draws a side profile, a
top-down plan and a 3D camera-eye view, checks a specification document for
compliance (Gemini API or a rule engine), and exports CSV / XLSX / PDF / PNG.

```
cctv_dual_view_simulator.py          entry point
  theme.py                           ttkbootstrap window factory + Tcl path setup
  ui/main_window.py                  DualViewCCTVDesignApp - the classic UI
    ui/canvas_drawer.py              side profile + top-down plan
    ui/view_3d_window.py             3D camera-eye view
    ui/modern_window.py              EN 62676-4 optics workbench (customtkinter)
    ui/spec_assistant.py             compliance matrix UI
    ui/camera_db_window.py           camera database editor (admin password)
  calculations.py                    THE optics engine
  perspective_3d.py                  3D projection engine
  compliance.py                      Gemini + rule-based spec analysis
  exporters.py                       CSV / XLSX / PDF / PNG
  database.py / config.py / models.py
cctv_optics_workbench.py             standalone launcher for the workbench
```

## Rule 1: the optics engine is the single source of truth

`calculations.calculate_for_camera()` and `calculations.ppm_at_distance()`
own the physics. UI modules draw; they never re-derive optics.

When a view needs a range for a PPM threshold, restate the engine's own
relation against the already-computed `OpticResult` so the two cannot drift
(see `modern_window.ground_distance_for_ppm`):

```python
optical = (result.res_width_px * result.focal_mm) / (ppm * result.sensor_width_mm)
ground  = sqrt(optical**2 - result.vertical_drop_m**2)   # if optical > drop
return min(ground, result.max_geom_dist_m)
```

## Rule 2: never call `calculate()` directly from a motion event

`calculate()` has fan-in 24 and runs the whole pipeline: every camera x lens
mode, the Treeview rebuild, the recommendations, the O(n^2) dead-zone analysis,
a full canvas repaint and the 3D window. Bound directly to `<B1-Motion>` it ran
60-120 times a second while Tk painted once per frame.

Measured on 4 cameras in "compare" mode: **18.93 ms -> 0.69 ms per drag event
(27x)** after coalescing.

The pattern, already implemented in `main_window.py`:

- `schedule_calculate(full=False)` - collapses a burst into one `after(33ms)` run
- `calculate(light=True)` - geometry + canvas only; skips table, recommendations,
  lens suggestion, alternative models, and the 3D window
- `flush_calculate()` - on `<ButtonRelease-1>` / `<ButtonRelease-3>`, one full pass

`view_3d_window.schedule_render()` does the same for the sliders, which
otherwise rebuilt a `Perspective3DEngine` per pixel of travel.

Anything continuous (drag, slider, `<Configure>` resize burst) goes through the
debouncer. Discrete actions (button, combobox, checkbox) may call directly.

## Rule 3: caches must be invalidated where `ppm_levels` changes

`_selected_design_level()` and `_levels_desc()` cache dict/sort results.
`_refresh_level_tree()` calls `_invalidate_level_cache()` and runs after every
`ppm_levels` mutation (add, delete, project load). Keep that invariant.

## Rule 4: Turkish strings, ASCII matching

`build_recommendations` once looked up the face level with
`"yuz" in name.lower()` while the level is named `"Optik: Yüz Tespit"`. ASCII
`u` never matches `ü`, so the warning never fired. Match on the actual
characters, or normalise both sides.

## EN 62676-4 colour system (optics workbench)

Pixel density thresholds, px/m across the target:

| Task | px/m | Colour |
|---|---|---|
| Teşhis / Identification | 250 | `#00E676` |
| Tanıma / Recognition | 125 | `#FFB300` |
| Gözlem / Observation | 62.5 | (not shaded) |
| Algılama / Detection | 25 | `#FF4D6D` |

Palette: ground `#1E1E2E`, panels `#2A2A3C`, wells `#232333`, hairline `#3A3A4E`,
text `#E6E6F0` / `#8A8AA3` / `#5F5F7A`, primary accent cyan `#00E5FF`,
secondary amber `#FFB300`, hazard `#FF2A2A` (failed requirement, dead zone only).

Tk canvas polygons have no alpha. DORI bands are composited in Pillow RGBA
(`Image.alpha_composite`, one sheet per band, alpha 38/255) and blitted as a
single `create_image`. Without Pillow it degrades to `stipple`. Range rings are
arcs, not horizontal lines: PPM depends on slant distance, so iso-range curves
are the physically correct grid for a conic FOV.

## Rule 5: the workbench window is a `tk.Toplevel`, not a `CTkToplevel`

`OpticsWorkbenchWindow(_WorkbenchBody, tk.Toplevel)`. With a
`ttkbootstrap.Window` parent on Windows, `CTkToplevel` mis-computes the grid
geometry and leaves the drawing canvas at 1x1, so `DoriPlanView.draw()` skips
(it requires >= 160 px). `_WorkbenchBody` is a mixin so the same body serves the
standalone `ctk.CTk` root and the embedded `tk.Toplevel`.

Child windows also need `lift()` + `focus_force()`; a maximized main window on
Windows otherwise leaves new Toplevels behind it. `view_3d_window` needs
`after(60, self.render_3d_view)` because the canvas is still below 100 px at
construction time.

## Building the Windows executable

`build_exe.bat` -> `cctv_simulator.spec`. Both must sit in the project root
along with `runtime_hook_tcltk.py`.

Three failure modes, all of which have actually happened here:

**1. `ModuleNotFoundError: No module named 'tkinter'`**
The building interpreter, or its venv, has no tkinter. `build_exe.bat` now
picks the first interpreter that has it and is not a conda path (PyInstaller +
Anaconda drops Tcl/Tk). The spec also ships the `tkinter` package as a file
tree, because on some Windows venv layouts the module graph collects the
`_tkinter` C extension but misses the Python package.

**2. `Can't find a usable init.tcl`**
Nothing set `TCL_LIBRARY`. PyInstaller's own tkinter runtime hook is only wired
in when `tkinter` is in the module graph; when the graph misses it, no hook.
`runtime_hook_tcltk.py` runs before application code, finds `init.tcl` /
`tk.tcl` inside the bundle, and sets both variables.

**3. `invalid command name "::msgcat::mcmset"`**
`ttkbootstrap` calls `::msgcat::mcmset` while building its theme. `msgcat` is
**not** a package inside `tcl8.6/` - it is a Tcl Module at
`tcl8/<ver>/msgcat-*.tm`. On Linux that directory is nested inside `tcl8.6/`,
so bundling `tcl8.6` catches it; on Windows it is a sibling under
`<Python>/tcl/`, so bundling `tcl8.6` alone loses it. The spec now locates the
module directory explicitly and ships it to `_tcl_data/tcl8`.

From source in a venv the same error is fixed by `configure_tk_paths()` running
at the **top of `theme.py`**, before `ttkbootstrap` is imported. Import order
matters: `theme.py` is imported first by the entry point.

Every build writes `build/tkinter-diagnostic.txt`: interpreter, where tkinter
and the Tcl/Tk libraries resolved from, which modules entered the bundle. Read
it before guessing.

## Verification habits that paid off here

- Run the Tk UI headless under `xvfb-run`, screenshot it, and look at the
  screenshot. Layout bugs (clipped legends, wrapped labels, cut-off cards) are
  invisible in code review and obvious in a PNG.
- For a refactor of `calculations.py`, diff the new output against the old over
  a few hundred randomised `CameraConfig` values. Optimisations there must be
  bit-identical except where a fix is intended and marked.
- For a UI change, dump every canvas item (type, coords, fill, font) from both
  versions and diff. That is how "only the ruler ticks changed" was proven.
- Platform layout differences hide bugs. The msgcat failure was invisible on
  Linux for four rounds because `tcl8/` is nested there. When a bug will not
  reproduce, suspect the platform before suspecting the report.
