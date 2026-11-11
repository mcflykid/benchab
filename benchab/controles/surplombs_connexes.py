"""Cartographie avant tranchage des faces inclinées vers le bas d'un STL."""

import argparse
import hashlib
import heapq
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
MAX_VISIBILITE_RAYONS = 256
TOLERANCE_VISIBILITE_MM = 1e-6
TOLERANCE_POIDS_BARYCENTRIQUES = 1e-9


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
    if not stat.S_ISREG(avant_chemin.st_mode):
        raise ValueError("La source STL doit être un fichier ordinaire.")
    # Un remplacement entre lstat et open ne doit jamais bloquer la lecture.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
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


def _evaluer_rayon_vertical(triangles, face_source, point, plateau_z_mm):
    """Classe une visée ponctuelle en mm ; contacts, projections dégénérées et doublons sont ambigus."""
    x, y, z_depart = (float(valeur) for valeur in point)
    tolerance = TOLERANCE_VISIBILITE_MM
    # Seul le segment fermé doit entrer dans le calcul ; les contacts aux
    # bornes sont ensuite distingués d'un obstacle strictement intermédiaire.
    minimum, maximum = triangles.min(axis=1), triangles.max(axis=1)
    candidats = (
        (minimum[:, 0] - tolerance <= x) & (maximum[:, 0] + tolerance >= x)
        & (minimum[:, 1] - tolerance <= y) & (maximum[:, 1] + tolerance >= y)
        & (minimum[:, 2] - tolerance <= z_depart) & (maximum[:, 2] + tolerance >= plateau_z_mm)
    )
    candidats[face_source] = False
    triangles = triangles[candidats]
    if not len(triangles):
        return {"statut": "DÉGAGÉE", "raison": "aucun triangle détecté sur le segment ouvert"}
    xy = triangles[:, :, :2]
    altitudes = triangles[:, :, 2]
    a, b, c = xy[:, 0], xy[:, 1], xy[:, 2]
    determinant = (
        (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
        - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    )
    longueurs = np.stack((
        np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1),
    ), axis=1)
    degeneres = np.abs(determinant) <= tolerance * longueurs.max(axis=1)
    point_xy = np.asarray([x, y], dtype=float)
    z_fibre_min = np.full(len(triangles), np.inf)
    z_fibre_max = np.full(len(triangles), -np.inf)
    for cote in range(3):
        depart = xy[:, cote]
        arrivee = xy[:, (cote + 1) % 3]
        direction = arrivee - depart
        longueur2 = np.einsum("ij,ij->i", direction, direction)
        longueur_nulle = longueur2 <= tolerance * tolerance
        proche_depart = np.linalg.norm(depart - point_xy, axis=1) <= tolerance
        proche_arrivee = np.linalg.norm(arrivee - point_xy, axis=1) <= tolerance
        aretes_ponctuelles = degeneres & longueur_nulle & proche_depart & proche_arrivee
        z_debut, z_fin = altitudes[:, cote], altitudes[:, (cote + 1) % 3]
        z_fibre_min = np.minimum(
            z_fibre_min,
            np.where(aretes_ponctuelles, np.minimum(z_debut, z_fin), np.inf),
        )
        z_fibre_max = np.maximum(
            z_fibre_max,
            np.where(aretes_ponctuelles, np.maximum(z_debut, z_fin), -np.inf),
        )
        assez_long = ~longueur_nulle
        fractions = np.zeros(len(triangles), dtype=float)
        fractions[assez_long] = np.clip(
            np.einsum("ij,ij->i", point_xy - depart[assez_long], direction[assez_long])
            / longueur2[assez_long], 0.0, 1.0,
        )
        projection = depart + fractions[:, None] * direction
        distance = np.linalg.norm(projection - point_xy, axis=1)
        touche_arete = degeneres & assez_long & (distance <= tolerance)
        z_arete = z_debut + fractions * (z_fin - z_debut)
        z_fibre_min = np.minimum(z_fibre_min, np.where(touche_arete, z_arete, np.inf))
        z_fibre_max = np.maximum(z_fibre_max, np.where(touche_arete, z_arete, -np.inf))
    # Les arêtes qui traversent le même XY décrivent les bornes Z de la fibre
    # du triangle projeté dégénéré ; une seule arête ne suffit pas pour trancher.
    fibre_touche_segment = (
        degeneres & np.isfinite(z_fibre_min)
        & (z_fibre_max >= plateau_z_mm - tolerance)
        & (z_fibre_min <= z_depart + tolerance)
    )
    if np.any(fibre_touche_segment):
        return {"statut": "AMBIGUË", "raison": "projection XY dégénérée sur la visée"}

    denominateur = np.where(degeneres, 1.0, determinant)
    alpha = ((b[:, 0] - x) * (c[:, 1] - y) - (b[:, 1] - y) * (c[:, 0] - x)) / denominateur
    beta = ((c[:, 0] - x) * (a[:, 1] - y) - (c[:, 1] - y) * (a[:, 0] - x)) / denominateur
    gamma = 1.0 - alpha - beta
    poids = np.column_stack((alpha, beta, gamma))
    interieur_ferme = (
        (~degeneres)
        & np.all(poids >= -TOLERANCE_POIDS_BARYCENTRIQUES, axis=1)
        & np.all(poids <= 1 + TOLERANCE_POIDS_BARYCENTRIQUES, axis=1)
    )
    z_intersection = np.einsum("ij,ij->i", poids, altitudes)
    avant_depart = z_intersection < z_depart - tolerance
    if np.any(interieur_ferme & (np.abs(z_intersection - z_depart) <= tolerance)):
        return {"statut": "AMBIGUË", "raison": "contact avec une autre face au départ"}
    touche_plan = interieur_ferme & avant_depart & (np.abs(z_intersection - plateau_z_mm) <= tolerance)
    if np.any(touche_plan):
        return {"statut": "AMBIGUË", "raison": "contact avec le plan de plateau"}
    frontiere = np.any(np.abs(poids) <= TOLERANCE_POIDS_BARYCENTRIQUES, axis=1)
    au_dessus_du_plan = z_intersection > plateau_z_mm + tolerance
    if np.any(interieur_ferme & avant_depart & au_dessus_du_plan & frontiere):
        return {"statut": "AMBIGUË", "raison": "intersection sur la frontière d'une projection"}
    contacts = np.sort(z_intersection[interieur_ferme & avant_depart & au_dessus_du_plan])
    if len(contacts) > 1 and np.any(np.diff(contacts) <= tolerance):
        return {"statut": "AMBIGUË", "raison": "superposition de triangles sur la visée"}
    if len(contacts):
        return {"statut": "OBSTRUÉE", "obstacle_z_mm": round(float(contacts[-1]), 6)}
    return {"statut": "DÉGAGÉE", "raison": "aucun triangle détecté sur le segment ouvert"}


