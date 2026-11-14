"""Estime la transmission directe d’une coque à partir de rayons dans le maillage."""

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from benchab.chemins import atelier_prive, sans_lien
from benchab.qualite.ecritures import renommer_exclusif
from benchab.qualite.empreintes import sha256_fichier
from benchab.visualisation.visionneuse import charger_maillage

MAX_OCTETS = 150 * 1024 * 1024
MAX_FACES = 3_000_000
LOT_RAYONS = 32
EPS_MM = 1e-5


def _rayons(mesh, origines, directions):
    """Itère sur les distances triées en mm par rayon, avec les lots bornés."""
    for debut in range(0, len(origines), LOT_RAYONS):
        o = origines[debut:debut + LOT_RAYONS]
        d = directions[debut:debut + LOT_RAYONS]
        points, indices, _ = mesh.ray.intersects_location(o, d, multiple_hits=True)
        distances = np.einsum("ij,ij->i", points - o[indices], d[indices])
        for indice in range(len(o)):
            dis = np.sort(distances[indices == indice])
            yield dis[np.r_[True, np.diff(dis) > EPS_MM]] if dis.size else dis


def _carte(mesh, sources, attenuation, bornes, n_angles, n_hauteurs):
    """Projette la surface latérale sur l’axe Z et mesure la matière traversée en mm."""
    bas, haut = bornes
    centre = (bas[:2] + haut[:2]) / 2
    rayon = np.linalg.norm(haut[:2] - bas[:2]) / 2 + 1
    angles = (np.arange(n_angles) + 0.5) * 2 * np.pi / n_angles
    z = haut[2] - (np.arange(n_hauteurs) + 0.5) * (haut[2] - bas[2]) / n_hauteurs
    axes = np.column_stack((np.cos(angles), np.sin(angles), np.zeros(n_angles)))
    dehors = np.tile(axes, (n_hauteurs, 1))
    origines = dehors * rayon + np.column_stack((
        np.broadcast_to(centre, (len(dehors), 2)), np.repeat(z, n_angles),
    ))
    # Une carte commune place chaque échantillon à la même hauteur et au même azimut.
    profondeurs = np.array([d[0] if len(d) else np.nan for d in _rayons(mesh, origines, -dehors)])
    points = origines - dehors * profondeurs[:, None]
    visibles = np.flatnonzero(np.isfinite(profondeurs))
    intensite = np.full(len(points), np.nan)
    epaisseur = np.full(len(points), np.nan)
    intensite[visibles] = epaisseur[visibles] = 0
    for source in sources:
        vecteurs = points[visibles] - source
        longueurs = np.linalg.norm(vecteurs, axis=1)
        if np.any(longueurs <= EPS_MM):
            raise ValueError("Source lumineuse confondue avec la surface échantillonnée.")
        directions = vecteurs / longueurs[:, None]
        origines_lumiere = np.broadcast_to(source, directions.shape)
        for indice, longueur, hits in zip(visibles, longueurs, _rayons(mesh, origines_lumiere, directions)):
            # Une traversée fiable se termine sur la surface ciblée. Une tangence
            # ou un départ dans la matière peut laisser un nombre impair d’impacts.
            trajet = hits[(hits > EPS_MM) & (hits <= longueur + EPS_MM)]
            if not len(trajet) or len(trajet) % 2 or abs(trajet[-1] - longueur) > EPS_MM:
                intensite[indice] = epaisseur[indice] = np.nan
                continue
            matiere = (trajet[1::2] - trajet[::2]).sum()
            epaisseur[indice] += matiere / len(sources)
            intensite[indice] += np.exp(-attenuation * matiere) / (len(sources) * longueur**2)
    return points.reshape(n_hauteurs, n_angles, 3), intensite.reshape(n_hauteurs, n_angles), epaisseur


