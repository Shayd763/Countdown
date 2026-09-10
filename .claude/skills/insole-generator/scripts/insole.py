#!/usr/bin/env python3
"""Parametric orthotic footbed generator.

Turns a handful of foot measurements into a watertight, 3D-printable insole
with the features a real supportive footbed has:

  * a cupped heel (low centre, raised medial + lateral rims),
  * a broad medial longitudinal arch fill that flows out of the heel cup,
  * a metatarsal dome behind the ball to offload the metatarsal heads,
  * a transverse toe crest just ahead of the ball,
  * a flat bottom that sits stably in the shoe.

Exports STL and 3MF plus an optional PNG preview.

Coordinate system (millimetres):
    x  -> lateral direction (width). +x / -x are the two sides of the foot.
    y  -> heel (y=0) to toe (y=length).
    z  -> up. The bottom sits flat on z=0; the contoured top is what the foot
          rests on, so total height at any point is the thickness there.
"""
from __future__ import annotations

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.spatial import Delaunay
from shapely.geometry import Polygon
from shapely import contains_xy
import trimesh


# --------------------------------------------------------------------------- #
# Small math helpers
# --------------------------------------------------------------------------- #
def _smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / max(edge1 - edge0, 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _window(t, start, peak, end):
    """Asymmetric raised-cosine window: 0 outside [start,end], 1 at peak."""
    out = np.zeros_like(t)
    up = (t >= start) & (t <= peak)
    dn = (t > peak) & (t <= end)
    out[up] = 0.5 - 0.5 * np.cos(np.pi * (t[up] - start) / max(peak - start, 1e-6))
    out[dn] = 0.5 + 0.5 * np.cos(np.pi * (t[dn] - peak) / max(end - peak, 1e-6))
    return out


def _bump(t, center, half_width):
    """Smooth compact bump, 1 at center, 0 by +/- half_width (raised cosine)."""
    d = np.abs(t - center) / max(half_width, 1e-6)
    return np.where(d < 1.0, 0.5 + 0.5 * np.cos(np.pi * d), 0.0)


# --------------------------------------------------------------------------- #
# Foot outline
# --------------------------------------------------------------------------- #
def _width_profile(length, heel_w, mid_w, fore_w, ball_frac):
    """Half-width(t) interpolator, t in [0,1] heel->toe.

    The widest point (ball) is placed at ``ball_frac`` so the outline matches
    the measured heel-to-ball length.
    """
    waist = min(0.40, ball_frac - 0.18)
    t = [0.00, 0.06, 0.16, waist,
         ball_frac - 0.08, ball_frac, min(ball_frac + 0.14, 0.93), 1.00]
    w = [0.34 * heel_w, 0.78 * heel_w, 1.00 * heel_w, 1.00 * mid_w,
         0.97 * fore_w, 1.00 * fore_w, 0.80 * fore_w, 0.42 * fore_w]
    t = np.asarray(t)
    # guard strict monotonicity
    for i in range(1, len(t)):
        if t[i] <= t[i - 1]:
            t[i] = t[i - 1] + 1e-3
    return PchipInterpolator(t, 0.5 * np.asarray(w))


def build_outline(p, n=400):
    length = p["foot_length"]
    half = p["_half"]
    medial_sign = p["medial_sign"]
    t = np.linspace(0.0, 1.0, n)
    y = t * length
    hw = half(t)

    window = np.sin(np.pi * np.clip((t - 0.10) / 0.75, 0, 1)) ** 2
    shift = medial_sign * p["_straighten"] * hw.max() * window

    x_med = shift + medial_sign * hw
    x_lat = shift - medial_sign * hw
    pts = np.vstack([np.column_stack([x_med, y]),
                     np.column_stack([x_lat[::-1], y[::-1]])])

    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    poly = poly.buffer(1.5).buffer(-1.5)  # clean/round the perimeter
    ext = np.asarray(poly.exterior.coords)[:-1]
    if _signed_area(ext) < 0:
        ext = ext[::-1]
    return ext, poly


def _signed_area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)


