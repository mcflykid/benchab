"""Mesure un surplomb depuis les normales du maillage et les zones déclarées."""

import argparse
import io
import json
import math
from pathlib import Path

import numpy as np
import trimesh

from benchab.controles.surplombs_connexes import MAX_FACES, _lire_source_stl
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.qualite.statuts import code_statut


def _positif(valeur, nom):
    """Lit le nombre positif en mm ou degrés déclaré, sans conversion implicite."""
    if type(valeur) not in {int, float} or not math.isfinite(valeur) or valeur <= 0:
        raise ValueError(f"{nom} doit être un nombre fini strictement positif.")
    return float(valeur)


def analyser(maillage, zones):
    """Mesure max, p99 pondéré par aire et position par zone, en degrés et mm.

    Les boîtes XYZ en mm sélectionnent les centroïdes de faces descendantes.
    Une paroi fine déclare son angle limite. Une zone pleine déclare sa hauteur
    de couche et sa largeur de ligne. Renvoie PASS, WARN ou NON VÉRIFIÉ
    pour cette convention numérique, sans modifier ni orienter le maillage.
    """
    if not isinstance(maillage, trimesh.Trimesh) or not 0 < len(maillage.faces) <= MAX_FACES:
        raise ValueError(f"Maillage triangulé de 1 à {MAX_FACES} faces requis.")
    if not isinstance(zones, list) or not 0 < len(zones) <= 100:
        raise ValueError("Déclarer de 1 à 100 zones.")
    centres = maillage.triangles_center
    normales = maillage.face_normals
    aires = maillage.area_faces
    if not all(np.isfinite(x).all() for x in (centres, normales, aires)) or np.any(aires <= 0):
        raise ValueError("Coordonnées, normales et aires finies et faces non dégénérées requises.")
    # La norme de la composante XY tient compte de la vraie pente, y compris sur une nervure oblique.
    angles = np.degrees(np.arctan2(np.maximum(0, -normales[:, 2]), np.linalg.norm(normales[:, :2], axis=1)))
    rapports = []
    noms = set()
    for zone in zones:
        if not isinstance(zone, dict) or not isinstance(zone.get("nom"), str) or not zone["nom"].strip():
            raise ValueError("Nom de zone obligatoire.")
        if zone["nom"] in noms or not isinstance(zone.get("source"), str) or not zone["source"].strip():
            raise ValueError("Nom de zone unique et source de son critère obligatoires.")
        noms.add(zone["nom"])
        boite = np.asarray(zone.get("boite_mm"), dtype=float)
        if boite.shape != (2, 3) or not np.isfinite(boite).all() or np.any(boite[0] >= boite[1]):
            raise ValueError("Boîte XYZ finie avec un volume positif requise.")
        type_zone = zone.get("type")
        hauteur = None
        if type_zone == "paroi_fine":
            limite = _positif(zone.get("angle_max_deg"), "angle_max_deg")
            if limite >= 90:
                raise ValueError("L’angle limite de la paroi doit être inférieur à 90°.")
        elif type_zone == "zone_pleine":
            hauteur = _positif(zone.get("hauteur_couche_mm"), "hauteur_couche_mm")
            largeur = _positif(zone.get("largeur_ligne_mm"), "largeur_ligne_mm")
            limite = math.degrees(math.atan2(largeur / 2, hauteur))
        else:
            raise ValueError("Type de zone attendu : paroi_fine ou zone_pleine.")
        selection = np.flatnonzero(
            (normales[:, 2] < 0) & np.all(centres >= boite[0], axis=1) & np.all(centres <= boite[1], axis=1)
        )
        ligne = dict(nom=zone["nom"], type=type_zone, boite_mm=boite.tolist(), source=zone["source"],
                     seuil_deg=limite, statut="NON VÉRIFIÉ", faces=int(len(selection)))
        if len(selection):
            ordre = selection[np.argsort(angles[selection], kind="stable")]
            cumule = np.cumsum(aires[ordre])
            maximum = float(angles[ordre[-1]])
            ligne.update(
                statut="WARN" if maximum > limite + 1e-9 else "PASS",
                angle_max_deg=maximum, position_max_xyz_mm=centres[ordre[-1]].tolist(),
                angle_p99_deg=float(angles[ordre[np.searchsorted(cumule, cumule[-1] * 0.99)]]),
                aire_mm2=float(cumule[-1]), surfaces_par_tranche_5_deg=[
                    {"angle_min_deg": bas, "aire_mm2": float(aires[selection][
                        (angles[selection] >= bas) & (angles[selection] < bas + 5)
                    ].sum())}
                    for bas in range(0, 91, 5)
                ],
            )
            if hauteur is not None:
                ligne["decalage_max_mm"] = hauteur * math.tan(math.radians(maximum)) if maximum < 90 else None
        rapports.append(ligne)
    statuts = {r["statut"] for r in rapports}
    statut = "NON VÉRIFIÉ" if "NON VÉRIFIÉ" in statuts else "WARN" if "WARN" in statuts else "PASS"
    return dict(statut=statut, code=code_statut(statut), zones=rapports, validation_physique=False)


def main(argv=None, *, prive=None):
    """Lit un STL en mm et une déclaration de zones, puis publie un JSON privé neuf."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--zones", type=Path, required=True)
    parser.add_argument("--unite-mm", action="store_true", required=True)
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        destination = destination_neuve(args.sortie, prive=prive)
        contenu, empreinte, faces = _lire_source_stl(args.source)
        mesh = trimesh.load_mesh(io.BytesIO(contenu), file_type="stl", process=False)
        if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) != faces:
            raise ValueError("Les faces chargées ne correspondent pas au STL empreinté.")
        if args.zones.stat().st_size > 128 * 1024:
            raise ValueError("Déclaration de zones hors budget de 128 Kio.")
        rapport = analyser(mesh, json.loads(args.zones.read_text(encoding="utf-8")))
        rapport["source_sha256"] = empreinte
        ecrire_json_neuf(destination, rapport, entrees=(args.source, args.zones))
    except (OSError, ValueError, TypeError) as erreur:
        parser.error(str(erreur))
    print(json.dumps({"statut": rapport["statut"], "rapport": str(destination)}, ensure_ascii=False))
    return rapport["code"]
