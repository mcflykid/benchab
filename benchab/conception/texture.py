"""Textures géométriques de surface avec protections de zones et bornes d'allocation.

Les contrôles restent numériques : aucune résistance, impression ou apparence
physique n'est déduite de la variante. L'original n'est jamais modifié.
"""

import argparse
import json
import math
import tempfile
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

from benchab.fabrication import remplissage_localise as RL
from benchab.qualite.ecritures import destination_neuve

LIGNE = 0.42  # largeur de ligne de la buse 0,4 (mm)
MOTIFS = ("moletage", "cannelures", "grain", "damier")
FACES_MAX = 4_000_000  # garde-fou mémoire (Mac à 16 Go)


def hauteur(motif, u, v):
    """Hauteur dans [0, 1] aux coordonnées exprimées en périodes."""
    if motif == "moletage":  # losanges : deux rainures croisées à 45°
        a, b = (u + v) / math.sqrt(2), (u - v) / math.sqrt(2)
        return 1 - np.maximum(np.abs(np.sin(np.pi * a)), np.abs(np.sin(np.pi * b))) ** 4
    if motif == "cannelures":
        return 0.5 + 0.5 * np.cos(2 * np.pi * u)
    if motif == "damier":
        return ((np.floor(u) + np.floor(v)) % 2).astype(float)
    if motif == "grain":  # bruit déterministe : somme de sinus incommensurables
        h = (
            sum(
                np.sin(f * u * 2.3 + p) * np.sin(f * v * 1.7 + 2 * p)
                for f, p in ((1.0, 0.0), (2.13, 1.3), (3.71, 2.1), (5.39, 0.7))
            )
            / 4
        )
        return 0.5 + 0.5 * h
    with Image.open(motif) as image:
        img = np.asarray(image.convert("L"), dtype=float) / 255
    H, W = img.shape
    x = (u % 1) * (W - 1)
    y = (v % 1) * (H - 1)
    return img[y.astype(int), x.astype(int)]


def _projection(t, v, n):
    p = float(t.get("periode", 3.0))
    if t.get("projection", "triplanaire") == "cylindrique":
        ax = "xyz".index(t.get("axe", "z"))
        o = [i for i in range(3) if i != ax]
        c = v[:, o].mean(0) if "centre" not in t else np.asarray(t["centre"], float)
        ang = np.arctan2(v[:, o[1]] - c[1], v[:, o[0]] - c[0])
        r = np.median(np.hypot(v[:, o[0]] - c[0], v[:, o[1]] - c[1]))
        return hauteur(t["motif"], ang * r / p, v[:, ax] / p)
    w = np.abs(n) ** 4
    w /= w.sum(1, keepdims=True) + 1e-12  # triplanaire : chaque axe pèse selon l'orientation de la face
    return sum(w[:, i] * hauteur(t["motif"], v[:, (i + 1) % 3] / p, v[:, (i + 2) % 3] / p) for i in range(3))


def poids_zone(sommets, texture):
    """Masque boîte optionnel, transition cubique continue vers zéro sur ses six bords.

    Coordonnées en mm dans le repère de la pièce. Ne lève aucune protection existante.
    """
    zone = texture.get("zone")
    transition = float(texture.get("transition_mm", 0))
    if not np.isfinite(transition) or transition < 0:
        raise ValueError("transition_mm doit être finie et positive ou nulle")
    if zone is None:
        if transition:
            raise ValueError("transition_mm exige une zone explicite")
        return np.ones(len(sommets))
    if not isinstance(zone, dict) or set(zone) != {"min", "max"}:
        raise ValueError("zone doit contenir min et max uniquement")
    bas, haut = np.asarray(zone["min"], float), np.asarray(zone["max"], float)
    if bas.shape != (3,) or haut.shape != (3,) or not np.isfinite([bas, haut]).all() or np.any(haut <= bas):
        raise ValueError("zone : trois bornes finies, min < max")
    if transition > np.min(haut - bas) / 2:
        raise ValueError("transition_mm dépasse la demi-largeur minimale de la zone")
    distance = np.minimum(sommets - bas, haut - sommets).min(axis=1)
    if transition == 0:
        return (distance >= 0).astype(float)
    t = np.clip(distance / transition, 0, 1)
    return t * t * (3 - 2 * t)


def _fini(valeur, nom, positif=False):
    if isinstance(valeur, bool):
        raise ValueError(f"{nom} : nombre fini requis")
    try:
        nombre = float(valeur)
    except (TypeError, ValueError, OverflowError) as ex:
        raise ValueError(f"{nom} : nombre fini requis") from ex
    if not math.isfinite(nombre) or (positif and nombre <= 0):
        raise ValueError(f"{nom} : nombre fini{' positif' if positif else ''} requis")
    return nombre


