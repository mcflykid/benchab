"""Contrôle numérique des deux premières couches.

Usage : benchab controle premiere-couche <tranche.3mf|fichier.gcode> [options]
--arcs-planaires qualifie les dépôts G17 à Z constant par distance analytique ;
sans l'option, les arcs restent NON VÉRIFIÉ.
Avec --json RAPPORT, --resume borne l’affichage et --detail affiche le rapport complet.

Méthode : les trajets extrudés des deux premières couches (types de la pièce seulement : parois, surfaces,
remplissages ; ni purge, ni
jupe, ni supports) sont tracés sur une grille (pas 0,1 mm, largeur de ligne lue dans le G-code, 0,42 mm par défaut).
Signale par plaque :
- îlot minuscule : zone extrudée isolée de moins de --ilot-max mm² en 1re couche (accroche faible, risque
d'arrachement) ;
- creux d'une couche : vide fermé de la 1re couche (moins de --creux-max mm²) recouvert à plus de 50 % dès la 2e
couche
  (gravure ou creux trop peu profond : inutile sous la pièce, et souvent des îlots à l'intérieur) ;
Statut WARN (code 1, non bloquant) si un défaut est trouvé, PASS sinon. Contrôle du fichier, pas de l'impression
réelle.

"""

import json
import math
import sys
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
from scipy import ndimage

from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.geometrie_arc import enveloppe_deposee_xyz
from benchab.gcode.lecture import lire_texte_gcode_3mf, plaques_gcode_archive
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

# Budget conservateur des tableaux NumPy/SciPy : 64 octets/cellule, 256 Mio.
MAX_CELLS = 4_000_000
MAX_SAMPLES = 2_000_000
MAX_SEGMENTS = 250_000
MAX_KERNEL = 4096
MAX_ARC_CELL_TESTS = 2_000_000
MAX_ARCS_PLANAIRES = 8192
MAX_DIAGNOSTICS_ARC = 128
TOLERANCE_Z_MARQUEUR_MM = 0.0005
TOLERANCE_RAYON_MM = 1e-9

PIECE = (
    "Outer wall",
    "Inner wall",
    "Overhang wall",
    "Bottom surface",
    "Top surface",
    "Internal solid infill",
    "Sparse infill",
    "Gap infill",
    "Bridge",
    "Internal Bridge",
    "Solid infill",
)


def gcodes(f):
    """Itère sur (numéro de plaque sans unité, texte G-code), sans modifier la source.

    Un seul contexte 3MF, avec vérification des numéros et du CRC de chaque G-code lu.
    Le G-code autonome conserve le numéro 1 et son mode de lecture historique.
    """
    f = Path(f)
    if f.suffix == ".gcode":
        yield 1, f.read_text(errors="replace")
        return
    with Archive3MF(f) as archive:
        # Conserver l’ordre par nom du rapport historique sans accepter les alias (#162).
        plaques = sorted(plaques_gcode_archive(archive), key=lambda plaque: plaque[1])
        for numero, membre in plaques:
            yield numero, lire_texte_gcode_3mf(f, membre, archive=archive)
        archive.verifier_source(f)


def deux_couches(g):
    """Extrait les segments extrudés [(x0, y0, x1, y1, largeur)] des deux premières couches."""
    couches, vus, w = [[], []], [], 0.42
    it = Interpreter()
    for line in g.splitlines():
        if line.startswith(";Z:"):
            z = float(line[3:])
            if not math.isfinite(z) or abs(z) > 1e6:
                raise ValueError("coordonnée Z invalide")
            z = round(z, 3)
            if z not in vus:
                vus.append(z)
            if len(vus) > 2:
                break
        elif line.startswith(";WIDTH:"):
            w = float(line[7:])
            if not math.isfinite(w) or not 0 < w <= 10:
                raise ValueError("largeur de ligne invalide (0 < largeur ≤ 10 mm)")
        move = it.feed_line(line)
        if move and move["op"] in {"G2", "G3"}:
            raise ValueError("arc analytique exact, mais représentation XY en segments non prise en charge")
        if move and vus and move["type"] in PIECE and move["deposited_length"] > 0:
            a, b = move["deposition_start"], move["end"]
            if sum(map(len, couches)) >= MAX_SEGMENTS:
                raise ValueError("budget de segments dépassé")
            couches[len(vus) - 1].append((a[0], a[1], b[0], b[1], w))
    if it.errors:
        raise ValueError("; ".join(it.errors))
    return couches, vus[:2]


