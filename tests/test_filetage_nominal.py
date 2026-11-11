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
        assert main([*options, "--sortie", str(tmp_path / f"p{i}")], prive=tmp_path) == 0
        rapport = json.loads((tmp_path / f"p{i}/rapport.json").read_bytes())
        stl = tmp_path / f"p{i}/filetage.stl"
        assert rapport["stl_sha256"] == hashlib.sha256(stl.read_bytes()).hexdigest()
        assert trimesh.load_mesh(stl).is_volume
    assert (tmp_path / "p1/filetage.stl").read_bytes() == (tmp_path / "p2/filetage.stl").read_bytes()
    assert main([*options, "--sortie", str(tmp_path / "p1")], prive=tmp_path) == 2
    assert capsys.readouterr().err


def test_route_cli_transmet_l_atelier_prive_explicite(tmp_path, monkeypatch, capsys):
    """Le chemin choisi prime sur l’atelier par défaut, même dans un autre environnement."""
    from benchab import cli
    from benchab.conception import filetage_nominal

    explicite, autre = tmp_path / "atelier-explicite", tmp_path / "autre"
    monkeypatch.setenv("BENCHAB_PRIVE", str(autre))
    assert cli.main(["--prive", str(explicite), "init"]) == 0
    recu = []

    def main(arguments, *, prive=None):
        recu.append(prive)
        return 0

    monkeypatch.setattr(filetage_nominal, "main", main)
    assert cli.main(["--prive", str(explicite), "conception", "filetage-nominal", "--", "--sortie", "dossier"]) == 0
    assert recu == [explicite]
    assert not autre.exists()
    capsys.readouterr()


def test_meplats_cotes_mesurables_et_jeu_radial_separe():
    """Le profil conserve les plateaux et les flancs dans une coupe axiale connue."""
    from benchab.conception.filetage_nominal import generer

    cotes = _cotes(pas_mm=2, longueur_mm=8, profondeur_radiale_mm=0.75,
                  meplat_crete_mm=0.5, meplat_fond_mm=0.5)
    piece, rapport = generer(**cotes)
    outil, r_outil = generer(**cotes, role="outil-interieur", jeu_radial_mm=0.2)
    assert piece.is_volume and outil.is_volume
    assert rapport["profil"] == "trapézoïdal nominal à méplats déclarés"
    assert rapport["angle_flancs_deg"] == pytest.approx(67.380135)
    assert r_outil["ajustement_reel"] == "NON VÉRIFIÉ"
    axe = piece.vertices[:-2]
    ligne = axe[(np.isclose(axe[:, 1], 0)) & (axe[:, 0] > 0)]
    rayons = {round(z, 5): x for x, _y, z in ligne}
    assert [rayons[z] for z in (0.0, 0.125, 0.25, 0.5, 0.75, 0.875, 1.0)] == pytest.approx(
        [4.0, 4.0, 4.0, 3.625, 3.25, 3.25, 3.25],
    )
    ecart = np.linalg.norm(outil.vertices[:-2, :2], axis=1) - np.linalg.norm(piece.vertices[:-2, :2], axis=1)
    assert ecart == pytest.approx(np.full(len(ecart), 0.2))


@pytest.mark.parametrize("cotes", [
    {"meplat_crete_mm": 0.3},
    {"meplat_crete_mm": 0.01, "meplat_fond_mm": 0.3},
    {"meplat_crete_mm": 0.8, "meplat_fond_mm": 0.8},
])
def test_meplats_incomplets_ou_flanc_insuffisant_refuses(cotes):
    """Aucun profil incomplet ou sous la résolution ne reçoit PASS."""
    from benchab.conception.filetage_nominal import generer

    with pytest.raises(ValueError, match="méplat|flanc"):
        generer(**_cotes(**cotes))


def test_profil_iso_m6_distingue_fond_arrondi_et_cretes_interieures():
    """Vérifie les diamètres nominaux ISO 724:2023 et les coupes de l’outil."""
    from benchab.conception.filetage_nominal import generer

    cotes = dict(diametre_mm=6, longueur_mm=4, pas_mm=1,
                 source_cotes="Témoin M6 × 1", profil_metrique_iso=True)
    vis, rv = generer(**cotes)
    outil, ro = generer(**cotes, role="outil-interieur", jeu_radial_mm=0.1)
    assert vis.is_volume and outil.is_volume
    assert rv["diametre_sur_flancs_nominal_mm"] == pytest.approx(5.350481, abs=1e-6)
    assert rv["diametre_mineur_nominal_mm"] == pytest.approx(4.773131, abs=1e-6)
    assert ro["diametre_mineur_nominal_mm"] == pytest.approx(4.917468, abs=1e-6)
    assert ro["rayon_crete_nominal_mm"] == 3
    assert ro["rayon_fond_nominal_mm"] == pytest.approx(4.917468 / 2, abs=1e-6)
    assert ro["rayon_crete_avec_jeu_mm"] == 3.1
    assert ro["rayon_fond_avec_jeu_mm"] == pytest.approx(4.917468 / 2 + 0.1, abs=1e-6)
    assert ro["ajustement_reel"] == "NON VÉRIFIÉ"

    # Coupe dans l’axe : le cercle de raccord est sous le plateau du profil intérieur.
    def rayons(mesh):
        sommets = mesh.vertices[:-2]
        return {round(z, 6): x for x, _y, z in sommets if x > 0 and abs(_y) < 1e-9}

    v, o = rayons(vis), rayons(outil)
    assert v[0] == pytest.approx(3)
    assert v[0.25] == pytest.approx(5.350481 / 2, abs=1e-6)
    assert v[0.375] == pytest.approx(4.917468 / 2, abs=1e-6)
    assert v[0.5] == pytest.approx(4.773131 / 2, abs=1e-6)
    assert v[0.375] > v[0.4375] > v[0.5]
    assert o[0.375] == pytest.approx(o[0.5])
    assert o[0.5] == pytest.approx(4.917468 / 2 + 0.1, abs=1e-6)
    bloc = trimesh.creation.box((12, 12, 4)).apply_translation((0, 0, 2))
    piece = trimesh.boolean.difference([bloc, outil], engine="manifold")
    assert piece.is_volume
    assert trimesh.boolean.intersection([piece, vis], engine="manifold").is_empty


def test_cli_filetage_iso_pas_fin_et_refus_de_cotes_concurrentes(tmp_path):
    """Le chemin CLI distingue les cotes ISO d’une déclaration sur mesure."""
    import json

    from benchab.conception.filetage_nominal import main

    options = ["--profil-metrique-iso", "--diametre-mm", "8", "--pas-mm", "1",
               "--longueur-mm", "4", "--sens", "gauche", "--source-cotes", "Témoin M8 × 1"]
    sortie = tmp_path / "iso"
    assert main([*options, "--sortie", str(sortie)], prive=tmp_path) == 0
    rapport = json.loads((sortie / "rapport.json").read_bytes())
    assert rapport["sens"] == "gauche"
    assert rapport["diametre_mineur_nominal_mm"] == pytest.approx(6.773131, abs=1e-6)
    assert trimesh.load_mesh(sortie / "filetage.stl").is_volume
    invalide = tmp_path / "invalide"
    assert main([*options, "--profondeur-radiale-mm", "0.6",
                 "--sortie", str(invalide)], prive=tmp_path) == 2
    assert not invalide.exists()
