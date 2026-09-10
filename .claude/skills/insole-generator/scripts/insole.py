#!/usr/bin/env python3
"""Parametric supportive insole generator (footprint + medial arch).

Builds a watertight 3D insole mesh from a handful of foot measurements and
exports it as STL and/or 3MF ready for printing.

Coordinate system (millimetres):
    x  -> lateral direction (width). +x / -x are the two sides of the foot.
    y  -> heel (y=0) to toe (y=length).
    z  -> up. The bottom of the insole sits flat on z=0 (rests in the shoe);
          the contoured top surface is what the foot rests on.
"""
from __future__ import annotations

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.spatial import Delaunay
from shapely.geometry import Polygon
from shapely import contains_xy
import trimesh


# --------------------------------------------------------------------------- #
# Foot outline
# --------------------------------------------------------------------------- #
def _width_profile(length, heel_w, mid_w, fore_w):
    """Monotone-safe half-width(t) interpolator, t in [0,1] heel->toe.

    Landmarks are fractions of foot length taken from typical foot morphology:
    rounded heel, narrow waist (arch), widest at the ball, tapering toe.
    Widths are full widths; we return half-widths.
    """
    # (t, full_width) control points
    t = [0.00, 0.06, 0.16, 0.40, 0.62, 0.72, 0.86, 1.00]
    w = [
        0.34 * heel_w,   # very back of heel (rounded)
        0.78 * heel_w,
        1.00 * heel_w,   # heel widest
        1.00 * mid_w,    # waist / arch (narrowest midfoot)
        0.97 * fore_w,
        1.00 * fore_w,   # ball widest
        0.80 * fore_w,
        0.42 * fore_w,   # toe end (rounded)
    ]
    pchip = PchipInterpolator(np.asarray(t), 0.5 * np.asarray(w))
    return pchip


def build_outline(length, heel_w, mid_w, fore_w, medial_sign,
                  medial_straighten=0.12, n=400):
    """Return an ordered (N,2) array of exterior boundary points (CCW).

    ``medial_straighten`` shifts the centreline toward the lateral side so the
    medial (big-toe) edge is straighter than the lateral edge, as in a real
    foot. ``medial_sign`` (+1/-1) selects which x direction is medial.
    """
    half = _width_profile(length, heel_w, mid_w, fore_w)
    t = np.linspace(0.0, 1.0, n)
    y = t * length
    hw = half(t)

    # Centreline offset: push the spine a little toward lateral so the medial
    # side is flatter. Strongest through the arch, fades at heel/toe.
    window = np.sin(np.pi * np.clip((t - 0.10) / 0.75, 0, 1)) ** 2
    shift = medial_sign * medial_straighten * hw.max() * window

    x_med = shift + medial_sign * hw
    x_lat = shift - medial_sign * hw

    # medial edge going heel->toe, then lateral edge toe->heel
    med = np.column_stack([x_med, y])
    lat = np.column_stack([x_lat[::-1], y[::-1]])
    pts = np.vstack([med, lat])

    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    # light smoothing of the perimeter keeps the printed edge clean
    poly = poly.buffer(1.2).buffer(-1.2)
    ext = np.asarray(poly.exterior.coords)[:-1]
    # ensure counter-clockwise
    if _signed_area(ext) < 0:
        ext = ext[::-1]
    return ext, poly


def _signed_area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)


def _resample_ring(ring, spacing):
    """Resample a closed ring to roughly uniform ``spacing`` (mm)."""
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
# Top-surface height field
# --------------------------------------------------------------------------- #
def _smooth_window(t, start, peak, end):
    """Asymmetric raised-cosine window, 0 outside [start,end], 1 at peak."""
    out = np.zeros_like(t)
    up = (t >= start) & (t <= peak)
    dn = (t > peak) & (t <= end)
    out[up] = 0.5 - 0.5 * np.cos(np.pi * (t[up] - start) / max(peak - start, 1e-6))
    out[dn] = 0.5 + 0.5 * np.cos(np.pi * (t[dn] - peak) / max(end - peak, 1e-6))
    return out