def _extraire_couches(g, arcs_planaires):
    """Extrait les dépôts linéaires et, sur demande, les arcs G17 admissibles."""
    if not arcs_planaires:
        couches, zs = deux_couches(g)
        return couches, zs, [[], []]
    couches, arcs, vus, w = [[], []], [[], []], [], 0.42
    z_courant = None
    nombre_arcs = 0
    it = Interpreter(error_limit=MAX_DIAGNOSTICS_ARC)
    for line in g.splitlines():
        if line.startswith(";Z:"):
            z = float(line[3:])
            if not math.isfinite(z) or abs(z) > 1e6:
                raise ValueError("coordonnée Z invalide")
            z = round(z, 3)
            if z not in vus:
                vus.append(z)
            if len(vus) > 2:
                break
            z_courant = z
        elif line.startswith(";WIDTH:"):
            w = float(line[7:])
            if not math.isfinite(w) or not 0 < w <= 10:
                raise ValueError("largeur de ligne invalide (0 < largeur ≤ 10 mm)")
        move = it.feed_line(line)
        if move and vus and move["type"] in PIECE and move["deposited_length"] > 0:
            if z_courant is None:
                raise ValueError("arc déposé sans marqueur de couche Z déclaré")
            couche = vus.index(z_courant)
            if sum(map(len, couches)) + sum(map(len, arcs)) >= MAX_SEGMENTS:
                raise ValueError("budget de segments et arcs dépassé")
            if move["op"] in {"G2", "G3"}:
                bornes = enveloppe_deposee_xyz(move)
                if bornes[2][0] != bornes[2][1]:
                    raise ValueError("arc hélicoïdal : seules les trajectoires G17 à Z constant sont qualifiées")
                if any(
                    abs(float(point[2]) - z_courant) > TOLERANCE_Z_MARQUEUR_MM
                    for point in (move["start"], move["end"])
                ):
                    raise ValueError("Z physique de l'arc incohérent avec le Z déclaré par la couche")
                nombre_arcs += 1
                if nombre_arcs > MAX_ARCS_PLANAIRES:
                    raise ValueError("budget d'arcs planaires dépassé (8192 arcs)")
                arcs[couche].append((move, w))
            else:
                a, b = move["deposition_start"], move["end"]
                couches[couche].append((a[0], a[1], b[0], b[1], w))
    if it.errors:
        raise ValueError("; ".join(it.errors))
    return couches, vus[:2], arcs


def _preparer_arcs(arcs, origine, forme, pas):
    """Prépare les arcs et compte leurs boîtes d'échantillonnage avant les masques."""
    valides = []
    total = 0
    for mouvement, largeur_arc in arcs:
        if not math.isfinite(largeur_arc) or not 0 < largeur_arc <= 10:
            raise ValueError("largeur d'arc invalide")
        bornes = enveloppe_deposee_xyz(mouvement)
        if bornes[2][0] != bornes[2][1]:
            raise ValueError("arc hélicoïdal : Z constant requis")
        arc = mouvement["arc_g17"]
        debut = float(arc["debut_depot_fraction"])
        sweep = float(arc["balayage_signe_rad"]) * (1 - debut)
        cx, cy = map(float, arc["centre_xy_mm"])
        rayon = float(arc["rayon_mm"])
        start, end = mouvement["start"], mouvement["end"]
        # Les tolérances plus larges de l'interpréteur servent à lire le G-code ;
        # ici on refuse les extrémités arrondies qui changent le cercle mesuré.
        if not all(
            math.isclose(
                math.hypot(point[0] - cx, point[1] - cy),
                rayon,
                rel_tol=0.0,
                abs_tol=TOLERANCE_RAYON_MM,
            )
            for point in (start, end)
        ):
            raise ValueError("extrémité arrondie non qualifiée pour le cercle analytique")
        demi = float(largeur_arc) / 2
        i0 = max(0, math.floor((bornes[0][0] - demi - origine[0]) / pas))
        i1 = min(forme[0] - 1, math.ceil((bornes[0][1] + demi - origine[0]) / pas))
        j0 = max(0, math.floor((bornes[1][0] - demi - origine[1]) / pas))
        j1 = min(forme[1] - 1, math.ceil((bornes[1][1] + demi - origine[1]) / pas))
        if i0 <= i1 and j0 <= j1:
            total += (i1 - i0 + 1) * (j1 - j0 + 1)
        if not math.isfinite(sweep) or abs(sweep) <= 0 or abs(sweep) > math.tau:
            raise ValueError("balayage G17 invalide")
        angle_debut = float(arc["angle_debut_rad"]) + float(arc["balayage_signe_rad"]) * debut
        valides.append((i0, i1, j0, j1, cx, cy, rayon, angle_debut, sweep, demi))
    return valides, total


