"""Préparation explicite, comparaison en mm et recalage rigide EXPÉRIMENTAL.

Implémentation indépendante ; provenance et différences : recherches/acquisition-meshy-2026-10-29.
Aucun réseau, aucune réparation implicite, aucun écrasement.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import trimesh

from benchab.imports3d.importer import (
    UNITES,
    PublicationNeuve,
    charger,
    empreinte,
    mesurer,
    valider_chemins,
    verifier_export,
)

LIMITES = [
    "Distances point-surface sur échantillons et sommets sélectionnés ; pas une borne de Hausdorff.",
    "Un détail non échantillonné peut échapper au contrôle ; aucune garantie physique ou DfAM.",
    "Matériaux, couleurs, textures et intersections internes non contrôlés.",
    "Le recalage ICP est local et expérimental ; il ne prouve pas une correspondance sémantique.",
]


def positif(x, nom):
    if not math.isfinite(x) or x <= 0:
        raise ValueError(nom + " doit être positif et fini.")
    return float(x)


def valide(mesh):
    if not mesh.nondegenerate_faces().any():
        raise ValueError("Aucune surface non dégénérée à comparer.")


def topologie(mesh):
    t = mesh.copy()
    t.merge_vertices()
    m = mesurer(t)
    m["euler"] = int(t.euler_number)
    m["solide_oriente_exterieur"] = bool(t.is_volume)
    return m


def preparation(mesh, axe_haut, hauteur_mm=None, poser=False):
    """Retourne une copie et sa matrice, dans l'ordre rotation/échelle/translation."""
    if axe_haut not in ("y", "z"):
        raise ValueError("Axe haut explicite : y ou z.")
    valide(mesh)
    matrice = np.eye(4)
    if axe_haut == "y":
        # Rotation propre de +90° autour de X : (x,y,z) -> (x,-z,y).
        matrice[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    copie = mesh.copy()
    copie.apply_transform(matrice)
    if hauteur_mm is not None:
        facteur = positif(hauteur_mm, "Hauteur") / positif(copie.extents[2], "Hauteur source")
        echelle = np.diag([facteur, facteur, facteur, 1.0])
        matrice = echelle @ matrice
        copie.apply_transform(echelle)
    if poser:
        translation = np.eye(4)
        translation[:3, 3] = [-copie.bounds[:, 0].mean(), -copie.bounds[:, 1].mean(), -copie.bounds[0, 2]]
        matrice = translation @ matrice
        copie.apply_transform(translation)
    if not np.isfinite(copie.vertices).all() or not np.isfinite(matrice).all():
        raise ValueError("Transformation non finie : échelle trop grande.")
    return copie, matrice


def points(mesh, nombre, graine):
    if isinstance(nombre, bool) or not isinstance(nombre, int) or not 100 <= nombre <= 50000:
        raise ValueError("Échantillons : entier de 100 à 50000.")
    valide(mesh)
    surface, _ = trimesh.sample.sample_surface(mesh, nombre, seed=graine)
    # Les sommets aident à exposer les petites pointes que l'aire seule sous-échantillonne.
    indices = np.linspace(0, len(mesh.vertices) - 1, min(len(mesh.vertices), nombre), dtype=int)
    return np.vstack([surface, mesh.vertices[indices]]), len(indices)


def distances(mesh, requetes):
    # Le maillage cible reste complet ; les lots bornent les matrices temporaires de rtree.
    resultats = []
    for debut in range(0, len(requetes), 256):
        _, d, _ = trimesh.proximity.closest_point(mesh, requetes[debut : debut + 256])
        resultats.append(d)
    d = np.concatenate(resultats)
    if not np.isfinite(d).all():
        raise ValueError("Distances non finies.")
    return d


def statistiques(d, p, seuil):
    indices = np.argsort(d)[-5:][::-1]
    return {
        "moyenne_mm": float(d.mean()),
        "rms_mm": float(np.sqrt(np.mean(d * d))),
        "p95_mm": float(np.quantile(d, 0.95)),
        "maximum_observe_mm": float(d.max()),
        "fraction_dans_tolerance": float(np.mean(d <= seuil)),
        "nombre_points": len(d),
        "pires_points": [{"point_mm": p[i].tolist(), "distance_mm": float(d[i])} for i in indices],
    }


def comparer(reference, candidat, tolerance_mm=0.1, nombre=4000, graine=0):
    seuil = positif(tolerance_mm, "Tolérance")
    if isinstance(graine, bool) or not isinstance(graine, int) or graine < 0:
        raise ValueError("Graine : entier positif ou nul.")
    pr, vr = points(reference, nombre, graine)
    pc, vc = points(candidat, nombre, graine)
    dr = distances(candidat, pr)
    dc = distances(reference, pc)
    mr, mc = topologie(reference), topologie(candidat)
    changements = [
        k
        for k in (
            "corps_connectes",
            "ferme_topologiquement",
            "orientation_coherente",
            "solide_oriente_exterieur",
            "euler",
        )
        if mr[k] != mc[k]
    ]
    delta_dimensions = np.abs(reference.extents - candidat.extents)
    delta_bornes = np.abs(reference.bounds - candidat.bounds)
    raisons = []
    if max(dr.max(), dc.max()) > seuil:
        raisons.append("Écart de surface observé au-delà de la tolérance.")
    if delta_dimensions.max() > seuil or delta_bornes.max() > seuil:
        raisons.append("Dimensions ou bornes déplacées au-delà de la tolérance.")
    if changements:
        raisons.append("Topologie modifiée : " + ", ".join(changements))
    if mc["faces_degenerees"] > mr["faces_degenerees"]:
        raisons.append("Nombre de faces dégénérées accru.")
    precision, rappel = np.mean(dc <= seuil), np.mean(dr <= seuil)
    fscore = 2 * precision * rappel / (precision + rappel) if precision + rappel else 0.0
    return {
        "schema": 1,
        "commande": "comparer",
        "statut": "ECART" if raisons else "CONFORME_ECHANTILLONS",
        "raisons": raisons,
        "repere": "Coordonnées physiques en mm, sans normalisation ni recalage.",
        "tolerance_mm": seuil,
        "echantillons_surface_par_mesh": nombre,
        "graine": graine,
        "sommets_testes": {"reference": vr, "candidat": vc},
        "reference": mr,
        "candidat": mc,
        "reference_vers_candidat": statistiques(dr, pr, seuil),
        "candidat_vers_reference": statistiques(dc, pc, seuil),
        "f_score_tolerance": float(fscore),
        "delta_dimensions_mm": delta_dimensions.tolist(),
        "delta_bornes_mm": delta_bornes.tolist(),
        "delta_volume_mm3": mc["volume_mm3"] - mr["volume_mm3"]
        if mr["volume_mm3"] is not None and mc["volume_mm3"] is not None
        else None,
        "limites": LIMITES,
    }


def recaler(reference, candidat, nombre=2000, graine=0, iterations=30):
    """ICP local, rotation propre et translation uniquement : jamais une mise à l'échelle."""
    if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= 100:
        raise ValueError("Itérations : entier de 1 à 100.")
    valide(reference)
    pc, _ = points(candidat, nombre, graine)
    initial = np.eye(4)
    initial[:3, 3] = reference.bounds.mean(axis=0) - candidat.bounds.mean(axis=0)
    matrice, _, cout = trimesh.registration.icp(
        pc, reference, initial=initial, max_iterations=iterations, threshold=1e-10, scale=False, reflection=False
    )
    rotation = matrice[:3, :3]
    if (
        not np.isfinite(matrice).all()
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8)
        or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-8)
    ):
        raise ValueError("Le recalage n’a pas produit une transformation rigide propre.")
    copie = candidat.copy()
    copie.apply_transform(matrice)
    return copie, matrice, float(cout)