def _resample_ring(ring, spacing):
    seg = np.diff(np.vstack([ring, ring[:1]]), axis=0)
    d = np.hypot(seg[:, 0], seg[:, 1])
    s = np.concatenate([[0], np.cumsum(d)])
    total = s[-1]
    m = max(int(round(total / spacing)), 32)
    su = np.linspace(0, total, m, endpoint=False)
    x = np.interp(su, s, np.concatenate([ring[:, 0], ring[:1, 0]]))
    y = np.interp(su, s, np.concatenate([ring[:, 1], ring[:1, 1]]))
    return np.column_stack([x, y])


# --------------------------------------------------------------------------- #
# Top-surface height field  (the orthotic shape)
# --------------------------------------------------------------------------- #
def top_height(x, y, p):
    """Height of the top surface above z=0 at points (x,y)."""
    length = p["foot_length"]
    t = np.clip(y / length, 0, 1)
    hw = np.maximum(p["_half"](t), 1e-3)

    # normalized lateral coord: med in [-1,1], +1 == medial edge, 0 == centre
    med = (x - p["_shift"](t)) * p["medial_sign"] / hw

    ball = p["_ball_frac"]
    z = np.full(x.shape, float(p["base_thickness"]))

    # ---- heel cup: low centre, rims rise medial + lateral + posterior ------ #
    depth = p["heel_cup_depth"]
    if depth > 0:
        cup_win = _window(t, -0.05, 0.0, p["heel_cup_end"])   # strongest at heel
        cup_win = np.clip(cup_win, 0, 1)
        side = np.clip((np.abs(med) - 0.22) / 0.78, 0, 1) ** 1.5   # rims
        post = _smoothstep(0.10, 0.0, t)                          # back wall
        rim = np.maximum(side, post * 0.9)
        z += depth * cup_win * rim

    # ---- medial longitudinal arch: broad dome flowing out of the heel ------ #
    ah = p["arch_height"]
    if ah > 0:
        arch_win = _window(t, p["arch_start"], p["arch_peak"], p["arch_end"])
        # cross-section: near 0 on the lateral side, broad plateau on medial
        cross = 0.5 * (1 + np.tanh((med + 0.10) / 0.33))
        cross *= 1.0 - 0.30 * np.clip((med - 0.65) / 0.35, 0, 1)  # ease rim
        # a little lateral arch support too (much lower)
        lateral = 0.18 * np.clip((-med - 0.2) / 0.8, 0, 1) ** 1.5
        z += ah * arch_win * cross + ah * arch_win * lateral

    # ---- metatarsal dome: just behind the ball, central-lateral ------------ #
    mh = p["metatarsal_height"]
    if mh > 0:
        mpos = p.get("metatarsal_pos") or (ball - 0.05)
        wy = _bump(t, mpos, 0.075)
        wx = np.exp(-((med + 0.12) / 0.60) ** 2)
        z += mh * wy * wx

    # ---- toe crest: transverse ridge just ahead of the ball ---------------- #
    ch = p["toe_crest_height"]
    if ch > 0:
        cpos = p.get("toe_crest_pos") or (ball + 0.05)
        wy = _bump(t, cpos, 0.045)
        wx = np.exp(-((med) / 0.80) ** 2)          # spans most of the width
        z += ch * wy * wx

    return z


# --------------------------------------------------------------------------- #
# Meshing
# --------------------------------------------------------------------------- #
def _triangulate(poly, boundary, interior_spacing):
    minx, miny, maxx, maxy = poly.bounds
    xs = np.arange(minx, maxx, interior_spacing)
    ys = np.arange(miny, maxy, interior_spacing)
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    inner = poly.buffer(-interior_spacing * 0.6)
    keep = contains_xy(inner, gx, gy)
    interior = np.column_stack([gx[keep], gy[keep]])

    pts = np.vstack([boundary, interior])
    tri = Delaunay(pts)
    cent = pts[tri.simplices].mean(axis=1)
    good = contains_xy(poly, cent[:, 0], cent[:, 1])
    return pts, tri.simplices[good], len(boundary)


