"""Conserve les indices d'une pièce fine et refuse un échange NPZ invalide."""

from io import BytesIO
from zipfile import ZipFile

import numpy as np
import pytest
import trimesh

from benchab.imports3d.maillage_indexe import lire, sauvegarder


def test_relecture_brute_conserve_memes_indices_et_petites_distances(tmp_path):
    """Deux surfaces proches restent deux composantes, même après aller-retour."""
    a = trimesh.creation.box(extents=[10.0, 2.0, 0.05])
    b = a.copy().apply_translation([0.0, 0.0, 0.0500001])
    source = trimesh.util.concatenate([a, b])
    fichier = tmp_path / "fin.npz"

    sauvegarder(fichier, source)
    relu = lire(fichier)

    assert relu.body_count == source.body_count == 2
    assert np.array_equal(relu.vertices, source.vertices)
    assert np.array_equal(relu.faces, source.faces)
    assert relu.volume == source.volume
    assert relu.is_winding_consistent and relu.is_volume


def test_refuse_des_faces_hors_limites(tmp_path):
    """Un triangle ne peut pas nommer un sommet absent du tableau."""
    fichier = tmp_path / "indice-invalide.npz"
    np.savez(fichier, vertices=np.zeros((3, 3), dtype="<f8"), faces=np.array([[0, 1, 3]], dtype="<i8"))

    with pytest.raises(ValueError, match="indices"):
        lire(fichier)


def test_refuse_longueur_declaree_incompatible_avant_chargement(tmp_path):
    """Une entête avec une taille invraisemblable ne déclenche pas d'allocation."""
    entete = BytesIO()
    np.lib.format.write_array_header_1_0(entete, {"descr": "<f8", "fortran_order": False, "shape": (10**9, 3)})
    fichier = tmp_path / "taille-invalide.npz"
    with ZipFile(fichier, "w") as archive:
        archive.writestr("vertices.npy", entete.getvalue())
        archive.writestr("faces.npy", b"")

    with pytest.raises(ValueError, match="Taille|taille"):
        lire(fichier)
