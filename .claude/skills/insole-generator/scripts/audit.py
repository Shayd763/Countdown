#!/usr/bin/env python3
"""Automated auditor for the insole generator.

Generates a footbed from a params file and checks it against the acceptance
spec in AUDIT.md: mesh integrity, weight-bearing zones staying low, arch
contour fidelity against an INDEPENDENT anatomical target, surface smoothness
(no pressure spikes), heel-cup sanity, shoe-fit, and printability.

The contour target is defined here from first principles (not from the
generator's own formulas) so the check is a real yardstick, not the design
grading its own homework.

Usage:
    python audit.py --params p.json [--out audit] [--figure]
Exit code is 0 only if every hard criterion passes.
"""
from __future__ import annotations
import argparse, json, sys
import numpy as np
import insole as I
from shapely import contains_xy


# --------------------------------------------------------------------------- #
# Thresholds (the spec, in numbers)
# --------------------------------------------------------------------------- #
TH = dict(
    heel_zone_max_rise=2.5,      # mm above base under the heel strike point
    ball_zone_max_rise=3.5,      # mm above base directly under the ball
    toe_zone_max_rise=2.5,       # mm above base under the toes
    forefoot_max_height=9.0,     # mm total, t>0.80 (won't jam the toe box)
    arch_apex_tol=3.5,           # mm, |actual apex - (base+arch_height)|
    arch_rms_max=4.0,            # mm, RMS deviation from anatomical target
    slope_max_deg=45.0,          # weight-bearing + arch surface
    heel_rim_slope_max_deg=68.0, # steeper allowed on the cup rim
    curvature_max=6.0,           # mm/mm^2-ish, peak |laplacian| (pressure spike)
    heel_rim_min=7.0,            # mm, rim height above cup centre (after rim rounding)
    heel_rim_max=20.0,
    width_margin_max=6.0,        # mm, footbed width - forefoot_width
    min_thickness=2.5,           # mm anywhere on the top surface
    length_tol=2.5,              # mm
)


# --------------------------------------------------------------------------- #
# Independent anatomical target (what a comfortable footbed SHOULD look like)
# --------------------------------------------------------------------------- #
def target_height(x, y, p):
    """Ideal top-surface height from first principles.

    - Heel strike (~t 0.08), the ball line and the toes are weight-bearing, so
      the surface sits at the base thickness there.
    - The medial longitudinal arch fills the gap between them: a smooth hump
      peaking at ``arch_height`` at the arch apex, zero at heel-strike and ball,
      ramping from the lateral side (no fill) to the medial side (full fill).
    Deliberately uses plain hann/ramp shapes, unlike the generator.
    """
    length = p["foot_length"]
    base = p["base_thickness"]
    t = np.clip(y / length, 0, 1)
    hw = np.maximum(p["_half"](t), 1e-3)
    med = (x - p["_shift"](t)) * p["medial_sign"] / hw

    strike = 0.08
    ball = p["_ball_frac"]
    apex = p.get("arch_peak", 0.34)

    # longitudinal hann: 0 at strike and ball, 1 at apex (two half-cosines)
    lo = np.zeros_like(t)
    up = (t >= strike) & (t <= apex)
    dn = (t > apex) & (t <= ball)
    lo[up] = 0.5 - 0.5 * np.cos(np.pi * (t[up] - strike) / max(apex - strike, 1e-6))
    lo[dn] = 0.5 + 0.5 * np.cos(np.pi * (t[dn] - apex) / max(ball - apex, 1e-6))
    # medial ramp: 0 lateral -> 1 medial (smooth)
    ramp = np.clip((med + 0.2) / 1.2, 0, 1)
    ramp = ramp * ramp * (3 - 2 * ramp)
    return base + p["arch_height"] * lo * ramp


# --------------------------------------------------------------------------- #
# Sampling helpers
# --------------------------------------------------------------------------- #
def _sample(p):
    ext, poly = I.build_outline(p)
    minx, miny, maxx, maxy = poly.bounds
    step = 1.5
    xs = np.arange(minx - 2, maxx + 2, step)
    ys = np.arange(miny - 2, maxy + 2, step)
    gx, gy = np.meshgrid(xs, ys)
    inside = contains_xy(poly, gx.ravel(), gy.ravel()).reshape(gx.shape)
    Z = I.top_height(gx.ravel(), gy.ravel(), p).reshape(gx.shape)  # analytic
    length = p["foot_length"]
    T = np.clip(gy / length, 0, 1)
    hw = np.maximum(p["_half"](T.ravel()).reshape(T.shape), 1e-3)
    MED = (gx - p["_shift"](T.ravel()).reshape(T.shape)) * p["medial_sign"] / hw
    return dict(poly=poly, ext=ext, gx=gx, gy=gy, Z=Z, T=T, MED=MED,
                inside=inside, step=step, bounds=(minx, miny, maxx, maxy))