def _valider(texture, piece):
    """Refuser paramètres et budget dangereux avant toute subdivision.

    Une subdivision découpe chaque triangle en quatre et divise ses arêtes
    par deux. La somme des 4**profondeur est donc une borne supérieure du
    nombre de faces allouées ; elle est prudente pour les triangles allongés.
    """
    t = dict(texture)
    for key, default in (("amplitude", 0.6), ("periode", 3.0), ("arete_max", 0.5)):
        t[key] = _fini(t.get(key, default), key, positif=True)
    projection = t.get("projection", "triplanaire")
    if projection not in {"triplanaire", "cylindrique"}:
        raise ValueError("projection inconnue")
    if t.get("axe", "z") not in {"x", "y", "z"}:
        raise ValueError("axe : x, y ou z requis")
    if "centre" in t:
        centre = np.asarray(t["centre"], float)
        if centre.shape != (2,) or not np.isfinite(centre).all():
            raise ValueError("centre : deux coordonnées finies requises")
    for key, default in (("angle_bas", 45), ("angle_haut", None)):
        value = t.get(key, default)
        if value is None and key == "angle_haut":
            continue
        t[key] = _fini(value, key)
        if not 0 <= t[key] <= 90:
            raise ValueError(f"{key} : angle entre 0 et 90 degrés requis")
    poids_zone(np.empty((0, 3)), t)
    vertices, faces = piece.vertices, piece.faces
    if not len(vertices) or not len(faces) or len(faces) > FACES_MAX or not np.isfinite(vertices).all():
        raise ValueError("maillage vide, non fini ou budget de faces déjà dépassé")
    tri = vertices[faces]
    edges = np.maximum.reduce([np.linalg.norm(tri[:, i] - tri[:, (i + 1) % 3], axis=1) for i in range(3)])
    if not np.isfinite(edges).all():
        raise ValueError("arêtes non finies")
    # Différence de logarithmes : pas de division qui déborde pour une arête
    # demandée minuscule. Au-delà de 12 niveaux le contrat max_iter échouerait.
    log_depth = np.log2(np.maximum(edges, np.finfo(float).tiny)) - math.log2(t["arete_max"])
    levels = np.where(edges > t["arete_max"], np.ceil(log_depth + 1e-12), 0).astype(int)
    if np.any(levels > 12):
        raise ValueError("subdivision : plus de 12 niveaux requis ; augmenter arete_max")
    upper = sum(4 ** int(level) * int(count) for level, count in zip(*np.unique(levels, return_counts=True)))
    if upper > FACES_MAX:
        raise ValueError(f"budget avant subdivision : au plus {upper} faces (> {FACES_MAX}) ; augmenter arete_max")
    return t, upper


def sommets_proteges(sommets, zone):
    """Volumes déclarés inclusifs, frontière comprise (tolérance 1e-7 mm).

    contains() d'un maillage exclut sa frontière et le cylindre triangulé
    sous-estime son rayon entre deux faces. Les deux formes déclarées sont
    testées analytiquement, après leur validation commune.
    """
    if not isinstance(zone, dict):
        raise ValueError("zone protégée : objet requis")
    RL.volume(zone)  # validation des dimensions et de la forme déclarées
    marge = _fini(zone.get("marge", 0), "marge")
    if marge < 0:
        raise ValueError("marge : positive ou nulle requise")
    tolerance = 1e-7
    if zone["forme"] == "boite":
        bas = np.asarray(zone["min"], float) - marge - tolerance
        haut = np.asarray(zone["max"], float) + marge + tolerance
        return np.all((sommets >= bas) & (sommets <= haut), axis=1)
    centre = np.asarray(zone["centre"], float)
    rayon = float(zone["rayon"]) + marge + tolerance
    demi_hauteur = float(zone["hauteur"]) / 2 + marge + tolerance
    return (np.hypot(sommets[:, 0] - centre[0], sommets[:, 1] - centre[1]) <= rayon) & (
        np.abs(sommets[:, 2] - centre[2]) <= demi_hauteur
    )


