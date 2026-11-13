"""Élasticité linéaire 3D d’un coupon prismatique plein en N, mm et MPa."""

import argparse
import json
from itertools import permutations
from math import prod
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

_MAX_CELLULES_FINES = 4_000


def _mailler(cotes, divisions):
    axes = [np.linspace(0, cote, n + 1) for cote, n in zip(cotes, divisions, strict=True)]
    noeuds = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    grille = np.arange(len(noeuds)).reshape(tuple(n + 1 for n in divisions))
    coins = np.stack([
        grille[i:i + divisions[0], j:j + divisions[1], k:k + divisions[2]].ravel()
        for i, j, k in np.ndindex(2, 2, 2)
    ], axis=1)
    # Les six tétraèdres partagent la grande diagonale et triangulent
    # les faces communes de la même manière d’une cellule à l’autre.
    ordre = [[0, a, a + b, 7] for a, b, _ in permutations((4, 2, 1))]
    return noeuds, coins[:, ordre].reshape(-1, 4)


def _materiau(module, poisson):
    mu = module / (2 * (1 + poisson))
    lam = module * poisson / ((1 + poisson) * (1 - 2 * poisson))
    matrice = np.diag([2 * mu] * 3 + [mu] * 3)
    matrice[:3, :3] += lam
    return matrice


def _resoudre(cotes, divisions, force, materiau):
    noeuds, tetra = _mailler(cotes, divisions)
    xyz = noeuds[tetra]
    aretes = xyz[:, 1:] - xyz[:, :1]
    volumes = np.abs(np.linalg.det(aretes)) / 6
    gradient = np.empty((len(tetra), 3, 4))
    gradient[:, :, 1:] = np.linalg.inv(aretes)
    gradient[:, :, 0] = -gradient[:, :, 1:].sum(axis=2)
    # Le vecteur de déformations utilise les cisaillements d’ingénierie.
    b = np.zeros((len(tetra), 6, 12))
    x, y, z = gradient.transpose(1, 0, 2)
    b[:, 0, 0::3], b[:, 1, 1::3], b[:, 2, 2::3] = x, y, z
    b[:, 3, 0::3], b[:, 3, 1::3] = y, x
    b[:, 4, 1::3], b[:, 4, 2::3] = z, y
    b[:, 5, 2::3], b[:, 5, 0::3] = x, z
    k_elements = (b.transpose(0, 2, 1) @ materiau @ b) * volumes[:, None, None]
    ddl = (3 * tetra[:, :, None] + np.arange(3)).reshape(-1, 12)
    taille = 3 * len(noeuds)
    lignes = np.broadcast_to(ddl[:, :, None], k_elements.shape).ravel()
    colonnes = np.broadcast_to(ddl[:, None, :], k_elements.shape).ravel()
    matrice = coo_matrix((k_elements.ravel(), (lignes, colonnes)), shape=(taille, taille)).tocsr()

    charges = np.zeros((len(noeuds), 3))
    applique = noeuds[:, 0] == cotes[0]
    # Les faces de bord triangulaires ont la même aire sur cette grille.
    # Une traction uniforme donne à chaque sommet un tiers de la force du triangle.
    faces = tetra[:, np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])].reshape(-1, 3)
    face_chargee = faces[applique[faces].all(axis=1)]
    poids = np.bincount(face_chargee.ravel(), minlength=len(noeuds)) / (3 * len(face_chargee))
    charges[:] = poids[:, None] * force
    appui = noeuds[:, 0] == 0
    libres = np.repeat(~appui, 3)
    deplacements = np.zeros(taille)
    deplacements[libres] = spsolve(matrice[libres][:, libres], charges.ravel()[libres])
    actions = (matrice @ deplacements).reshape(-1, 3) - charges
    ddl_xyz = deplacements.reshape(-1, 3)
    contraintes = (materiau @ b @ deplacements[ddl, None]).squeeze(axis=2)
    sx, sy, sz, xy, yz, zx = contraintes.T
    von_mises = np.sqrt(((sx - sy)**2 + (sy - sz)**2 + (sz - sx)**2) / 2 + 3 * (xy**2 + yz**2 + zx**2))
    zones = np.minimum((4 * xyz[:, :, 0].mean(axis=1) / cotes[0]).astype(int), 3)
    moyennes = np.bincount(zones, weights=von_mises * volumes, minlength=4) / np.bincount(
        zones, weights=volumes, minlength=4,
    )
    if not np.isfinite(deplacements).all() or not np.isfinite(moyennes).all() or not np.isfinite(actions).all():
        raise ValueError("Résolution volumique non finie.")
    reaction = actions[appui].sum(axis=0)
    residu = float(np.max(np.abs(actions.ravel()[libres])))
    if max(np.linalg.norm(reaction + force), residu) > 1e-6 * np.linalg.norm(force):
        raise ValueError("Équilibre des forces non établi sur le maillage.")
    return {
        "divisions_xyz": list(divisions), "tetraedres": len(tetra),
        "deplacement_face_mm": (poids @ ddl_xyz).tolist(),
        "reaction_appui_n": reaction.tolist(),
        "residu_max_dof_libre_n": residu,
        "von_mises_moyenne_par_zone_mpa": moyennes.tolist(),
    }