def _count_peaks(z, prominence):
    """Count strict local maxima above ``prominence``."""
    peaks = 0
    for i in range(1, len(z) - 1):
        if z[i] > z[i - 1] and z[i] >= z[i + 1] and z[i] > prominence:
            peaks += 1
    return peaks


def _zone_stat(s, tmask, medmask, base):
    m = s["inside"] & tmask & medmask
    if not m.any():
        return 0.0
    return float(np.nanmax(s["Z"][m]) - base)


# --------------------------------------------------------------------------- #
# The audit
# --------------------------------------------------------------------------- #
def audit(params):
    p = I._prepare(dict(params))
    mesh = I.generate_insole(params)
    s = _sample(p)
    base = p["base_thickness"]
    inside = s["inside"]
    T, MED, Z = s["T"], s["MED"], s["Z"]
    ball = p["_ball_frac"]
    results = []

    def add(name, ok, value, target, detail=""):
        results.append(dict(name=name, ok=bool(ok), value=value,
                            target=target, detail=detail))

    # -- A. mesh integrity ------------------------------------------------- #
    add("watertight", mesh.is_watertight, mesh.is_watertight, True)
    add("winding_consistent", mesh.is_winding_consistent,
        mesh.is_winding_consistent, True)
    add("single_body", mesh.body_count == 1, int(mesh.body_count), 1)
    import tempfile, os
    ok_exp = True
    with tempfile.TemporaryDirectory() as d:
        for fmt in ("stl", "3mf"):
            try:
                pth = os.path.join(d, f"a.{fmt}")
                mesh.export(pth)
                import trimesh
                rm = trimesh.load(pth, force="mesh")
                ok_exp = ok_exp and rm.is_watertight
            except Exception as e:
                ok_exp = False
    add("exports_reload_watertight", ok_exp, ok_exp, True)

    # -- B. weight-bearing zones stay low ---------------------------------- #
    # weight-bearing heel = central calcaneus contact patch: forward of the
    # posterior cup wall (t>0.07) and inboard of the side rims (|med|<0.35)
    heel_rise = _zone_stat(s, (T > 0.07) & (T < 0.15), np.abs(MED) < 0.35, base)
    add("heel_strike_low", heel_rise <= TH["heel_zone_max_rise"],
        round(heel_rise, 2), f"<= {TH['heel_zone_max_rise']}",
        "height above base under the heel")
    ball_rise = _zone_stat(s, (T > ball - 0.03) & (T < ball + 0.03),
                           np.abs(MED) < 0.6, base)
    add("ball_low", ball_rise <= TH["ball_zone_max_rise"],
        round(ball_rise, 2), f"<= {TH['ball_zone_max_rise']}",
        "height above base directly under the ball (met dome sits BEHIND it)")
    toe_rise = _zone_stat(s, (T > 0.90), np.abs(MED) < 0.8, base)
    add("toe_low", toe_rise <= TH["toe_zone_max_rise"],
        round(toe_rise, 2), f"<= {TH['toe_zone_max_rise']}")

    # -- C. arch contour fidelity vs independent target -------------------- #
    Ztar = target_height(s["gx"].ravel(), s["gy"].ravel(), p).reshape(Z.shape)
    arch_reg = inside & (T > 0.15) & (T < ball - 0.03) & (MED > -0.2)
    rms = float(np.sqrt(np.nanmean((Z[arch_reg] - Ztar[arch_reg]) ** 2)))
    add("arch_contour_rms", rms <= TH["arch_rms_max"], round(rms, 2),
        f"<= {TH['arch_rms_max']}", "RMS deviation from anatomical arch target")
    apex_h = float(np.nanmax(Z[inside & (T > 0.2) & (T < 0.55)]))
    apex_err = abs(apex_h - (base + p["arch_height"]))
    add("arch_apex_height", apex_err <= TH["arch_apex_tol"], round(apex_h, 2),
        f"{base + p['arch_height']:.1f} +/- {TH['arch_apex_tol']}")

    # -- D. smoothness / no pressure spikes -------------------------------- #
    gy_, gx_ = np.gradient(Z, s["step"], s["step"])
    slope = np.degrees(np.arctan(np.hypot(gx_, gy_)))
    wb_arch = inside & (T > p["heel_cup_end"])
    slope_wb = float(np.nanmax(slope[wb_arch])) if wb_arch.any() else 0.0
    add("slope_weightbearing", slope_wb <= TH["slope_max_deg"],
        round(slope_wb, 1), f"<= {TH['slope_max_deg']} deg",
        "steepest slope outside the heel cup")
    heel_reg = inside & (T <= p["heel_cup_end"])
    slope_heel = float(np.nanmax(slope[heel_reg])) if heel_reg.any() else 0.0
    add("slope_heel_rim", slope_heel <= TH["heel_rim_slope_max_deg"],
        round(slope_heel, 1), f"<= {TH['heel_rim_slope_max_deg']} deg")
    lap = (np.abs(np.gradient(gx_, s["step"], axis=1))
           + np.abs(np.gradient(gy_, s["step"], axis=0)))
    curv = float(np.nanpercentile(lap[inside], 99.5))
    add("curvature_no_spike", curv <= TH["curvature_max"], round(curv, 2),
        f"<= {TH['curvature_max']}", "99.5th-pct surface curvature")

    # -- E. heel cup sanity ------------------------------------------------ #
    hc = inside & (T < 0.15)
    if hc.any():
        rim = float(np.nanmax(Z[hc]))
        cup_center = _zone_stat(s, (T > 0.05) & (T < 0.12),
                                np.abs(MED) < 0.25, 0.0)
        rim_h = rim - cup_center
    else:
        rim_h = 0.0
    add("heel_cup_depth", TH["heel_rim_min"] <= rim_h <= TH["heel_rim_max"],
        round(rim_h, 2), f"{TH['heel_rim_min']}-{TH['heel_rim_max']}",
        "rim height above cup centre")

    # -- F. shoe fit ------------------------------------------------------- #
    ff_h = _zone_stat(s, (T > 0.80), np.abs(MED) < 0.95, 0.0)
    add("forefoot_not_bulky", ff_h <= TH["forefoot_max_height"],
        round(ff_h, 2), f"<= {TH['forefoot_max_height']}",
        "max total height in the forefoot/toe box")
    width = float(s["ext"][:, 0].max() - s["ext"][:, 0].min())
    margin = width - p["forefoot_width"]
    add("width_fits_shoe", margin <= TH["width_margin_max"], round(width, 1),
        f"<= forefoot+{TH['width_margin_max']} ({p['forefoot_width']+TH['width_margin_max']:.0f})")
    length_err = abs(mesh.extents[1] - p["foot_length"])
    add("length_matches", length_err <= TH["length_tol"],
        round(float(mesh.extents[1]), 1), f"{p['foot_length']} +/- {TH['length_tol']}")

    # -- G. printability --------------------------------------------------- #
    min_th = float(np.nanmin(Z[inside]))
    add("min_thickness", min_th >= TH["min_thickness"], round(min_th, 2),
        f">= {TH['min_thickness']}")
    add("heightfield_no_overhang", True, "n/a", True,
        "flat bottom + single-valued top => no support needed by construction")

    # -- H. handedness, single support, rounded toe ------------------------ #
    band = inside & (T > 0.20) & (T < ball - 0.05)
    hi = band & (MED > 0.4)
    lo = band & (MED < -0.4)
    med_hi = float(np.nanmean(Z[hi])) if hi.any() else base
    med_lo = float(np.nanmean(Z[lo])) if lo.any() else base
    # the medial-vs-lateral gap scales with arch height; require a clear,
    # arch-proportional bias to the medial (inside) edge
    med_thr = max(1.5, 0.20 * p["arch_height"])
    add("arch_on_medial_side", (med_hi - med_lo) >= med_thr,
        round(med_hi - med_lo, 2), f">= {med_thr:.1f} mm (medial higher than lateral)",
        "arch must be on the inside (medial) edge, not the outside")

    # count forefoot support waves along a lateral-ish line (arch ~absent there)
    ts = np.linspace(0.55, 0.99, 220)
    hwl = p["_half"](ts)
    xs = p["_shift"](ts) + (-0.10) * hwl * p["medial_sign"]
    zl = I.top_height(xs, ts * p["foot_length"], p) - base
    n_waves = _count_peaks(zl, prominence=1.5)
    add("single_forefoot_support", n_waves <= 1, n_waves, "<= 1 wave",
        "metatarsal + toe crest reading as two ridges")

    toe_edge = inside & (T > 0.93)
    tmin = float(np.nanmin(Z[toe_edge])) if toe_edge.any() else base
    add("toe_edge_rounded", tmin <= base - 0.4, round(tmin, 2),
        f"<= {base - 0.4:.1f}", "toe rim should roll down, not be a vertical cliff")

    hard_fail = [r for r in results if not r["ok"]]
    summary = dict(
        passed=len(hard_fail) == 0,
        n_checks=len(results),
        n_failed=len(hard_fail),
        volume_cm3=round(mesh.volume / 1000.0, 1),
        bbox_mm=[round(v, 1) for v in mesh.extents.tolist()],
        results=results,
    )
    return summary, p, s, Ztar, mesh