def _couleurs(carte, maximum):
    """Associe les estimations à une palette fixe et réserve le gris aux rayons inconnus."""
    valeurs = np.nan_to_num(carte) / (maximum or 1)
    palette = np.array([[15, 22, 47], [54, 74, 129], [203, 98, 68], [255, 240, 168]])
    rgb = np.stack([np.interp(valeurs, np.linspace(0, 1, 4), canal) for canal in palette.T], axis=-1)
    rgb[~np.isfinite(carte)] = [110, 113, 117]
    return rgb.astype(np.uint8)


def _vue(points, couleurs, destination, bornes):
    """Rastérise la face latérale en vue 3/4 avec profondeur et échelle communes."""
    direction = np.array([1.0, -1.0, 0.65])
    direction /= np.linalg.norm(direction)
    droite = np.array([1, 1, 0]) / np.sqrt(2)
    haut = np.cross(direction, droite)
    pixels = (points - bornes.mean(axis=0)) @ np.stack((droite, -haut), axis=1)
    pixels = pixels * (490 / np.linalg.norm(np.ptp(bornes, axis=0))) + 260
    profondeur = points @ direction
    facettes = []
    for i in range(points.shape[0] - 1):
        for j in range(points.shape[1]):
            k = (j + 1) % points.shape[1]
            coins = [(i, j), (i + 1, j), (i + 1, k), (i, k)]
            poly = [pixels[c] for c in coins]
            if np.isfinite(poly).all():
                facettes.append((np.mean([profondeur[c] for c in coins]), poly, tuple(map(int, couleurs[i, j]))))
    image = Image.new("RGB", (520, 520), "#191d25")
    dessin = ImageDraw.Draw(image)
    for _, poly, couleur in sorted(facettes, key=lambda x: x[0]):
        dessin.polygon([tuple(p) for p in poly], fill=couleur)
    image.save(destination)


def _charger(fichier):
    """Charge une coque en mm et écarte les faces dupliquées ou dégénérées en mémoire."""
    mesh = charger_maillage(fichier)
    if len(mesh.faces) > MAX_FACES:
        raise ValueError("La coque dépasse trois millions de facettes.")
    # Les doublons et triangles dégénérés faussent les intersections sans ajouter de matière.
    uniques = mesh.unique_faces() & mesh.nondegenerate_faces()
    if not uniques.all():
        mesh.update_faces(uniques)
        mesh.remove_unreferenced_vertices()
    if not mesh.is_volume or not np.isfinite(mesh.bounds).all():
        raise ValueError("Coque étanche et orientée requise après retrait des faces dupliquées ou dégénérées.")
    return mesh, int(len(uniques) - uniques.sum())


