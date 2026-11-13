"""Échantillonne une course hélicoïdale coaxiale à partir de deux volumes."""

import argparse
import json
import math
from pathlib import Path

import numpy as np

from benchab.chemins import atelier_prive, sans_lien
from benchab.conception.decoupe import _vers_trimesh
from benchab.conception.decoupe.geometrie import solide
from benchab.conception.vissage import profil_radial
from benchab.projets.validation_apercu import verifier_candidate
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.statuts import code_statut
from benchab.visualisation.visionneuse import charger_maillage

MAX_FACES_SOURCE = 500_000
MAX_FACES_INTERFACE = 100_000
MAX_POSES = 201
MAX_FACES_POSES = 8_000_000
TOLERANCE_MM3 = 0.01


def _intervalle(zone):
    """Valide deux bornes Z locales en mm dans l’ordre croissant."""
    if len(zone) != 2 or not all(map(math.isfinite, zone)) or not zone[0] < zone[1]:
        raise ValueError("La zone doit contenir deux bornes Z finies dans l’ordre croissant.")
    return zone


def _jeu(male, femelle, bas, haut):
    """Échantillonne le jeu sur les rayons XY en mm à la pose de butée."""
    if bas >= haut:
        return None
    # La première intersection définit l'alésage seulement si l'axe femelle est libre.
    z = bas + (np.arange(12) + 0.5) * (haut - bas) / 12
    male, femelle = _vers_trimesh(male), _vers_trimesh(femelle)
    origine = np.column_stack((np.zeros((len(z), 2)), z))
    if not male.contains(origine).all() or femelle.contains(origine).any():
        return None
    rayon_male = profil_radial(male.triangles, z, angles=72)
    rayon_femelle = profil_radial(femelle.triangles, z, cote="interieur", angles=72)
    difference = rayon_femelle - rayon_male
    return float(difference.min()) if np.isfinite(difference).all() and np.all(rayon_male > 0) else None


def mesurer(
    male, femelle, *, pas_mm, sens, butee_mm, phase_deg, course_mm, increment_mm,
    zone_male_mm, zone_femelle_mm, depassement_mm=0.2,
):
    """Mesure les collisions en mm³, le jeu radial en mm et la course en tours.

    Les deux maillages partagent l’axe Z et l’origine XY. La pièce mâle part
    au-dessus de la butée et descend. Retourne PASS, FAIL ou
    NON VÉRIFIÉ sur les zones Z et poses déclarées. Aucun original modifié.
    """
    zone_male_mm, zone_femelle_mm = _intervalle(zone_male_mm), _intervalle(zone_femelle_mm)
    dimensions = (pas_mm, butee_mm, phase_deg, course_mm, increment_mm, depassement_mm)
    if not all(map(math.isfinite, dimensions)) or min(pas_mm, course_mm, increment_mm, depassement_mm) <= 0:
        raise ValueError("Pas, course, incrément et dépassement doivent être finis et positifs.")
    if sens not in ("droite", "gauche"):
        raise ValueError("Sens attendu : droite ou gauche.")
    resultat = {
        "pas_mm": pas_mm, "sens": sens, "butee_translation_z_mm": butee_mm,
        "phase_butee_deg": phase_deg, "course_mm": course_mm,
        "zone_male_mm": list(zone_male_mm), "zone_femelle_mm": list(zone_femelle_mm),
        "tolerance_intersection_mm3": TOLERANCE_MM3, "validation_physique": False,
        "portee": "Collisions échantillonnées dans les tranches axiales déclarées.",
    }
    nm, nf = len(male.faces), len(femelle.faces)
    intervals = course_mm / increment_mm
    if nm + nf > MAX_FACES_SOURCE or not math.isfinite(intervals) or intervals > MAX_POSES - 1:
        return {**resultat, "statut": "NON VÉRIFIÉ", "raison": "Budget de triangles source ou de poses dépassé."}
    poses = np.linspace(course_mm, 0, max(1, math.ceil(intervals)) + 1)
    volumes = [
        solide(mesh).trim_by_plane((0, 0, 1), zone[0]).trim_by_plane((0, 0, -1), -zone[1])
        for mesh, zone in ((male, zone_male_mm), (femelle, zone_femelle_mm))
    ]
    mobile, fixe = volumes
    total = mobile.num_tri() + fixe.num_tri()
    budget = total * (3 * len(poses) + 1)
    resultat["triangles"] = {"source": [nm, nf], "interface": [mobile.num_tri(), fixe.num_tri()]}
    if any(v.is_empty() for v in volumes) or total > MAX_FACES_INTERFACE or budget > MAX_FACES_POSES:
        return {**resultat, "statut": "NON VÉRIFIÉ", "raison": "Interface vide ou budget de collisions dépassé."}
    signe = 1 if sens == "droite" else -1

    def evaluer(ecart, direction=signe, decalage=0):
        """Évalue une pose rigide dans les zones retenues, en mm, degrés et mm³."""
        translation = float(butee_mm + ecart)
        rotation = float(phase_deg + decalage + direction * (360 * ecart / pas_mm))
        place = mobile.rotate((0, 0, rotation % 360)).translate((0, 0, translation))
        intersection = place ^ fixe
        if intersection.status().name != "NoError":
            raise ValueError("Le calcul d’intersection du vissage a échoué.")
        return {"translation_z_mm": translation, "rotation_deg": rotation,
                "intersection_mm3": abs(float(intersection.volume()))}

    course = [evaluer(p) for p in poses]
    essais = {
        "phase_opposee": max((evaluer(p, decalage=180) for p in poses), key=lambda e: e["intersection_mm3"]),
        "sens_inverse": max((evaluer(p, direction=-signe) for p in poses), key=lambda e: e["intersection_mm3"]),
        "depassement": evaluer(-depassement_mm),
    }
    maximum = max(p["intersection_mm3"] for p in course)
    place = mobile.rotate((0, 0, phase_deg % 360)).translate((0, 0, butee_mm))
    mb, fb = place.bounding_box(), fixe.bounding_box()
    bas, haut = max(mb[2], fb[2]), min(mb[5], fb[5])
    recouvrement = max(0, haut - bas)
    jeu = _jeu(place, fixe, bas, haut)
    confirme = recouvrement > 0 and essais["depassement"]["intersection_mm3"] > TOLERANCE_MM3
    statut = "FAIL" if maximum > TOLERANCE_MM3 else "PASS" if confirme else "NON VÉRIFIÉ"
    return {
        **resultat, "statut": statut, "poses": course, "contre_essais": essais, "intersection_max_mm3": maximum,
        "nombre_tours": course_mm / pas_mm, "recouvrement_axial_butee_mm": recouvrement,
        "jeu_radial_min_butee_mm": jeu, "jeu_couverture": "NON VÉRIFIÉ" if jeu is None else "12 plans, 72 rayons",
        "increment_max_mm": float(poses[0] - poses[1]),
    }


