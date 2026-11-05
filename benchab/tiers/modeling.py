"""Contrôles géométriques MIT ; les contrôles existants ne couvrent pas les cartes de contact et invariants.

Copyright (c) 2026 Mohsen Esmaeili. Licence : modeling-LICENSE.txt.
Fonctions originales sélectionnées ; provenance et SHA dans modeling-sources.json.
"""

import itertools
import sys

import numpy as np
import trimesh

SPLIT_ALIAS = {}
_results = []


def load_parts(path):
    """name -> world-transformed Trimesh (same-name nodes merged)."""
    loaded = trimesh.load(path)
    if isinstance(loaded, trimesh.Trimesh):
        return {"part": loaded}
    parts = {}
    for node in loaded.graph.nodes_geometry:
        T, gname = loaded.graph[node]
        m = loaded.geometry[gname].copy()
        m.apply_transform(T)
        key = node or gname
        if key in parts:
            parts[key] = trimesh.util.concatenate([parts[key], m])
        else:
            parts[key] = m
    return parts


def overlap_volume(a, b):
    """Intersection volume in mm^3, or None if boolean failed (non-volume input)."""
    try:
        inter = trimesh.boolean.intersection([a, b], engine="manifold")
        if inter is None or inter.is_empty:
            return 0.0
        return abs(inter.volume)
    except Exception:
        return None


def min_distance(a, b, n=800, seed=0):
    """Approx min surface-to-surface distance via sampled points (unsigned)."""
    pts, _ = trimesh.sample.sample_surface(a, n, seed=seed)
    d = trimesh.proximity.ProximityQuery(b).on_surface(pts)[1]
    return float(d.min())


def bbox_gap(a, b):
    """Cheap lower bound on the gap between two AABBs (0 if they overlap)."""
    lo = np.maximum(a.bounds[0], b.bounds[0])
    hi = np.minimum(a.bounds[1], b.bounds[1])
    gap = np.maximum(lo - hi, 0)
    return float(np.linalg.norm(gap))


def audit(parts, clearance, allow, label=""):
    failures, warnings = [], []
    names = sorted(parts)
    for na, nb in itertools.combinations(names, 2):
        a, b = parts[na], parts[nb]
        if bbox_gap(a, b) > clearance:
            continue
        vol = overlap_volume(a, b)
        # print-speed sub-splits: pieces of one object alias to the parent for the
        # whitelist lookup, and same-parent pieces may touch (their designed seam).
        na, nb = SPLIT_ALIAS.get(na, na), SPLIT_ALIAS.get(nb, nb)
        if na == nb:
            continue
        pair = frozenset((na, nb))
        if vol is None:
            warnings.append(f"  ?  {na} vs {nb}: boolean failed (non-volume mesh), fix watertightness and re-run")
            continue
        if vol > 1e-3:
            line = f"{na} vs {nb}: OVERLAP {vol:.2f} mm^3{label}"
            if pair in allow:
                warnings.append(f"  ~  {line} (whitelisted)")
            else:
                failures.append(f"  X  {line}")
            continue
        d = min_distance(a, b)
        if d < clearance:
            warnings.append(f"  !  {na} vs {nb}: TIGHT {d:.2f} mm (< {clearance}){label}")
    return failures, warnings


