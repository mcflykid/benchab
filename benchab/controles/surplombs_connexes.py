"""Cartographie avant tranchage des faces inclinées vers le bas d'un STL."""

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
import struct
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import trimesh

from benchab.qualite.ecritures import ecrire_json_neuf, sortie_privee

MAX_SOURCE_OCTETS = 64 * 1024 * 1024
MAX_FACES = 50_000


def _compter_faces_stl(contenu):
    """Compte les facettes STL binaires ou textuelles avant de créer les objets Python du graphe."""
    if len(contenu) >= 84:
        nombre = struct.unpack_from("<I", contenu, 80)[0]
        if 84 + nombre * 50 == len(contenu):
            return nombre
    try:
        texte = contenu.decode("ascii")
    except UnicodeDecodeError as erreur:
        raise ValueError("Le STL n'est ni un binaire de longueur cohérente ni un STL ASCII valide.") from erreur
    nombre = sum(1 for _ in re.finditer(r"\bfacet\s+normal\b", texte, flags=re.IGNORECASE))
    if nombre == 0:
        raise ValueError("Le STL ne contient aucune facette reconnue.")
    return nombre


def _lire_source_stl(chemin):
    """Lit une seule fois un STL ordinaire borné et relie ses octets à l'identité du fichier ouvert."""
    source = Path(chemin)
    avant_chemin = source.lstat()
    if stat.S_ISLNK(avant_chemin.st_mode):
        raise ValueError("La source STL ne peut pas être un lien symbolique.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descripteur = os.open(source, flags)
    try:
        avant_fd = os.fstat(descripteur)
        if not stat.S_ISREG(avant_fd.st_mode):
            raise ValueError("La source STL doit être un fichier ordinaire.")
        if (avant_fd.st_dev, avant_fd.st_ino) != (avant_chemin.st_dev, avant_chemin.st_ino):
            raise ValueError("La source STL a changé entre son identification et son ouverture.")
        if avant_fd.st_size > MAX_SOURCE_OCTETS:
            raise ValueError(f"Le STL dépasse le budget de {MAX_SOURCE_OCTETS} octets.")
        blocs = []
        total = 0
        while True:
            bloc = os.read(descripteur, min(1024 * 1024, MAX_SOURCE_OCTETS + 1 - total))
            if not bloc:
                break
            blocs.append(bloc)
            total += len(bloc)
            if total > MAX_SOURCE_OCTETS:
                raise ValueError(f"Le STL dépasse le budget de {MAX_SOURCE_OCTETS} octets.")
        contenu = b"".join(blocs)
        apres_fd = os.fstat(descripteur)
        apres_chemin = source.lstat()
        signature_avant = (
            avant_fd.st_dev, avant_fd.st_ino, avant_fd.st_size, avant_fd.st_mtime_ns, avant_fd.st_ctime_ns,
        )
        signature_apres = (
            apres_fd.st_dev, apres_fd.st_ino, apres_fd.st_size, apres_fd.st_mtime_ns, apres_fd.st_ctime_ns,
        )
        if stat.S_ISLNK(apres_chemin.st_mode) or (apres_chemin.st_dev, apres_chemin.st_ino) != (
            apres_fd.st_dev, apres_fd.st_ino,
        ) or signature_apres != signature_avant or len(contenu) != avant_fd.st_size:
            raise ValueError("La source STL a changé pendant sa lecture ; rapport refusé.")
    finally:
        os.close(descripteur)
    nombre_faces = _compter_faces_stl(contenu)
    if nombre_faces > MAX_FACES:
        raise ValueError(f"Le STL dépasse le budget de {MAX_FACES} triangles.")
    return contenu, hashlib.sha256(contenu).hexdigest(), nombre_faces


def _adjacence_exacte(triangles):
    """Construit l'adjacence par arêtes de coordonnées exactement égales, sans fusion approchée."""
    aretes = defaultdict(list)
    for face, triangle in enumerate(triangles):
        sommets = [tuple(float(valeur) for valeur in point) for point in triangle]
        for depart, arrivee in zip(sommets, sommets[1:] + sommets[:1]):
            cle = tuple(sorted((depart, arrivee)))
            sens = 1 if depart < arrivee else -1
            aretes[cle].append((face, sens))
    adjacency = []
    ouvertes = non_manifold = orientation_incoherente = 0
    for incidents in aretes.values():
        if len(incidents) == 1:
            ouvertes += 1
        elif len(incidents) == 2:
            if incidents[0][1] == incidents[1][1]:
                orientation_incoherente += 1
            adjacency.append((incidents[0][0], incidents[1][0]))
        else:
            non_manifold += 1
            if len({incident[1] for incident in incidents}) < len(incidents):
                orientation_incoherente += 1
            faces = [incident[0] for incident in incidents]
            adjacency.extend((faces[0], face) for face in faces[1:])
    return np.asarray(adjacency, dtype=int).reshape((-1, 2)), {
        "aretes_ouvertes": ouvertes,
        "aretes_non_manifold": non_manifold,
        "aretes_orientation_incoherente": orientation_incoherente,
    }


def _composantes_faces(nombre_faces, adjacency):
    """Numérote les composantes surfaciques à partir des arêtes géométriquement partagées."""
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


def cartographier(maillage, *, source, unite, angle_max_deg=50.0, source_sha256=None):
    """Mesure les groupes de faces orientées vers le bas sans prédire les supports physiques.

    Entrées : maillage triangulé, provenance, unité explicite (« mm »), seuil en degrés et SHA-256 facultatif.
    Sortie : groupes connexes avec aires en mm², altitudes/hauteurs en mm et angles en degrés.
    La topologie est mesurée par égalité exacte des coordonnées ; aucun sommet n'est fusionné ni triangle réorienté.
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
    adjacency, topologie = _adjacence_exacte(triangles := np.asarray(maillage.triangles, dtype=float))
    composante, voisins = _composantes_faces(len(maillage.faces), adjacency)
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
        "source_sha256": source_sha256,
        "unite": "mm",
        "topologie_maillage": {
            **topologie,
            "statut": (
                "AMBIGUË"
                if topologie["aretes_non_manifold"] or topologie["aretes_orientation_incoherente"]
                else "OUVERTE" if topologie["aretes_ouvertes"] else "RENSEIGNÉE"
            ),
            "coherence_orientation": (
                "AMBIGUË"
                if topologie["aretes_non_manifold"] or topologie["aretes_orientation_incoherente"]
                else "NON VÉRIFIÉE" if topologie["aretes_ouvertes"] else "COHÉRENTE LOCALEMENT"
            ),
            "sens_normales": "calculées depuis l'ordre des sommets des triangles ; aucune réorientation appliquée",
        },
        "seuil_inclinaison_deg": angle_max_deg,
        "definition": "faces à normale descendante, inclinées au plus du seuil par rapport à l'horizontale",
        "interpretation": "mesures de maillage avant tranchage ; aucune nécessité physique de support n'est conclue",
        "groupes": groupes,
    }


def main(argv=None, *, prive=None):
    """Produit un JSON privé neuf depuis un STL ; unités mm déclarées par option, code 0 si mesuré.

    Entrées : chemin STL, --unite-mm obligatoire, seuil optionnel en degrés et --sortie facultative.
    Sortie : rapport JSON écrit sans écrasement, ou JSON sur stdout ; erreurs d'entrée et de sortie code 2.
    L'entrée est empreintée avant et après lecture ; une sortie reste dans l'atelier privé choisi et est neuve.
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
        contenu, empreinte, nombre_faces = _lire_source_stl(args.source)
        # Le décodeur consomme le blob empreinté ; rouvrir le chemin dissocierait preuve et maillage.
        maillage = trimesh.load_mesh(io.BytesIO(contenu), file_type="stl", process=False)
        if len(maillage.faces) != nombre_faces:
            raise ValueError("Le nombre de triangles décodé diffère du contenu STL empreinté.")
        rapport = cartographier(
            maillage,
            source=args.source,
            unite="mm",
            angle_max_deg=args.angle_max_deg,
            source_sha256=empreinte,
        )
        if args.sortie:
            sortie = sortie_privee(args.sortie, prive=prive)
            ecrire_json_neuf(sortie, rapport, entrees=[args.source])
        else:
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, ImportError, RuntimeError) as erreur:
        print(f"BENCHAB : {erreur}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
