"""

Risque de décollement au plateau et accroche conseillée (outil commun, Claude, 24/10/2026 ; proposition 8 retenue
par l'utilisateur).
usage : python -I decollement.py <projet>/candidats/plaques.3mf [--placement preuves/placement.json] [--candidats
candidats] [--json sortie.json]
        (pièces posées = candidats/<modèle>.stl ; réglages par objet lus dans placement.json, réglage de projet dans
        le 3MF)
        python -I decollement.py <piece.stl> [...]                         (sans réglages : recommandation seule)
Pour chaque pièce posée : empreinte au plateau (faces à moins de 0,05 mm), plus grande longueur, élancement (hauteur
/ √aire d'appui),
coins vifs convexes de l'empreinte (angle intérieur < 100°). Recommandation (règles de départ, THÉORIQUES, à
requalifier par les
retours d'impression ; PLA sur PEI texturé à 55 °C) :
- « bordure » si élancement > 3 (pièce haute sur petite assise : bascule ou arrachement) ou appui < 60 mm² ;
- « oreilles » si longueur > 100 mm avec des coins vifs (côtés ≥ 3 mm, contour simplifié à 1 mm) (le retrait tire
sur les coins) : brim_type = brim_ears, que le slicer pose
  seulement aux coins plus aigus que brim_ears_max_angle (125° dans notre profil de référence) ;
- « aucune » sinon.
Si le 3MF est fourni, compare avec brim_type réellement réglé (objet, sinon projet) : WARN (code 1, non bloquant)
quand une pièce à
risque n'a ni oreilles ni bordure. Le slicer en mode auto_brim décide seul : signalé comme NON VÉRIFIÉ.

"""

import argparse
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

from benchab import reglages as schema_reglages


def empreinte(m):
    n, c = m.face_normals, m.triangles_center
    z0 = m.bounds[0, 2]
    tri = m.triangles[(n[:, 2] < -0.9) & (c[:, 2] < z0 + 0.05)][:, :, :2]
    ps = [Polygon(t) for t in tri if Polygon(t).area > 1e-6]
    return unary_union([p.buffer(1e-3) for p in ps]).buffer(-1e-3) if ps else Polygon()


def coins_vifs(poly, angle_max=100.0):
    n = 0
    for p in getattr(poly, "geoms", [poly]):
        if p.is_empty:
            continue
        c = np.array(p.simplify(1.0).exterior.coords)[:-1]  # contour simplifié à 1 mm : un arrondi ne fait pas de coin
        if len(c) < 3:
            continue
        sgn = 1 if Polygon(c).exterior.is_ccw else -1
        for i in range(len(c)):
            a, b, d = c[i - 1], c[i], c[(i + 1) % len(c)]
            u, v = a - b, d - b
            if min(np.linalg.norm(u), np.linalg.norm(v)) < 3:
                continue  # côtés de moins de 3 mm : détail, pas un coin qui tire
            ang = math.degrees(math.acos(np.clip(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-12), -1, 1)))
            convexe = sgn * ((b - a)[0] * (d - b)[1] - (b - a)[1] * (d - b)[0]) > 0
            if convexe and ang < angle_max:
                n += 1
    return n


def evaluer(m):
    e = empreinte(m)
    A = e.area
    H = float(m.extents[2])
    L = float(max(m.extents[:2]))
    el = H / math.sqrt(A) if A > 0 else float("inf")
    k = coins_vifs(e)
    if el > 3 or A < 60:
        rec, why = "bordure", f"élancement {el:.1f} (> 3) ou appui {A:.0f} mm² (< 60)"
    elif L > 100 and k:
        rec, why = "oreilles", f"longueur {L:.0f} mm et {k} coin(s) vif(s)"
    else:
        rec, why = "aucune", "appui suffisant, pas de coin vif sur une grande longueur"
    return {
        "appui_mm2": round(A),
        "longueur_mm": round(L, 1),
        "hauteur_mm": round(H, 1),
        "elancement": round(el, 2),
        "coins_vifs": k,
        "recommandation": rec,
        "raison": why,
    }


def depuis_projet(plaques, placement, candidats):
    """Pièces du projet (candidats/<modèle>.stl, déjà posées) et brim_type : réglage de l'objet
    (placement.json), sinon du 3MF. Réglages en unités natives (mm, s, °C, %) lus via le
    dictionnaire typé ; aucun réglage inconnu transformé."""
    with zipfile.ZipFile(plaques) as archive:
        st = schema_reglages.decoder(archive.read("Metadata/project_settings.config"))
    out, vus = [], {}
    for o in json.loads(Path(placement).read_text())["objects"]:
        f = Path(candidats) / f"{o.get('model', o['name'])}.stl"
        if f not in vus:
            vus[f] = trimesh.load_mesh(f)
        out.append(((o["name"], (o.get("reglages") or {}).get("brim_type")), vus[f]))
    return out, st.get("brim_type", "auto_brim")


def controler(arguments, *, placement=None, candidats=None):
    """Évalue des maillages STL ou les pièces référencées par un 3MF et un placement."""
    res, pire = {}, 0
    if arguments[0].suffix.lower() == ".3mf":
        racine_projet = arguments[0].resolve().parents[1]
        pieces, defaut = depuis_projet(
            arguments[0],
            placement or racine_projet / "preuves/placement.json",
            candidats or racine_projet / "candidats",
        )
        for i, ((nom, bo), m) in enumerate(pieces):
            r = evaluer(m)
            b = bo or defaut
            r["brim_type"] = b
            if r["recommandation"] == "aucune":
                r["statut"] = "PASS"
            elif b == "auto_brim":
                r["statut"] = "NON VÉRIFIÉ"
                r["detail"] = "auto_brim : le slicer décide seul ; vérifier la bordure tranchée"
            elif b in ("no_brim", None):
                r["statut"] = "WARN"
                r["detail"] = f"à risque ({r['recommandation']} conseillée) mais brim_type = {b}"
            else:
                r["statut"] = "PASS"
            res[f"{nom} #{i + 1}"] = r
            pire = max(pire, r["statut"] == "WARN")
    else:
        for fichier in arguments:
            if fichier.suffix.lower() != ".stl":
                raise ValueError(f"format non pris en charge : {fichier.suffix}")
            res[fichier.stem] = evaluer(trimesh.load_mesh(fichier))
    return res, int(bool(pire))


def main(argv=None):
    """Rapporte le risque de décollement géométrique, sans validation physique."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fichiers", nargs="+", type=Path)
    parser.add_argument("--placement", type=Path)
    parser.add_argument("--candidats", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    try:
        resultats, code = controler(args.fichiers, placement=args.placement, candidats=args.candidats)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(resultats, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("Contrôle refusé : " + str(error), file=sys.stderr)
        return 2
    for k, r in resultats.items():
        print(
            f"{r.get('statut', '-'):11s} {k} : {r['recommandation']} ({r['raison']})"
            f"{' ; ' + r['detail'] if r.get('detail') else ''}"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
