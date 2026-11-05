"""Adaptateur localisable du contrôle d'orientation existant, sans fabrication."""

import datetime
import hashlib
import re
from pathlib import Path

import numpy as np

from benchab.controles.orientations_supports import SEUIL, faces_surplomb

VERSION_OUTIL = hashlib.sha256(
    (Path(__file__).resolve().parents[1] / "controles/orientations_supports.py").read_bytes()
).hexdigest()


def diagnostiquer(mesh, piece, source, sha256, maximum=24):
    if (
        not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or maximum <= 0
        or (not isinstance(piece, str))
        or (not piece)
        or (not isinstance(source, str))
        or (not source)
        or (not isinstance(sha256, str))
        or (not re.fullmatch("[0-9a-f]{64}", sha256))
    ):
        raise ValueError("Provenance et limite de diagnostic requises")
    if mesh is None or not len(mesh.vertices) or (not len(mesh.faces)):
        raise ValueError("Maillage vide ; diagnostic non localisable")
    if not np.isfinite(mesh.vertices).all():
        raise ValueError("Coordonnées non finies ; diagnostic non localisable")
    indices = faces_surplomb(mesh)
    retenus = sorted(indices, key=lambda i: (-mesh.area_faces[i], int(i)))[:maximum]
    anomalies = []
    for i in retenus:
        triangle = mesh.triangles[i]
        anomalies.append(
            {
                "schema": 1,
                "id": f"surplomb:{piece}:{sha256[:12]}:{i}",
                "type": "surplomb_geometrique",
                "piece": piece,
                "triangle_source": int(i),
                "statut_execution": "PASS",
                "resultat": "WARNING",
                "gravite": "AVERTISSEMENT",
                "source": source,
                "sha256": sha256,
                "outil": "orientations_supports.faces_surplomb",
                "outil_sha256": VERSION_OUTIL,
                "repere": "modele",
                "unite_coordonnees": "mm",
                "point_modele": mesh.triangles_center[i].tolist(),
                "boite_modele": [triangle.min(axis=0).tolist(), triangle.max(axis=0).tolist()],
                "normale_modele": mesh.face_normals[i].tolist(),
                "valeur": float(-mesh.face_normals[i, 2]),
                "seuil": float(SEUIL),
                "unite_mesure": "composante normale vers -Z",
                "condition": "valeur > seuil et centre Z > 0,4 mm",
                "precision": (
                    "Centre du triangle source chargé ; marqueur indicatif sur un aperçu éventuellement allégé."
                ),
                "limite": (
                    "Risque géométrique selon le repère du modèle, pas un besoin de su"
                    "pport prouvé ni un défaut physique."
                ),
            }
        )
    return {
        "schema": 1,
        "piece": piece,
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "statut_execution": "PASS",
        "resultat": "WARNING" if len(indices) else "PASS",
        "faces_detectees": int(len(indices)),
        "faces_localisees": len(anomalies),
        "selection_bornee": len(indices) > len(anomalies),
        "anomalies": anomalies,
        "source": source,
        "sha256": sha256,
        "repere": "modele",
        "limite": "Absence de cette catégorie ne valide pas les autres risques ni la fabrication.",
    }


def charger_diagnostic(path, piece, source=None):
    path = Path(path)
    before = path.stat()

    def signature(s):
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    from benchab.qualite.empreintes import sha256_fichier

    empreinte = sha256_fichier(path)
    from benchab.visualisation.visionneuse import charger_maillage

    mesh = charger_maillage(path, process=False)
    if signature(before) != signature(path.stat()):
        raise ValueError("Source modifiée pendant son contrôle")
    from benchab.visualisation.inspection_geometrique import enrichir

    return (mesh, enrichir(mesh, diagnostiquer(mesh, piece, source or path.name, empreinte)))
