"""Prépare des modificateurs radiaux disjoints sans supposer leur effet dans le G-code."""

import math
import re

import manifold3d as mf
import numpy as np
import trimesh

from benchab import reglages
from benchab.conception.decoupe import _vers_trimesh
from benchab.conception.decoupe.geometrie import solide

_MAX_PALIERS = 5
_MAX_FACES = 250_000
_SEGMENTS_SPHERE = 32
_CLE_DENSITE = "sparse_infill_density"


def _valider_paliers(paliers):
    """Lit des rayons en mm strictement croissants et des densités en % typées."""
    if not isinstance(paliers, list) or not 1 <= len(paliers) <= _MAX_PALIERS:
        raise ValueError("Déclarer de un à cinq paliers radiaux")
    dernier_rayon = 0.0
    normalises = []
    for indice, palier in enumerate(paliers, start=1):
        if not isinstance(palier, dict) or set(palier) != {"rayon_mm", "densite"}:
            raise ValueError(f"Palier {indice} : rayon_mm et densite requis")
        rayon = palier["rayon_mm"]
        if type(rayon) not in (int, float) or not 0.2 <= rayon <= 500 or rayon - dernier_rayon < 0.2:
            raise ValueError(f"Palier {indice} : rayons croissants espacés d’au moins 0,2 mm, plafond 500 mm")
        densite = palier["densite"]
        if not isinstance(densite, str) or not re.fullmatch(r"(?:0|[1-9][0-9]?|100)%", densite):
            raise ValueError(f"Palier {indice} : densité entière de 0% à 100% requise")
        options = reglages.Reglages()
        options[_CLE_DENSITE] = densite
        if reglages.valeur_brute(options, _CLE_DENSITE) != densite:
            raise ValueError(f"Palier {indice} : densité non conservée par le dictionnaire typé")
        normalises.append((float(rayon), options))
        dernier_rayon = rayon
    return normalises


def modificateurs_radiaux(piece, centre_mm, paliers, *, volume_min_mm3=0.1):
    """Retourne des coquilles d’un champ radial, bornées à la matière nominale.

    Centre et rayons en mm ; densités en % explicites et seuil numérique en
    mm³. Les régions sont disjointes en volume, et les paliers hors pièce ou
    trop petits donnent NON VÉRIFIÉ sans candidat partiel. PASS décrit
    uniquement la géométrie des volumes de modification. L’effet dans le
    G-code et la résistance restent NON VÉRIFIÉS. Aucun fichier, réglage
    installé ou maillage d’entrée n’est modifié.
    """
    if not isinstance(piece, trimesh.Trimesh) or not 1 <= len(piece.faces) <= _MAX_FACES:
        raise ValueError("Un maillage non vide de 250 000 faces au plus est requis")
    if not np.isfinite(piece.vertices).all() or np.max(np.abs(piece.vertices)) > 10000 or not piece.is_volume:
        raise ValueError("La pièce doit former un volume orienté fermé dans les bornes ±10 000 mm")
    if (
        not isinstance(centre_mm, (tuple, list))
        or len(centre_mm) != 3
        or any(type(v) not in (float, int) or not -10000 <= v <= 10000 for v in centre_mm)
    ):
        raise ValueError("Centre : trois coordonnées finies entre -10 000 et 10 000 mm requises")
    if type(volume_min_mm3) not in (int, float) or not 0.000001 <= volume_min_mm3 <= 1000:
        raise ValueError("Seuil de volume entre 0,000001 et 1 000 mm³ requis")
    normalises = _valider_paliers(paliers)
    base = solide(piece)
    volume_base = float(base.volume())
    if not math.isfinite(volume_base) or volume_base <= 0:
        raise ValueError("Le noyau géométrique n’a pas confirmé le volume de la pièce")
    seuil_booleen = max(1e-9, volume_base * 1e-10)
    centre = list(map(float, centre_mm))
    rapport = {
        "statut": "NON VÉRIFIÉ",
        "centre_mm": centre,
        "volume_min_mm3": float(volume_min_mm3),
        "effet_gcode": "NON VÉRIFIÉ",
        "validation_physique": False,
        "limite": "Coquilles de sphères maillées, sans analyse de contrainte ni sélection automatique du trancheur.",
        "paliers": [],
    }
    precedent = None
    volumes = []
    modificateurs = []
    rayon_precedent = 0.0
    for indice, (rayon, options) in enumerate(normalises, 1):
        # Le découpage par différence enlève la zone déjà attribuée ;
        # aucun ordre d’application du slicer n’est supposé.
        sphere = mf.Manifold.sphere(rayon, _SEGMENTS_SPHERE).translate(centre)
        cumul = base ^ sphere
        coquille = cumul if precedent is None else cumul - precedent
        volume = float(coquille.volume())
        palier = {
            "nom": f"palier-radial-{indice}",
            "rayon_min_mm": rayon_precedent,
            "rayon_max_mm": rayon,
            "densite": reglages.valeur_brute(options, _CLE_DENSITE),
            "volume_mesure_mm3": volume if math.isfinite(volume) else None,
        }
        rapport["paliers"].append(palier)
        if (
            coquille.status() != mf.Error.NoError
            or coquille.is_empty()
            or not math.isfinite(volume)
            or volume < volume_min_mm3
        ):
            return [], {**rapport, "raison": f"Palier {indice} sans volume suffisant dans la pièce"}
        # Des faces en contact peuvent produire un volume signé à l’arrondi près.
        essais = [coquille - base, *(coquille ^ autre for autre in volumes)]
        if any(
            essai.status() != mf.Error.NoError or not abs(float(essai.volume())) <= seuil_booleen
            for essai in essais
        ):
            return [], {**rapport, "raison": f"Palier {indice} hors de la pièce ou en recouvrement"}
        mesh = _vers_trimesh(coquille)
        if not mesh.is_volume:
            return [], {**rapport, "raison": f"Palier {indice} non fermé après conversion en maillage"}
        modificateurs.append(
            {
                "name": palier["nom"],
                "mesh": mesh,
                "subtype": "modifier_part",
                "settings": options,
            }
        )
        precedent = cumul
        rayon_precedent = rayon
        volumes.append(coquille)
    attribue = math.fsum(p["volume_mesure_mm3"] for p in rapport["paliers"])
    if attribue > volume_base + seuil_booleen:
        return [], {**rapport, "raison": "Somme des paliers supérieure au volume de la pièce"}
    return modificateurs, {
        **rapport,
        "statut": "PASS",
        "volume_piece_mm3": volume_base,
        "volume_non_attribue_mm3": max(0.0, volume_base - attribue),
        "tolerance_booleenne_mm3": seuil_booleen,
    }
