# Insole acceptance spec (audit rubric)

This is the definition of "usable, comfortable, contours to a foot inside a
shoe", expressed as measurable criteria. `scripts/audit.py` checks a generated
footbed against every criterion and returns pass/fail plus an audit figure.
A design is **accepted** only when all hard criteria pass across a spread of
foot sizes and arch types — not just one example.

## How to run the loop

```bash
# single design
python scripts/audit.py --params my_params.json --out audit --figure

# it exits 0 only if every check passes; --figure writes audit_figure.png
```

Run it on several representative feet (small/large, low/high arch, left/right,
and a minimal-input case) before trusting a geometry change. Treat any failure
as a defect to fix in the geometry — resist the urge to relax a threshold
unless you can justify the new number anatomically (and if you do, write the
reason next to it, as the heel-zone definitions below do).

## What the auditor cannot do

It validates geometry, printability, and contour against a *modeled* plantar
surface, and renders views for visual inspection. It cannot certify real
physical comfort. The loop's exit condition is "passes the spec and looks
right"; the last mile is always a real test print worn in the target shoe.

## The criteria

Thresholds live in `TH` at the top of `audit.py`. Grouped by intent:

### A. Mesh integrity (printable at all)
| Check | Requirement |
|---|---|
| `watertight` | mesh is closed |
| `winding_consistent` | consistent face normals |
| `single_body` | exactly one connected solid |
| `exports_reload_watertight` | STL and 3MF re-import as watertight |

### B. Weight-bearing zones stay low
The foot presses the insole flat where it bears weight; support goes in the
gaps. So under the heel-strike patch, directly under the ball, and under the
toes the surface must sit near the base thickness.
| Check | Requirement | Rationale |
|---|---|---|
| `heel_strike_low` | ≤ 2.5 mm above base | zone is the central calcaneus patch: `t∈[0.07,0.15]`, `|med|<0.35` — forward of the posterior cup wall and inboard of the side rims |
| `ball_low` | ≤ 3.5 mm above base | met dome sits *behind* the ball, not under it |
| `toe_low` | ≤ 2.5 mm above base | toes rest flat |

### C. Arch contour fidelity (contours to the foot)
Checked against an **independent** anatomical target defined in `audit.py`
(a plain hann hump ramping lateral→medial), so the generator can't grade its
own homework.
| Check | Requirement |
|---|---|
| `arch_contour_rms` | ≤ 4.0 mm RMS deviation from the target over the arch region |
| `arch_apex_height` | peak within ± 3.5 mm of `base + arch_height` |

### D. Smoothness (no pressure points)
| Check | Requirement | Rationale |
|---|---|---|
| `slope_weightbearing` | ≤ 45° outside the heel cup | steep ramps under load dig in |
| `slope_heel_rim` | ≤ 68° | the cup rim may be steeper, but still printable/comfortable |
| `curvature_no_spike` | 99.5th-pct surface curvature ≤ 6.0 | no knife-edge ridges |

### E. Heel cup sanity
| Check | Requirement |
|---|---|
| `heel_cup_depth` | rim rises 8–20 mm above the cup centre |

### F. Shoe fit
| Check | Requirement | Rationale |
|---|---|---|
| `forefoot_not_bulky` | forefoot/toe total height ≤ 9 mm | won't jam the toe box |
| `width_fits_shoe` | footbed width ≤ forefoot width + 6 mm | fits the shoe interior |
| `length_matches` | length within ± 2.5 mm of `foot_length` | |

### G. Printability
| Check | Requirement |
|---|---|
| `min_thickness` | ≥ 2.5 mm everywhere on the top surface |
| `heightfield_no_overhang` | flat bottom + single-valued top ⇒ no supports needed (by construction) |

## Reading the audit figure

`audit_figure.png` has four panels:
1. **3D top surface** (z exaggerated ×2) — overall shape.
2. **Top height map** — bright = supportive; check the arch is a medial band
   and the heel centre is dark (low).
3. **Arch: actual vs target** — the solid line should hug the dashed target.
4. **Across-foot sections** — heel should be a U (cupped), arch a lateral→medial
   ramp, met/crest gentle domes.

## Change log of thresholds

- Heel weight-bearing zone defined as `t∈[0.07,0.15] × |med|<0.35` (central
  calcaneus contact patch), excluding the posterior and side cup walls, which
  are support features, not weight-bearing surface.
