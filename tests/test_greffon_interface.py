"""Témoins numériques de greffe d'une interface femelle, en mm."""

import hashlib
import json

import manifold3d as mf
import pytest
import trimesh

from benchab.cli import main
from benchab.conception.decoupe import _vers_trimesh


def _anneau(interieur, exterieur, hauteur):
    return mf.Manifold.cylinder(hauteur, exterieur, circular_segments=96) - mf.Manifold.cylinder(
        hauteur, interieur, circular_segments=96
    )


@pytest.mark.parametrize(("support", "empreinte", "statut"), [
    (_anneau(7, 12, 4), True, "PASS"),
    (_anneau(9, 12, 4), True, "FAIL"),
    (_anneau(4, 12, 4), True, "FAIL"),
    (_anneau(7, 12, 4), False, "NON VÉRIFIÉ"),
])
def test_greffe_cli(tmp_path, capsys, support, empreinte, statut):
    """Le support doit recouvrir le col sans envahir le passage, avec une source identifiée."""
    source, base = (tmp_path / nom for nom in ("source.stl", "base.stl"))
    _vers_trimesh(_anneau(5, 10, 4)).export(source)
    _vers_trimesh(support).export(base)
    sha = hashlib.sha256(source.read_bytes()).hexdigest() if empreinte else "0" * 64
    cible = tmp_path / "greffe"
    assert main(["--prive", str(tmp_path), "init"]) == 0
    capsys.readouterr()
    code = main([
        "--prive", str(tmp_path), "conception", "greffer-interface", "--", str(source), str(base), "--sha256", sha,
        "--zone-mm", "0.5", "3.5", "8", "--sortie", str(cible),
    ])
    sortie = capsys.readouterr()
    assert sortie.out, sortie.err
    rapport = json.loads(sortie.out)
    assert rapport["statut"] == statut
    assert rapport["validation_physique"] is False
    assert code == (0 if statut == "PASS" else 2)
    if statut == "PASS":
        mesure = json.loads((cible / "rapport.json").read_text())
        assert mesure["couverture_commune"] == 1
        assert mesure["recouvrement_mm3"] > 0
        assert trimesh.load_mesh(cible / "greffe.stl").is_volume
    else:
        assert not cible.exists()


def test_greffe_filet_nominal(tmp_path):
    """Un col creusé par l'outil nominal conserve un profil hélicoïdal mesurable."""
    from benchab.conception.decoupe.geometrie import solide
    from benchab.conception.filetage_nominal import generer
    from benchab.conception.greffer_interface import greffer

    outil, _ = generer(
        diametre_mm=8, pas_mm=1.5, longueur_mm=6, profondeur_radiale_mm=0.65,
        source_cotes="témoin numérique", role="outil-interieur", jeu_radial_mm=0.2,
    )
    col = mf.Manifold.cylinder(5, 8, circular_segments=96).translate((0, 0, 0.5)) - solide(outil)
    source, base = (tmp_path / nom for nom in ("filet.stl", "support.stl"))
    _vers_trimesh(col).export(source)
    _vers_trimesh(_anneau(5.25, 9, 5)).export(base)
    stl, rapport = greffer(
        source, base, sha256=hashlib.sha256(source.read_bytes()).hexdigest(), zone_mm=(0.75, 4.5, 6),
    )
    assert rapport["statut"] == "PASS", rapport
    assert rapport["couverture_commune"] == 1
    assert rapport["ecart_radial_max_mm"] < 0.001
    assert rapport["stl_sha256"] == hashlib.sha256(stl).hexdigest()
