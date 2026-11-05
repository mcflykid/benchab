"""Les commandes de fabrication délèguent aux interfaces métier sans changer leur argv."""

from unittest.mock import patch

from benchab.cli import main


def appeler(prive, *arguments):
    return main(["--prive", str(prive), "fabrication", *arguments])


def test_commandes_fabrication_transmettent_arguments_et_contexte(tmp_path, atelier_initialise):
    """Vérifie commandes fabrication transmettent arguments et contexte ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    cas = [
        ("preparation", "benchab.fabrication.preparation", {"base": True}, ["creer", "--id", "P01"]),
        ("optimisation", "benchab.fabrication.optimisation", {"base": True}, ["proposer", "--objectif", "qualite"]),
        (
            "strategies-procede",
            "benchab.fabrication.strategies_procede",
            {"base": True},
            ["--projet", "projet", "--source", "source.3mf"],
        ),
        ("plaques-mixtes", "benchab.fabrication.plaques_mixtes", {"prive": True}, ["--projets", "essai"]),
        ("resistance", "benchab.fabrication.resistance", {"prive": True}, ["--projet", "projet"]),
        ("rattachements", "benchab.fabrication.rattachements", {}, ["--projet", "projet"]),
        ("retour", "benchab.fabrication.retour", {"base": True}, ["--projet", "projet", "--id", "P01"]),
    ]
    for commande, chemin_module, clef_contexte, directs in cas:
        arguments_cli = ["--", *directs] if directs and directs[0].startswith("-") else directs
        with patch(f"{chemin_module}.main", return_value=0) as fonction:
            assert appeler(prive, commande, *arguments_cli) == 0
        transmis, options = fonction.call_args
        assert transmis[0] == directs
        if clef_contexte.get("base"):
            assert options["base"].prive == prive
            assert set(options) == {"base"}
        elif clef_contexte.get("prive"):
            assert options == {"prive": prive}
        else:
            assert options == {}


def test_couches_variables_recoit_prive_du_contexte(tmp_path, atelier_initialise):
    """Vérifie couches variables recoit prive du contexte ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    with patch("benchab.fabrication.couches_variables.cli", return_value=0) as fonction:
        assert appeler(prive, "couches-variables", "source.3mf", "--mode", "qualite") == 0
    assert fonction.call_args.args[0] == ["--prive", str(prive), "source.3mf", "--mode", "qualite"]


def test_exportable_recoit_prive_du_contexte(tmp_path, atelier_initialise):
    """Vérifie exportable recoit prive du contexte ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    with patch("benchab.fabrication.exportable.main", return_value=0) as fonction:
        assert appeler(prive, "exportable", "source.stl", "--sortie", "sortie.stl") == 0
    assert fonction.call_args.args == (["source.stl", "--sortie", "sortie.stl"],)
    assert fonction.call_args.kwargs == {"prive": prive}


def test_envois_slicer_utilise_archive_privee_par_defaut_et_refuse_archive_exterieure(tmp_path, atelier_initialise):
    """Vérifie envois slicer utilise archive privee par defaut et refuse archive exterieure.

    Atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    with patch("benchab.fabrication.envois_slicer.main", return_value=0) as fonction:
        assert appeler(prive, "envois-slicer", "--", "archiver", "--source", "exports") == 0
    assert fonction.call_args.args[0] == ["archiver", "--source", "exports"]
    assert fonction.call_args.kwargs["prive"].resolve() == prive.resolve()


def test_envois_slicer_archive_exterieure_refusee(tmp_path, atelier_initialise):
    """Vérifie envois slicer archive exterieure refusee ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    source = tmp_path / "exports"
    source.mkdir()
    hors = tmp_path / "archive-exterieure"
    assert (
        main(
            [
                "--prive",
                str(prive),
                "fabrication",
                "envois-slicer",
                "--",
                "archiver",
                "--source",
                str(source),
                "--archive",
                str(hors),
            ]
        )
        == 2
    )
    assert not hors.exists()


def test_exportable_cli_ecrit_un_stl_neuf_dans_base_prive(tmp_path, capsys):
    import trimesh

    from benchab.fabrication.exportable import main as exporter

    prive = tmp_path / "prive"
    modele = prive / "projets/essai/candidats/piece.stl"
    modele.parent.mkdir(parents=True)
    trimesh.creation.box([10, 12, 14]).export(modele)
    sortie = prive / "projets/essai/candidats/piece-exportable.stl"
    assert exporter([str(modele), "--sortie", str(sortie)], prive=prive) == 0
    assert sortie.is_file()
    assert '"sortie"' in capsys.readouterr().out
    with patch("trimesh.load_mesh") as charger:
        try:
            exporter([str(modele), "--sortie", str(tmp_path / "hors.stl")], prive=prive)
        except SystemExit as erreur:
            assert erreur.code == 2
        else:
            raise AssertionError("Une sortie extérieure doit être refusée.")
    charger.assert_not_called()
