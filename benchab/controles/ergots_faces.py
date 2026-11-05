"""


Ergots par face d'assemblage (outil commun, Claude, 27/10/2026 ; règle de l'utilisateur après l'oubli de l'horloge
V4 : au moins 2
ergots par face où deux pièces se collent ou s'emboîtent, voir docs/guides/mecanismes-imprimes.md).
usage : python -I ergots_faces.py <dossier des STL en position d'assemblage> [--json sortie.json] [--ecarts
ecarts.json]
        [--contact 0.6] [--aire-min 100] [--minimum 2]
Méthode, pour chaque paire de pièces :
- faces de contact : triangles de l'une à moins de --contact mm de l'autre, regroupés par plan (normale à 10° près,
plan à 1 mm près) ;
  seules les faces PLANES d'au moins --aire-min mm² sont examinées (joints de coupe, coutures) ;
- ergots : logements fermés (trous intérieurs de la section) dans la zone de contact, coupée à 1 mm de profondeur
dans chaque pièce ;
  les logements des deux pièces qui se recouvrent comptent pour un seul ergot (clé séparée ou ergot intégré dans un
logement).
  Une gouttière ou une rainure qui débouche au bord de la face n'est pas un logement fermé : elle ne compte pas.
Statut par face : PASS si au moins --minimum ergots ; WARN si un écart est déclaré dans --ecarts ({"A|B":
"justification arbitrée
par l'utilisateur"}) ; FAIL sinon. Code 0 si aucun FAIL. Contrôle géométrique : il ne vérifie ni le jeu ni la tenue
des ergots.


"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import trimesh as tm
from shapely.geometry import Polygon
from shapely.ops import unary_union


def faces_contact(a, b, contact=0.6, aire_min=100.0):
    """
    Plans de contact de a contre b : [(point, normale sortante de a, triangles 3D)].
    """
    lo, hi = np.maximum(a.bounds[0], b.bounds[0]) - contact, np.minimum(a.bounds[1], b.bounds[1]) + contact
    if np.any(lo > hi):
        return []
    c = a.triangles_center
    dans = np.all((c >= lo) & (c <= hi), axis=1)
    ids = np.where(dans)[0]
    if not len(ids):
        return []
    _, dist, _ = tm.proximity.closest_point(b, c[ids])
    ids = ids[dist <= contact]
    plans = []
    for i in ids:
        n = a.face_normals[i]
        d = float(np.dot(n, c[i]))
        for p in plans:
            if np.dot(p["n"], n) > math.cos(math.radians(10)) and abs(np.dot(p["n"], c[i]) - p["d"]) < 1.0:
                p["ids"].append(i)
                break
        else:
            plans.append({"n": n, "d": d, "ids": [i]})
    return [
        (a.triangles_center[p["ids"]].mean(0), p["n"], a.triangles[p["ids"]])
        for p in plans
        if a.area_faces[p["ids"]].sum() >= aire_min
    ]


def repere(o, n):
    u = np.cross(n, [0, 0, 1.0] if abs(n[2]) < 0.9 else [1.0, 0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    return lambda P: np.c_[(np.asarray(P) - o) @ u, (np.asarray(P) - o) @ v]


def trous(m, o, n, en2d, prof):
    """
    Trous intérieurs de la section de m dans le plan à prof mm à l'intérieur de m (n sortante de m).
    """
    s = m.section(plane_origin=o - n * prof, plane_normal=n)
    if s is None:
        return []
    polys = [Polygon(en2d(np.asarray(e))).buffer(0) for e in s.discrete if len(e) > 3]
    pleins = [p for p in polys if p.area > 0]
    # la section brute ne distingue pas extérieurs et trous : un contour contenu dans un autre contour est un trou
    return [
        p
        for p in pleins
        if any(q is not p and q.area > p.area and q.contains(p.representative_point()) for q in pleins)
    ]


def compter(a, b, o, n, tris, prof=1.0):
    en2d = repere(o, n)
    zone = unary_union([Polygon(en2d(t)).buffer(0.3) for t in tris]).buffer(-0.2)
    lg = [h for h in trous(a, o, n, en2d, prof) + trous(b, o, -n, en2d, prof) if h.area >= 4 and h.intersects(zone)]
    groupes = unary_union([h.buffer(0.05) for h in lg]) if lg else None
    return 0 if groupes is None else (len(groupes.geoms) if hasattr(groupes, "geoms") else 1), round(float(zone.area))


def main(a):
    """Rapporte les zones d’ergots en mm², sans modifier la géométrie."""
    if not a or a[0].startswith("--"):
        print(__doc__)
        return 2

    def opt(k, d):
        return float(a[a.index(k) + 1]) if k in a else d

    contact, aire_min, mini = opt("--contact", 0.6), opt("--aire-min", 100.0), int(opt("--minimum", 2))
    ecarts = json.loads(Path(a[a.index("--ecarts") + 1]).read_text()) if "--ecarts" in a else {}
    pieces = {f.stem: tm.load_mesh(f) for f in sorted(Path(a[0]).glob("*.stl"))}
    res = {"dossier": a[0], "minimum": mini, "faces": []}
    noms = list(pieces)
    for i, x in enumerate(noms):
        for y in noms[i + 1 :]:
            for o, n, tris in faces_contact(pieces[x], pieces[y], contact, aire_min):
                k, aire = compter(pieces[x], pieces[y], o, n, tris)
                cle = f"{x}|{y}"
                ec = ecarts.get(cle) or ecarts.get(f"{y}|{x}")
                st = "PASS" if k >= mini else ("WARN" if ec else "FAIL")
                res["faces"].append(
                    {
                        "pieces": [x, y],
                        "centre": np.round(o, 1).tolist(),
                        "normale": np.round(n, 3).tolist(),
                        "aire_contact_mm2": aire,
                        "ergots": k,
                        "statut": st,
                        **({"ecart": ec} if ec else {}),
                    }
                )
                print(
                    f"{st:5s} {x} / {y} : face de {aire} mm² en {np.round(o, 0).tolist()}, {k} ergot(s)"
                    f"{' — écart : ' + ec if ec and st == 'WARN' else ''}"
                )
    if not res["faces"]:
        print("NON APPLICABLE : aucune face de contact plane entre pièces")
    res["statut"] = (
        "FAIL"
        if any(f["statut"] == "FAIL" for f in res["faces"])
        else (
            "WARN"
            if any(f["statut"] == "WARN" for f in res["faces"])
            else ("PASS" if res["faces"] else "NON APPLICABLE")
        )
    )
    if "--json" in a:
        Path(a[a.index("--json") + 1]).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return 1 if res["statut"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