def appliquer(piece, texture, contraintes=None):
    """Construit la variante et son rapport; FAIL interdit de retenir la variante."""
    t, budget_faces = _valider(texture, piece)
    C = contraintes or {}
    ctl = []

    def ok(condition):
        return "PASS" if condition else "FAIL"

    if t.get("motif") not in MOTIFS and not Path(str(t.get("motif"))).is_file():
        raise ValueError(f"motif {t.get('motif')!r} inconnu (motifs : {', '.join(MOTIFS)} ou image)")
    amp, per, arete = float(t.get("amplitude", 0.6)), float(t.get("periode", 3.0)), float(t.get("arete_max", 0.5))
    # Les paramètres et la borne d'allocation ont déjà été contrôlés.
    v, f = trimesh.remesh.subdivide_to_size(piece.vertices, piece.faces, max_edge=arete, max_iter=12)
    if len(f) > FACES_MAX:
        raise ValueError(f"{len(f)} faces après subdivision (> {FACES_MAX}) : augmenter arete_max ou réduire la pièce")
    m = trimesh.Trimesh(v, f, process=True)
    n = m.vertex_normals
    v = m.vertices.copy()
    masque = np.ones(len(v), bool)
    masque &= n[:, 2] > -math.cos(math.radians(float(t.get("angle_bas", 45))))  # dessous et plateau : jamais
    masque &= v[:, 2] > m.bounds[0][2] + 1e-6
    if t.get("angle_haut") is not None:
        masque &= n[:, 2] < math.cos(math.radians(float(t["angle_haut"])))
    for z in C.get("protege", []):
        masque &= ~sommets_proteges(v, z)
    poids = poids_zone(v, t)
    masque &= poids > 0
    h = _projection(t, v, n)
    d = (h - 0.5) * amp * masque * poids  # symétrique : 0,5 neutre
    var = trimesh.Trimesh(v + n * d[:, None], m.faces, process=False)
    corps = len(var.split(only_watertight=False))
    ctl.append(
        {
            "controle": "solide valide",
            "statut": ok(var.is_watertight and var.is_volume and corps == 1),
            "detail": f"fermé {var.is_watertight}, volume {var.is_volume}, corps {corps}",
        }
    )
    bas = np.abs(var.vertices[~masque] - v[~masque]).max() if (~masque).any() else 0.0
    ctl.append(
        {
            "controle": "plateau, dessous et régions protégées inchangés",
            "statut": ok(bas < 1e-9),
            "detail": f"déplacement max hors texture {bas:.2e} mm",
        }
    )
    ctl.append(
        {
            "controle": "motif imprimable par la buse",
            "statut": ok(per / 2 >= 2 * LIGNE),
            "detail": f"demi-période {per / 2:.2f} mm (minimum 2 lignes = {2 * LIGNE:.2f} mm)",
        }
    )
    lim = min(1.0, 0.1 * float(min(piece.extents)))
    ctl.append(
        {
            "controle": "amplitude raisonnable",
            "statut": "PASS" if amp <= lim else "WARN",
            "detail": f"{amp} mm (repère : ≤ {lim:.2f} mm, 10 % de la plus petite dimension, plafond 1 mm)",
        }
    )
    ctl.append(
        {
            "controle": "petits reliefs à revoir",
            "statut": "WARN",
            "detail": (
                "sommets du relief = petits îlots : revue locale (couches_courtes.py, "
                "temps de couche) avant toute livraison"
            ),
        }
    )
    statut = "FAIL" if any(c["statut"] == "FAIL" for c in ctl) else "WARN"
    texture_rapport = dict(t)
    if texture_rapport.get("motif") not in MOTIFS:
        texture_rapport["motif"] = Path(str(texture_rapport["motif"])).name
    return var, {
        "statut": statut,
        "texture": texture_rapport,
        "faces": len(var.faces),
        "budget_faces_avant_subdivision": budget_faces,
        "part_texturee": round(float(masque.mean()), 3),
        "volume_reference_mm3": round(float(piece.volume), 1),
        "volume_texture_mm3": round(float(var.volume), 1),
        "controles": ctl,
        "limite": (
            "géométrie seulement : imprimabilité réelle, aspect, grip et lumière à confirmer "
            "par tranchage et retour physique"
        ),
    }


def main(argv=None):
    """Génère un témoin de texture (mm) dans l’atelier choisi ou le temporaire, sans écrasement."""
    parser = argparse.ArgumentParser(description=__doc__)
    commandes = parser.add_subparsers(dest="commande", required=True)
    essai = commandes.add_parser("essai")
    essai.add_argument("--sortie", type=Path, default=Path(tempfile.gettempdir()) / "texture-essai")
    args = parser.parse_args(argv)
    out = destination_neuve(args.sortie)
    out.mkdir(parents=True)
    cube = trimesh.creation.box(extents=(30, 30, 30))
    cube.apply_translation((15, 15, 15))
    var, r = appliquer(
        cube, {"motif": "moletage", "periode": 3.0, "amplitude": 0.6, "angle_haut": 30, "arete_max": 0.5}
    )
    var.export(out / "cube-molete.stl")
    (out / "cube-molete.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in r.items() if k != "texture"}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