def masquer_arcs(arcs, origine, forme, pas, *, budget=MAX_ARC_CELL_TESTS):
    """Marque les points de grille à distance analytique d'arcs déposés, sans corde.

    Entrées : mouvements G17 avec largeur en mm, origine et pas de grille en mm, forme XY.
    Sortie : masque booléen ; refuse hélices, géométries invalides et budgets dépassés.
    Chaque point de grille est un échantillon numérique, pas une mesure physique.
    """
    if not math.isfinite(pas) or pas <= 0:
        raise ValueError("pas fini strictement positif requis")
    if len(forme) != 2 or any(not isinstance(n, int) or n <= 0 for n in forme) or math.prod(forme) > MAX_CELLS:
        raise ValueError("budget de grille dépassé (4 millions de cellules)")
    valides, total = _preparer_arcs(arcs, origine, forme, pas)
    if total > budget:
        raise ValueError("budget de cellules testées pour arcs dépassé")
    masque = np.zeros(forme, dtype=bool)
    tol = 1e-12
    for i0, i1, j0, j1, cx, cy, rayon, a0, sweep, demi in valides:
        if i0 > i1 or j0 > j1:
            continue
        x = origine[0] + np.arange(i0, i1 + 1, dtype=float) * pas
        y = origine[1] + np.arange(j0, j1 + 1, dtype=float) * pas
        xx, yy = np.meshgrid(x, y, indexing="ij")
        angles = np.arctan2(yy - cy, xx - cx)
        angle_delta = (angles - a0) % math.tau if sweep > 0 else (a0 - angles) % math.tau
        dans_arc = angle_delta <= abs(sweep) + tol
        radial = np.abs(np.hypot(xx - cx, yy - cy) - rayon)
        debut_xy = (cx + rayon * math.cos(a0), cy + rayon * math.sin(a0))
        fin_angle = a0 + sweep
        fin_xy = (cx + rayon * math.cos(fin_angle), cy + rayon * math.sin(fin_angle))
        distance_debut = np.hypot(xx - debut_xy[0], yy - debut_xy[1])
        distance_fin = np.hypot(xx - fin_xy[0], yy - fin_xy[1])
        distance = np.where(dans_arc, radial, np.minimum(distance_debut, distance_fin))
        couverture_arc = distance <= demi + tol
        if not couverture_arc.any():
            raise ValueError("arc déposé entièrement invisible sur la grille numérique")
        masque[i0 : i1 + 1, j0 : j1 + 1] |= couverture_arc
    return masque


