---
name: cctv-simulator
description: Deep reference for the CCTV Dual-View Simulator (Python/Tkinter) - the EN 62676-4 DORI colour system and thresholds, the optics engine contract, terrain/DEM and tile-server rules, Tk threading pitfalls, and the PyInstaller/Tcl build failures that have already cost real debugging time. Use when editing anything under cctv_simulator/, when touching the Tk UI or the online map downloader, or when building the Windows executable.
---

# CCTV Dual-View Simulator - deep reference

CLAUDE.md carries the day-to-day rules. This file carries the detail that would
bloat it: exact thresholds, colour values, formulas, and the full story behind
each build failure.

Everything here was verified against the running code, not assumed.

## EN 62676-4 pixel density thresholds

Pixels per metre across the target:

| Task | px/m | Colour |
|---|---|---|
| Teşhis / Identification | 250 | `#00E676` |
| Tanıma / Recognition | 125 | `#FFB300` |
| Gözlem / Observation | 62.5 | (not shaded) |
| Algılama / Detection | 25 | `#FF4D6D` |

Palette: ground `#1E1E2E`, panels `#2A2A3C`, wells `#232333`, hairline `#3A3A4E`,
text `#E6E6F0` / `#8A8AA3` / `#5F5F7A`, primary accent cyan `#00E5FF`, secondary
amber `#FFB300`, hazard `#FF2A2A` (failed requirement and dead zone only).

Tk canvas polygons have no alpha. DORI bands are composited in Pillow RGBA
(`Image.alpha_composite`, one sheet per band, alpha 38/255) and blitted as a
single `create_image`. Without Pillow it degrades to `stipple`.

Range rings are **arcs, not horizontal lines**: PPM depends on slant distance, so
iso-range curves are the physically correct grid for a conic FOV.

## The optics relation

```
PPM = (res_width_px * focal_mm) / (optical_dist_m * sensor_width_mm)
```

Inverted for "how far out does this PPM threshold sit", restated against an
already-computed `OpticResult` so the two cannot drift
(`modern_window.ground_distance_for_ppm`):

```python
optical = (result.res_width_px * result.focal_mm) / (ppm * result.sensor_width_mm)
ground  = sqrt(optical**2 - result.vertical_drop_m**2)   # if optical > drop
return min(ground, result.max_geom_dist_m)
```

## MTF -> effective resolution

Slanted-edge e-SFR per ISO 12233: ESF -> LSF -> FFT -> MTF. Nyquist is
0.5 cycles/pixel. Effective resolution ratio `k = MTF50 / 0.5`; effective
megapixels = nominal x k².

Validated end to end: measured vs analytic Gaussian MTF50 within 0.4-3.1%.
Worked example: a 4 MP camera at MTF50 = 0.28 behaves as **1.28 MP**, and its
Identification range collapses from 15.5 m to 7.9 m. This is the single number
that most changes a real design, and the application does not yet surface it.

## Terrain, DEM and tile servers

**Never present invented relief as a measurement.** A viewshed is nothing but
terrain occlusion, so fabricated topography produces a confident, wrong coverage
report. `TerrainData.is_measured` is True only for a real DEM (GeoTIFF via
rasterio, Terrarium tiles, an elevation API). Procedural presets, greyscale
heightmaps (no vertical datum - values are relative, not metres) and the
last-resort fallback are False, and the UI raises `showwarning`, not `showinfo`.
Carry the flag into exports too.

**Elevation source.** Terrarium RGB terrain tiles, AWS Open Data, no key:

```
https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
elev_m = (R * 256 + G + B / 256) - 32768
```

SRTM/3DEP source, so ~30 m is the real limit; `_select_dem_zoom` floors the
target cell size at 20 m rather than pretending finer. Sentinel values outside
-500..9000 m are voids, not ground. Fallback chain: Terrarium -> Open-Elevation
batch POST -> flagged synthetic.

**Orientation.** `z_grid` row 0 is **south** (increasing +Y is north); a tile
mosaic's row 0 is north; the renderer applies `np.flipud`. A DEM written into
`z_grid` without `flipud` mirrors the entire terrain north-south, which looks
plausible and is wrong.

**Tile axis order.** Esri World Imagery is `/tile/{z}/{y}/{x}`. OpenStreetMap and
OpenTopoMap are `{z}/{x}/{y}`. They were once all written with Esri's order, so
the two OSM-family basemaps silently rendered a different part of the world.

**Politeness.** Real identifying User-Agent - spoofing a browser string breaks the
OSM tile usage policy and gets IP ranges banned. `max_workers` 2 for OSM and
OpenTopoMap, 6 for Esri and Terrarium. On-disk cache under
`%APPDATA%\<app>\tile-cache`. `MAX_TILES = 400` budget, checked before fetching.
Show the per-source attribution string.