def _prepare(p):
    """Fill defaults and cache derived geometry helpers."""
    p["medial_sign"] = 1.0 if p.get("side", "right") == "left" else -1.0

    length = p["foot_length"]
    heel_w = p["heel_width"]
    fore_w = p["forefoot_width"]
    mid_w = p.get("midfoot_width") or 0.62 * fore_w

    # ball position from the heel-to-ball measurement, else a typical 0.72
    htb = p.get("heel_to_ball")
    ball = (htb / length) if htb else 0.72
    ball = float(np.clip(ball, 0.60, 0.82))
    p["_ball_frac"] = ball

    p.setdefault("base_thickness", 3.5)
    p.setdefault("arch_height", 16.0)
    # arch apex: honour a measured "arch peak from heel" (mm) if supplied,
    # otherwise a typical anatomical position.
    apk = p.get("arch_peak_mm")
    if apk and "arch_peak" not in p:
        p["arch_peak"] = float(np.clip(apk / length, 0.20, 0.48))
    p.setdefault("arch_peak", 0.34)
    p.setdefault("arch_start", max(0.10, p["arch_peak"] - 0.16))
    p.setdefault("arch_end", ball - 0.06)
    p.setdefault("heel_cup_depth", 12.0)
    p.setdefault("heel_cup_end", 0.30)
    p.setdefault("metatarsal_height", 5.0)
    p.setdefault("toe_crest_height", 6.0)
    p.setdefault("resolution", 2.5)

    p["_half"] = _width_profile(length, heel_w, mid_w, fore_w, ball)
    p["_straighten"] = 0.12
    ms = 0.12 * p["_half"](np.linspace(0, 1, 400)).max()

    def shift(t):
        window = np.sin(np.pi * np.clip((t - 0.10) / 0.75, 0, 1)) ** 2
        return p["medial_sign"] * ms * window

    p["_shift"] = shift
    return p


def generate_insole(params):
    """Build and return a watertight trimesh.Trimesh footbed."""
    p = _prepare(dict(params))
    ext, poly = build_outline(p)
    boundary = _resample_ring(ext, spacing=2.0)
    pts2d, faces, n_bnd = _triangulate(poly, boundary, p["resolution"])

    z_top = top_height(pts2d[:, 0], pts2d[:, 1], p)
    n = len(pts2d)
    verts = np.vstack([np.column_stack([pts2d, z_top]),
                       np.column_stack([pts2d, np.zeros(n)])])

    top_f = faces
    bot_f = faces[:, ::-1] + n

    ring = np.arange(n_bnd)
    nxt = np.roll(ring, -1)
    wall = []
    for a, b in zip(ring, nxt):
        wall.append([a, b, b + n])
        wall.append([a, b + n, a + n])
    wall = np.asarray(wall)

    mesh = trimesh.Trimesh(vertices=verts,
                           faces=np.vstack([top_f, bot_f, wall]),
                           process=True)
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.fix_normals()
    if mesh.volume < 0:
        mesh.invert()
    return mesh


