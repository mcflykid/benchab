"""Vérifie le contraste prédit et la comparaison avec les mêmes réglages."""

import json

import numpy as np
import trimesh

from benchab.visualisation import apercu_lampe


def test_epaisseur_et_comparaison(tmp_path):
    """À distance extérieure égale, une paroi deux fois plus épaisse transmet moins."""
    fichiers = []
    for i, rayon_interne in enumerate((9, 8)):
        chemin = tmp_path / f"coque{i}.stl"
        coque = trimesh.creation.annulus(r_min=rayon_interne, r_max=10, height=10, sections=48)
        if i == 0:
            coque.faces = np.vstack((coque.faces, coque.faces[:1]))
        coque.export(chemin)
        fichiers.append(chemin)
    destination = tmp_path / "apercu"
    rapport = apercu_lampe.produire(
        fichiers, destination, sources_mm=[[0, 0, 0]], attenuation_mm_inv=1, prive=tmp_path,
    )
    mince, epaisse = rapport["candidates"]
    assert mince["faces_dupliquees_ou_degenerees_ignorees"] == 1
    assert mince["epaisseur_mediane_mm"] < epaisse["epaisseur_mediane_mm"]
    assert mince["intensite_mediane_relative"] > epaisse["intensite_mediane_relative"]
    assert json.loads((destination / "rapport.json").read_text()) == rapport
    for candidate in rapport["candidates"]:
        assert (destination / candidate["deroule"]).is_file()
        assert (destination / candidate["vue"]).is_file()
