"""Silhouette locale → extrusion STL 2,5D par pixels, avec trous conservés.

Exemple : python contours.py silhouette.png --mode alpha --seuil 127
  --largeur-mm 80 --epaisseur-mm 3 --sortie nouvelle-piece.stl --rapport preuve.json
Aucune reconstruction du volume caché, interpolation, lissage ou service distant.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image
from scipy import ndimage


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def positif(value, nom):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(nom + " doit être un nombre fini strictement positif.")


def extraire(source, mode, seuil, max_pixels=50000):
    """Pixels carrés ; composantes 4-connexes ; recadrage sur la silhouette."""
    if type(max_pixels) is not int or max_pixels <= 0:
        raise ValueError("max_pixels doit être un entier positif.")
    if mode not in ("clair", "sombre", "alpha") or type(seuil) is not int or not 0 <= seuil <= 255:
        raise ValueError("Mode clair/sombre/alpha et seuil entier 0..255 requis.")
    with Image.open(source) as image:
        if image.width * image.height > max_pixels:
            raise ValueError("Image au-delà de max_pixels ; réduire explicitement sa résolution.")
        if mode == "alpha":
            if "A" not in image.getbands():
                raise ValueError("Le mode alpha exige un canal alpha.")
            a = np.asarray(image.getchannel("A"))
        else:
            a = np.asarray(image.convert("L"))
        mask = a < seuil if mode == "sombre" else a > seuil
    if not mask.any():
        raise ValueError("Silhouette vide.")
    yy, xx = np.where(mask)
    box = [int(xx.min()), int(yy.min()), int(xx.max() + 1), int(yy.max() + 1)]
    mask = mask[box[1] : box[3], box[0] : box[2]]
    # Deux carrés qui ne se touchent que par un coin créeraient une arête
    # verticale commune non manifold ; leur contour est donc ambigu.
    if min(mask.shape) > 1:
        tl, tr, bl, br = mask[:-1, :-1], mask[:-1, 1:], mask[1:, :-1], mask[1:, 1:]
        if np.any((tl & br & ~tr & ~bl) | (tr & bl & ~tl & ~br)):
            raise ValueError("Contacts diagonaux ambigus : modifier explicitement le masque.")
    labels, n = ndimage.label(mask, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
    parts = []
    for ident in range(1, n + 1):
        y, x = np.where(labels == ident)
        parts.append(
            {
                "id": ident,
                "pixels": len(x),
                "bbox_pixels_recadres": [int(x.min()), int(y.min()), int(x.max() + 1), int(y.max() + 1)],
            }
        )
    return mask, {
        "taille_image_pixels": [int(a.shape[1]), int(a.shape[0])],
        "bbox_source_pixels": box,
        "taille_recadree_pixels": [mask.shape[1], mask.shape[0]],
        "composants_4_connexes": parts,
        "pixels_pleins": int(mask.sum()),
    }


def frontieres(mask):
    """Segments orientés CCW autour des pixels pleins, dans le repère XY Z-up."""
    h, w = mask.shape
    edges = []
    for r, c in zip(*np.where(mask)):
        y = h - r - 1
        if r == h - 1 or not mask[r + 1, c]:
            edges.append(((int(c), int(y)), (int(c + 1), int(y))))
        if c == w - 1 or not mask[r, c + 1]:
            edges.append(((int(c + 1), int(y)), (int(c + 1), int(y + 1))))
        if r == 0 or not mask[r - 1, c]:
            edges.append(((int(c + 1), int(y + 1)), (int(c), int(y + 1))))
        if c == 0 or not mask[r, c - 1]:
            edges.append(((int(c), int(y + 1)), (int(c), int(y))))
    return edges


def extruder(mask, largeur_mm, epaisseur_mm, max_faces=500000):
    positif(largeur_mm, "largeur_mm")
    positif(epaisseur_mm, "epaisseur_mm")
    if type(max_faces) is not int or max_faces <= 0:
        raise ValueError("max_faces doit être un entier positif.")
    edges = frontieres(mask)
    nfaces = 4 * int(mask.sum()) + 2 * len(edges)
    if nfaces > max_faces:
        raise ValueError("Extrusion au-delà de max_faces ; réduire explicitement la résolution.")
    pitch = largeur_mm / mask.shape[1]
    vertices, faces, ids = [], [], {}

    def v(x, y, z):
        key = (int(x), int(y), int(z))
        if key not in ids:
            ids[key] = len(vertices)
            vertices.append([x * pitch, y * pitch, z * epaisseur_mm])
        return ids[key]

    for r, c in zip(*np.where(mask)):
        y = mask.shape[0] - r - 1
        b0, b1, b2, b3 = [v(x, yy, 0) for x, yy in [(c, y), (c + 1, y), (c + 1, y + 1), (c, y + 1)]]
        t0, t1, t2, t3 = [v(x, yy, 1) for x, yy in [(c, y), (c + 1, y), (c + 1, y + 1), (c, y + 1)]]
        faces.extend([[b0, b2, b1], [b0, b3, b2], [t0, t1, t2], [t0, t2, t3]])
    for (x0, y0), (x1, y1) in edges:
        b0, b1, t1, t0 = v(x0, y0, 0), v(x1, y1, 0), v(x1, y1, 1), v(x0, y0, 1)
        faces.extend([[b0, b1, t1], [b0, t1, t0]])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    return mesh, edges, pitch


def mesures(mesh):
    return {
        "dimensions_mm": mesh.extents.tolist(),
        "bornes_mm": mesh.bounds.tolist(),
        "volume_mm3": float(mesh.volume),
        "sommets": len(mesh.vertices),
        "faces": len(mesh.faces),
        "ferme_topologiquement": bool(mesh.is_watertight),
        "orientation_coherente": bool(mesh.is_winding_consistent),
        "corps_connectes": len(mesh.split(only_watertight=False)),
    }


def creer(source, sortie, rapport, mode, seuil, largeur_mm, epaisseur_mm, max_pixels=50000, max_faces=500000):
    source, sortie, rapport = Path(source).resolve(), Path(sortie).resolve(), Path(rapport).resolve()
    if len({source, sortie, rapport}) != 3 or sortie.exists() or rapport.exists():
        raise ValueError("Source, STL et rapport distincts ; aucun écrasement autorisé.")
    if sortie.suffix.lower() != ".stl" or rapport.suffix.lower() != ".json":
        raise ValueError("Sortie .stl et rapport .json requis.")
    empreinte = sha256(source)
    mask, analyse = extraire(source, mode, seuil, max_pixels)
    mesh, edges, pitch = extruder(mask, largeur_mm, epaisseur_mm, max_faces)
    avant = mesures(mesh)
    if not avant["ferme_topologiquement"] or not avant["orientation_coherente"]:
        raise ValueError("Extrusion non manifold ou orientation incohérente : export refusé.")
    # STL binaire convertit en float32 : contrôler les pertes avant publication.
    from io import BytesIO

    payload = mesh.export(file_type="stl")
    relu = trimesh.load(BytesIO(payload), file_type="stl", process=True)
    apres = mesures(relu)
    expected_volume = int(mask.sum()) * pitch * pitch * epaisseur_mm
    if (
        not apres["ferme_topologiquement"]
        or not apres["orientation_coherente"]
        or apres["corps_connectes"] != len(analyse["composants_4_connexes"])
        or not math.isclose(apres["volume_mm3"], expected_volume, rel_tol=1e-5)
        or not np.allclose(apres["dimensions_mm"], mesh.extents, rtol=1e-5, atol=0)
    ):
        raise ValueError("Relecture STL non conforme (topologie, dimensions ou volume).")
    if sha256(source) != empreinte:
        raise ValueError("Image source modifiée pendant la génération.")
    preuve = {
        "schema": 1,
        "source": str(source),
        "source_sha256": empreinte,
        "source_preservee": True,
        "mode": mode,
        "seuil": seuil,
        "largeur_mm": largeur_mm,
        "epaisseur_mm": epaisseur_mm,
        "pas_pixel_mm": pitch,
        "max_pixels": max_pixels,
        "max_faces": max_faces,
        "analyse": analyse,
        "frontieres_grille_xy": edges,
        "avant_export": avant,
        "apres_export": apres,
        "volume_attendu_mm3": expected_volume,
        "sortie": str(sortie),
        "sortie_sha256": hashlib.sha256(payload).hexdigest(),
        "statut": "PASS",
        "limites": [
            "Extrusion 2,5D constante du masque ; aucune reconstruction du volume caché.",
            "Contour en escalier à la résolution pixel, sans lissage implicite.",
            "Contrôle numérique uniquement ; aucune validation mécanique ou physique.",
            "STL sans couleur ni unité embarquée : coordonnées exprimées en mm.",
        ],
    }
    sortie.parent.mkdir(parents=True, exist_ok=True)
    rapport.parent.mkdir(parents=True, exist_ok=True)
    # Réserver les deux nouveaux chemins avant d’écrire ; ne jamais tronquer un existant.
    created = []
    try:
        with sortie.open("xb") as out:
            created.append(sortie)
            with rapport.open("x", encoding="utf-8") as doc:
                created.append(rapport)
                out.write(payload)
                json.dump(preuve, doc, ensure_ascii=False, indent=2, allow_nan=False)
    except Exception:
        # Uniquement nos nouveaux fichiers incomplets, jamais une sortie préexistante.
        for p in created:
            p.unlink()
        raise
    return preuve


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--mode", choices=["clair", "sombre", "alpha"], required=True)
    parser.add_argument("--seuil", type=int, required=True)
    parser.add_argument("--largeur-mm", type=float, required=True)
    parser.add_argument("--epaisseur-mm", type=float, required=True)
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--rapport", type=Path, required=True)
    parser.add_argument("--max-pixels", type=int, default=50000)
    parser.add_argument("--max-faces", type=int, default=500000)
    a = parser.parse_args(argv)
    try:
        result = creer(
            a.source, a.sortie, a.rapport, a.mode, a.seuil, a.largeur_mm, a.epaisseur_mm, a.max_pixels, a.max_faces
        )
        print(json.dumps({"statut": result["statut"], "rapport": str(a.rapport)}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError, OverflowError) as error:
        print(json.dumps({"statut": "FAIL", "erreur": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
