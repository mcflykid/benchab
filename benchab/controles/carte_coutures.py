"""Cartographie les premiers dépôts de paroi extérieure après chaque changement de couche."""

import argparse
import html
import json
import math
import re
from pathlib import Path

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import iter_lignes_gcode_3mf, plaques_gcode_archive
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve
from benchab.qualite.statuts import code_statut


def _largeur_circulaire(angles, fraction):
    """Calcule en degrés le plus petit arc couvrant la fraction des angles."""
    tries = sorted(angles)
    doublon = tries + [x + 360 for x in tries]
    effectif = math.ceil(fraction * len(tries))
    return min(doublon[i + effectif - 1] - a for i, a in enumerate(tries))


def analyser(source, *, centre_xy_mm):
    """Renvoie les départs (mm, °) par plaque et objet avec leur dispersion angulaire.

    Le centre XY déclaré est exprimé en mm dans le repère du G-code. PASS décrit
    le relevé nominal. Une grammaire partiellement reconnue, un vase déclaré ou
    aucun départ exploitable donnent NON VÉRIFIÉ. Aucune archive n’est modifiée.
    """
    if len(centre_xy_mm) != 2 or not all(math.isfinite(v) for v in centre_xy_mm):
        raise ValueError("Centre XY fini en mm requis.")
    cx, cy = centre_xy_mm
    series = {}
    erreurs = []
    with Archive3MF(source) as archive:
        config = archive.reglages() if "Metadata/project_settings.config" in archive.noms() else {}
        if "spiral_mode" in config and reglages.valeur_brute(config, "spiral_mode") == "1":
            erreurs.append("Mode vase déclaré dans le 3MF : une couture par couche n’est pas définie.")
        for plaque, membre in plaques_gcode_archive(archive):
            it = Interpreter(error_limit=30)
            presents = set()
            couche = None
            for ligne in iter_lignes_gcode_3mf(source, membre, archive=archive):
                if re.fullmatch(r";\s*(?:spiral_mode|spiral_vase)\s*=\s*1", ligne.strip(), re.I):
                    diagnostic = f"Plaque {plaque} : le mode vase ne décrit pas une couture par couche."
                    if diagnostic not in erreurs:
                        erreurs.append(diagnostic)
                mouvement = it.feed_line(ligne)
                if not mouvement or mouvement["deposited_length"] <= 0 or mouvement["layer"] < 0:
                    continue
                if (mouvement["type"] or "").casefold() not in {"outer wall", "outer perimeter"}:
                    continue
                objet = mouvement["object"]
                if couche != mouvement["layer"]:
                    couche = mouvement["layer"]
                    presents.clear()
                if objet in presents:
                    continue
                presents.add(objet)
                debut = mouvement["deposition_start"]
                if debut is None:
                    diagnostic = f"Plaque {plaque} : départ de dépôt sur arc non cartographié."
                    if diagnostic not in erreurs:
                        erreurs.append(diagnostic)
                    continue
                x, y, z = debut
                if math.hypot(x - cx, y - cy) < 1e-9:
                    diagnostic = f"Plaque {plaque} : départ confondu avec le centre déclaré."
                    if diagnostic not in erreurs:
                        erreurs.append(diagnostic)
                    continue
                ligne_serie = series.setdefault((plaque, objet), dict(plaque=plaque, objet=objet, points=[]))
                if len(ligne_serie["points"]) >= 20000 or len(series) > 200:
                    raise ValueError("Budget de cartographie dépassé.")
                ligne_serie["points"].append({
                    "couche": couche, "xyz_mm": [x, y, z],
                    "angle_deg": math.degrees(math.atan2(y - cy, x - cx)) % 360,
                    "z_variable": abs(mouvement["end"][2] - z) > 0.001,
                })
            erreurs.extend(f"Plaque {plaque} : {e}" for e in it.errors)
        archive.verifier_source(source)
    for serie in series.values():
        points = serie["points"]
        angles = [p["angle_deg"] for p in points]
        variation = sum(p["z_variable"] for p in points)
        serie.update(
            largeur_95_deg=_largeur_circulaire(angles, 0.95),
            concentration=abs(sum(complex(math.cos(math.radians(a)), math.sin(math.radians(a)))
                                  for a in angles)) / len(angles),
            secteurs_10_deg=len({int(a // 10) for a in angles}),
            raccords_z_variable=variation,
            part_z_variable=variation / len(points),
        )
    statut = "PASS" if series and not erreurs else "NON VÉRIFIÉ"
    return dict(statut=statut, code=code_statut(statut), centre_xy_mm=list(centre_xy_mm),
                series=list(series.values()), erreurs=erreurs)


def _svg(rapports):
    """Dessine un panneau angle (°) et altitude (mm) par archive, à échelles communes."""
    groupes = [r["series"] for r in rapports]
    hauteurs = [p["xyz_mm"][2] for series in groupes for s in series for p in s["points"]]
    bas, haut = (min(hauteurs), max(hauteurs)) if hauteurs else (0, 1)
    echelle = max(haut - bas, 0.001)
    largeur = 460 * len(rapports)
    lignes = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{largeur}" height="365" role="img">',
              '<rect width="100%" height="100%" fill="white"/>',
              '<title>Départs de paroi extérieure par angle et altitude</title>']
    for i, (rapport, series) in enumerate(zip(rapports, groupes, strict=True)):
        x0 = 30 + 460 * i
        lignes.append(f'<text x="{x0}" y="25">{html.escape(rapport["fichier"])}</text>')
        lignes.append(f'<rect x="{x0}" y="45" width="390" height="260" fill="#f2f5f8"/>')
        lignes.append(f'<text x="{x0}" y="330">0 à 360° · Z {bas:g} à {haut:g} mm</text>')
        for serie in series:
            for point in serie["points"]:
                x = x0 + 390 * point["angle_deg"] / 360
                y = 300 - 250 * (point["xyz_mm"][2] - bas) / echelle
                couleur = "#b64926" if point["z_variable"] else "#255e9a"
                lignes.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2" fill="{couleur}"/>')
    return "\n".join([*lignes, "</svg>"])


def main(argv=None, *, prive=None):
    """Produit un JSON et un SVG facultatif dans l’atelier choisi, avec le verdict de lecture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--comparaison", type=Path)
    parser.add_argument("--centre-xy", type=float, nargs=2, required=True)
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--svg", type=Path)
    args = parser.parse_args(argv)
    try:
        entrees = [args.source] + ([args.comparaison] if args.comparaison else [])
        sortie = destination_neuve(args.sortie, prive=prive)
        svg = destination_neuve(args.svg, prive=prive) if args.svg else None
        rapports = [{"fichier": source.name, **analyser(source, centre_xy_mm=args.centre_xy)} for source in entrees]
        with PublicationNeuve(entrees, [sortie, svg] if svg else [sortie]) as publication:
            publication.temporaire(sortie).write_text(
                json.dumps(rapports, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
            )
            if svg:
                publication.temporaire(svg).write_text(_svg(rapports), encoding="utf-8")
            publication.publier()
    except (OSError, ValueError, KeyError) as erreur:
        parser.error(str(erreur))
    print(sortie)
    return max(r["code"] for r in rapports)
