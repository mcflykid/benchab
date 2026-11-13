"""Recherche les vides fins dans une interface déclarée du maillage."""

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely import contains_xy
from shapely.errors import GEOSException
from shapely.ops import unary_union

from benchab.chemins import atelier_prive, sans_lien
from benchab.controles.couches_courtes import _sections_xy
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.statuts import code_statut
from benchab.visualisation.visionneuse import charger_maillage

MAX_CELLULES = 3_000_000
MAX_COUPE_FACES = 100_000_000


def _occupation(mesh, minimum, forme, pas):
    """Échantillonne la matière aux centres des voxels par coupes XY, en mm."""
    coordonnees = [a + pas * (np.arange(n) + 0.5) for a, n in zip(minimum, forme)]
    x, y = np.meshgrid(*coordonnees[:2], indexing="ij")
    plein = np.zeros(forme, dtype=bool)
    for i, z in enumerate(coordonnees[2]):
        sections = _sections_xy(mesh, z)
        if sections:
            plein[:, :, i] = contains_xy(unary_union(sections), x, y)
    return plein, coordonnees


def _segments_fins(plein, axe, indice_zone, seuil, pas):
    """Retient les intervalles de vide bornés par de la matière sur l’axe de largeur."""
    lignes = np.moveaxis(plein, axe, 0)
    zones = np.moveaxis(indice_zone, axe, 0)
    resultat = []
    masque = np.zeros_like(lignes)
    for autre in zip(*np.nonzero(zones.any(axis=0))):
        ligne = lignes[(slice(None), *autre)]
        bords = np.flatnonzero(ligne[1:] != ligne[:-1]) + 1
        for a, b in zip(bords[:-1], bords[1:]):
            largeur = (b - a) * pas
            # L’incertitude d’une cellule est conservée autour du seuil.
            if not ligne[a] and largeur <= seuil + pas and zones[(slice(a, b), *autre)].all():
                masque[(slice(a, b), *autre)] = True
                point = list(autre)
                point.insert(axe, (a + b - 1) // 2)
                resultat.append((tuple(point), largeur))
    return resultat, np.moveaxis(masque, 0, axe)


def _image(plein, point, axe):
    """Rend une coupe verticale avec le vide retenu en couleur."""
    transverse = 1 if axe == 0 else 0
    coupe = np.take(plein, point[transverse], axis=transverse).T[::-1]
    couleur = np.where(coupe[..., None], [50, 74, 99], [243, 245, 249]).astype(np.uint8)
    horizontal = 0 if transverse == 1 else 1
    couleur[coupe.shape[0] - 1 - point[2], point[horizontal]] = [235, 111, 38]
    image = Image.fromarray(couleur)
    facteur = max(1, min(32, 900 // max(image.size)))
    return image.resize(tuple(n * facteur for n in image.size), Image.Resampling.NEAREST)


def mesurer(mesh, zone_mm, *, axe, largeur_ligne_mm, pas_mm, porteuse=False):
    """Renvoie un rapport et une coupe optionnelle pour une interface XYZ en mm.

    PASS/WARN/FAIL/NON VÉRIFIÉ portent sur la grille géométrique. La largeur
    est mesurée selon X ou Y et la profondeur selon Z. Aucun maillage modifié.
    """
    zone = np.asarray(zone_mm, dtype=float)
    if (zone.shape != (2, 3) or not np.isfinite(zone).all() or np.any(zone[0] >= zone[1])
            or axe not in ("x", "y") or not math.isfinite(largeur_ligne_mm) or not math.isfinite(pas_mm)
            or largeur_ligne_mm <= 0 or not 0 < pas_mm <= largeur_ligne_mm / 2 or type(porteuse) is not bool):
        raise ValueError("Zone XYZ, axe x/y, largeur de ligne et pas ≤ demi-largeur requis.")
    seuil = 2 * largeur_ligne_mm
    rapport = {"zone_mm": zone.tolist(), "axe_largeur": axe, "pas_mm": pas_mm, "seuil_mm": seuil,
               "incertitude_cote_mm": pas_mm, "porteuse": porteuse, "validation_physique": False,
               "portee": "Connectivité sur grille 3D. Une ouverture plus fine que le pas peut échapper à la grille."}
    if not mesh.is_volume or not np.isfinite(mesh.bounds).all():
        return {**rapport, "statut": "NON VÉRIFIÉ", "raison": "Volume fermé et orienté requis."}, None
    minimum = mesh.bounds[0] - pas_mm
    dimensions = np.ptp(mesh.bounds, axis=0) / pas_mm + 2
    if (not np.isfinite(dimensions).all() or np.any(dimensions > MAX_CELLULES)
            or np.prod(np.ceil(dimensions)) > MAX_CELLULES
            or math.ceil(dimensions[2]) * len(mesh.faces) > MAX_COUPE_FACES):
        return {**rapport, "statut": "NON VÉRIFIÉ", "raison": "Budget de grille ou de coupes dépassé."}, None
    forme = tuple(np.ceil(dimensions).astype(int))
    try:
        plein, coordonnees = _occupation(mesh, minimum, forme, pas_mm)
    except (GEOSException, ValueError):
        return {**rapport, "statut": "NON VÉRIFIÉ", "raison": "Une section du volume est inexploitable."}, None
    axes_zone = [(a >= lo) & (a <= hi) for a, lo, hi in zip(coordonnees, *zone)]
    zone_grille = axes_zone[0][:, None, None] & axes_zone[1][None, :, None] & axes_zone[2][None, None, :]
    if not (plein & zone_grille).any():
        return {**rapport, "statut": "NON VÉRIFIÉ", "raison": "Aucune matière échantillonnée dans la zone."}, None
    candidats, masque = _segments_fins(plein, "xy".index(axe), zone_grille, seuil, pas_mm)
    if not candidats:
        return {**rapport, "statut": "PASS", "vides": []}, None
    # La marge autour du solide relie les ouvertures à l’air extérieur.
    voisins = np.ones((3, 3, 3), dtype=bool)
    labels, _ = ndimage.label(~plein, structure=voisins)
    exterieur = labels[0, 0, 0]
    faces, _ = ndimage.label(~plein)
    exterieur_faces = faces[0, 0, 0]
    # Deux fentes peuvent atteindre le même air extérieur sans former un défaut unique.
    zones_fines, _ = ndimage.label(masque, structure=voisins)
    groupes = {}
    for point, largeur in candidats:
        groupes.setdefault(zones_fines[point], []).append((point, largeur))
    vides, images = [], []
    for mesures in groupes.values():
        points, largeurs = zip(*mesures)
        ouvert = any(labels[p] == exterieur for p in points)
        ouvert_faces = any(faces[p] == exterieur_faces for p in points)
        cote_max = max(largeurs)
        if ouvert != ouvert_faces or cote_max + pas_mm > seuil:
            statut = "NON VÉRIFIÉ"
        else:
            statut = "FAIL" if porteuse and not ouvert else "WARN"
        zs = [p[2] for p in points]
        ancre = min(points, key=lambda p: abs(p[2] - (min(zs) + max(zs)) / 2))
        vides.append({
            "statut": statut, "ouverture_echantillonnee": bool(ouvert),
            "ouverture_par_faces": bool(ouvert_faces),
            "position_mm": [float(coordonnees[i][p]) for i, p in enumerate(ancre)],
            "largeur_min_mm": min(largeurs), "largeur_max_mm": cote_max,
            "profondeur_z_echantillonnee_mm": (max(zs) - min(zs) + 1) * pas_mm,
            "portee_profondeur": "Étendue des intervalles fins retenus dans la zone.",
        })
        images.append(ancre)
    priorite = {"FAIL": 0, "NON VÉRIFIÉ": 1, "WARN": 2}
    i = min(range(len(vides)), key=lambda i: priorite[vides[i]["statut"]])
    return {**rapport, "statut": vides[i]["statut"], "vides": vides}, _image(plein, images[i], "xy".index(axe))


def main(argv=None, *, prive=None):
    """Contrôle un STL privé et publie un rapport en mm avec une coupe PNG optionnelle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stl", type=Path)
    parser.add_argument("--zone-mm", type=float, nargs=6, required=True)
    parser.add_argument("--axe", choices=("x", "y"), required=True)
    parser.add_argument("--largeur-ligne-mm", type=float, required=True)
    parser.add_argument("--pas-mm", type=float, required=True)
    parser.add_argument("--porteuse", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--coupe", type=Path)
    args = parser.parse_args(argv)
    racine = atelier_prive(prive)
    source = sans_lien(args.stl, racine=racine, strict=True)
    if source.suffix.casefold() != ".stl" or not source.is_file() or source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("STL régulier de 64 Mio au plus requis.")
    sha = sha256_fichier(source)
    rapport, image = mesurer(
        charger_maillage(source), [args.zone_mm[:3], args.zone_mm[3:]], axe=args.axe,
        largeur_ligne_mm=args.largeur_ligne_mm, pas_mm=args.pas_mm, porteuse=args.porteuse,
    )
    if sha256_fichier(source) != sha:
        raise ValueError("Maillage modifié pendant le contrôle.")
    rapport["source_sha256"] = sha
    destinations = {}
    if args.json is not None:
        destinations["json"] = destination_neuve(args.json, prive=racine, temporaire=False)
    if args.coupe is not None and image is not None:
        destinations["coupe"] = destination_neuve(args.coupe, prive=racine, temporaire=False)
        rapport["coupe"] = destinations["coupe"].relative_to(racine).as_posix()
    texte = json.dumps(rapport, allow_nan=False, ensure_ascii=False, indent=2)
    if destinations:
        with PublicationNeuve([source], list(destinations.values())) as publication:
            if "json" in destinations:
                publication.temporaire(destinations["json"]).write_text(texte + "\n", encoding="utf-8")
            if "coupe" in destinations:
                image.save(publication.temporaire(destinations["coupe"]), format="PNG")
            publication.publier()
    print(texte)
    return code_statut(rapport["statut"])