def tracer(segs, origine, forme, pas):
    """Projette les segments et leur largeur en mm sur une grille XY bornée de pas en mm."""
    if not math.isfinite(pas) or pas <= 0:
        raise ValueError("pas fini strictement positif requis")
    if len(forme) != 2 or any(not isinstance(n, int) or n <= 0 for n in forme) or math.prod(forme) > MAX_CELLS:
        raise ValueError("budget de grille dépassé (4 millions de cellules)")
    if segs:
        data = np.asarray(segs, dtype=float)
        if (
            data.ndim != 2
            or data.shape[1] != 5
            or not np.isfinite(data).all()
            or np.any(np.abs(data[:, :4]) > 1e6)
            or np.any(data[:, 4] <= 0)
            or np.any(data[:, 4] > 10)
        ):
            raise ValueError("segments ou largeurs invalides")
        samples = np.maximum(2, np.ceil(np.hypot(data[:, 2] - data[:, 0], data[:, 3] - data[:, 1]) / (pas / 2)))
        if not np.isfinite(samples).all() or samples.sum() > MAX_SAMPLES:
            raise ValueError("budget de points de tracé dépassé")
        radius = max(1, int(round(float(data[:, 4].max()) / pas / 2)))
        if (2 * radius + 1) ** 2 > MAX_KERNEL:
            raise ValueError("budget du noyau de largeur dépassé")
    m = np.zeros(forme, bool)
    if not segs:
        return m
    s = np.array(segs)
    L = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
    n = np.maximum(2, np.ceil(L / (pas / 2)).astype(int))
    for k in np.unique(np.round(s[:, 4] / pas).astype(int)):  # une dilatation par largeur de ligne
        sel = np.round(s[:, 4] / pas).astype(int) == k
        c = np.zeros(forme, bool)
        for (x0, y0, x1, y1, _), nn in zip(s[sel], n[sel]):
            t = np.linspace(0, 1, nn)
            i = ((x0 + (x1 - x0) * t - origine[0]) / pas).astype(int)
            j = ((y0 + (y1 - y0) * t - origine[1]) / pas).astype(int)
            c[np.clip(i, 0, forme[0] - 1), np.clip(j, 0, forme[1] - 1)] = True
        r = max(1, int(round(k / 2)))
        yy, xx = np.ogrid[-r : r + 1, -r : r + 1]
        m |= ndimage.binary_dilation(c, structure=xx**2 + yy**2 <= r * r)
    return m


def controler(g, ilot_max=3.0, creux_max=150.0, pas=0.1, *, arcs_planaires=False):
    """Rend PASS, WARN ou NON VÉRIFIÉ avec seuils en mm² et grille en mm.

    L'option arcs_planaires qualifie les arcs G17 déposés à Z constant par mesure
    numérique ; aucun statut ne constitue une validation physique.
    """
    try:
        for value in (ilot_max, creux_max, pas):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("seuils et pas finis strictement positifs requis")
        return _controler(g, ilot_max, creux_max, pas, arcs_planaires=arcs_planaires)
    except (ValueError, OverflowError, MemoryError, IndexError) as exc:
        return {"statut": "NON VÉRIFIÉ", "detail": str(exc) or "budget mémoire dépassé"}


