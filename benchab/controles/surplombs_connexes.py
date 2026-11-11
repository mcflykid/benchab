"""Cartographie avant tranchage des faces inclinées vers le bas d'un STL."""

import argparse
import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import trimesh

from benchab.qualite.ecritures import ecrire_json_neuf, sortie_privee


def _composantes_faces(nombre_faces, adjacency):
    """Numérote les composantes surfaciques à partir des paires d'adjacence du maillage."""
    voisins = defaultdict(list)
    for a, b in adjacency:
        voisins[int(a)].append(int(b))
        voisins[int(b)].append(int(a))
    numeros = np.full(nombre_faces, -1, dtype=int)
    numero = 0
    for depart in range(nombre_faces):
        if numeros[depart] >= 0:
            continue
        numeros[depart] = numero
        attente = deque([depart])
        while attente:
            face = attente.popleft()
            for voisine in voisins[face]:
                if numeros[voisine] < 0:
                    numeros[voisine] = numero
                    attente.append(voisine)
        numero += 1
    return numeros, voisins


def cartographier(maillage, *, source, unite, angle_max_deg=50.0):
    """Mesure les groupes de faces orientées vers le bas sans prédire les supports physiques.

    Entrées : maillage triangulé, provenance source, unité explicite (« mm ») et angle maximal en degrés.
    Sortie : groupes connexes avec aires en mm², altitudes/hauteurs en mm et angles en degrés.
    Les accès au plateau et la possibilité de pont restent NON VÉRIFIÉS ; aucun réglage n'est modifié.
    """
    if unite != "mm":
        raise ValueError("L'unité métrique doit être explicitement établie comme mm.")
    if not math.isfinite(angle_max_deg) or not 0 < angle_max_deg <= 90:
        raise ValueError("L'angle maximal doit être fini, supérieur à 0 et inférieur ou égal à 90 degrés.")
    if not isinstance(maillage, trimesh.Trimesh) or len(maillage.faces) == 0:
        raise ValueError("Un maillage triangulé non vide est requis.")
    if not np.isfinite(maillage.vertices).all() or not np.isfinite(maillage.face_normals).all():
        raise ValueError("Le maillage contient des coordonnées non finies.")
    normales = np.asarray(maillage.face_normals, dtype=float)
    aires = np.asarray(maillage.area_faces, dtype=float)
    if not np.isfinite(aires).all() or np.any(aires <= 0):
        raise ValueError("Le maillage contient des faces d'aire nulle ou non finie.")

    # Le seuil décrit seulement une inclinaison géométrique ; il ne constitue pas une règle de support.
    angle_faces = np.degrees(np.arccos(np.clip(np.abs(normales[:, 2]), 0.0, 1.0)))
    selection = (normales[:, 2] < 0) & (angle_faces <= angle_max_deg)
    adjacency = np.asarray(maillage.face_adjacency, dtype=int).reshape((-1, 2))
    composante, voisins = _composantes_faces(len(maillage.faces), adjacency)
    triangles = np.asarray(maillage.triangles, dtype=float)
    groupes = []
    visites = set()
    for depart in np.flatnonzero(selection):
        depart = int(depart)
        if depart in visites:
            continue
        visites.add(depart)
        attente = deque([depart])
        faces = []
        while attente:
            face = attente.popleft()
            faces.append(face)
            for voisine in voisins[face]:
                if selection[voisine] and voisine not in visites:
                    visites.add(voisine)
                    attente.append(voisine)
        ids = np.asarray(faces, dtype=int)
        sommets_z = triangles[ids, :, 2].reshape(-1)
        poids = aires[ids]
        groupes.append({
            "id": f"surplomb-{len(groupes) + 1:03d}",
            "composante_maillage": int(composante[depart]) + 1,
            "nb_faces": int(len(ids)),
            "aire_mm2": round(float(poids.sum()), 6),
            "angle_surface_horizontale_deg": {
                "min": round(float(angle_faces[ids].min()), 6),
                "moyenne_ponderee": round(float(np.average(angle_faces[ids], weights=poids)), 6),
                "max": round(float(angle_faces[ids].max()), 6),
            },
            "altitude_mm": {
                "min": round(float(sommets_z.min()), 6),
                "max": round(float(sommets_z.max()), 6),
            },
            "hauteur_mm": {
                "min": round(float(sommets_z.min()), 6),
                "max": round(float(sommets_z.max()), 6),
                "etendue": round(float(sommets_z.max() - sommets_z.min()), 6),
            },
            "acces_plateau": {"statut": "NON VÉRIFIÉ", "raison": "aucun test de visibilité ni de tranchage"},
            "pont_possible": {"statut": "NON VÉRIFIÉ", "raison": "franchissabilité non mesurée"},
        })
    return {
        "schema": "benchab-surplombs-connexes-v1",
        "statut": "RENSEIGNÉE",
        "source": str(source),
        "unite": "mm",
        "seuil_inclinaison_deg": angle_max_deg,
        "definition": "faces à normale descendante, inclinées au plus du seuil par rapport à l'horizontale",
        "interpretation": "mesures de maillage avant tranchage ; aucune nécessité physique de support n'est conclue",
        "groupes": groupes,
    }


def main(argv=None):
    """Produit un JSON privé neuf depuis un STL ; unités mm déclarées par option, code 0 si mesuré.

    Entrées : chemin STL, --unite-mm obligatoire, seuil optionnel en degrés et --sortie facultative.
    Sortie : rapport JSON écrit sans écrasement, ou JSON sur stdout ; erreurs d'entrée et de sortie code 2.
    Aucun appel de slicer, aucun réglage modifié, aucune validation physique.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Maillage triangulé STL dont les coordonnées sont exprimées en mm")
    parser.add_argument("--unite-mm", action="store_true", help="Confirme que les coordonnées STL sont en mm")
    parser.add_argument(
        "--angle-max-deg", type=float, default=50.0,
        help="Seuil géométrique en degrés, sans verdict physique",
    )
    parser.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf ; sans option, écrit sur stdout")
    args = parser.parse_args(argv)
    try:
        if not args.unite_mm:
            raise ValueError("--unite-mm est requis : STL ne porte pas d'unité déclarée.")
        if args.source.suffix.lower() != ".stl" or not args.source.is_file():
            raise ValueError("Une source STL existante est requise.")
        maillage = trimesh.load_mesh(args.source, process=False)
        rapport = cartographier(maillage, source=args.source, unite="mm", angle_max_deg=args.angle_max_deg)
        if args.sortie:
            sortie = sortie_privee(args.sortie)
            ecrire_json_neuf(sortie, rapport, entrees=[args.source])
        else:
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, ImportError, RuntimeError) as erreur:
        print(f"BENCHAB : {erreur}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
