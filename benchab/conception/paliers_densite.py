"""Prépare des modificateurs de densité disjoints, sans déduire leur effet G-code."""

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


def _valider_paliers(paliers, cle):
    """Lit des distances en mm strictement croissantes et des densités en % typées."""
    if not isinstance(paliers, list) or not 1 <= len(paliers) <= _MAX_PALIERS:
        raise ValueError("Déclarer de un à cinq paliers")
    derniere_distance = 0.0
    normalises = []
    for indice, palier in enumerate(paliers, start=1):
        if not isinstance(palier, dict) or set(palier) != {cle, "densite"}:
            raise ValueError(f"Palier {indice} : {cle} et densite requis")
        distance = palier[cle]
        if type(distance) not in (int, float) or not 0.2 <= distance <= 500 or distance - derniere_distance < 0.2:
            raise ValueError(f"Palier {indice} : distances croissantes espacées d’au moins 0,2 mm, plafond 500 mm")
        densite = palier["densite"]
        if not isinstance(densite, str) or not re.fullmatch(r"(?:0|[1-9][0-9]?|100)%", densite):
            raise ValueError(f"Palier {indice} : densité entière de 0% à 100% requise")
        options = reglages.Reglages()
        options[_CLE_DENSITE] = densite
        if reglages.valeur_brute(options, _CLE_DENSITE) != densite:
            raise ValueError(f"Palier {indice} : densité non conservée par le dictionnaire typé")
        normalises.append((float(distance), options))
        derniere_distance = distance
    return normalises


def _piece_et_volume(piece, volume_min_mm3):
    """Vérifie le maillage fermé et le seuil numérique ; retourne volume en mm³."""
    if not isinstance(piece, trimesh.Trimesh) or not 1 <= len(piece.faces) <= _MAX_FACES:
        raise ValueError("Un maillage non vide de 250 000 faces au plus est requis")
    if not np.isfinite(piece.vertices).all() or np.max(np.abs(piece.vertices)) > 10000 or not piece.is_volume:
        raise ValueError("La pièce doit former un volume orienté fermé dans les bornes ±10 000 mm")
    if type(volume_min_mm3) not in (int, float) or not 0.000001 <= volume_min_mm3 <= 1000:
        raise ValueError("Seuil de volume entre 0,000001 et 1 000 mm³ requis")
    base = solide(piece)
    volume_base = float(base.volume())
    if not math.isfinite(volume_base) or volume_base <= 0:
        raise ValueError("Le noyau géométrique n’a pas confirmé le volume de la pièce")
    return base, volume_base


def _vecteur(valeurs, etiquette):
    """Lit trois coordonnées finies, sans déduire le repère ni l’unité."""
    if (
        not isinstance(valeurs, (tuple, list))
        or len(valeurs) != 3
        or any(type(v) not in (float, int) or not -10000 <= v <= 10000 for v in valeurs)
    ):
        raise ValueError(f"{etiquette} : trois coordonnées finies entre -10 000 et 10 000 requises")
    return np.array(valeurs, dtype=float)