def _controler(g, ilot_max, creux_max, pas, *, arcs_planaires=False):
    try:
        (c1, c2), zs, (a1, a2) = _extraire_couches(g, arcs_planaires)
    except ValueError as ex:
        return {"statut": "NON VÉRIFIÉ", "detail": str(ex)}
    if not c1 and not a1:
        return {"statut": "NON VÉRIFIÉ", "detail": "aucune extrusion de pièce en 1re couche"}
    segments = c1 + c2
    if segments:
        coords = np.array([s[:4] for s in segments], dtype=float)
    else:
        coords = np.empty((0, 4), dtype=float)
    bornes_arcs = [enveloppe_deposee_xyz(m)[0:2] for m, _ in a1 + a2]
    coords_arcs = [v for bornes in bornes_arcs for axe in bornes for v in axe]
    if (coords.size and (not np.isfinite(coords).all() or np.any(np.abs(coords) > 1e6))) or any(
        not math.isfinite(v) or abs(v) > 1e6 for v in coords_arcs
    ):
        raise ValueError("coordonnées non finies ou extrêmes")
    if coords.size:
        xmin, ymin = coords[:, [0, 2]].min(), coords[:, [1, 3]].min()
        xmax, ymax = coords[:, [0, 2]].max(), coords[:, [1, 3]].max()
    else:
        xmin, ymin = math.inf, math.inf
        xmax, ymax = -math.inf, -math.inf
    for bornes in bornes_arcs:
        xmin, xmax = min(xmin, *bornes[0]), max(xmax, *bornes[0])
        ymin, ymax = min(ymin, *bornes[1]), max(ymax, *bornes[1])
    marge = max(2.0, max((largeur / 2 for _, largeur in a1 + a2), default=0.0))
    o = (xmin - marge, ymin - marge)
    forme = (int((xmax - o[0] + marge) / pas) + 1, int((ymax - o[1] + marge) / pas) + 1)
    if math.prod(forme) > MAX_CELLS:
        raise ValueError("budget de grille dépassé (4 millions de cellules)")
    if arcs_planaires:
        _, cellules_arcs = _preparer_arcs(a1 + a2, o, forme, pas)
        if cellules_arcs > MAX_ARC_CELL_TESTS:
            raise ValueError("budget de cellules testées pour arcs dépassé")
    m1, m2 = tracer(c1, o, forme, pas), tracer(c2, o, forme, pas)
    if arcs_planaires:
        m1 |= masquer_arcs(a1, o, forme, pas)
        m2 |= masquer_arcs(a2, o, forme, pas)
    px = pas * pas
    defauts = []
    lab, n = ndimage.label(
        ndimage.binary_dilation(m1, iterations=2)
    )  # traces à moins de 0,4 mm l'une de l'autre = même îlot (interstices)
    lab = np.where(m1, lab, 0)
    if n:
        aires = ndimage.sum(m1, lab, range(1, n + 1)) * px
        ctr = ndimage.center_of_mass(m1, lab, range(1, n + 1))
        for ar, (i, j) in zip(aires, ctr):
            if 0 < ar < ilot_max:
                defauts.append(
                    {
                        "type": "îlot minuscule",
                        "aire_mm2": round(float(ar), 2),
                        "xy": [round(o[0] + i * pas, 1), round(o[1] + j * pas, 1)],
                    }
                )
    trous = ndimage.binary_fill_holes(m1) & ~m1
    lab, n = ndimage.label(trous)
    for k in range(1, n + 1):
        z = lab == k
        ar = z.sum() * px
        if ar < 0.3 or ar > creux_max:
            continue
        cov = float((z & m2).sum() / z.sum())
        if cov > 0.5:
            i, j = np.argwhere(z).mean(0)
            defauts.append(
                {
                    "type": "creux d'une seule couche (recouvert dès la 2e)",
                    "aire_mm2": round(float(ar), 1),
                    "recouvert_pct": round(100 * cov),
                    "xy": [round(o[0] + i * pas, 1), round(o[1] + j * pas, 1)],
                }
            )
    resultat = {
        "statut": "WARN" if defauts else "PASS",
        "z_couches": zs,
        "segments": [len(c1), len(c2)],
        "aire_1re_couche_mm2": round(float(m1.sum() * px)),
        "defauts": defauts,
    }
    if arcs_planaires:
        resultat["arcs_planaires"] = {
            "statut": "PASS",
            "nombre": len(a1) + len(a2),
            "couches": [len(a1), len(a2)],
            "methode": "distance analytique aux arcs déposés, évaluée sur la grille numérique",
            "cellules_testees_max": MAX_ARC_CELL_TESTS,
            "arcs_max": MAX_ARCS_PLANAIRES,
            "diagnostics_max": MAX_DIAGNOSTICS_ARC,
            "tolerance_z_marqueur_mm": TOLERANCE_Z_MARQUEUR_MM,
            "tolerance_rayon_absolue_mm": TOLERANCE_RAYON_MM,
            "grille": {
                "pas_mm": pas,
                "origine_xy_mm": [round(o[0], 9), round(o[1], 9)],
                "forme_xy_cellules": list(forme),
            },
            "largeurs_ligne_mm": sorted({largeur for _, largeur in a1 + a2}),
            "portee": "qualification géométrique numérique ; aucune validation physique",
        }
    return resultat