def _visibilite_groupe(triangles, indices, plateau_z_mm, plan_valide, budget):
    """Produit des visées ponctuelles bornées ; les faces non échantillonnées restent sans conclusion."""
    if not plan_valide:
        return {"visées_échantillonnées": {
            "statut": "AMBIGUË", "raison": "plan de plateau invalide ou en contact avec le maillage",
            "plateau_z_mm": plateau_z_mm if math.isfinite(plateau_z_mm) else None,
            "methode": "barycentres de faces surplombantes, visées verticales vers −Z",
            "portee": "visées échantillonnées ; ne décrit aucune face entière",
            "faces_candidates": len(indices), "rayons_total": 0, "rayons_omis": len(indices),
            "degages": 0, "obstrues": 0, "ambigus": 0, "echantillons": [],
        }}
    nombre = min(len(indices), budget)
    positions = np.linspace(0, len(indices) - 1, num=nombre, dtype=int) if nombre else np.array([], dtype=int)
    faces = [indices[int(position)] for position in positions]
    echantillons = []
    for face in faces:
        point = np.asarray(triangles[face], dtype=float).mean(axis=0)
        resultat = _evaluer_rayon_vertical(triangles, face, point, plateau_z_mm)
        echantillons.append({
            "face_id": int(face), "point_xyz_mm": [round(float(v), 6) for v in point], **resultat,
        })
    degages = sum(item["statut"] == "DÉGAGÉE" for item in echantillons)
    obstrues = sum(item["statut"] == "OBSTRUÉE" for item in echantillons)
    ambigus = len(echantillons) - degages - obstrues
    omis = len(indices) - len(faces)
    statut = "AMBIGUË" if ambigus or omis else "OBSTRUÉE" if obstrues else "DÉGAGÉE"
    return {"visées_échantillonnées": {
        "statut": statut, "plateau_z_mm": round(float(plateau_z_mm), 6),
        "methode": "barycentres de faces surplombantes, visées verticales vers −Z",
        "portee": "visées échantillonnées aux barycentres ; ne décrit aucune face entière ni le groupe complet",
        "faces_candidates": len(indices), "rayons_total": len(echantillons), "rayons_omis": omis,
        "degages": degages, "obstrues": obstrues, "ambigus": ambigus, "echantillons": echantillons,
    }}


