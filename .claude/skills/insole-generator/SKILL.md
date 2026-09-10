---
name: insole-generator
description: >-
  Generate a custom, 3D-printable shoe insole (orthotic footbed) as an STL and
  3MF file from a set of foot measurements. Use this skill whenever the user
  provides foot measurements (length, width, arch, etc.) and wants an insole,
  footbed, orthotic, arch support, or foot-support model to print, or asks to
  "make/design/model an insole" or turn measurements into a printable foot
  support. Trigger even when the user does not say the word "STL" — any request
  to produce a printable insole/orthotic from measurements belongs here.
---

# Insole Generator

Turn a handful of foot measurements into a watertight, 3D-printable orthotic
footbed with the features a real supportive insole has — a **cupped heel**, a
**broad medial longitudinal arch**, a **metatarsal dome**, and a **toe crest** —
on a flat bottom that sits in the shoe. Output is STL and/or 3MF plus an
optional PNG preview.

These support features are **on by default** (that is what makes the insole
usable); each can be tuned or turned off by setting its height/depth to 0.

## When to use this

The user has (or can give you) foot measurements and wants a physical insole
they can print and put in a shoe. Typical asks: "make me an insole for these
measurements", "I need arch support modelled for a 26cm foot", "design an
orthotic footbed", "turn this footprint into a printable STL".

## Workflow

1. **Collect measurements.** You need at minimum `foot_length`, `heel_width`,
   and `forefoot_width` (all in millimetres), plus which `side` (left/right).
   For a supportive insole, also get `arch_height`. If the user has more
   measurements, use them; otherwise the script fills sensible defaults.
   If any *required* measurement is missing, ask for it — do not guess the
   foot length. See `references/measuring-and-printing.md` for exactly how to
   measure each value and for healthy default ranges.

2. **Set up the environment (once).** The generator needs a few Python
   libraries:
   ```bash
   pip install -r scripts/requirements.txt
   ```
   `matplotlib` is only needed for `--preview` and can be skipped.

3. **Build the params.** Either pass measurements as flags, or write a JSON
   file like `examples/example_params.json` and pass `--params`. JSON is
   cleaner when there are many values or you want to keep a record.

4. **Generate.** Run the script (see below). Always generate a `--preview` when
   you can — the user usually cannot open an STL quickly, and the preview PNG
   (outline + arch height map + cross-sections) is the fastest way for them to
   confirm the arch is on the right side and the right height before printing.

5. **Sanity-check the report.** The script prints a JSON report. Confirm
   `watertight: true` before handing over the file — a non-watertight mesh may
   not slice cleanly. Check the bounding box matches the foot (length and width
   in mm, height ≈ `base_thickness + arch_height`).

6. **Deliver.** Give the user the `.stl` (universally supported by slicers) and
   `.3mf` (preserves units/metadata), the preview PNG, and a one-line summary
   of the key dimensions and print settings from the reference file.

## Running the generator

From flags:
```bash
python scripts/insole.py \
  --side right --foot_length 260 --heel_width 62 --forefoot_width 98 \
  --arch_height 18 --out my_insole --preview
```

From a JSON params file:
```bash
python scripts/insole.py --params my_params.json --out my_insole --preview
```

Useful options:
- `--formats stl,3mf` — which files to write (default both). `stl` alone is fine
  for most slicers.
- `--preview [PATH]` — also write a PNG preview (defaults to `<out>_preview.png`).
- `--out BASENAME` — output path prefix.
- Any measurement can be overridden on the command line even when using
  `--params` (flags win), which is handy for quick "try arch_height 15" tweaks.

## Parameters

Required:
- `foot_length` (mm) — heel to tip of longest toe.
- `heel_width` (mm) — widest part of the heel.
- `forefoot_width` (mm) — width across the ball of the foot (the widest point).
- `side` — `left` or `right` (decides which side the arch goes on).

Strongly recommended (these place the support features correctly):
- `heel_to_ball` (mm) — heel to the ball of the foot. Sets where the widest
  point, arch end, metatarsal pad and toe crest sit. If omitted, a typical
  ratio (0.72 × length) is assumed.
- `arch_height` (mm) — peak height of the medial arch above the base
  (default 16). Higher = more aggressive support. See the reference file.
- `arch_peak_mm` (mm) — measured "arch peak from heel", if the capture app
  provides it; positions the arch apex. Otherwise a typical apex (~0.34 of
  length) is used.

Common:
- `midfoot_width` (mm) — waist width; if omitted, estimated from forefoot width.
- `base_thickness` (mm) — flat floor thickness under the whole insole
  (default 3.5).
- `heel_cup_depth` (mm) — how high the heel rims rise above the base to cradle
  the heel (default 12; set 0 for a flat heel).
- `metatarsal_height` (mm) — metatarsal dome behind the ball (default 5).
- `toe_crest_height` (mm) — transverse ridge just ahead of the ball that the
  toes curl over (default 6).

Advanced / optional:
- `arch_start`, `arch_peak`, `arch_end` — where along the foot (0=heel, 1=toe)
  the arch ramps up, peaks and ramps down. Auto-derived from `heel_to_ball`
  and `arch_peak_mm`; override only for fine control.
- `metatarsal_pos`, `toe_crest_pos` — longitudinal positions (0..1) of the pad
  and crest; auto-derived from the ball position.
- `resolution` (mm) — interior mesh spacing; smaller = smoother + heavier file
  (default 2.5). 2.0 gives a finer surface; 4.0 a lighter file.

## Notes on the geometry

- The **bottom is flat** (z=0) so the insole rests stably in the shoe; the
  **top is the contoured surface** the foot sits on. Total height at any point
  is the thickness there.
- **Heel cup**: the heel centre sits at the base thickness and the medial,
  lateral and posterior rims rise around it (`heel_cup_depth`), cradling the
  heel and controlling it at strike.
- **Medial longitudinal arch**: a *broad* dome (not a narrow spike) that is low
  on the lateral side and fills the whole medial arch, flowing forward out of
  the heel cup and easing down before the ball. On the medial (inner) side,
  automatically mirrored for left vs right feet.
- **Metatarsal dome**: a rise just *behind* the ball to spread load off the
  metatarsal heads.
- **Toe crest**: a transverse ridge just *ahead* of the ball that the toes
  rest over.
- The forefoot and toes stay near the base thickness so the shoe still fits.
- The outline is derived from the width measurements (widest point placed at
  the measured `heel_to_ball`) with a naturally straighter medial edge and
  rounded heel/toe — a foot shape, not an ellipse.
- Meshes are made watertight (top surface + flat bottom + side walls) and
  normals are fixed, so they drop straight into a slicer.

For how to take each measurement, healthy value ranges, material and print
settings (infill, orientation, flexible filament), read
`references/measuring-and-printing.md`.
