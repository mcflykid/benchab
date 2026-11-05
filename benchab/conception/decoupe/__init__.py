"""Découpe expérimentale avec goupilles d'alignement et contrôles géométriques.

Les résultats numériques ne prouvent ni le jeu réel, ni la tenue d'un collage.
L'original n'est jamais modifié. Les fichiers dérivés restent dans prive/ ou
`/private/tmp`.
"""

import json
import math
import sys
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh

from benchab.fabrication import remplissage_localise as RL

R = Path(__file__).resolve().parents[3]

GOUPILLES = {
    "diametre": 5.0,
    "longueur": 10.0,
    "jeu": 0.2,
    "fond": 0.3,
    "nombre": 2,
    "positions": None,
    "paroi_min": 1.2,
}
SEGMENTS = 48
MAX_CANDIDATS = 100000
MAX_PAIRES = 262144
UNITES = {"mm": 1.0, "cm": 10.0, "m": 1000.0}


def _vers_manifold(m):
    # Le noyau refuse les tableaux en lecture seule provenant d'autres bibliothèques.
    r = mf.Manifold(
        mf.Mesh64(
            vert_properties=np.array(m.vertices, np.float64, order="C"),
            tri_verts=np.array(m.faces, np.uint64, order="C"),
        )
    )
    if r.is_empty() or not str(r.status()).endswith("NoError"):
        raise ValueError(f"pièce non solide pour la découpe ({r.status()}) : réparer avec imports-3d/reparer.py")
    return r


def _vers_trimesh(r):
    """Convertit sans fusionner les sommets, ce qui peut ouvrir certains maillages fins."""
    m = r.to_mesh64()
    return trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts), process=False)


def _repere(n):
    """Rotation 3 × 3 qui envoie l'axe z sur n."""
    z = np.array([0.0, 0.0, 1.0])
    if np.allclose(n, z):
        return np.eye(3)
    if np.allclose(n, -z):
        return np.diag([1.0, -1.0, -1.0])
    return trimesh.geometry.align_vectors(z, n)[:3, :3]


def _cylindre(rayon, hauteur, centre, n):
    c = mf.Manifold.cylinder(hauteur, rayon, rayon, SEGMENTS, True)
    return c.transform(np.hstack([_repere(n), np.reshape(centre, (3, 1))]))


def _plan(plan):
    if not isinstance(plan, dict) or set(plan) != {"origine", "normale"}:
        raise ValueError("plan : origine et normale uniquement")
    o, n = np.asarray(plan["origine"], float), np.asarray(plan["normale"], float)
    if o.shape != (3,) or n.shape != (3,) or not np.isfinite([o, n]).all() or np.linalg.norm(n) < 1e-9:
        raise ValueError("plan : trois coordonnées finies, normale non nulle")
    return o, n / np.linalg.norm(n)


def _parametres(goupilles):
    g = dict(GOUPILLES, **(goupilles or {}))
    if set(g) != set(GOUPILLES):
        raise ValueError("goupilles : clé inconnue " + ", ".join(sorted(set(g) - set(GOUPILLES))))
    for k in ("diametre", "longueur", "jeu", "fond", "paroi_min"):
        if (
            isinstance(g[k], bool)
            or not isinstance(g[k], (int, float))
            or not np.isfinite(g[k])
            or g[k] < 0
            or (k in ("diametre", "longueur") and g[k] == 0)
        ):
            raise ValueError(f"goupilles.{k} doit être fini et positif")
    if type(g["nombre"]) is not int or not 0 <= g["nombre"] <= 8:
        raise ValueError("goupilles.nombre : entier de 0 à 8")
    if g["positions"] is not None:
        try:
            nombre_positions = len(g["positions"])
        except TypeError:
            raise ValueError("goupilles.positions : liste de positions requise") from None
        if nombre_positions != g["nombre"]:
            raise ValueError("goupilles.positions : une position par goupille demandée requise")
    if g["diametre"] < 2 * 0.42 * 3:
        raise ValueError("goupilles.diametre < 2,5 mm : trop fin pour la buse de 0,4 mm")
    return g


def section(piece, o, n):
    """Renvoie les polygones de section et la matrice du plan local vers la pièce."""
    s = piece.section(plane_normal=n, plane_origin=o)
    if s is None:
        return [], None
    plan2d, vers_3d = s.to_2D(normal=n)
    return list(plan2d.polygons_full), vers_3d