def cartographier(
    maillage, *, source, unite, angle_max_deg=50.0, source_sha256=None, plateau_z_mm=None, groupe_indices=None,
):
    """Mesure les groupes de faces orientées vers le bas sans prédire les supports physiques.

    Entrées : maillage triangulé, provenance, unité (« mm »), seuil en degrés et SHA-256 facultatif.
    Sortie : groupes connexes avec aires en mm², altitudes/hauteurs en mm et angles en degrés.
    La topologie est mesurée par égalité exacte des coordonnées ; aucun sommet n'est fusionné ni triangle réorienté.
    Les visées opt-in exigent un SHA-256 du STL exact et sont bornées ; accès global et ponts restent NON VÉRIFIÉS.
    Le groupe nommé par ``groupe_indices`` inclut ses indices de triangles, sans unité.
    Aucun réglage n'est modifié.
    """
    if unite != "mm":
        raise ValueError("L'unité métrique doit être explicitement établie comme mm.")
    if not math.isfinite(angle_max_deg) or not 0 < angle_max_deg <= 90:
        raise ValueError("L'angle maximal doit être fini, supérieur à 0 et inférieur ou égal à 90 degrés.")
    if not isinstance(maillage, trimesh.Trimesh) or len(maillage.faces) == 0:
        raise ValueError("Un maillage triangulé non vide est requis.")
    if plateau_z_mm is not None and (
        not isinstance(source_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
    ):
        raise ValueError("Les visées vers le plateau exigent le SHA-256 du STL exact.")
    if len(maillage.faces) > MAX_FACES:
        raise ValueError(f"Le STL dépasse le budget de {MAX_FACES} triangles.")
    if not np.isfinite(maillage.vertices).all() or not np.isfinite(maillage.face_normals).all():
        raise ValueError("Le maillage contient des coordonnées non finies.")
    normales = np.asarray(maillage.face_normals, dtype=float)
    aires = np.asarray(maillage.area_faces, dtype=float)
    triangles = np.asarray(maillage.triangles, dtype=float)
    if not np.isfinite(aires).all() or np.any(aires <= 0):
        raise ValueError("Le maillage contient des faces d'aire nulle ou non finie.")

    # Le seuil décrit seulement une inclinaison géométrique ; il ne constitue pas une règle de support.
    angle_faces = np.degrees(np.arccos(np.clip(np.abs(normales[:, 2]), 0.0, 1.0)))
    selection = (normales[:, 2] < 0) & (angle_faces <= angle_max_deg)
    adjacency, topologie = _adjacence_exacte(triangles)
    composante, voisins = _composantes_faces(len(maillage.faces), adjacency)
    groupes = []
    visites = set()
    budget_rayon = MAX_VISIBILITE_RAYONS
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
        groupe = {
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
        }
        if groupe_indices == groupe["id"]:
            groupe["indices_faces"] = sorted(faces)
        if plateau_z_mm is not None:
            # Un groupe au niveau du plateau ne prive pas un îlot distinct
            # de visées au-dessus de ce même plan.
            plateau_valide = math.isfinite(plateau_z_mm) and plateau_z_mm < (
                float(sommets_z.min()) - TOLERANCE_VISIBILITE_MM
            )
            groupe["visibilite_verticale_plateau"] = _visibilite_groupe(
                triangles, faces, float(plateau_z_mm), plateau_valide, budget_rayon
            )
            groupe["acces_plateau"]["raison"] = "visées ponctuelles uniquement ; aucun accès global ni tranchage établi"
            budget_rayon -= groupe["visibilite_verticale_plateau"]["visées_échantillonnées"]["rayons_total"]
        groupes.append(groupe)
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


def _resume(rapport, sortie):
    """Garde les cinq groupes d’aire la plus grande en mm² et indique les omissions."""
    groupes = rapport["groupes"]
    grands = heapq.nlargest(5, groupes, key=lambda groupe: groupe["aire_mm2"])
    return {
        "statut": rapport["statut"], "total_groupes": len(groupes),
        "groupes_omis": len(groupes) - len(grands),
        "topologie": rapport["topologie_maillage"]["statut"],
        "appuis_et_ponts": "NON VÉRIFIÉS",
        "points_importants": [
            {cle: groupe[cle] for cle in ("id", "aire_mm2", "altitude_mm", "nb_faces")}
            for groupe in grands
        ],
        "rapport": str(sortie),
    }


def main(argv=None, *, prive=None):
    """Produit un JSON privé neuf depuis un STL ; unités mm déclarées par option, code 0 si mesuré.

    Entrées : chemin STL, --unite-mm obligatoire, seuil optionnel en degrés, plan --plateau-z-mm optionnel.
    Sortie : rapport JSON neuf et visionneuse optionnelle, visées en mm, résumé, détail ou JSON stdout ;
    erreurs d'entrée et de sortie code 2.
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
    parser.add_argument(
        "--plateau-z-mm", type=float,
        help="Active des visées verticales ponctuelles vers un plan déclaré sous le maillage, en mm",
    )
    sorties = parser.add_mutually_exclusive_group()
    sorties.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf ; sans option, écrit sur stdout")
    sorties.add_argument("--dossier-apercu", type=Path, help="Dossier neuf pour le rapport et la visionneuse autonome")
    affichage = parser.add_mutually_exclusive_group()
    affichage.add_argument("--resume", action="store_true", help="Affiche cinq groupes au plus et le chemin du rapport")
    affichage.add_argument("--detail", action="store_true", help="Affiche aussi le rapport complet et son chemin")
    args = parser.parse_args(argv)
    try:
        if (args.resume or args.detail) and args.sortie is None and args.dossier_apercu is None:
            raise ValueError("--resume ou --detail exige --sortie ou --dossier-apercu.")
        if args.dossier_apercu is not None and args.plateau_z_mm is None:
            raise ValueError("--dossier-apercu exige --plateau-z-mm.")
        if not args.unite_mm:
            raise ValueError("--unite-mm est requis : STL ne porte pas d'unité déclarée.")
        if args.source.suffix.lower() != ".stl" or not args.source.is_file():
            raise ValueError("Une source STL existante est requise.")
        contenu, empreinte, nombre_faces = _lire_source_stl(args.source)
        # Le décodeur consomme le blob empreinté ; rouvrir le chemin dissocierait preuve et maillage.
        maillage = trimesh.load_mesh(io.BytesIO(contenu), file_type="stl", process=False)
        if not isinstance(maillage, trimesh.Trimesh) or len(maillage.faces) != nombre_faces:
            raise ValueError("Le nombre de triangles décodé diffère du contenu STL empreinté.")
        rapport = cartographier(
            maillage,
            source=args.source,
            unite="mm",
            angle_max_deg=args.angle_max_deg,
            source_sha256=empreinte,
            plateau_z_mm=args.plateau_z_mm,
        )
        if args.dossier_apercu:
            from benchab.visualisation.apercu_surplombs import publier

            chemins = publier(maillage, rapport, args.dossier_apercu, prive=prive)
            synthese = rapport if args.detail else _resume(rapport, chemins["rapport"])
            print(json.dumps({**synthese, **chemins}, ensure_ascii=False, allow_nan=False))
        elif args.sortie:
            sortie = sortie_privee(args.sortie, prive=prive)
            ecrire_json_neuf(sortie, rapport, entrees=[args.source])
            if args.resume:
                print(json.dumps(_resume(rapport, sortie), ensure_ascii=False, allow_nan=False))
            elif args.detail:
                print(json.dumps({**rapport, "rapport": str(sortie)}, ensure_ascii=False, indent=2, allow_nan=False))
        else:
            print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, ImportError, RuntimeError) as erreur:
        print(f"BENCHAB : {erreur}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
