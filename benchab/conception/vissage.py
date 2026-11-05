"""Mesures géométriques paramétrées de profils de vissage sur des maillages.

Les résultats décrivent les intersections échantillonnées du modèle. Ils ne
qualifient ni le frottement ni l'ajustement physique d'un filetage imprimé.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import trimesh

from benchab.chemins import atelier_prive, sans_lien

MAX_PROFIL_CELLS = 4_000_000


def profil_radial(triangles, hauteurs, *, cote="exterieur", angles=256, phase=0.0):
    """Retourne les rayons extrêmes des triangles coupés par des plans XY.

    ``cote`` vaut ``"interieur"`` (première intersection positive) ou
    ``"exterieur"`` (dernière intersection positive). ``phase`` est en indice
    angulaire, ce qui permet de comparer des phases sans interpoler les profils.
    """
    t = np.asarray(triangles, dtype=float)
    z = np.asarray(hauteurs, dtype=float)
    if t.ndim != 3 or t.shape[1:] != (3, 3) or not np.isfinite(t).all():
        raise ValueError("triangles attendus sous forme finie (n, 3, 3)")
    if z.ndim != 1 or not np.isfinite(z).all():
        raise ValueError("hauteurs finies attendues")
    if cote not in {"interieur", "exterieur"}:
        raise ValueError("cote doit être interieur ou exterieur")
    if not isinstance(angles, int) or angles < 4 or not math.isfinite(phase):
        raise ValueError("échantillonnage angulaire invalide")
    if len(z) * angles > MAX_PROFIL_CELLS:
        raise ValueError("budget de profil dépassé (4 millions de rayons)")

    lo, hi = t[:, :, 2].min(axis=1), t[:, :, 2].max(axis=1)
    theta = (np.arange(angles, dtype=float) + phase) * (2 * np.pi / angles)
    dx, dy = np.cos(theta)[:, None], np.sin(theta)[:, None]
    result = np.full((len(z), angles), np.inf if cote == "interieur" else 0.0)
    for row, hauteur in enumerate(z):
        ts = t[(lo < hauteur) & (hi >= hauteur)]
        if not len(ts):
            continue
        pts = np.zeros((len(ts), 2, 2), dtype=float)
        n = np.zeros(len(ts), dtype=np.int8)
        for j, k in ((0, 1), (1, 2), (2, 0)):
            a, b = ts[:, j], ts[:, k]
            crossing = (a[:, 2] < hauteur) != (b[:, 2] < hauteur)
            ids = np.flatnonzero(crossing)
            aa, bb = a[crossing], b[crossing]
            if len(ids):
                fraction = (hauteur - aa[:, 2]) / (bb[:, 2] - aa[:, 2])
                pts[ids, n[ids]] = aa[:, :2] + fraction[:, None] * (bb[:, :2] - aa[:, :2])
                n[ids] += 1
        pts = pts[n == 2]
        if not len(pts):
            continue
        a, s = pts[:, 0], pts[:, 1] - pts[:, 0]
        den = dx * s[None, :, 1] - dy * s[None, :, 0]
        with np.errstate(divide="ignore", invalid="ignore"):
            radius = (a[:, 0] * s[:, 1] - a[:, 1] * s[:, 0])[None, :] / den
            fraction = (a[None, :, 0] * dy - a[None, :, 1] * dx) / den
        valid = (fraction >= -1e-8) & (fraction <= 1 + 1e-8) & (radius >= 0) & np.isfinite(radius)
        fill = np.inf if cote == "interieur" else 0.0
        radii = np.where(valid, radius, fill)
        result[row] = radii.min(axis=1) if cote == "interieur" else radii.max(axis=1)
    return result


def jeu_radial_minimal(profil_femelle, profil_male, *, index_axial, phase=0):
    """Mesure le jeu radial minimal entre profils déjà échantillonnés.

    ``index_axial`` associe chaque ligne du profil mâle à une ligne du profil
    femelle. Un indice hors plage ou un profil non fini/incomplet est refusé.
    """
    femelle = np.asarray(profil_femelle, dtype=float)
    male = np.asarray(profil_male, dtype=float)
    indices = np.asarray(index_axial)
    if femelle.ndim != 2 or male.ndim != 2 or femelle.shape[1] != male.shape[1]:
        raise ValueError("profils bidimensionnels de même échantillonnage requis")
    if not np.isfinite(male).all() or np.isnan(femelle).any():
        raise ValueError("profils incomplets ou non finis")
    if indices.shape != (len(male),) or not np.issubdtype(indices.dtype, np.integer):
        raise ValueError("un indice axial entier est requis par ligne mâle")
    if len(indices) and (indices.min() < 0 or indices.max() >= len(femelle)):
        raise ValueError("indice axial hors du profil femelle")
    if not isinstance(phase, int):
        raise ValueError("phase angulaire entière requise")
    aligned = np.roll(femelle[indices], phase, axis=1)
    difference = aligned - male
    if not np.isfinite(difference).any():
        raise ValueError("aucune intersection radiale exploitable")
    return float(np.nanmin(np.where(np.isfinite(difference), difference, np.nan)))


def profil_fichier(chemin, *, cote="exterieur", pas_z_mm=0.1, angles=256, phase=0.0):
    """Profile un maillage local et renvoie un résumé géométrique sans données privées incorporées."""
    path = Path(chemin)
    if not math.isfinite(pas_z_mm) or pas_z_mm <= 0:
        raise ValueError("pas axial fini strictement positif requis")
    if not path.is_file():
        raise FileNotFoundError(path)
    mesh = trimesh.load_mesh(path, process=False)
    if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
        raise ValueError("maillage triangulé requis")
    z_min, z_max = map(float, mesh.bounds[:, 2])
    if not math.isfinite(z_min) or not math.isfinite(z_max) or z_min >= z_max:
        raise ValueError("bornes Z du maillage invalides")
    nombre = int(math.floor((z_max - z_min) / pas_z_mm)) + 1
    if nombre * angles > MAX_PROFIL_CELLS:
        raise ValueError("budget de profil dépassé (4 millions de rayons)")
    hauteurs = z_min + np.arange(nombre, dtype=float) * pas_z_mm
    profils = profil_radial(mesh.triangles, hauteurs, cote=cote, angles=angles, phase=phase)
    present = np.isfinite(profils) if cote == "interieur" else profils > 0
    report = {
        "statut": "PASS" if present.any() else "NON VÉRIFIÉ",
        "fichier": path.name,
        "sha256": _sha256(path),
        "cote": cote,
        "parametres": {"pas_z_mm": pas_z_mm, "angles": angles, "phase_indice": phase},
        "echantillons": int(profils.size),
        "hauteurs_echantillonnees": int(len(hauteurs)),
        "rayons_sans_intersection": int((~present).sum()),
        "rayon_min_mm": float(profils[present].min()) if present.any() else None,
        "rayon_max_mm": float(profils[present].max()) if present.any() else None,
        "limite": "Profil radial numérique échantillonné; ne valide ni filetage FDM, ni frottement, ni tenue physique.",
    }
    return report


def _sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main(argv=None, *, prive=None):
    """Commande CLI locale; rapport JSON privé, longueurs en mm et aucune validation physique."""
    parser = argparse.ArgumentParser(description="Échantillonne le profil radial d'un maillage de vissage.")
    parser.add_argument("maillage", type=Path)
    parser.add_argument("--cote", choices=("interieur", "exterieur"), default="exterieur")
    parser.add_argument("--pas-z-mm", type=float, default=0.1)
    parser.add_argument("--angles", type=int, default=256)
    parser.add_argument("--phase-indice", type=float, default=0.0)
    parser.add_argument("--json", type=Path, help="Écrit le résumé dans le dossier privé.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    result = profil_fichier(
        args.maillage, cote=args.cote, pas_z_mm=args.pas_z_mm, angles=args.angles, phase=args.phase_indice
    )
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.json:
        private = atelier_prive(prive)
        destination = sans_lien(args.json.expanduser().absolute())
        if not destination.is_relative_to(private):
            raise ValueError("le rapport personnel doit rester sous BENCHAB_PRIVE")
        destination = sans_lien(destination, racine=private)
        args.json.parent.mkdir(parents=True, exist_ok=True)
        with args.json.open("x") as flux:
            flux.write(text)
    print(text, end="")
    return 2 if result["statut"] == "NON VÉRIFIÉ" else 0