def calculer(dimensions_mm, force_n, young_mpa, poisson, divisions, *, source_matiere):
    """Compare deux maillages d’un coupon XYZ plein avec appui X=0 et traction sur X=L.

    Force résultante en N, module en MPa, cotes en mm et Poisson sans unité.
    Le rapport donne les déplacements en mm, réactions en N et contraintes
    moyennes en MPa. La tenue reste NON VÉRIFIÉE. Aucun fichier ni réglage modifié.
    """
    for valeurs, nom, bas, haut in (
        (dimensions_mm, "dimensions", 0.5, 1000), (force_n, "force", -1e6, 1e6),
        ((young_mpa,), "module de Young", 0.1, 1e6), ((poisson,), "Poisson", 0, 0.45),
    ):
        if any(type(v) not in (int, float) or not bas <= v <= haut for v in valeurs):
            raise ValueError(f"Valeur hors domaine pour {nom}.")
    if len(dimensions_mm) != 3 or len(force_n) != 3 or not any(force_n):
        raise ValueError("Déclarer trois cotes et une force XYZ non nulle.")
    if len(divisions) != 3 or any(type(n) is not int or not 2 <= n <= 40 for n in divisions):
        raise ValueError("Déclarer 2 à 40 divisions par axe.")
    if 8 * prod(divisions) > _MAX_CELLULES_FINES:
        raise ValueError(f"Budget de {_MAX_CELLULES_FINES} cellules sur le maillage fin dépassé.")
    if not isinstance(source_matiere, str) or not source_matiere.strip():
        raise ValueError("Source des propriétés de l’étude requise.")
    cotes = tuple(dimensions_mm)
    force = np.asarray(force_n)
    materiau = _materiau(young_mpa, poisson)
    maillages = [_resoudre(cotes, tuple(n * facteur for n in divisions), force, materiau) for facteur in (1, 2)]
    longueur, largeur, hauteur = cotes
    fx, fy, fz = force
    reference = [
        fx * longueur / (young_mpa * largeur * hauteur),
        fy * longueur**3 / (young_mpa * hauteur * largeur**3 / 4),
        fz * longueur**3 / (young_mpa * largeur * hauteur**3 / 4),
    ]
    grossier, fin = (np.array(m["deplacement_face_mm"]) for m in maillages)
    contrainte_a, contrainte_b = (np.array(m["von_mises_moyenne_par_zone_mpa"]) for m in maillages)
    return {
        "schema": 1, "validation_physique": "NON VÉRIFIÉ", "dimensions_xyz_mm": list(cotes),
        "plateau_declare": "Z=0", "face_bloquee": "X=0", "face_chargee": "X=L",
        "force_repartie_xyz_n": list(force_n),
        "materiau_isotrope": {"young_mpa": young_mpa, "poisson": poisson, "source": source_matiere},
        "maillages": maillages,
        "reference_analytique": {"modele": "Traction uniforme et flexion Euler-Bernoulli de console élancée",
                                 "deplacement_face_mm": reference},
        "variation_relative_deplacement": float(np.linalg.norm(fin - grossier) / np.linalg.norm(fin)),
        "variation_contrainte_par_zone_mpa": np.abs(contrainte_b - contrainte_a).tolist(),
        "zones_axiales_mm": [longueur * i / 4 for i in range(5)],
        "limites": [
            "Coupon plein isotrope, petits déplacements, appui parfaitement bloqué et traction uniforme.",
            "La variation entre maillages est un indicateur numérique sans borne rigoureuse.",
            "Les contraintes sont moyennées par volume dans quatre zones selon le centre des tétraèdres.",
            "La référence de poutre néglige cisaillement et effets d’encastrement.",
            "Les propriétés, l’anisotropie et la tenue d’un remplissage imprimé restent à mesurer.",
        ],
    }


def main(argv=None, *, prive=None):
    """Publie un rapport privé neuf de contraintes 3D (N, mm, MPa) ou retourne un refus."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dimensions-mm", type=float, nargs=3, required=True)
    parser.add_argument("--force-n", type=float, nargs=3, required=True)
    parser.add_argument("--young-mpa", type=float, required=True)
    parser.add_argument("--poisson", type=float, required=True)
    parser.add_argument("--divisions", type=int, nargs=3, required=True)
    parser.add_argument("--source-matiere", required=True)
    parser.add_argument("--sortie", type=Path, required=True)
    args = vars(parser.parse_args(argv))
    try:
        sortie = destination_neuve(args.pop("sortie"), prive=prive, temporaire=False)
        rapport = calculer(**args)
        ecrire_json_neuf(sortie, rapport)
    except (OSError, ValueError, np.linalg.LinAlgError) as erreur:
        parser.exit(2, f"BENCHAB : calcul refusé : {erreur}\n")
    print(json.dumps({"rapport": str(sortie), "validation_physique": rapport["validation_physique"]},
                     ensure_ascii=False))
    return 0
