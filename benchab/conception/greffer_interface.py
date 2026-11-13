"""Réemploi d'une interface femelle par union de volumes dans un repère déclaré."""

import argparse
import hashlib
import io
import json
import math
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh

from benchab.chemins import sans_lien
from benchab.conception.decoupe import _vers_trimesh
from benchab.conception.decoupe.geometrie import solide
from benchab.conception.vissage import profil_radial
from benchab.qualite.ecritures import publier_dossier_neuf

_MAX_STL = 151_000_000
_ANGLES = 256
_HAUTEURS = 150
_TOLERANCE_RADIALE_MM = 0.001
_RECOUVREMENT_MIN_MM3 = 1e-5


def _lire_stl(chemin, sha_attendu=None):
    chemin = sans_lien(chemin)
    if chemin.suffix.lower() != ".stl":
        raise ValueError("Un fichier STL en mm est requis.")
    with chemin.open("rb") as flux:
        contenu = flux.read(_MAX_STL + 1)
    if len(contenu) > _MAX_STL:
        raise ValueError("STL hors budget. Isoler le col et déclarer l’empreinte du fichier isolé.")
    sha = hashlib.sha256(contenu).hexdigest()
    if sha_attendu is not None and sha_attendu != sha:
        raise ValueError("L’empreinte SHA-256 de la source diffère.")
    return solide(trimesh.load_mesh(io.BytesIO(contenu), file_type="stl")), sha


def _profil(corps, hauteurs):
    triangles = _vers_trimesh(corps).triangles
    # Limite le produit angles × segments de l'analyseur aux maillages de col.
    if len(triangles) > 150_000:
        raise ValueError("Zone trop détaillée pour le contrôle radial. Réduire la zone du col.")
    return profil_radial(triangles, hauteurs, cote="interieur", angles=_ANGLES, phase=0.371)


def _cylindre(bas, haut, rayon):
    return mf.Manifold.cylinder(haut - bas, rayon, circular_segments=_ANGLES).translate((0, 0, bas))


def _mesurer(source, support, bas, haut, rayon, rapport):
    zone = _cylindre(bas, haut, rayon)
    reference = source ^ zone
    if reference.is_empty():
        raise ValueError("La zone ne contient aucune matière source.")
    hauteurs = np.linspace(bas, haut, _HAUTEURS + 2)[1:-1]
    profil_source = _profil(reference, hauteurs)
    # L'intersection cylindrique ajoute une paroi artificielle près du rayon limite.
    # Elle ne peut pas compter comme premier contact du filet.
    limite = rayon * math.cos(math.pi / _ANGLES) - _TOLERANCE_RADIALE_MM
    contacts_source = np.isfinite(profil_source) & (profil_source < limite)
    rapport["couverture_source"] = float(contacts_source.mean())
    if not contacts_source.all() or profil_source.min() <= _TOLERANCE_RADIALE_MM:
        raise ValueError("Zone insuffisante pour caractériser un profil femelle sur tous les rayons.")
    passage = _cylindre(bas, haut, float(profil_source.min()) / 2)
    if (reference ^ passage).volume() > _RECOUVREMENT_MIN_MM3:
        raise ValueError("Le passage autour de l’axe est obstrué dans la source.")
    rapport["recouvrement_mm3"] = (reference ^ support).volume()
    if rapport["recouvrement_mm3"] <= _RECOUVREMENT_MIN_MM3:
        return None, "Recouvrement volumique insuffisant entre le col et le support."
    # Le contrôle porte sur les octets effectivement remis à l'utilisateur.
    union = reference + support
    stl = _vers_trimesh(union).export(file_type="stl")
    try:
        relu = solide(trimesh.load_mesh(io.BytesIO(stl), file_type="stl"))
    except ValueError:
        return None, "Le STL relu ne forme pas un solide fermé."
    rapport["composantes"] = len(relu.decompose())
    if rapport["composantes"] != 1:
        return None, "Le STL relu contient plusieurs composantes."
    resultat = relu ^ zone
    profil_final = _profil(resultat, hauteurs)
    communs = contacts_source & np.isfinite(profil_final) & (profil_final < limite)
    rapport["couverture_commune"] = float(communs.mean())
    rapport["ecart_radial_max_mm"] = (
        float(np.abs(profil_final[communs] - profil_source[communs]).max()) if communs.any() else None
    )
    # Le différentiel volumique complète les rayons entre deux échantillons.
    # La tolérance tient compte de l'écriture des sommets du STL en float32.
    rapport["ecart_zone_mm3"] = (reference - resultat).volume() + (resultat - reference).volume()
    rapport["tolerance_zone_mm3"] = max(_RECOUVREMENT_MIN_MM3, reference.volume() * 1e-6)
    if (
        not communs.all()
        or rapport["ecart_radial_max_mm"] > _TOLERANCE_RADIALE_MM
        or rapport["ecart_zone_mm3"] > rapport["tolerance_zone_mm3"]
    ):
        return None, "Le STL relu altère la zone déclarée de l’interface."
    return stl, None


