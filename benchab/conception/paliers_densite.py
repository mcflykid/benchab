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
    volumes, rapport = _assembler(
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

    if not volumes:
        return volumes, rapport
    for region, palier in zip(volumes, rapport["paliers"], strict=True):
        profondeurs = altitude - np.asarray(region["mesh"].vertices) @ normale
        if (
            not np.isfinite(profondeurs).all()
            or profondeurs.min() < palier["profondeur_min_mm"] - tolerance_mm
            or profondeurs.max() > palier["profondeur_max_mm"] + tolerance_mm
        ):
            return [], {
                **rapport, "statut": "NON VÉRIFIÉ",
                "raison": "Sommet d’un modificateur au-delà des plans de son palier",
            }
    return volumes, rapport


def modificateurs_depuis_contraintes(etude, seuils, *, volume_min_mm3=0.1):
    """Convertit les moyennes de von Mises d’un coupon plein en tranches expérimentales.

    L’étude fournit les cotes et limites axiales en mm et au moins deux
    maillages ordonnés. Les seuils explicites (MPa, % entier) sont choisis
    pour l’étude. PASS couvre seulement la géométrie ; NON VÉRIFIÉ si
    l’enveloppe observée change de classe. Aucun fichier ni réglage modifié.
    """
    if not isinstance(etude, dict) or etude.get("schema") != 1:
        raise ValueError("Rapport de contraintes volumétriques de schéma 1 requis")
    cotes = _vecteur(etude.get("dimensions_xyz_mm"), "Cotes du coupon")
    if not np.all((0.5 <= cotes) & (cotes <= 1000)):
        raise ValueError("Cotes hors domaine du coupon volumique")
    longueur, largeur, hauteur = cotes.tolist()
    limites = [longueur * i / 4 for i in range(5)]
    if etude.get("zones_axiales_mm") != limites:
        raise ValueError("Quatre zones axiales du coupon, dans son repère, requises")
    if not isinstance(seuils, (list, tuple)) or not 1 <= len(seuils) <= _MAX_PALIERS:
        raise ValueError("Déclarer de un à cinq couples seuil MPa et densité")
    if any(not isinstance(s, (list, tuple)) or len(s) != 2 for s in seuils):
        raise ValueError("Chaque seuil contient une contrainte en MPa et une densité")
    bornes = [s[0] for s in seuils]
    if (any(type(x) not in (float, int) or not math.isfinite(x) or x < 0 for x in bornes)
            or bornes[0] != 0 or any(a >= b for a, b in zip(bornes, bornes[1:]))):
        raise ValueError("Les seuils en MPa commencent à zéro et croissent strictement")
    options = _valider_paliers(
        [{"profondeur_mm": i, "densite": s[1]} for i, s in enumerate(seuils, 1)], "profondeur_mm",
    )
    densites = [reglages.valeur_brute(r, _CLE_DENSITE) for _, r in options]
    if any(int(a[:-1]) >= int(b[:-1]) for a, b in zip(densites, densites[1:])):
        raise ValueError("Les densités doivent croître strictement avec les seuils")
    maillages = etude.get("maillages")
    if not isinstance(maillages, list) or not 2 <= len(maillages) <= 12:
        raise ValueError("Déclarer de deux à douze maillages du même problème")
    divisions_avant = [0, 0, 0]
    mesures = []
    for maillage in maillages:
        if not isinstance(maillage, dict):
            raise ValueError("Maillage invalide")
        divisions = maillage.get("divisions_xyz")
        sigma = maillage.get("von_mises_moyenne_par_zone_mpa")
        if (not isinstance(divisions, list) or len(divisions) != 3
                or any(type(x) is not int or x < 2 or x <= a for x, a in zip(divisions, divisions_avant))
                or not isinstance(sigma, list) or len(sigma) != 4
                or any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in sigma)):
            raise ValueError("Divisions croissantes et quatre contraintes finies en MPa requises")
        divisions_avant = divisions
        mesures.append(sigma)
    zones = []
    zones_instables = []
    paliers = []
    for i, valeurs in enumerate(zip(*mesures, strict=True)):
        bas, haut = min(valeurs), max(valeurs)
        choix = sum(haut >= seuil for seuil in bornes) - 1
        # Un seuil traversé par les maillages rend la classe indéterminée (#83).
        stable = sum(bas >= seuil for seuil in bornes) - 1 == choix
        if not stable:
            zones_instables.append(i + 1)
        densite = densites[choix]
        zones.append({"x_mm": limites[i:i + 2], "enveloppe_moyennes_mpa": [bas, haut],
                      "densite": densite if stable else None})
        if paliers and paliers[-1]["densite"] == densite:
            paliers[-1]["profondeur_mm"] = limites[i + 1]
        else:
            paliers.append({"profondeur_mm": limites[i + 1], "densite": densite})
    details = {"seuils_mpa_et_densites": list(zip(bornes, densites, strict=True)),
               "zones": zones, "zones_instables": zones_instables, "validation_physique": False,
               "effet_gcode": "NON VÉRIFIÉ", "loi_mecanique": "NON VÉRIFIÉ"}
    if zones_instables:
        return [], {**details, "statut": "NON VÉRIFIÉ", "raison": "Un seuil est franchi entre maillages"}
    # Reconstruit uniquement le pavé étudié ; aucun rapprochement avec un STL arbitraire.
    piece = trimesh.creation.box(cotes)
    piece.apply_translation([longueur / 2, largeur / 2, hauteur / 2])
    volumes, rapport = modificateurs_depuis_plan(piece, [0, 0, 0], [-1, 0, 0], paliers,
                                                 volume_min_mm3=volume_min_mm3)
    if not volumes:
        return [], {**rapport, **details}
    moyenne = math.fsum(int(p["densite"][:-1]) * p["volume_mesure_mm3"] for p in rapport["paliers"])
    return volumes, {**rapport, **details,
                     "densite_moyenne_ponderee_volume_pct": moyenne / rapport["volume_piece_mm3"]}