def _paire_eloignee(candidats):
    """Diamètre exact des points candidats, mémoire bornée même sur leur enveloppe."""
    from shapely.geometry import MultiPoint

    hull = MultiPoint(candidats).convex_hull
    points = np.asarray(hull.exterior.coords)[:-1] if hull.geom_type == "Polygon" else np.asarray(hull.coords)
    if len(points) < 2:
        return None
    taille = max(1, min(64, MAX_PAIRES // len(points)))
    maximum = -1.0
    choix = None
    for debut in range(0, len(points), taille):
        delta = points[debut : debut + taille, None, :] - points[None, :, :]
        distances = np.einsum("ijk,ijk->ij", delta, delta)
        i, j = np.unravel_index(distances.argmax(), distances.shape)
        if distances[i, j] > maximum:
            maximum = distances[i, j]
            choix = [points[debut + i], points[j]]
    return choix


def positions_auto(polygones, vers_3d, marge, nombre):
    """Points éloignés sur une grille bornée, à au moins `marge` du bord."""
    from shapely.ops import unary_union

    if nombre == 0:
        return []
    zone = unary_union([p.buffer(-marge) for p in polygones])
    if zone.is_empty:
        return []
    if nombre == 1:
        c = zone.representative_point()
        choix = [[c.x, c.y]]
    else:
        x0, y0, x1, y1 = zone.bounds
        largeur, hauteur = x1 - x0, y1 - y0
        if not np.isfinite([x0, y0, x1, y1, largeur, hauteur]).all() or min(largeur, hauteur) <= 0:
            raise ValueError("Section non finie ou dégénérée")
        pas = max(min(largeur, hauteur) / 25, 0.25, max(largeur, hauteur) / MAX_CANDIDATS)
        nx, ny = max(1, math.ceil(largeur / pas)), max(1, math.ceil(hauteur / pas))
        while nx * ny > MAX_CANDIDATS:
            pas *= max(1.1, math.sqrt(nx * ny / MAX_CANDIDATS))
            nx, ny = max(1, math.ceil(largeur / pas)), max(1, math.ceil(hauteur / pas))
        gx, gy = np.meshgrid(np.linspace(x0, x1, nx + 2)[1:-1], np.linspace(y0, y1, ny + 2)[1:-1])
        import shapely

        cand = np.c_[gx.ravel(), gy.ravel()]
        cand = cand[shapely.contains_xy(zone, cand[:, 0], cand[:, 1])]
        if not len(cand):
            c = zone.representative_point()
            cand = np.array([[c.x, c.y]])
        choix = _paire_eloignee(cand)
        if choix is None or np.linalg.norm(choix[0] - choix[1]) < 2 * marge:
            c = zone.representative_point()
            choix = [[c.x, c.y]]
        while len(choix) < nombre:
            dist = np.min([np.linalg.norm(cand - c, axis=1) for c in choix], axis=0)
            if dist.max() < 2 * marge:
                break  # plus de place pour une goupille séparée
            choix.append(cand[dist.argmax()])
    return list(trimesh.transform_points(np.c_[np.asarray(choix, float), np.zeros(len(choix))], vers_3d))


def _epaisseur(piece, p, direction):
    """Distance du point p (dans la matière) à la surface le long de direction ; 0 si aucun impact."""
    loc, _, _ = piece.ray.intersects_location([p + direction * 1e-4], [direction])
    return float(np.min(np.linalg.norm(loc - p, axis=1))) if len(loc) else 0.0


def decouper(piece, plan, goupilles=None, contraintes=None):
    """(parties {'dessus', 'dessous'}, goupilles [trimesh], rapport). Statut FAIL si un contrôle impératif échoue."""
    o, n = _plan(plan)
    g = _parametres(goupilles)
    C = contraintes or {}
    ctl = []

    def ok(condition):
        return "PASS" if condition else "FAIL"

    M = _vers_manifold(piece)
    v0 = M.volume()
    polys, vers_3d = section(piece, o, n)
    if not polys:
        raise ValueError("le plan ne coupe pas la pièce")
    aire = sum(p.area for p in polys)
    r_trou = g["diametre"] / 2 + g["jeu"]
    prof = g["longueur"] / 2 + g["fond"]
    if g["positions"] is not None:
        pts = [np.asarray(p, float) for p in g["positions"]]
        if any(p.shape != (3,) or not np.isfinite(p).all() for p in pts):
            raise ValueError("goupilles.positions : points à trois coordonnées")
        pts = [p - n * np.dot(p - o, n) for p in pts]  # projetés sur le plan
    else:
        pts = positions_auto(polys, vers_3d, r_trou + g["paroi_min"], g["nombre"])
    if g["nombre"] and len(pts) < g["nombre"]:
        ctl.append(
            {
                "controle": "place des goupilles",
                "statut": "FAIL",
                "detail": f"{len(pts)}/{g['nombre']} goupille(s) logeable(s) avec {g['paroi_min']} mm de paroi",
            }
        )
    for i, p in enumerate(pts):
        for j, q in enumerate(pts[:i]):
            distance = float(np.linalg.norm(p - q))
            matiere = distance - 2 * r_trou
            ctl.append(
                {
                    "controle": f"espacement des goupilles {j + 1}/{i + 1}",
                    "statut": ok(matiere >= g["paroi_min"] - 1e-6),
                    "detail": f"{matiere:.3f} mm de matière entre logements (minimum {g['paroi_min']} mm)",
                }
            )
    # Parois : autour (distance au bord de la section) et au-delà du fond, de chaque côté.
    import shapely
    from shapely.ops import unary_union

    sec = unary_union(polys)
    inv = np.linalg.inv(vers_3d)
    for i, p in enumerate(pts):
        q = trimesh.transform_points([p], inv)[0]
        pt = shapely.Point(q[0], q[1])
        bord = sec.boundary.distance(pt)  # bord extérieur et trous de la section
        dedans = sec.contains(pt)
        haut, bas = _epaisseur(piece, p, n), _epaisseur(piece, p, -n)
        paroi_ok = dedans and bord - r_trou >= g["paroi_min"] - 1e-6 and min(haut, bas) - prof >= g["paroi_min"] - 1e-6
        ctl.append(
            {
                "controle": f"goupille {i + 1} : parois",
                "statut": ok(paroi_ok),
                "detail": (
                    f"paroi latérale {bord - r_trou:.2f} mm, "
                    f"au-delà du fond {min(haut, bas) - prof:.2f} mm (≥ {g['paroi_min']})"
                ),
            }
        )
    # Zones protégées : ni traversées par le plan, ni entamées par un logement.
    trous = [_cylindre(r_trou, 2 * prof, p, n) for p in pts]
    for z in C.get("protege", []):
        zm = RL.volume(z)
        s = (zm.vertices - o) @ n
        traverse = s.min() < -1e-9 and s.max() > 1e-9
        entame = any((_vers_manifold(zm) ^ t).volume() > 1e-6 for t in trous)
        ctl.append(
            {
                "controle": f"zone protégée {z.get('nom', '?')}",
                "statut": ok(not traverse and not entame),
                "detail": ("plan traversant ; " if traverse else "") + ("logement dedans" if entame else "")
                or "intacte",
            }
        )
    perce = M - mf.Manifold.batch_boolean(trous, mf.OpType.Add) if trous else M
    dessus, dessous = perce.split_by_plane(tuple(n), float(np.dot(n, o)))
    parties = {"dessus": _vers_trimesh(dessus), "dessous": _vers_trimesh(dessous)}
    for (nom, m), noyau in zip(parties.items(), (dessus, dessous)):
        corps = len(noyau.decompose())
        ctl.append(
            {
                "controle": f"partie {nom} : solide",
                "statut": "PASS"
                if m.is_watertight and m.is_volume and corps == 1
                else ("WARN" if m.is_watertight and m.is_volume else "FAIL"),
                "detail": f"fermée {m.is_watertight}, {corps} corps"
                + (" (plusieurs îlots séparés : vérifier qu’ils sont voulus)" if corps > 1 else ""),
            }
        )
    v_trous = v0 - perce.volume()
    somme = dessus.volume() + dessous.volume()
    ctl.append(
        {
            "controle": "conservation du volume",
            "statut": ok(abs(somme - perce.volume()) <= 1e-4 * v0),
            "detail": f"original {v0:.2f} mm³ = parties {somme:.2f} + logements {v_trous:.2f}",
        }
    )
    ecart = (
        np.abs(np.vstack([parties["dessus"].bounds[0], parties["dessous"].bounds[0]]).min(0) - piece.bounds[0]).max()
        + np.abs(np.vstack([parties["dessus"].bounds[1], parties["dessous"].bounds[1]]).max(0) - piece.bounds[1]).max()
    )
    ctl.append(
        {
            "controle": "dimensions extérieures conservées",
            "statut": ok(ecart < 1e-3),
            "detail": f"écart des bornes {ecart:.5f} mm",
        }
    )
    pions = [_vers_trimesh(_cylindre(g["diametre"] / 2, g["longueur"], p, n)) for p in pts]
    for i, p in enumerate(pions):
        if not p.is_watertight:
            ctl.append({"controle": f"goupille {i + 1} : solide", "statut": "FAIL", "detail": "non fermée"})
    statut = (
        "FAIL"
        if any(c["statut"] == "FAIL" for c in ctl)
        else ("WARN" if any(c["statut"] == "WARN" for c in ctl) else "PASS")
    )
    rapport = {
        "statut": statut,
        "plan": {"origine": o.tolist(), "normale": n.tolist()},
        "goupilles": dict(g, positions=[np.round(p, 4).tolist() for p in pts]),
        "section_mm2": round(aire, 3),
        "volumes_mm3": {
            "original": round(v0, 3),
            "dessus": round(dessus.volume(), 3),
            "dessous": round(dessous.volume(), 3),
            "logements": round(v_trous, 3),
            "goupilles": round(sum(p.volume for p in pions), 3),
        },
        "controles": ctl,
        "impression": {
            "dessus": f"face de coupe au plateau : poser la normale {(-n).round(3).tolist()} vers le bas",
            "dessous": f"face de coupe au plateau : poser la normale {n.round(3).tolist()} vers le bas",
            "goupilles": "couchées de préférence (fibres le long de l’axe) ; jeu à confirmer sur la bobine",
        },
        "limites": [
            "Jeu théorique, non mesuré pour cette bobine ; aucun assemblage imprimé.",
            "Tenue de l’assemblage (collage, cisaillement) non calculée.",
            "Positions automatiques : recherche sur une grille bornée, sans prise en compte des efforts.",
        ],
    }
    return parties, pions, rapport


def ecrire(parties, pions, rapport, sortie, *, prive=None):
    from .atelier import _destination

    sortie = _destination(sortie, prive)
    sortie.mkdir(parents=True)
    for nom, m in parties.items():
        m.export(sortie / f"partie-{nom}.stl")
    for i, p in enumerate(pions):
        p.export(sortie / f"goupille-{i + 1}.stl")
    (sortie / "decoupe.json").write_text(json.dumps(rapport, indent=1, ensure_ascii=False))
    return sortie


def main(argv=None, *, prive=None):
    import argparse

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"proposer", "calculer", "voir", "catalogue"}:
        from .cli import main as intelligente

        return intelligente(argv, prive=prive)
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = a.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("essai")
    e.add_argument("--sortie")
    d = sub.add_parser("decouper")
    d.add_argument("source")
    d.add_argument("--unites", required=True, choices=list(UNITES))
    d.add_argument("--plan", required=True)
    d.add_argument("--sortie", required=True)
    for k in ("diametre", "longueur", "jeu", "paroi"):
        d.add_argument("--" + k, type=float)
    d.add_argument("--nombre", type=int)
    o = a.parse_args(argv)
    if o.cmd == "essai":
        piece = trimesh.creation.box((40, 40, 40))
        piece.apply_translation((20, 20, 20))
        parties, pions, r = decouper(piece, {"origine": [20, 20, 20], "normale": [0, 0, 1]})
    else:
        piece = trimesh.load(o.source, force="mesh")
        piece.apply_scale(UNITES[o.unites])
        v = [float(x) for x in o.plan.split(",")]
        if len(v) != 6:
            a.error("--plan : ox,oy,oz,nx,ny,nz")
        g = {
            k: getattr(o, c)
            for k, c in (
                ("diametre", "diametre"),
                ("longueur", "longueur"),
                ("jeu", "jeu"),
                ("paroi_min", "paroi"),
                ("nombre", "nombre"),
            )
            if getattr(o, c) is not None
        }
        parties, pions, r = decouper(piece, {"origine": v[:3], "normale": v[3:]}, g)
        r["source"] = {"fichier": Path(o.source).name, "unites": o.unites}
    if o.sortie:
        print("écrit :", ecrire(parties, pions, r, o.sortie, prive=prive))
    print(json.dumps({k: r[k] for k in ("statut", "volumes_mm3", "controles")}, indent=1, ensure_ascii=False))
    return 0 if r["statut"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