def _assembler(
    base, volume_base, normalises, forme_cumulee, *,
    prefixe, nom_distance, volume_min_mm3, details, limite, fermer_cote_sortant=None,
):
    """Construit des paliers disjoints et refuse le lot entier si une vérification échoue."""
    seuil_booleen = max(1e-9, volume_base * 1e-10)
    rapport = {
        "statut": "NON VÉRIFIÉ",
        **details,
        "volume_min_mm3": float(volume_min_mm3),
        "effet_gcode": "NON VÉRIFIÉ",
        "validation_physique": False,
        "limite": limite,
        "paliers": [],
    }
    precedent = None
    volumes = []
    modificateurs = []
    distance_precedente = 0.0
    for indice, (distance, options) in enumerate(normalises, 1):
        # Le découpage par différence enlève la zone déjà attribuée ;
        # aucun ordre d’application du slicer n’est supposé.
        cumul = forme_cumulee(distance)
        if precedent is None:
            coquille = cumul
        elif fermer_cote_sortant is not None:
            coquille = fermer_cote_sortant(cumul, distance_precedente)
        else:
            coquille = cumul - precedent
        volume = float(coquille.volume())
        palier = {
            "nom": f"{prefixe}-{indice}",
            f"{nom_distance}_min_mm": distance_precedente,
            f"{nom_distance}_max_mm": distance,
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
        distance_precedente = distance
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


def modificateurs_radiaux(piece, centre_mm, paliers, *, volume_min_mm3=0.1):
    """Retourne des coquilles d’un champ radial, bornées à la matière nominale.

    Centre et rayons en mm ; densités en % explicites et seuil numérique en
    mm³. Les régions sont disjointes en volume, et les paliers hors pièce ou
    trop petits donnent NON VÉRIFIÉ sans candidat partiel. PASS décrit
    uniquement la géométrie des volumes de modification. L’effet dans le
    G-code et la résistance restent NON VÉRIFIÉS. Aucun fichier, réglage
    installé ou maillage d’entrée n’est modifié.
    """
    centre = _vecteur(centre_mm, "Centre en mm").tolist()
    normalises = _valider_paliers(paliers, "rayon_mm")
    base, volume_base = _piece_et_volume(piece, volume_min_mm3)
    return _assembler(
        base, volume_base, normalises, lambda r: base ^ mf.Manifold.sphere(r, _SEGMENTS_SPHERE).translate(centre),
        prefixe="palier-radial", nom_distance="rayon", volume_min_mm3=volume_min_mm3,
        details={"centre_mm": centre},
        limite="Coquilles de sphères maillées, sans analyse de contrainte ni sélection automatique du trancheur.",
    )


def modificateurs_depuis_plan(piece, point_plan_mm, normale_sortante, paliers, *, volume_min_mm3=0.1):
    """Prépare des tranches de densité à distance d’une face extérieure déclarée.

    Le point du plan et les profondeurs sont en mm, la normale sortante
    est une direction et le seuil est en mm³. Le plan doit contenir une
    face de la pièce, entièrement située du côté intérieur. Les densités
    sont en % ; PASS porte sur la géométrie, NON VÉRIFIÉ refuse une
    tranche insuffisante. Aucun réglage ni fichier n’est modifié.
    """
    point = _vecteur(point_plan_mm, "Point du plan en mm")
    normale = _vecteur(normale_sortante, "Normale du plan")
    norme = np.linalg.norm(normale)
    if norme <= 1e-12:
        raise ValueError("Plan : normale sortante non nulle requise")
    normale /= norme
    normalises = _valider_paliers(paliers, "profondeur_mm")
    base, volume_base = _piece_et_volume(piece, volume_min_mm3)
    altitude = float(np.dot(point, normale))
    distances = np.asarray(piece.vertices) @ normale - altitude
    tolerance_mm = 1e-6
    # Une vraie face et le demi-espace intérieur distinguent le dessus
    # d’un plan tangent à une arête, décalé ou traversant.
    faces_du_plan = np.all(np.abs(distances[piece.faces]) <= tolerance_mm, axis=1)
    if distances.max() > tolerance_mm or not np.any(faces_du_plan & (piece.face_normals @ normale > 1 - 1e-9)):
        raise ValueError("Plan : une face extérieure avec toute la pièce du côté intérieur est requise")
    direction = normale.tolist()
    opposee = (-normale).tolist()
    return _assembler(
        base, volume_base, normalises, lambda d: base.trim_by_plane(direction, altitude - d),
        prefixe="palier-plan", nom_distance="profondeur", volume_min_mm3=volume_min_mm3,
        # Deux coupes planes évitent les faces résiduelles d’une
        # soustraction de volumes partageant des faces coplanaires.
        fermer_cote_sortant=lambda cumul, d: cumul.trim_by_plane(opposee, d - altitude),
        details={
            "point_plan_mm": point.tolist(), "normale_sortante": direction, "tolerance_plan_mm": tolerance_mm,
        },
        limite="Tranches parallèles au plan déclaré ; aucun effet G-code ni tenue physique établis.",
    )