def _boule_distance(corps):
    boule = mf.Manifold.sphere(1, _SEGMENTS_SPHERE)
    if corps.num_tri() * boule.num_tri() > 1_000_000:
        raise ValueError("Budget de calcul de distance dépassé : utiliser un maillage plus simple")
    # Le maillage inscrit borne l’écart à une boule de rayon exact.
    # Son écart radial maximal vient de la face la plus proche du centre.
    maillage_boule = _vers_trimesh(boule)
    inrayon = float(np.min(np.einsum("ij,ij->i", maillage_boule.triangles_center, maillage_boule.face_normals)))
    return boule, inrayon


def modificateurs_autour_fixation(piece, fixation, paliers, *, volume_min_mm3=0.1):
    """Retourne des paliers autour d’une fixation maillée extérieure.

    Les deux solides partagent le repère en mm. Les paliers déclarent une
    distance en mm et une densité en %. PASS porte sur leur géométrie
    disjointe dans la pièce, NON VÉRIFIÉ indique une opération incomplète.
    La distance suit une boule polyédrique inscrite. Aucune entrée n’est
    modifiée et aucun effet G-code ou comportement physique n’est déduit.
    """
    normalises = _valider_paliers(paliers, "distance_mm")
    base, volume_base = _piece_et_volume(piece, volume_min_mm3)
    obstacle, _ = _piece_et_volume(fixation, volume_min_mm3)
    boule, inrayon = _boule_distance(obstacle)
    intersection = base ^ obstacle
    if intersection.status() != mf.Error.NoError or abs(intersection.volume()) > max(1e-9, volume_base * 1e-10):
        return [], {
            "statut": "NON VÉRIFIÉ", "raison": "La fixation chevauche la matière ou l’intersection est indéterminée.",
            "effet_gcode": "NON VÉRIFIÉ", "validation_physique": False, "paliers": [],
        }

    return _assembler(
        base, volume_base, normalises, lambda d: base ^ obstacle.minkowski_sum(boule.scale((d, d, d))),
        prefixe="palier-fixation", nom_distance="distance", volume_min_mm3=volume_min_mm3,
        details={"erreur_radiale_boule_max_mm": (1 - inrayon) * normalises[-1][0],
                 "triangles_fixation": obstacle.num_tri()},
        limite="Dilatation de la fixation maillée ; aucun effet G-code ni tenue physique établis.",
    )


def modificateurs_depuis_surface(piece, paliers, *, volume_min_mm3=0.1):
    """Retourne des bandes internes depuis la surface d’un solide fermé.

    Les profondeurs sont en mm et les densités en %. PASS décrit les
    volumes disjoints confinés à la pièce. NON VÉRIFIÉ indique un défaut
    géométrique ou un palier vide. La distance suit une boule inscrite.
    Aucun réglage, modèle source ou résultat physique n’est modifié.
    """
    normalises = _valider_paliers(paliers, "profondeur_mm")
    base, volume_base = _piece_et_volume(piece, volume_min_mm3)
    boule, inrayon = _boule_distance(base)

    def couche_cumulee(profondeur):
        coeur = base.minkowski_difference(boule.scale((profondeur,) * 3))
        # Un cœur vide peut être normal ; un échec du noyau ne qualifie aucune bande.
        if coeur.status() != mf.Error.NoError:
            return coeur
        return base - coeur

    return _assembler(
        base, volume_base, normalises, couche_cumulee,
        prefixe="palier-surface", nom_distance="profondeur", volume_min_mm3=volume_min_mm3,
        details={"erreur_radiale_boule_max_mm": (1 - inrayon) * normalises[-1][0]},
        limite="Érosion de la pièce maillée ; aucun effet G-code ni tenue physique établis.",
    )