def produire(fichiers, destination, *, sources_mm, attenuation_mm_inv, prive=None, n_angles=72, n_hauteurs=36):
    """Écrit deux vues PNG par STL en mm et un rapport NON VÉRIFIÉ physiquement.

    Une à huit positions XYZ en mm représentent une source étendue à poids
    égaux. L’atténuation en mm⁻¹ est déclarée et la puissance relative est
    commune aux candidates. Les données et résultats restent dans l’atelier.
    """
    sources = np.asarray(sources_mm, dtype=float)
    if (sources.ndim != 2 or sources.shape[1] != 3 or not 1 <= len(sources) <= 8
            or not np.isfinite(sources).all()):
        raise ValueError("Déclarer une à huit positions XYZ finies en mm.")
    if not np.isfinite(attenuation_mm_inv) or attenuation_mm_inv <= 0:
        raise ValueError("Déclarer une atténuation strictement positive en mm⁻¹.")
    if not 12 <= n_angles <= 144 or not 8 <= n_hauteurs <= 96:
        raise ValueError("Résolution attendue : 12 à 144 angles et 8 à 96 hauteurs.")
    if not 1 <= len(fichiers) <= 2:
        raise ValueError("Un ou deux STL requis.")
    racine = atelier_prive(prive)
    destination = sans_lien(destination, racine=racine)
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("La sortie doit être neuve et son dossier parent doit exister.")
    fichiers = [sans_lien(f, racine=racine, strict=True) for f in fichiers]
    if any(f.suffix.lower() != ".stl" or not f.is_file() or f.stat().st_size > MAX_OCTETS for f in fichiers):
        raise ValueError("STL régulier de 150 Mio au plus requis.")
    empreintes = [sha256_fichier(f) for f in fichiers]
    bornes = []
    for fichier in fichiers:
        mesh, _ = _charger(fichier)
        bornes.append(mesh.bounds.copy())
        del mesh
    cadre = np.array([np.min([b[0] for b in bornes], axis=0), np.max([b[1] for b in bornes], axis=0)])
    cartes = []
    for fichier in fichiers:
        mesh, doublons = _charger(fichier)
        cartes.append((*_carte(mesh, sources, attenuation_mm_inv, cadre, n_angles, n_hauteurs), doublons))
        del mesh
    if empreintes != [sha256_fichier(f) for f in fichiers]:
        raise ValueError("Un STL a changé pendant la production.")
    maximum = max(float(c[np.isfinite(c)].max(initial=0)) for _, c, _, _ in cartes)
    rapport = {
        "schema": 1, "validation_physique": "NON VÉRIFIÉ",
        "modele": "Transmission directe hypothétique, sans diffusion ni réflexion.",
        "sources_mm": sources.tolist(), "attenuation_mm_inv": attenuation_mm_inv,
        "grille_angles_hauteurs": [n_angles, n_hauteurs], "cadre_mm": cadre.tolist(),
        "maximum_commun": maximum, "candidates": [],
    }
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".lampe-") as temporaire:
        dossier = Path(temporaire)
        for i, (fichier, empreinte, (points, carte, epaisseurs, doublons)) in enumerate(
            zip(fichiers, empreintes, cartes), 1,
        ):
            deroule, vue = f"coque-{i}-deroule.png", f"coque-{i}-vue.png"
            couleurs = _couleurs(carte, maximum)
            Image.fromarray(couleurs).resize((n_angles * 6, n_hauteurs * 6), Image.Resampling.NEAREST).save(
                dossier / deroule
            )
            _vue(points, couleurs, dossier / vue, cadre)
            valeurs = carte[np.isfinite(carte)]
            couverture = "COMPLÈTE" if valeurs.size == carte.size else "PARTIELLE" if valeurs.size else "NON VÉRIFIÉ"
            rapport["candidates"].append({
                "fichier": fichier.relative_to(racine).as_posix(), "sha256": empreinte, "deroule": deroule, "vue": vue,
                "faces_dupliquees_ou_degenerees_ignorees": doublons,
                "couverture": couverture,
                "echantillons_exploitables": int(valeurs.size), "echantillons_total": int(carte.size),
                "epaisseur_mediane_mm": float(np.nanmedian(epaisseurs)) if valeurs.size else None,
                "intensite_mediane_relative": float(np.median(valeurs) / maximum) if maximum and valeurs.size else None,
            })
        (dossier / "rapport.json").write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
        renommer_exclusif(dossier, destination)
    return rapport


def main(argv=None, *, prive=None):
    """Génère les cartes de lumière relative depuis des STL privés et des hypothèses déclarées."""
    parser = argparse.ArgumentParser(description="Estime le rendu d’une coque allumée en lumière directe.")
    parser.add_argument("stl", type=Path, nargs="+", help="Un ou deux STL en mm, dans le même repère")
    parser.add_argument("--source-mm", type=float, nargs=3, action="append", required=True, metavar=("X", "Y", "Z"))
    parser.add_argument("--attenuation-mm-inv", type=float, required=True)
    parser.add_argument("--angles", type=int, default=72)
    parser.add_argument("--hauteurs", type=int, default=36)
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    rapport = produire(
        args.stl, args.sortie, sources_mm=args.source_mm, attenuation_mm_inv=args.attenuation_mm_inv,
        prive=prive, n_angles=args.angles, n_hauteurs=args.hauteurs,
    )
    print(json.dumps(rapport, ensure_ascii=False))
    return 0