def main(argv=None, *, prive=None):
    """Contrôle des STL privés dans le même repère XY et enregistre le rapport."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("male", type=Path)
    parser.add_argument("femelle", type=Path)
    parser.add_argument("--pas-mm", type=float, required=True)
    parser.add_argument("--sens", choices=("droite", "gauche"), required=True)
    parser.add_argument("--butee-mm", type=float, required=True)
    parser.add_argument("--phase-deg", type=float, required=True)
    parser.add_argument("--course-mm", type=float, required=True)
    parser.add_argument("--increment-mm", type=float, required=True)
    parser.add_argument("--zone-male-mm", type=float, nargs=2, required=True)
    parser.add_argument("--zone-femelle-mm", type=float, nargs=2, required=True)
    parser.add_argument("--depassement-mm", type=float, default=0.2)
    parser.add_argument("--json", type=Path)
    args = vars(parser.parse_args(argv))
    racine = atelier_prive(prive)
    sortie = args.pop("json")
    fichiers = [sans_lien(args.pop(champ), racine=racine, strict=True) for champ in ("male", "femelle")]
    if any(f.suffix.casefold() != ".stl" or not f.is_file() or f.stat().st_size > 32 * 1024 * 1024 for f in fichiers):
        raise ValueError("STL réguliers de 32 Mio au plus requis.")
    preuves = [verifier_candidate(f, prive=racine) for f in fichiers]
    empreintes = list(map(sha256_fichier, fichiers))
    if any(preuve and preuve["source_sha256"] != sha for preuve, sha in zip(preuves, empreintes)):
        raise ValueError("Maillage changé après sa validation visuelle.")
    rapport = mesurer(*(charger_maillage(f) for f in fichiers), **args)
    if list(map(sha256_fichier, fichiers)) != empreintes:
        raise ValueError("Maillage modifié pendant le contrôle.")
    rapport["sources_sha256"] = dict(zip(("male", "femelle"), empreintes))
    if sortie is not None:
        ecrire_json_neuf(destination_neuve(sortie, prive=racine, temporaire=False), rapport)
    print(json.dumps(rapport, ensure_ascii=False, allow_nan=False))
    return code_statut(rapport["statut"])
