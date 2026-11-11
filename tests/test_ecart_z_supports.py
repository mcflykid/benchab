import json
import zipfile

from benchab import cli
from benchab.controles.ecart_z_supports import analyser


def _archive(path, plates):
    with zipfile.ZipFile(path, "w") as archive:
        for nom, texte in plates.items():
            archive.writestr(nom, texte)


def _gcode():
    return (
        "\n".join(
            [
                "G90",
                "M83",
                "G1 X0 Y0 Z0.2 F6000",
                "G92 X100 Y200 Z0.2 E0",
                ";TYPE:Support",
                ";HEIGHT:0.2",
                ";WIDTH:0.6",
                ";Z:99",
                "G1 X110 Y200 E1 F1200",
                "G92 X100 Y200 Z0.2 E0",
                "G1 X98 Y200 F6000",
                "G1 Z0.6",
                ";TYPE:Outer wall",
                ";HEIGHT:0.2",
                ";WIDTH:0.6",
                ";Z:0.4",
                "G1 X118 Y200 E1 F1200",
            ]
        )
        + "\n"
    )


def test_ecart_z_emploie_depots_reels_apres_g92_et_ignore_commentaire(tmp_path):
    source = tmp_path / "preuve.3mf"
    _archive(source, {"Metadata/plate_1.gcode": _gcode()})

    rapport = analyser(source)

    assert rapport["statut"] == "ESTIMATION"
    assert rapport["ecart_z_local_mm"]["minimum"] == 0.2
    assert rapport["source"]["sha256"]
    assert rapport["plaque"]["membre"] == "Metadata/plate_1.gcode"
    assert rapport["limites"]["surface_contact_physique_mm2"] is None


def test_plaque_ambigue_donne_non_mesurable_sans_couverture(tmp_path):
    source = tmp_path / "multi.3mf"
    _archive(source, {"Metadata/plate_1.gcode": _gcode(), "Metadata/plate_2.gcode": _gcode()})

    rapport = analyser(source)

    assert rapport["statut"] == "NON MESURABLE"
    assert rapport["raison"] == "choix_plaque_requis"
    assert "couverture" not in rapport
    json.dumps(rapport, allow_nan=False)


def test_alias_de_plaque_refuse_sans_mesure(tmp_path):
    source = tmp_path / "alias.3mf"
    _archive(source, {"Metadata/plate_1.gcode": _gcode(), "Metadata/plate_01.gcode": _gcode()})

    rapport = analyser(source, plaque=1)

    assert rapport["statut"] == "NON MESURABLE"
    assert rapport["raison"] == "numero_plaque_duplique"
    assert "couverture" not in rapport


def test_budget_echantillons_borne_a_quinze_millions(tmp_path):
    source = tmp_path / "budget.3mf"
    long_gcode = "G90\nM83\nG1 X0 Y0 Z1 F6000\n;TYPE:Support\n;HEIGHT:0.2\n;WIDTH:0.4\nG1 X160000 E1 F1200\n"
    _archive(source, {"Metadata/plate_1.gcode": long_gcode})

    rapport = analyser(source, plaque=1)

    assert rapport["statut"] == "PARTIEL"
    assert rapport["budgets"]["echantillons_max"] == 15_000_000
    assert rapport["budgets"]["arret"] == "budget_echantillons"
    assert rapport["ecart_z_local_mm"]["minimum"] is None


def test_cli_est_diagnostic_sans_base_ni_mesure(tmp_path, monkeypatch, capsys):
    source = tmp_path / "multi.3mf"
    _archive(source, {"Metadata/plate_1.gcode": _gcode(), "Metadata/plate_2.gcode": _gcode()})

    def interdit(*args, **kwargs):
        raise AssertionError("aucun atelier ni mesure ne doit être ouvert")

    monkeypatch.setattr(cli, "Base", interdit)
    monkeypatch.setattr(cli, "Mesure", interdit)
    code = cli.main(["--prive", str(tmp_path / "atelier-absent"), "controle", "ecart-z-supports", str(source)])

    sortie = json.loads(capsys.readouterr().out)
    assert code == 0
    assert sortie["statut"] == "NON MESURABLE"
    assert sortie["raison"] == "choix_plaque_requis"
    assert not (tmp_path / "atelier-absent").exists()