def chemins_neufs(source, sortie, rapport, reference=None):
    valider_chemins([source] + ([reference] if reference else []), [rapport] + ([sortie] if sortie else []))
    if sortie and Path(sortie).suffix.lower() != ".stl":
        raise ValueError("Sortie géométrique : STL en mm requis.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("commande", choices=["preparer", "comparer", "recaler-experimental"])
    p.add_argument("source", type=Path)
    p.add_argument("--unites", choices=UNITES, required=True)
    p.add_argument("--rapport", type=Path, required=True)
    p.add_argument("--sortie", type=Path)
    p.add_argument("--axe-haut", choices=["y", "z"])
    p.add_argument("--hauteur-mm", type=float)
    p.add_argument("--poser", action="store_true")
    p.add_argument("--reference", type=Path)
    p.add_argument("--unites-reference", choices=UNITES)
    p.add_argument("--tolerance-mm", type=float, default=0.1)
    p.add_argument("--echantillons", type=int, default=4000)
    p.add_argument("--graine", type=int, default=0)
    a = p.parse_args(argv)
    try:
        chemins_neufs(a.source, a.sortie, a.rapport, a.reference)
        if a.commande == "preparer":
            if not a.sortie or not a.axe_haut or a.reference or a.unites_reference:
                raise ValueError("preparer exige --sortie et --axe-haut ; pas de référence.")
        else:
            if not a.reference or not a.unites_reference or a.axe_haut or a.hauteur_mm is not None or a.poser:
                raise ValueError(
                    "Comparaison/recalage : référence et unités requises ; transformations de préparation interdites."
                )
            if (a.commande == "comparer" and a.sortie) or (a.commande == "recaler-experimental" and not a.sortie):
                raise ValueError("Une sortie STL est requise seulement pour le recalage.")
        h = empreinte(a.source)
        hr = empreinte(a.reference) if a.reference else None
        mesh = charger(a.source, a.unites)
        ref = charger(a.reference, a.unites_reference) if a.reference else None
        resultat = None
        if a.commande == "preparer":
            resultat, matrice = preparation(mesh, a.axe_haut, a.hauteur_mm, a.poser)
            r = {
                "schema": 1,
                "commande": "preparer",
                "statut": "PREPARE",
                "axe_haut_entree": a.axe_haut,
                "hauteur_cible_mm": a.hauteur_mm,
                "poser": a.poser,
                "matrice_mm": matrice.tolist(),
                "avant": topologie(mesh),
                "limites": LIMITES,
            }
        elif a.commande == "comparer":
            r = comparer(ref, mesh, a.tolerance_mm, a.echantillons, a.graine)
        else:
            resultat, matrice, cout = recaler(ref, mesh, a.echantillons, a.graine)
            r = {
                "schema": 1,
                "commande": a.commande,
                "statut": "EXPERIMENTAL",
                "matrice_mm": matrice.tolist(),
                "cout_icp_mm2": cout,
                "avant": comparer(ref, mesh, a.tolerance_mm, a.echantillons, a.graine),
                "apres": comparer(ref, resultat, a.tolerance_mm, a.echantillons, a.graine),
                "limites": LIMITES,
            }
        r.update(source=str(a.source.resolve()), source_sha256=h, unites_entree=a.unites)
        if ref is not None:
            r.update(
                reference_fichier=str(a.reference.resolve()), reference_sha256=hr, unites_reference=a.unites_reference
            )
        if empreinte(a.source) != h or (hr and empreinte(a.reference) != hr):
            raise ValueError("Une entrée a changé pendant le calcul.")
        with PublicationNeuve(
            [a.source] + ([a.reference] if a.reference else []), ([a.sortie] if a.sortie else []) + [a.rapport]
        ) as publication:
            if resultat is not None:
                temp = publication.temporaire(a.sortie)
                temp.write_bytes(resultat.export(file_type="stl"))
                relu = charger(temp, "mm")
                verifier_export(resultat, relu)
                r["export_relu"] = topologie(relu)
                r.update(sortie=a.sortie.name, sortie_sha256=empreinte(temp))
            r["sources_preservees"] = empreinte(a.source) == h and (not hr or empreinte(a.reference) == hr)
            if not r["sources_preservees"]:
                raise ValueError("Une entrée a changé avant la fin du contrôle.")
            publication.temporaire(a.rapport).write_text(
                json.dumps(r, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
            )
            json.loads(publication.temporaire(a.rapport).read_text(encoding="utf-8"))
            if empreinte(a.source) != h or (hr and empreinte(a.reference) != hr):
                raise ValueError("Une entrée a changé avant publication.")
            publication.publier()
        print(json.dumps({"statut": r["statut"], "rapport": str(a.rapport)}, ensure_ascii=False))
        return 1 if r["statut"] == "ECART" else 0
    except (ValueError, OSError, TypeError, ImportError) as e:
        p.exit(2, f"ERREUR : {e}\n")


if __name__ == "__main__":
    raise SystemExit(main())
