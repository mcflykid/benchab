"""Filetages triangulaires numériques à cotes explicitement déclarées."""

import numpy as np
import pytest
import trimesh


def _cotes(**modifications):
    return {
        "diametre_mm": 8,
        "longueur_mm": 9,
        "pas_mm": 1.5,
        "profondeur_radiale_mm": 0.65,
        "source_cotes": "fixture nominale sans norme",
        **modifications,
    }


def test_filetage_exterieur_volume_ferme_et_nominal():
    from benchab.conception.filetage_nominal import generer

    modele, rapport = generer(**_cotes())
    assert modele.is_volume and modele.body_count == 1
    assert modele.bounds[:, 2] == pytest.approx([0, 9])
    assert rapport["statut"] == "PASS"
    assert rapport["ajustement_reel"] == "NON VÉRIFIÉ"
    rayon = np.linalg.norm(modele.vertices[:-2, :2], axis=1)
    assert rayon.min() == pytest.approx(3.35)
    assert rayon.max() == pytest.approx(4)


def test_sens_droit_et_gauche_miroirs_et_memes_volumes():
    from benchab.conception.filetage_nominal import generer

    droit, _ = generer(**_cotes(sens="droite"))
    gauche, _ = generer(**_cotes(sens="gauche"))
    assert droit.volume == pytest.approx(gauche.volume)
    assert not np.array_equal(droit.vertices, gauche.vertices)
    for theta in (0.0, np.pi / 2, np.pi):

        def point_cretes(maillage, angle):
            coord = maillage.vertices[:-2]
            ang = np.arctan2(coord[:, 1], coord[:, 0])
            rayons = np.linalg.norm(coord[:, :2], axis=1)
            diff = np.arctan2(np.sin(ang - angle), np.cos(ang - angle))
            p = coord[np.isclose(diff, 0) & np.isclose(rayons, 4)]
            assert len(p) > 0
            return np.sort(p[:, 2])

        # Un miroir du plan Y=0 échange les sens sans changer la cote Z.
        assert point_cretes(droit, theta) == pytest.approx(point_cretes(gauche, -theta))


def test_outil_interieur_ajoute_seulement_le_jeu_radial_declare():
    from benchab.conception.filetage_nominal import generer

    vis, _ = generer(**_cotes())
    outil, rapport = generer(**_cotes(role="outil-interieur", jeu_radial_mm=0.2))
    assert outil.is_volume and outil.volume > vis.volume
    assert rapport["jeu_radial_mm"] == 0.2 and rapport["ajustement_reel"] == "NON VÉRIFIÉ"
    ecart = np.linalg.norm(outil.vertices[:-2, :2], axis=1) - np.linalg.norm(vis.vertices[:-2, :2], axis=1)
    assert ecart == pytest.approx(np.full(len(ecart), 0.2))
    bloc = trimesh.creation.box((15, 15, 9)).apply_translation((0, 0, 4.5))
    piece = trimesh.boolean.difference([bloc, outil], engine="manifold")
    assert piece.is_volume and piece.volume < bloc.volume


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"pas_mm": 0}, "Pas"),
        ({"profondeur_radiale_mm": 4}, "profondeur"),
        ({"longueur_mm": 200, "pas_mm": 0.5}, "budget"),
        ({"source_cotes": " "}, "source"),
        ({"role": "outil-interieur"}, "jeu radial"),
    ],
)
def test_filetage_refuse_cotes_ou_budget_inexploitables(options, message):
    from benchab.conception.filetage_nominal import generer

    with pytest.raises(ValueError, match=message):
        generer(**_cotes(**options))


def test_publication_stl_rejouable_et_refuse_remplacement(tmp_path, capsys):
    """La copie numérique relue garde son volume et ses octets au rejeu."""
    import hashlib
    import json

    from benchab.conception.filetage_nominal import main

    options = [
        "--diametre-mm",
        "8",
        "--longueur-mm",
        "9",
        "--pas-mm",
        "1.5",
        "--profondeur-radiale-mm",
        "0.65",
        "--source-cotes",
        "fixture de test",
    ]
    for i in (1, 2):
        assert main([*options, "--sortie", str(tmp_path / f"p{i}")]) == 0
        rapport = json.loads((tmp_path / f"p{i}/rapport.json").read_bytes())
        stl = tmp_path / f"p{i}/filetage.stl"
        assert rapport["stl_sha256"] == hashlib.sha256(stl.read_bytes()).hexdigest()
        assert trimesh.load_mesh(stl).is_volume
    assert (tmp_path / "p1/filetage.stl").read_bytes() == (tmp_path / "p2/filetage.stl").read_bytes()
    assert main([*options, "--sortie", str(tmp_path / "p1")]) == 2
    assert capsys.readouterr().err
