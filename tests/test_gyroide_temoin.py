"""Témoin #87 : géométrie numérique, sans envoi ni contrôle physique."""

from io import BytesIO

import pytest
import trimesh

from benchab.conception.gyroide_temoin import generer_coupon


def test_coupon_confine_preserve_la_zone_et_relit_un_stl_ferme():
    """Les octets reproduits contiennent un seul volume dans les cotes déclarées."""
    parametres = dict(
        dimensions_mm=(24, 16, 16), paroi_mm=2, cellule_mm=8, seuil=0.55, pas_mm=0.55,
        protections=(((10, 6, 4), (14, 10, 12)),),
    )
    stl, rapport = generer_coupon(**parametres)
    stl_rejoue, autre = generer_coupon(**parametres)
    relu = trimesh.load_mesh(BytesIO(stl), file_type="stl")
    assert stl == stl_rejoue and rapport == autre
    assert relu.is_volume and relu.is_watertight
    assert relu.bounds.tolist() == [[0, 0, 0], [24, 16, 16]]
    assert 3392 < relu.volume < 6144
    assert rapport["volume_mm3"] == pytest.approx(relu.volume, abs=1e-4)
    assert rapport["zone_protegee_perdue_mm3"] == pytest.approx(0)
    assert rapport["debord_mm3"] == pytest.approx(0)
    assert rapport["corps_positifs"] == 1
    assert rapport["statut"] == "PASS" and rapport["tenue_physique"] == "NON VÉRIFIÉ"


@pytest.mark.parametrize(("modification", "motif"), [
    ({"protections": (((-1, 0, 0), (3, 3, 3)),)}, "protection"),
    ({"protections": (((2, 2, 2), (22, 14, 14)),)}, "zone disponible"),
    ({"pas_mm": 2.0}, "résolution"),
    ({"dimensions_mm": (64, 64, 64), "pas_mm": 0.15}, "budget"),
    ({"seuil": float("nan")}, "seuil"),
    ({"dimensions_mm": (True, 16, 16)}, "dimensions"),
])
def test_coupon_refuse_un_domaine_non_borne(modification, motif):
    """La géométrie ne doit pas compenser un contrat invalide."""
    parametres = dict(dimensions_mm=(24, 16, 16), paroi_mm=2, cellule_mm=8, seuil=0.55, pas_mm=0.55)
    with pytest.raises(ValueError, match=motif):
        generer_coupon(**(parametres | modification))


def test_cli_publie_un_temoin_sans_ouvrir_la_base(tmp_path, capsys):
    """Le dossier explicite est le seul résultat durable ; une collision le préserve."""
    import hashlib
    import json

    from benchab.cli import main

    sortie = tmp_path / "nouveau"
    commande = [
        "--prive", str(tmp_path), "conception", "gyroide-temoin",
        "--dimensions-mm", "24", "16", "16", "--paroi-mm", "2",
        "--cellule-mm", "8", "--seuil", "0.55", "--pas-mm", "0.55",
        "--protection-mm", "10", "6", "4", "14", "10", "12", "--sortie", str(sortie),
    ]
    assert main(commande) == 0
    resume = json.loads(capsys.readouterr().out)
    assert resume["statut"] == "PASS" and resume["tenue_physique"] == "NON VÉRIFIÉ"
    stl = (sortie / "gyroide.stl").read_bytes()
    rapport = (sortie / "rapport.json").read_bytes()
    preuve = json.loads(rapport)
    assert preuve["stl_sha256"] == hashlib.sha256(stl).hexdigest()
    assert preuve["protections_mm"] == [[[10, 6, 4], [14, 10, 12]]]
    assert not (tmp_path / "benchab.sqlite").exists()
    assert main(commande) == 2
    assert "Sortie existante" in capsys.readouterr().err
    assert (sortie / "gyroide.stl").read_bytes() == stl
    assert (sortie / "rapport.json").read_bytes() == rapport
