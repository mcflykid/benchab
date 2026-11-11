"""Comparaison préparatoire de palettes entre archives synthétiques."""

import json
from zipfile import ZipFile

from benchab.cli import main as cli_main
from benchab.controles.comparer_projets_3mf import comparer


def _archive(path, settings):
    """Écrit uniquement les réglages fictifs utiles au contrat de comparaison."""
    with ZipFile(path, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(settings))


def test_palette_comparee_par_identite_et_ordre_sans_affirmer_peinture(tmp_path):
    """Compare des couleurs par slot seulement si les identités et leur ordre concordent."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    slots = ["Filament A", "Filament B"]
    _archive(source, {
        "filament_settings_id": slots,
        "filament_colour": ["#112233", "#AABBCC"],
        "top_shell_layers": "4",
    })
    _archive(candidate, {
        "filament_settings_id": slots,
        "filament_colour": ["#112233", "#AABBCC"],
        "top_shell_layers": "4",
    })

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "PASS"
    assert [slot["identite"] for slot in rapport["palette"]["slots"]] == slots
    assert rapport["reglages"]["statut"] == "PASS"
    assert rapport["peinture"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_palette_ordre_different_est_signale_et_attribution_reste_non_verifiee(tmp_path):
    """Une même couleur sous des identités réordonnées ne prouve pas la conservation du slot."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {
        "filament_settings_id": ["Filament A", "Filament B"],
        "filament_colour": ["#112233", "#AABBCC"],
    })
    _archive(candidate, {
        "filament_settings_id": ["Filament B", "Filament A"],
        "filament_colour": ["#112233", "#AABBCC"],
    })

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["slots"] == []
    assert rapport["palette"]["attributions"]["statut"] == "NON VÉRIFIÉ"
    assert {d["cle"] for d in rapport["reglages"]["differences"]} == {"filament_settings_id"}


def test_absence_de_reglage_est_signalee_et_sources_restent_intactes(tmp_path):
    """Une clé absente du candidat est visible et les deux fichiers restent identiques."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"top_shell_layers": "4"})
    _archive(candidate, {})
    octets_source, octets_candidate = source.read_bytes(), candidate.read_bytes()

    rapport = comparer(source, candidate)

    assert any(d["cle"] == "top_shell_layers" for d in rapport["reglages"]["absences"])
    assert len(rapport["sha256"]["source"]) == 64
    assert len(rapport["sha256"]["candidat"]) == 64
    assert source.read_bytes() == octets_source
    assert candidate.read_bytes() == octets_candidate


def test_cli_est_un_diagnostic_json_sans_ouvrir_un_atelier(tmp_path, capsys, monkeypatch):
    """Le contrôle compare deux fixtures sans Base, mesure, sortie fichier ou réglage modifié."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    monkeypatch.setattr("benchab.cli.Base", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Base")))

    code = cli_main(["controle", "comparer-projets-3mf", str(source), str(candidate)])

    sortie = capsys.readouterr()
    assert code == 2  # La peinture par facette demeure NON VÉRIFIÉE.
    assert sortie.err == ""
    rapport = json.loads(sortie.out)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["statut"] == "PASS"


def test_palette_signale_couleur_litteralement_differente(tmp_path):
    """Une couleur modifiée au même slot est signalée sans conversion ou normalisation."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112234"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "WARN"
    assert rapport["palette"]["attributions"]["statut"] == "PASS"
    assert rapport["palette"]["slots"][0]["statut"] == "DIFFÉRENT"
    assert rapport["reglages"]["statut"] == "WARN"


def test_cle_inconnue_reste_non_verifiee_meme_identique(tmp_path):
    """Une clé hors dictionnaire n’est pas présentée comme préservée par égalité brute."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"vendor_painted_faces": ["01", "02"]})
    _archive(candidate, {"vendor_painted_faces": ["01", "02"]})

    rapport = comparer(source, candidate)

    assert rapport["reglages"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["reglages"]["non_verifies"][0]["cle"] == "vendor_painted_faces"
    assert rapport["peinture"]["extensions_asn_prusa"] == "NON VÉRIFIÉ"


def test_absence_de_config_projet_reste_non_verifiee(tmp_path):
    """Un 3MF sans réglages projet n’est pas traité comme un projet vide équivalent."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})

    rapport = comparer(source, candidate)

    assert rapport["reglages"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["peinture"]["statut"] == "NON VÉRIFIÉ"


def test_cli_archive_illisible_est_refuse_sans_traceback(tmp_path, capsys):
    """Une archive qui n’est pas un ZIP retourne le code technique 2 sans traceback."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    source.write_bytes(b"not a zip")
    _archive(candidate, {})

    code = cli_main(["controle", "comparer-projets-3mf", str(source), str(candidate)])

    sortie = capsys.readouterr()
    assert code == 2
    assert sortie.out == ""
    assert "comparaison 3MF refusée" in sortie.err
    assert "Traceback" not in sortie.err
