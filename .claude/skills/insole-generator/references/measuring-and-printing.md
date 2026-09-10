# Measuring the foot & printing the insole

This reference covers (1) how to obtain each measurement the generator needs,
(2) healthy default ranges to sanity-check user input, and (3) how to print the
result so it is comfortable and durable.

## Table of contents
- [How to measure](#how-to-measure)
- [Typical value ranges](#typical-value-ranges)
- [Choosing the arch height](#choosing-the-arch-height)
- [Print settings](#print-settings)
- [Safety and fit notes](#safety-and-fit-notes)

## How to measure

Best done barefoot, standing (weight-bearing gives a realistic footprint),
with the foot on a sheet of paper. All values in **millimetres**.

- **foot_length** — Stand on paper, mark the back of the heel and the tip of the
  longest toe (not always the big toe). Measure the straight-line distance.
- **forefoot_width** (ball width) — The widest part of the foot, across the
  ball (the metatarsal heads, just behind the toes). This is usually the widest
  measurement of the whole foot.
- **heel_width** — The widest part of the heel.
- **midfoot_width** (optional) — The narrowest part of the arch/waist region,
  roughly halfway along the foot. If not supplied, it is estimated as ~62% of
  the forefoot width, which is typical.
- **arch_height** — This is a *design* value (how tall to make the support),
  not a measured one for most users. See
  [Choosing the arch height](#choosing-the-arch-height). If the user has a
  measured arch clearance (navicular height / arch gap under the foot), you can
  start the support a few mm below that so it contacts without jamming.
- **side** — left or right. Generate each foot separately (a pair = two runs
  with `--side left` and `--side right`).

Tips for accuracy:
- Trace the foot outline too — it's a good cross-check against the generated
  outline in the preview PNG.
- Measure both feet; they often differ. Make each insole to its own foot.
- If the user only gives a shoe size, convert to length as an approximation
  and tell them a real measurement will fit better. Rough guide: EU size ≈
  (length_mm + 15) / 6.67; US men's ≈ length_mm/25.4 × 3 − 22. Prefer a real
  measurement whenever possible.

## Typical value ranges

Use these to catch obvious input errors (e.g. cm entered as mm). These are
broad adult ranges — children and outliers fall outside them.

| Measurement      | Typical adult range | Note                              |
|------------------|---------------------|-----------------------------------|
| foot_length      | 220–300 mm          | ~24–30 cm                         |
| forefoot_width   | 85–115 mm           | widest point                      |
| heel_width       | 50–75 mm            | ~60–65% of forefoot width         |
| midfoot_width    | 50–75 mm            | narrowest; < forefoot width       |
| arch_height      | 10–25 mm            | see below                         |
| base_thickness   | 2.5–5 mm            | thicker = stiffer, less shoe room |

If a value looks off by ~10x (e.g. `foot_length: 26`), the user probably gave
centimetres — confirm and convert before generating.

## Choosing the arch height

The `arch_height` is the peak rise of the medial support above the base. There
is no single correct value — it depends on the user's arch and comfort
tolerance. Reasonable starting points:

- **Low / subtle support (flat feet easing in, or first-time users):** 10–14 mm
- **Moderate support (most people):** 14–18 mm
- **High / pronounced support (high arches, aggressive correction):** 18–25 mm

Guidance to give the user:
- Start lower than you think. An arch that is too high is uncomfortable and can
  cause pain; too low is merely less effective. It's easy to reprint taller.
- New orthotics should be broken in gradually (an hour or two a day at first).
- The support should feel like firm contact along the inner arch, not a hard
  point pushing into one spot. If they feel a single pressure point, reduce
  `arch_height` or widen the arch by moving `arch_start`/`arch_end` further
  apart.

## Print settings

The generated bottom is flat, which is the ideal print orientation.

- **Orientation:** print flat-side-down on the bed. No supports needed — the
  top surface is a gentle slope well under overhang limits.
- **Material:**
  - **TPU / flexible filament (recommended)** for a cushioned, shoe-like feel.
    Print slow (15–25 mm/s), ~95A shore for firm support or softer for cushion.
  - **PLA/PETG** works for a rigid "hard orthotic" but is stiff underfoot; pair
    with a thin foam top layer for comfort.
- **Infill:** 15–25% gyroid gives a good stiffness/comfort balance and some
  flex. Higher infill = firmer.
- **Layer height:** 0.2 mm is fine; 0.15 mm for a smoother top surface.
- **Walls:** 2–3 perimeters so edges hold up to repeated flexing.
- **Units:** the model is in millimetres. STL carries no units, so if a slicer
  imports it at the wrong scale, set units to mm. The 3MF file records units
  explicitly and avoids this.

## Safety and fit notes

- This is a comfort/support aid, not a medical device. For diabetes, chronic
  foot pain, diagnosed deformities, or post-injury needs, tell the user to see
  a podiatrist — custom medical orthotics are prescribed and fitted
  professionally.
- Check the insole fits the shoe: it should sit flat without buckling. If the
  shoe has a removable factory insole, trace it and compare against the
  preview outline; you can tune `forefoot_width`/`heel_width` to match.
- Reprint and iterate — the whole point of a parametric generator is that a
  small tweak (arch a bit lower, a touch narrower) is one quick re-run away.