def test_arcs_et_roles_inconnus_sont_visibles_sans_corde(tmp_path):
    source = tmp_path / "incertain.3mf"
    texte = (
        "\n".join(
            [
                "G90",
                "M83",
                "G17",
                "G1 X0 Y0 Z1 F6000",
                ";TYPE:Support",
                ";HEIGHT:0.2",
                ";WIDTH:0.6",
                "G1 X10 E1 F1200",
                ";TYPE:Outer wall",
                "G1 Z1.4",
                "G1 X-5 E1 F1200",
                "G1 X1 Y0 E1 F1200",
                "G3 X0 Y1 I-1 J0 E1 F1200",
                ";TYPE:Experimental role",
                "G1 X20 E1 F1200",
            ]
        )
        + "\n"
    )
    _archive(source, {"Metadata/plate_1.gcode": texte})

    rapport = analyser(source, plaque=1)

    assert rapport["statut"] == "PARTIEL"
    assert rapport["exclusions"]["trajectoires_non_lineaires"] == 1
    assert rapport["exclusions"]["roles_inconnus"] == 1


def test_cli_archive_illisible_retourne_code_2_sans_atelier(tmp_path, monkeypatch, capsys):
    source = tmp_path / "gcode-invalide.3mf"
    _archive(source, {"Metadata/plate_1.gcode": b"\xff"})

    def interdit(*args, **kwargs):
        raise AssertionError("aucun atelier ni mesure ne doit être ouvert")

    monkeypatch.setattr(cli, "Base", interdit)
    monkeypatch.setattr(cli, "Mesure", interdit)
    code = cli.main(["--prive", str(tmp_path / "atelier-absent"), "controle", "ecart-z-supports", str(source)])

    sortie = capsys.readouterr()
    assert code == 2
    assert "lecture de l’écart Z refusée" in sortie.err
    assert not sortie.out
    assert not (tmp_path / "atelier-absent").exists()


def test_cli_conteneur_zip_invalide_est_un_refus_technique(tmp_path, capsys):
    """Un 3MF tronqué ne remonte pas une exception non traitée."""
    source = tmp_path / "tronque.3mf"
    source.write_bytes(b"PK\x03\x04")
    assert cli.main(["controle", "ecart-z-supports", str(source)]) == 2
    sortie = capsys.readouterr()
    assert "lecture de l’écart Z refusée" in sortie.err
    assert not sortie.out


def test_role_support_inconnu_ne_devient_pas_support_mesure(tmp_path):
    """Un préfixe seul n'établit pas le rôle du dépôt."""
    source = tmp_path / "role.3mf"
    texte = _gcode() + ";TYPE:Support experiment\nG1 X128 Y200 E1 F1200\n"
    _archive(source, {"Metadata/plate_1.gcode": texte})
    rapport = analyser(source)
    assert rapport["statut"] == "PARTIEL"
    assert rapport["exclusions"]["roles_inconnus"] == 1
    assert rapport["couverture"]["segments_mesures"] == 2


def test_depot_hors_limite_ne_fait_pas_accuser_les_dimensions(tmp_path):
    """Une coordonnée hors contrat rend le dépôt inexploitable avec WIDTH/HEIGHT valides."""
    source = tmp_path / "borne.3mf"
    texte = _gcode() + ";WIDTH:0.6\n;HEIGHT:0.2\nG1 X10000101 Y200 E1 F1200\n"
    _archive(source, {"Metadata/plate_1.gcode": texte})
    rapport = analyser(source)
    assert rapport["statut"] == "PARTIEL"
    assert rapport["exclusions"]["depots_inexploitables"] == 1
    assert "depots_sans_dimensions" not in rapport["exclusions"]
    assert rapport["couverture"]["segments_inexploitables"] == 1