# --------------------------------------------------------------------------- #
# Preview (no 3D viewer needed)
# --------------------------------------------------------------------------- #
def render_preview(params, out_path):
    """PNG: outline, top-height map, and both cross-sections + long. profiles."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False

    p = _prepare(dict(params))
    length = p["foot_length"]
    ext, poly = build_outline(p)
    minx, miny, maxx, maxy = poly.bounds
    gx, gy = np.meshgrid(np.linspace(minx, maxx, 200),
                         np.linspace(miny, maxy, 400))
    inside = contains_xy(poly, gx.ravel(), gy.ravel()).reshape(gx.shape)
    z = top_height(gx.ravel(), gy.ravel(), p).reshape(gx.shape)
    z = np.where(inside, z, np.nan)

    ball = p["_ball_frac"]
    ms = p["medial_sign"]

    def xy_at(med_vals, tf):
        yv = tf * length
        hw = float(p["_half"](tf))
        xs = p["_shift"](np.array([tf]))[0] + np.asarray(med_vals) * hw * ms
        zs = top_height(xs, np.full_like(xs, yv), p)
        return zs

    def profile(med):
        ts = np.linspace(0, 1, 240)
        hw = p["_half"](ts)
        xs = p["_shift"](ts) + med * hw * ms
        zs = top_height(xs, ts * length, p)
        return ts * length, zs

    fig = plt.figure(figsize=(17, 8))
    ax0 = fig.add_subplot(1, 4, 1)
    ax0.plot(ext[:, 0], ext[:, 1], "-k"); ax0.set_aspect("equal")
    ax0.axvline(0, color="gray", ls=":")
    ax0.set_title(f"Outline ({p.get('side','right')} foot)")

    ax1 = fig.add_subplot(1, 4, 2)
    im = ax1.imshow(z, origin="lower", extent=[minx, maxx, miny, maxy],
                    aspect="equal", cmap="viridis")
    ax1.set_title("Top height (mm)\n(bright = supportive)")
    fig.colorbar(im, ax=ax1, shrink=0.7)

    ax2 = fig.add_subplot(1, 4, 3)
    med = np.linspace(-1, 1, 80)
    for tf, lbl in [(0.08, "heel cup"), (p["arch_peak"], "arch"),
                    (ball - 0.05, "met pad"), (ball + 0.05, "toe crest")]:
        ax2.plot(med, xy_at(med, tf), label=lbl)
    ax2.axvline(0, color="gray", ls=":"); ax2.legend(); ax2.grid(alpha=0.3)
    ax2.set_title("Across-foot sections")
    ax2.set_xlabel("lateral (-1) <-> medial (+1)"); ax2.set_ylabel("height (mm)")

    ax3 = fig.add_subplot(1, 4, 4)
    for med_v, lbl in [(0.55, "medial line"), (0.0, "centre"),
                       (-0.55, "lateral line")]:
        yy, zz = profile(med_v)
        ax3.plot(yy, zz, label=lbl)
    ax3.legend(); ax3.grid(alpha=0.3)
    ax3.set_title("Heel->toe profiles")
    ax3.set_xlabel("heel <-> toe (mm)"); ax3.set_ylabel("height (mm)")

    plt.tight_layout()
    plt.savefig(out_path, dpi=90)
    plt.close(fig)
    return True


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main():
    import argparse, json, sys
    ap = argparse.ArgumentParser(description="Generate a printable orthotic footbed.")
    ap.add_argument("--params", help="JSON file of measurements")
    ap.add_argument("--out", default="insole", help="output basename")
    ap.add_argument("--formats", default="stl,3mf")
    ap.add_argument("--preview", nargs="?", const="__auto__",
                    help="also write a PNG preview (optionally give a path)")
    for k in ["foot_length", "heel_width", "forefoot_width", "midfoot_width",
              "heel_to_ball", "arch_height", "arch_peak", "arch_peak_mm",
              "base_thickness",
              "heel_cup_depth", "metatarsal_height", "toe_crest_height",
              "resolution"]:
        ap.add_argument(f"--{k}", type=float)
    ap.add_argument("--side", choices=["left", "right"])
    args = ap.parse_args()

    params = {}
    if args.params:
        with open(args.params) as f:
            params = json.load(f)
    for k, v in vars(args).items():
        if k in ("params", "out", "formats", "preview"):
            continue
        if v is not None:
            params[k] = v

    missing = [r for r in ["foot_length", "heel_width", "forefoot_width"]
               if r not in params]
    if missing:
        sys.exit(f"Missing required measurements: {', '.join(missing)}")

    mesh = generate_insole(params)
    report = {
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "volume_cm3": round(mesh.volume / 1000.0, 2),
        "bbox_mm": [round(v, 1) for v in mesh.extents.tolist()],
        "faces": int(len(mesh.faces)),
    }
    for fmt in [f.strip() for f in args.formats.split(",") if f.strip()]:
        path = f"{args.out}.{fmt}"
        mesh.export(path)
        print(f"wrote {path}")

    if args.preview:
        ppath = f"{args.out}_preview.png" if args.preview == "__auto__" else args.preview
        print(f"wrote {ppath}" if render_preview(params, ppath)
              else "preview skipped (matplotlib not installed)")

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    _main()