# --------------------------------------------------------------------------- #
# Figure
# --------------------------------------------------------------------------- #
def render_audit(p, s, Ztar, out_path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    except Exception:
        return False
    Z = np.where(s["inside"], s["Z"], np.nan)
    minx, miny, maxx, maxy = s["bounds"]
    length = p["foot_length"]; ball = p["_ball_frac"]; ms = p["medial_sign"]

    fig = plt.figure(figsize=(18, 8))

    ax = fig.add_subplot(1, 4, 1, projection="3d")
    stride = 3
    X = s["gx"][::stride, ::stride]; Y = s["gy"][::stride, ::stride]
    Zs = Z[::stride, ::stride]
    ax.plot_surface(X, Y, np.nan_to_num(Zs, nan=p["base_thickness"]),
                    cmap="viridis", linewidth=0, antialiased=True)
    ax.set_box_aspect((np.ptp(X), np.ptp(Y), np.ptp(Y) * 0.5))
    ax.view_init(elev=35, azim=-60); ax.set_title("Top surface (z x2)")
    ax.set_zlim(0, np.ptp(Y) * 0.5)

    ax1 = fig.add_subplot(1, 4, 2)
    im = ax1.imshow(Z, origin="lower", extent=[minx, maxx, miny, maxy],
                    aspect="equal", cmap="viridis")
    ax1.set_title("Top height (mm)"); fig.colorbar(im, ax=ax1, shrink=0.7)

    def prof(field, med):
        ts = np.linspace(0, 1, 240); hw = p["_half"](ts)
        xs = p["_shift"](ts) + med * hw * ms
        return ts * length, field(xs, ts * length)
    ax2 = fig.add_subplot(1, 4, 3)
    yy, za = prof(lambda x, y: I.top_height(x, y, p), 0.55)
    _, zt = prof(lambda x, y: target_height(x, y, p), 0.55)
    ax2.plot(yy, za, label="actual (medial)")
    ax2.plot(yy, zt, "--", label="anatomical target")
    ax2.legend(); ax2.grid(alpha=0.3); ax2.set_title("Arch: actual vs target")
    ax2.set_xlabel("heel <-> toe (mm)"); ax2.set_ylabel("height (mm)")

    ax3 = fig.add_subplot(1, 4, 4)
    med = np.linspace(-1, 1, 90)
    for tf, lbl in [(0.08, "heel"), (p.get("arch_peak", 0.34), "arch"),
                    (ball - 0.05, "met"), (ball + 0.05, "crest")]:
        yv = tf * length; hw = float(p["_half"](tf))
        xs = p["_shift"](np.array([tf]))[0] + med * hw * ms
        ax3.plot(med, I.top_height(xs, np.full_like(xs, yv), p), label=lbl)
    ax3.axvline(0, color="gray", ls=":"); ax3.legend(); ax3.grid(alpha=0.3)
    ax3.set_title("Across-foot sections")
    ax3.set_xlabel("lateral(-1) <-> medial(+1)"); ax3.set_ylabel("height (mm)")

    plt.tight_layout(); plt.savefig(out_path, dpi=88); plt.close(fig)
    return True


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", default="audit")
    ap.add_argument("--figure", action="store_true")
    args = ap.parse_args()
    with open(args.params) as f:
        params = json.load(f)
    summary, p, s, Ztar, mesh = audit(params)

    print(f"\n{'PASS' if summary['passed'] else 'FAIL'}  "
          f"({summary['n_checks']-summary['n_failed']}/{summary['n_checks']} checks)  "
          f"vol={summary['volume_cm3']}cm3  bbox={summary['bbox_mm']}")
    for r in summary["results"]:
        mark = "ok " if r["ok"] else "XX "
        print(f"  {mark}{r['name']:<26} {str(r['value']):>8}  (target {r['target']})"
              + (f"  - {r['detail']}" if r["detail"] and not r["ok"] else ""))
    with open(f"{args.out}_report.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {args.out}_report.json")
    if args.figure:
        print("wrote " + f"{args.out}_figure.png"
              if render_audit(p, s, Ztar, f"{args.out}_figure.png")
              else "figure skipped (no matplotlib)")
    sys.exit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    _main()