def sweep(parts, name, axis, start_deg, end_deg, steps, clearance, allow=(), pivot=None):
    """Contrôle une rotation discrète d'une pièce dans un assemblage.

    `parts` associe noms et maillages en mm ; `axis` vaut X, Y ou Z, les angles
    sont en degrés et `steps` inclut les deux extrémités. Renvoie un état par
    angle avec les échecs/alertes de `audit`. Les maillages d'entrée restent
    inchangés ; le balayage ne prouve ni trajectoire continue ni montage réel.
    """
    if name not in parts:
        raise ValueError("La pièce à balayer doit exister dans l'assemblage")
    if axis not in ("X", "Y", "Z"):
        raise ValueError("Axe attendu : X, Y ou Z")
    valeurs = (start_deg, end_deg, clearance)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) for v in valeurs):
        raise ValueError("Angles et jeu doivent être finis")
    if clearance <= 0 or type(steps) is not int or not 2 <= steps <= 181:
        raise ValueError("Jeu positif et 2 à 181 positions exigés")
    centre = np.asarray(parts[name].centroid if pivot is None else pivot, dtype=float)
    if centre.shape != (3,) or not np.isfinite(centre).all():
        raise ValueError("Pivot attendu en coordonnées x,y,z finies, en mm")
    angles = np.linspace(float(start_deg), float(end_deg), steps)
    positions = []
    for angle in angles:
        transform = trimesh.transformations.rotation_matrix(
            np.deg2rad(angle), {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[axis], point=centre
        )
        mobile = parts[name].copy()
        mobile.apply_transform(transform)
        placees = dict(parts)
        placees[name] = mobile
        echecs, alertes = audit(placees, clearance, set(allow), label=f" à {angle:.3f}°")
        positions.append({"angle_deg": round(float(angle), 6), "echecs": echecs, "alertes": alertes})
    return positions


def self_thickness(shell, min_wall, n=1500, seed=0):
    pts, fid = trimesh.sample.sample_surface(shell, n, seed=seed)
    th = trimesh.proximity.thickness(shell, pts, method="ray")
    th = th[np.isfinite(th) & (th > 1e-3)]
    if th.size == 0:
        sys.exit("no valid thickness rays; is the mesh watertight?")
    return float(np.percentile(th, 1)), float(th.min())


def breach_check(shell, parts, names, min_wall, pitch, seed=0):
    from scipy import ndimage

    vg = shell.voxelized(pitch).fill()  # solid outer form, cavities filled
    # pad one empty voxel of margin: the matrix spans exactly the mesh bounds, and
    # EDT needs to see "outside" beyond the outermost solid voxels
    mat = np.pad(vg.matrix, 1, constant_values=False)
    edt = ndimage.distance_transform_edt(mat) * pitch
    results = []
    for name, part in zip(names, parts):
        pts, _ = trimesh.sample.sample_surface(part, 2000, seed=seed)
        idx = (
            np.round(trimesh.transformations.transform_points(pts, np.linalg.inv(vg.transform))).astype(int) + 1
        )  # +1 for the pad
        inb = np.all((idx >= 0) & (idx < mat.shape), axis=1)
        depth = np.zeros(len(pts))
        ii = idx[inb]
        depth[inb] = edt[ii[:, 0], ii[:, 1], ii[:, 2]]
        i = int(np.argmin(depth))
        results.append((name, float(depth[i]), pts[i]))
    return results


def placed_meshes(path, skip=()):
    """name -> world-transformed mesh, from a GLB scene (or a single mesh file)."""
    loaded = trimesh.load(path)
    out = {}
    if isinstance(loaded, trimesh.Scene):
        for node in loaded.graph.nodes_geometry:
            T, gname = loaded.graph[node]
            name = node if node else gname
            if any(k in name.lower() for k in skip):
                continue
            m = loaded.geometry[gname].copy()
            m.apply_transform(T)
            out[name] = m
    else:
        out[path] = loaded
    return out


def fit_map(meshes, near=2.5, patch_band=0.6, samples=2600, far=2.0, seed=0, max_patch=260, designed=()):
    """designed: iterable of frozenset({a, b}) name pairs whose press is INTENDED (whitelist)."""
    designed = set(designed)
    names = sorted(meshes)
    rows = []
    rng = np.random.default_rng(seed)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = meshes[names[i]], meshes[names[j]]
            lo = np.maximum(a.bounds[0], b.bounds[0])
            hi = np.minimum(a.bounds[1], b.bounds[1])
            if np.any(lo - hi > near):
                continue  # AABBs further apart than `near`
            small, big = (a, b) if a.area <= b.area else (b, a)
            try:
                pts, _ = trimesh.sample.sample_surface(small, samples, seed=rng)
                d = trimesh.proximity.signed_distance(big, pts)  # >0 = inside `big`
            except Exception as e:
                print("  ! %s <-> %s failed: %s" % (names[i], names[j], e), file=sys.stderr)
                continue
            k = int(np.argmax(d))
            if d[k] < -far:
                continue  # nothing meaningfully close
            vol = 0.0
            if d[k] > 0.005:
                # BOOLEAN-CONFIRM presses: the sampler can glitch at sharp corners (a real case
                # reported PRESS 0.09 on a centered blade); the intersection volume is the truth
                try:
                    iv = trimesh.boolean.intersection([a, b])
                    vol = float(iv.volume) if iv is not None and len(iv.faces) else 0.0
                except Exception:
                    vol = -1.0  # unknown — keep the press flag
            sel = np.where(d > -patch_band)[0]
            if len(sel) > max_patch:
                sel = rng.choice(sel, max_patch, replace=False)
            rows.append(
                dict(
                    a=names[i],
                    b=names[j],
                    mm=round(float(-d[k]), 3),
                    press=bool(d[k] > 0.005 and vol != 0.0),
                    vol=round(vol, 2),
                    designed=frozenset((SPLIT_ALIAS.get(names[i], names[i]), SPLIT_ALIAS.get(names[j], names[j])))
                    in designed
                    or SPLIT_ALIAS.get(names[i], names[i]) == SPLIT_ALIAS.get(names[j], names[j]),
                    at=[round(float(x), 2) for x in pts[k]],
                    patch=[
                        [
                            round(float(pts[q][0]), 2),
                            round(float(pts[q][1]), 2),
                            round(float(pts[q][2]), 2),
                            round(float(-d[q]), 3),
                        ]
                        for q in sel
                    ],
                )
            )
    rows.sort(key=lambda r: (-1e3 - r["mm"]) if r["press"] else r["mm"])
    return rows


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print(f"  {'ok ' if ok else 'X  '}{name}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


def finish():
    fails = [n for n, ok, _ in _results if not ok]
    print(f"\n{len(_results) - len(fails)}/{len(_results)} invariants hold")
    if fails:
        print("REGRESSED: " + ", ".join(fails))
        sys.exit(1)


def bore_pierces(mesh, start, direction, length, n=60):
    """True if a bore is open along its real axis: probe points along the axis must
    all be OUTSIDE the solid. start/direction in mm / unit vector."""
    d = np.asarray(direction, float)
    d /= np.linalg.norm(d)
    pts = np.asarray(start, float) + np.outer(np.linspace(0, length, n), d)
    return not mesh.contains(pts).any()


def open_from(mesh, point, direction, reach=500.0):
    """True if `point` (e.g. a pocket floor center) can be reached from `direction`
    without passing through the solid, i.e. the pocket is open (a bearing/motor can
    actually be inserted). Catches the sealed-void 'lid that is not a lid' class."""
    d = np.asarray(direction, float)
    d /= np.linalg.norm(d)
    pts = np.asarray(point, float) + np.outer(np.linspace(1e-3, reach, 120), d)
    return not mesh.contains(pts).any()


def clearance(a, b, n=600, seed=0):
    """Approx min surface distance between two parts (mm)."""
    pts, _ = trimesh.sample.sample_surface(a, n, seed=seed)
    return float(trimesh.proximity.ProximityQuery(b).on_surface(pts)[1].min())


def bore_is_keyed(mesh, center, axis, radius, z, n=90):
    """True if a bore section at height z deviates from a plain circle (D-flat, key,
    spline). A perfectly round result means the 'driven' part FREEWHEELS on its
    shaft; torque paths through plain bores have shipped to plastic twice."""
    ax = np.asarray(axis, float)
    ax /= np.linalg.norm(ax)
    # two perpendicular vectors spanning the section plane
    u = np.cross(ax, [1, 0, 0])
    u = np.cross(ax, [0, 1, 0]) if np.linalg.norm(u) < 1e-6 else u
    u /= np.linalg.norm(u)
    v = np.cross(ax, u)
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.asarray(center, float) + z * ax + radius * (np.outer(np.cos(th), u) + np.outer(np.sin(th), v))
    inside = mesh.contains(ring)
    # plain bore: no ring point at bore radius is inside the solid; keyed: some are
    return inside.any()
