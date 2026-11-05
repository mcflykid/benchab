"""Contrôles bornés sur le maillage source, jamais sur son aperçu allégé.

La base est celle du repère du modèle : ce n'est pas un contact plateau attesté.
Aucune écriture, réparation, décision de fabrication ou déduction de paroi."""

import hashlib
import re
from pathlib import Path

import numpy as np

VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def enrichir(mesh, diagnostic, maximum=24, faces_max=200000):
    if type(maximum) is not int or maximum < 1 or type(faces_max) is not int or (faces_max < 1):
        raise ValueError("limites entières positives requises")
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError("maillage source vide ou non fini")
    if np.any(mesh.faces < 0) or np.any(mesh.faces >= len(mesh.vertices)):
        raise ValueError("indices de faces hors du maillage")
    bounds = np.asarray(mesh.bounds)
    if bounds.shape != (2, 3) or not np.isfinite(bounds[1] - bounds[0]).all():
        raise ValueError("dimensions non finies")
    d = {**diagnostic, "anomalies": list(diagnostic.get("anomalies", []))}
    mesures = {
        "dimensions_source_mm": (bounds[1] - bounds[0]).tolist(),
        "boite_source_mm": bounds.tolist(),
        "faces_source": len(mesh.faces),
        "volume_maillage_mm3": None,
        "statut_volume": "NON_VERIFIE",
        "repere": "modele",
        "base_z_source_mm": float(bounds[0, 2]),
        "aire_base_modele_mm2": None,
        "precision": "Maillage STL source, convention mm du projet ; pas une mesure CAO exacte ni de fabrication.",
    }
    d["mesures"] = mesures
    d["non_verifie"] = [
        "parois fines",
        "petits îlots locaux",
        "supports réellement tranchés",
        "contact plateau dans la pose de fabrication",
        "jeux et mouvements physiques",
    ]
    if len(mesh.faces) > faces_max:
        mesures["topologie"] = "NON_VERIFIE_LIMITE_TAILLE"
        d["non_verifie"].append(f"topologie et base : {len(mesh.faces)} faces > limite {faces_max}")
        if d.get("resultat") == "PASS":
            d["resultat"] = "NON_VERIFIE"
        return d
    t = mesh.copy()
    t.merge_vertices(digits_vertex=8)
    if not np.isfinite(t.area_faces).all():
        raise ValueError("aires non finies")
    counts = np.bincount(t.edges_unique_inverse, minlength=len(t.edges_unique))
    ouverts = np.flatnonzero(counts == 1)
    multiples = np.flatnonzero(counts > 2)
    degeneres = np.flatnonzero(t.area_faces == 0)
    mesures.update(
        topologie="CALCULEE_SUR_COPIE_SOUDEE_1E-8_MM",
        bords_ouverts=len(ouverts),
        aretes_multiples=len(multiples),
        triangles_degeneres=len(degeneres),
        orientation_coherente=bool(t.is_winding_consistent),
    )
    nouveaux = []

    def ajouter(type_, index, points, valeur, seuil, condition, unite, limite):
        points = np.asarray(points, float)
        nouveaux.append(
            {
                "schema": 1,
                "id": f"{type_}:{d['piece']}:{d['sha256'][:12]}:{index}",
                "type": type_,
                "piece": d["piece"],
                "source": d["source"],
                "sha256": d["sha256"],
                "outil": "inspection_geometrique.enrichir",
                "outil_sha256": VERSION,
                "statut_execution": "PASS",
                "resultat": "WARNING",
                "gravite": "AVERTISSEMENT",
                "repere": "modele",
                "unite_coordonnees": "mm",
                "point_modele": points.mean(axis=0).tolist(),
                "boite_modele": [points.min(axis=0).tolist(), points.max(axis=0).tolist()],
                "valeur": float(valeur),
                "seuil": float(seuil),
                "condition": condition,
                "unite_mesure": unite,
                "precision": (
                    "Source soudée numériquement à 8 décimales sur copie ; point indic"
                    "atif, orientation de vue indicative."
                ),
                "limite": limite,
            }
        )

    for type_, indices in (("bord_ouvert", ouverts), ("arete_multiple", multiples)):
        for i in indices[:maximum]:
            ajouter(
                type_,
                int(i),
                t.vertices[t.edges_unique[i]],
                counts[i],
                2,
                "nombre de triangles incidents différent de 2",
                "triangles incidents",
                "Topologie numérique locale ; ne prouve ni défaut imprimé ni réparation nécessaire.",
            )
    for i in degeneres[:maximum]:
        ajouter(
            "triangle_degenere",
            int(i),
            t.triangles[i],
            0,
            0,
            "aire du triangle égale à zéro",
            "mm²",
            "Triangle source sans aire, pas une analyse physique.",
        )
    ferme = t.is_watertight and t.is_winding_consistent and (not len(degeneres)) and np.all(bounds[1] > bounds[0])
    volume = float(t.volume) if ferme else None
    if volume is not None and np.isfinite(volume) and (volume > 0):
        mesures.update(volume_maillage_mm3=volume, statut_volume="CALCULE_MAILLAGE_FERME_ORIENTE")
    elif volume is not None and volume < 0:
        ajouter(
            "orientation_inversee",
            0,
            t.triangles[0],
            volume,
            0,
            "volume signé négatif",
            "mm³",
            "Orientation du maillage à vérifier ; aucune inversion automatique effectuée.",
        )
    triangles = t.triangles
    base = np.all(np.abs(triangles[:, :, 2] - bounds[0, 2]) <= 1e-06, axis=1)
    aire = np.abs(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 2]) / 2
    total = float(aire[base].sum())
    mesures["aire_base_modele_mm2"] = total
    if total < 4:
        if total > 0:
            point = np.average(triangles[base].mean(axis=1), axis=0, weights=aire[base])
        else:
            point = t.vertices[t.faces.reshape(-1)][np.argmin(t.vertices[t.faces.reshape(-1), 2])]
        ajouter(
            "petite_base_modele",
            0,
            [point],
            total,
            4,
            "somme des aires projetées à Z minimum < seuil indicatif 4 mm²",
            "mm²",
            (
                "Base du repère source, pas une pose plateau attestée. Somme de tr"
                "iangles, pas union des surfaces ; seuil théorique non calibré."
            ),
        )
    d["anomalies"].extend(nouveaux)
    d["selection_bornee"] = d.get("selection_bornee", False) or any(
        (len(x) > maximum for x in (ouverts, multiples, degeneres))
    )
    if nouveaux:
        d["resultat"] = "WARNING"
    return d


def comparer(a, b):
    """Comparer des mesures source, hors pose et sans correspondance inventée."""
    ma, mb = (a.get("mesures"), b.get("mesures"))
    if not ma or not mb:
        raise ValueError("mesures source absentes")
    if any((not re.fullmatch("[a-f0-9]{64}", str(d.get("sha256", ""))) for d in (a, b))):
        raise ValueError("identités source absentes")
    va, vb = (ma.get("volume_maillage_mm3"), mb.get("volume_maillage_mm3"))
    return {
        "sources_identiques": a.get("sha256") == b.get("sha256"),
        "delta_dimensions_mm": [y - x for x, y in zip(ma["dimensions_source_mm"], mb["dimensions_source_mm"])],
        "delta_volume_maillage_mm3": vb - va if va is not None and vb is not None else None,
        "repere": "modele, hors pose",
        "limite": (
            "Différences de maillage source ; aucune correspondance de faces, "
            "pose de fabrication ou validation physique déduite."
        ),
    }