def top_height(x, y, p):
    """Height of the top surface above z=0 at points (x,y)."""
    length = p["foot_length"]
    t = np.clip(y / length, 0, 1)
    half = p["_half"]
    hw = np.maximum(half(t), 1e-3)

    # normalized lateral coord: med in [-1,1], +1 == medial edge
    med = (x - p["_shift"](t)) * p["medial_sign"] / hw

    z = np.full(x.shape, p["base_thickness"], dtype=float)

    # ---- medial longitudinal arch ----
    wy = _smooth_window(t, p["arch_start"], p["arch_peak"], p["arch_end"])
    # cross-section: peaks a bit inside the medial edge, fades to lateral side
    gx = np.exp(-((med - 0.55) / 0.42) ** 2)
    gx[med < -0.15] = 0.0
    # ease the arch down right at the medial rim so the edge stays comfortable
    rim = np.clip((1.0 - med) / 0.18, 0, 1)
    rim = np.where(med > 0.82, 0.5 - 0.5 * np.cos(np.pi * rim), 1.0)
    z += p["arch_height"] * wy * gx * rim

    # ---- optional heel cup ----
    if p.get("heel_cup_depth", 0) > 0:
        wyh = _smooth_window(t, 0.0, 0.02, p.get("heel_cup_end", 0.26))
        edge = np.clip((np.abs(med) - 0.35) / 0.65, 0, 1) ** 1.5
        z += p["heel_cup_depth"] * wyh * edge

    # ---- optional metatarsal pad ----
    if p.get("metatarsal_height", 0) > 0:
        c = p.get("metatarsal_pos", 0.66)
        wym = np.exp(-((t - c) / 0.045) ** 2)
        gxm = np.exp(-((med + 0.1) / 0.5) ** 2)
        z += p["metatarsal_height"] * wym * gxm

    return z


# --------------------------------------------------------------------------- #
# Meshing
# --------------------------------------------------------------------------- #
def _triangulate(poly, boundary, interior_spacing):
    """Delaunay of boundary + interior grid points, clipped to polygon."""
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
    # keep triangles whose centroid lies inside the polygon (handles the waist)
    cent = pts[tri.simplices].mean(axis=1)
    good = contains_xy(poly, cent[:, 0], cent[:, 1])
    faces = tri.simplices[good]
    return pts, faces, len(boundary)


def _prepare(p):
    """Fill defaults and cache the width profile / centreline shift so the
    outline builder and the height field agree on geometry."""
    medial_sign = 1.0 if p.get("side", "right") == "left" else -1.0
    p["medial_sign"] = medial_sign

    length = p["foot_length"]
    heel_w = p["heel_width"]
    fore_w = p["forefoot_width"]
    mid_w = p.get("midfoot_width") or 0.62 * fore_w
    p["_mid_w"] = mid_w

    p.setdefault("base_thickness", 3.5)
    p.setdefault("arch_height", 16.0)
    p.setdefault("arch_start", 0.16)
    p.setdefault("arch_peak", 0.40)
    p.setdefault("arch_end", 0.63)

    half = _width_profile(length, heel_w, mid_w, fore_w)
    ms = 0.12 * half(np.linspace(0, 1, 400)).max()

    def shift(t):
        window = np.sin(np.pi * np.clip((t - 0.10) / 0.75, 0, 1)) ** 2
        return medial_sign * ms * window

    p["_half"] = half
    p["_shift"] = shift
    return p


def generate_insole(params):
    """Build and return a watertight trimesh.Trimesh insole."""
    p = _prepare(dict(params))
    length = p["foot_length"]
    heel_w = p["heel_width"]
    fore_w = p["forefoot_width"]
    mid_w = p["_mid_w"]
    medial_sign = p["medial_sign"]

    ext, poly = build_outline(length, heel_w, mid_w, fore_w, medial_sign)
    boundary = _resample_ring(ext, spacing=2.0)

    pts2d, faces, n_bnd = _triangulate(poly, boundary,
                                       interior_spacing=p.get("resolution", 3.0))

    z_top = top_height(pts2d[:, 0], pts2d[:, 1], p)

    n = len(pts2d)
    top_v = np.column_stack([pts2d, z_top])
    bot_v = np.column_stack([pts2d, np.zeros(n)])
    verts = np.vstack([top_v, bot_v])

    top_f = faces
    bot_f = faces[:, ::-1] + n  # flip winding, offset to bottom block

    # side walls around the boundary ring (first n_bnd points are the ring)
    ring = np.arange(n_bnd)
    nxt = np.roll(ring, -1)
    wall = []
    for a, b in zip(ring, nxt):
        ta, tb = a, b
        ba, bb = a + n, b + n
        wall.append([ta, tb, bb])
        wall.append([ta, bb, ba])
    wall = np.asarray(wall)

    all_f = np.vstack([top_f, bot_f, wall])
    mesh = trimesh.Trimesh(vertices=verts, faces=all_f, process=True)
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.fix_normals()
    if not mesh.is_winding_consistent:
        mesh.fix_normals()
    if mesh.volume < 0:
        mesh.invert()
    return mesh