def _resume(plaques, statut, sortie):
    """Rend cinq plaques prioritaires et cinq défauts au plus par plaque, avec le rapport complet."""
    ordre = {"NON VÉRIFIÉ": 0, "WARN": 1, "PASS": 2}
    choix = sorted(plaques.items(), key=lambda item: ordre[item[1]["statut"]])[:5]
    details = {}
    for nom, preuve in choix:
        defauts = preuve.get("defauts", [])
        details[nom] = {
            "statut": preuve["statut"],
            "nombre_defauts": len(defauts),
            "defauts": defauts[:5],
            "defauts_omis": max(0, len(defauts) - 5),
        }
        if "detail" in preuve:
            texte = preuve["detail"]
            details[nom]["detail"] = texte[:512]
            if len(texte) > 512:
                details[nom]["detail_tronque"] = True
    return {
        "statut": statut, "rapport": str(sortie), "nombre_plaques": len(plaques),
        "plaques_omises": len(plaques) - len(details), "plaques": details,
    }


def main(argv=None):
    """Contrôle la première couche en mm² ; --arcs-planaires active l'analyse G17 numérique."""
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0].startswith("--"):
        print(__doc__)
        return 2

    def opt(n, d):
        if n not in a:
            return d
        position = a.index(n) + 1
        if position == len(a):
            raise ValueError(f"Valeur requise après {n}")
        return float(a[position])

    resume, detail = "--resume" in a, "--detail" in a
    arcs_planaires = "--arcs-planaires" in a
    if (resume and detail) or ((resume or detail) and "--json" not in a):
        print("Utiliser --resume ou --detail avec --json RAPPORT.", file=sys.stderr)
        return 2
    sortie = None
    if "--json" in a:
        try:
            index_json = a.index("--json")
            if index_json + 1 >= len(a) or a[index_json + 1].startswith("--"):
                raise ValueError("Chemin requis après --json")
            # Refuser avant lecture/calcul pour protéger la source (#152, chemins-et-atelier.md).
            sortie = destination_neuve(Path(a[index_json + 1]))
        except (OSError, ValueError, IndexError) as erreur:
            print(f"Sortie JSON refusée : {erreur}", file=sys.stderr)
            return 2

    try:
        options = (opt("--ilot-max", 3.0), opt("--creux-max", 150.0), opt("--pas", 0.1))
        res = {f"plaque {k}": controler(g, *options, arcs_planaires=arcs_planaires) for k, g in gcodes(a[0])}
    except (OSError, ValueError, BadZipFile) as erreur:
        print(f"NON VÉRIFIÉ {a[0]} : {erreur}", file=sys.stderr)
        return 2
    statut = (
        "NON VÉRIFIÉ"
        if not res or any(r["statut"] == "NON VÉRIFIÉ" for r in res.values())
        else "WARN" if any(r["statut"] == "WARN" for r in res.values()) else "PASS"
    )
    rapport = {"fichier": a[0], "plaques": res}
    if not (resume or detail):
        for k, r in res.items():
            print(
                f"{r['statut']:5s} {k} : {len(r.get('defauts', []))} défaut(s) de 1re couche"
                + "".join(
                    f"\n      - {d['type']} : {d['aire_mm2']} mm² vers X {d['xy'][0]} Y {d['xy'][1]}"
                    for d in r.get("defauts", [])[:12]
                )
            )
    if sortie is not None:
        try:
            ecrire_json_neuf(sortie, rapport, entrees=[Path(a[0])])
        except (OSError, ValueError) as erreur:
            print(f"Sortie JSON refusée : {erreur}", file=sys.stderr)
            return 2
        # L’adresse n’est annoncée que si le rapport complet existe (#35).
        if resume:
            print(json.dumps(_resume(res, statut, sortie), ensure_ascii=False, allow_nan=False))
        elif detail:
            print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
    return {"PASS": 0, "WARN": 1, "NON VÉRIFIÉ": 2}[statut]


if __name__ == "__main__":
    raise SystemExit(main())