**Cropping** happens in global Web Mercator pixel space. Interpolating linearly
in latitude is wrong: Mercator Y is `asinh(tan(lat))`. Verified: a requested
2000 m extent crops to 1999.3 x 2000.3 m, edge latitudes within 6e-4 deg of the
bounding box.

**Zoom** is derived from the requested ground sample distance, then clamped to
the server maximum and walked back down until the tile count fits the budget. A
fixed zoom-per-bucket table lied: "Ultra HD ~0.5 m/px" silently became 3.8 m/px
at 10 km.

## Tk threading

Network work runs on a background thread and returns through `after(0, ...)`.
Two traps, both of which actually happened here:

**`exc` is deleted when the `except` block ends.** A lambda closing over it and
run later by `after(0)` raises `NameError: cannot access free variable 'exc'`.
The measured consequence: a failed download showed the user **zero** dialogs.
Bind `err = str(exc)` to a local name inside the block.

**Callbacks touch destroyed widgets after the dialog closes.** Measured on the
real dialog under Xvfb: cancelling mid-download produced **108** Tk callback
errors, and the cancelled download's result was applied anyway. The `_post()`
wrapper (`winfo_exists`, plus `TclError`/`RuntimeError` -> a private
`_DownloadCancelled`) unwinds the worker quietly: 0 errors, nothing applied.
Reuse that pattern for any new background job.

Note that `widget.after()` itself does **not** raise after the widget is
destroyed - the failure surfaces later, inside the scheduled callback. Guard at
post time, not by trusting `after` to complain.

## Drag performance, measured

`calculate()` has fan-in 24 and runs every camera x lens mode, the Treeview
rebuild, the recommendations, the O(n^2) dead-zone analysis, a full canvas
repaint and the 3D window. Bound directly to `<B1-Motion>` it ran 60-120 times a
second while Tk painted once per frame.

On 4 cameras in "compare" mode: **18.93 ms -> 0.69 ms per drag event (27x)**.

## The three Windows build failures

**1. `ModuleNotFoundError: No module named 'tkinter'`**
The building interpreter, or its venv, has no tkinter. `build_exe.bat` picks the
first interpreter that has it and is not a conda path (PyInstaller + Anaconda
drops Tcl/Tk). Some Windows venv layouts collect the `_tkinter` C extension but
miss the Python package, so the spec ships `tkinter` as a file tree.

Caution when asserting on the TOC: `_tkinter` is listed by full archive path, not
bare name. Match `os.path.basename(n).startswith("_tkinter.")`.

**2. `Can't find a usable init.tcl`**
Nothing set `TCL_LIBRARY`. PyInstaller's own tkinter runtime hook is only wired
in when `tkinter` is in the module graph; when the graph misses it, no hook.
`runtime_hook_tcltk.py` runs before application code, finds `init.tcl` / `tk.tcl`
inside the bundle, and sets both variables.

**3. `invalid command name "::msgcat::mcmset"`**
`ttkbootstrap` calls `::msgcat::mcmset` while building its theme. `msgcat` is
**not** a package inside `tcl8.6/` - it is a Tcl Module at `tcl8/<ver>/msgcat-*.tm`.
**On Linux that directory is nested inside `tcl8.6/`, so bundling `tcl8.6` catches
it; on Windows it is a sibling under `<Python>/tcl/`, so bundling `tcl8.6` alone
loses it.** That asymmetry is why the failure could not be reproduced on Linux for
four rounds.

From source in a venv the same error is fixed by `configure_tk_paths()` running at
the **top of `theme.py`**, before `ttkbootstrap` is imported. Import order matters:
`theme.py` is imported first by the entry point.

**Cascade.** ttkbootstrap's Style is a process-wide singleton; one msgcat failure
poisoned every later `StyledButton` with "application has been destroyed", killing
the Camera DB and Spec Assistant windows. `theme.disable_ttkbootstrap(reason)`
switches the whole process to plain ttk after the first failure.

**Empty, unclosable windows.** Root cause was `console=False` sending Tk callback
tracebacks to a null stderr, plus a window calling its build method *before*
`protocol("WM_DELETE_WINDOW", ...)`. Set the protocol first, wrap the build in
`errors.guarded_build`, and keep `console=True` until a build is proven.

Every build writes `build/tkinter-diagnostic.txt`: interpreter, where tkinter and
the Tcl/Tk libraries resolved from, which modules entered the bundle. Read it
before guessing.

## Claims that turned out to be wrong

Kept so they are not re-derived:

- A dangling `tkinter._default_root` does **not** break `StringVar` while the root
  is alive. The only observed failure is `tkfont.families()` after `root.destroy()`.
- Ray aliasing in the viewshed was **not** a real problem: measured ray spacing
  0.29-2.68 m against 5-10 m terrain cells.