# --------------------------------------------------------------------------- #
# Preview (top-down height map + cross-sections) — no 3D viewer needed
# --------------------------------------------------------------------------- #
def render_preview(params, out_path):
    """Write a PNG showing the outline, top-surface height map and cross
    sections. Lets the user sanity-check a design without opening the STL.
    Requires matplotlib; returns False if it is not installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False

    p = _prepare(dict(params))
    length = p["foot_length"]
    ext, poly = build_outline(length, p["heel_width"], p["_mid_w"],
                              p["forefoot_width"], p["medial_sign"])
    minx, miny, maxx, maxy = poly.bounds
    gx, gy = np.meshgrid(np.linspace(minx, maxx, 160),
                         np.linspace(miny, maxy, 320))
    inside = contains_xy(poly, gx.ravel(), gy.ravel()).reshape(gx.shape)
    z = top_height(gx.ravel(), gy.ravel(), p).reshape(gx.shape)
    z = np.where(inside, z, np.nan)

    fig, axs = plt.subplots(1, 3, figsize=(15, 7))
    axs[0].plot(ext[:, 0], ext[:, 1], "-k")
    axs[0].set_aspect("equal")
    axs[0].set_title(f"Outline ({p.get('side','right')} foot)")
    axs[0].axvline(0, color="gray", ls=":")
    im = axs[1].imshow(z, origin="lower", extent=[minx, maxx, miny, maxy],
                       aspect="equal", cmap="viridis")
    axs[1].set_title("Top surface height (mm)")
    fig.colorbar(im, ax=axs[1], shrink=0.7)
    for frac, lbl in [(p["arch_peak"], "arch"), (0.68, "ball"), (0.12, "heel")]:
        row = np.argmin(np.abs(gy[:, 0] - frac * length))
        axs[2].plot(gx[row, :], z[row, :], label=f"{lbl}")
    axs[2].legend()
    axs[2].set_title("Cross-sections")
    axs[2].set_xlabel("lateral <-> medial (mm)")
    axs[2].set_ylabel("height (mm)")
    axs[2].grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=90)
    plt.close(fig)
    return True


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main():
    import argparse, json, sys
    ap = argparse.ArgumentParser(description="Generate a printable insole.")
    ap.add_argument("--params", help="JSON file of measurements")
    ap.add_argument("--out", default="insole", help="output basename")
    ap.add_argument("--formats", default="stl,3mf")
    ap.add_argument("--preview", nargs="?", const="__auto__",
                    help="also write a PNG preview (optionally give a path)")
    # allow all params on the command line too
    for k in ["foot_length", "heel_width", "forefoot_width", "midfoot_width",
              "arch_height", "base_thickness", "heel_cup_depth",
              "metatarsal_height", "resolution"]:
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

    required = ["foot_length", "heel_width", "forefoot_width"]
    missing = [r for r in required if r not in params]
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
    for fmt in args.formats.split(","):
        fmt = fmt.strip()
        if not fmt:
            continue
        path = f"{args.out}.{fmt}"
        mesh.export(path)
        print(f"wrote {path}")

    if args.preview:
        ppath = f"{args.out}_preview.png" if args.preview == "__auto__" else args.preview
        if render_preview(params, ppath):
            print(f"wrote {ppath}")
        else:
            print("preview skipped (matplotlib not installed)")

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    _main()