def greffer(source, support, *, sha256, zone_mm):
    """Greffe deux STL en mm et retourne (octets STL ou None, rapport numérique).

    zone_mm décrit Zmin, Zmax et rayon autour de l'axe Z, en repère commun.
    PASS exige une interface conservée et un solide continu ; FAIL signale
    un défaut mesuré ; NON VÉRIFIÉ indique une preuve d'entrée insuffisante.
    Aucune écriture ni validation physique.
    """
    rapport = {
        "statut": "NON VÉRIFIÉ",
        "validation_physique": False,
        "zone_mm": list(zone_mm),
        "angles": _ANGLES,
        "hauteurs": _HAUTEURS,
        "tolerance_radiale_mm": _TOLERANCE_RADIALE_MM,
    }
    try:
        bas, haut, rayon = zone_mm
        if not all(map(math.isfinite, zone_mm)) or bas >= haut or rayon <= 0:
            rapport["zone_mm"] = None
            raise ValueError("Zone finie requise : Zmin < Zmax et rayon positif.")
        original, rapport["source_sha256"] = _lire_stl(source, sha256)
        base, rapport["support_sha256"] = _lire_stl(support)
        stl, erreur = _mesurer(original, base, bas, haut, rayon, rapport)
    except (OSError, ValueError) as erreur:
        rapport["motif"] = str(erreur)
        return None, rapport
    rapport["statut"] = "FAIL" if erreur else "PASS"
    if erreur:
        rapport["motif"] = erreur
    else:
        rapport["stl_sha256"] = hashlib.sha256(stl).hexdigest()
    return stl, rapport


def main(argv=None, *, prive=None):
    """Contrôle deux STL et écrit la greffe PASS en dossier privé neuf ; codes 0 ou 2."""
    parser = argparse.ArgumentParser(description="Greffe une interface femelle identifiée dans un repère commun.")
    parser.add_argument("source", type=Path)
    parser.add_argument("support", type=Path)
    parser.add_argument("--sha256", required=True, help="Empreinte SHA-256 attendue de la source")
    parser.add_argument("--zone-mm", type=float, nargs=3, required=True, metavar=("ZMIN", "ZMAX", "RAYON"))
    parser.add_argument("--sortie", type=Path, required=True, help="Dossier privé neuf")
    args = parser.parse_args(argv)
    stl, rapport = greffer(args.source, args.support, sha256=args.sha256, zone_mm=args.zone_mm)
    if stl is not None:
        try:
            contenu = json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            cible = publier_dossier_neuf(
                args.sortie, {"greffe.stl": stl, "rapport.json": contenu.encode()}, prive=prive
            )
            rapport["rapport"] = str(cible / "rapport.json")
        except (OSError, ValueError) as erreur:
            rapport.update(statut="NON VÉRIFIÉ", motif=str(erreur))
    print(json.dumps(rapport, ensure_ascii=False, allow_nan=False))
    return 0 if rapport["statut"] == "PASS" else 2
