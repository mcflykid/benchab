"""Contrats privés du retour photo, sur images et archives synthétiques."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from benchab.projets import retour_photo_assiste as retour_photo


def _archive(chemin):
    with ZipFile(chemin, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/project_settings.config", '{"layer_height":"0.2"}')
        archive.writestr("Metadata/plate_1.gcode", "G1 X1 Y2 Z0.2 E1\n")


def test_suggestion_preserve_photo_relies_zone_declaree_et_ne_touche_pas_au_projet(tmp_path):
    prive = tmp_path / "prive"
    (prive / "projets").mkdir(parents=True)
    photo = tmp_path / "photo.png"
    Image.new("RGB", (20, 12), "red").save(photo)
    source_sha = hashlib.sha256(photo.read_bytes()).hexdigest()
    archive = tmp_path / "piece.3mf"
    _archive(archive)
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    declaration = {
        "schema": 1,
        "source_sha256": archive_sha,
        "plaque": 1,
        "region_id": "couture-A",
        "z_min_mm": 1.0,
        "z_max_mm": 4.0,
    }
    destination = prive / "retours-photo" / "suggestion-001"

    rapport = retour_photo.creer_suggestion(
        photo,
        destination,
        prive=prive,
        archive_3mf=archive,
        declaration=declaration,
        plaque=1,
        region_id="couture-A",
    )

    assert (destination / "photo-source.png").read_bytes() == photo.read_bytes()
    assert rapport["photo"]["source_sha256"] == source_sha
    assert rapport["suggestion"] == "CANDIDAT"
    assert rapport["liaison"]["statut"] == "PASS"
    assert rapport["liaison"]["gcode_sha256"]
    assert rapport["liaison"]["reglages_sha256"]
    assert rapport["interpretation_photo"] == "NON VÉRIFIÉ"
    assert not list((prive / "projets").iterdir())


def test_mauvais_sha_et_confirmation_separee_ne_publient_que_si_explicite(tmp_path):
    prive = tmp_path / "prive"
    prive.mkdir()
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (8, 8), "blue").save(photo)
    suggestion = prive / "retours-photo" / "suggestion-001"
    rapport = retour_photo.creer_suggestion(photo, suggestion, prive=prive)
    fiche = suggestion / "suggestion.json"
    sha = hashlib.sha256(fiche.read_bytes()).hexdigest()
    confirmation = prive / "retours-photo" / "confirmations" / "conf-001.json"

    with pytest.raises(ValueError, match="empreinte"):
        retour_photo.confirmer_suggestion(fiche, "0" * 64, "zone observée", confirmation, prive=prive)
    assert not confirmation.exists()
    resultat = retour_photo.confirmer_suggestion(fiche, sha, "zone observée par la personne", confirmation, prive=prive)
    assert resultat["suggestion_sha256"] == sha
    assert resultat["confirmation_humaine"] is True
    assert rapport["suggestion"] == "CANDIDAT"
    assert json.loads(fiche.read_text())["suggestion"] == "CANDIDAT"


def test_archive_non_identique_reste_non_verifiee_et_destination_neuve(tmp_path):
    prive = tmp_path / "prive"
    prive.mkdir()
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    archive = tmp_path / "piece.3mf"
    _archive(archive)
    declaration = {
        "schema": 1,
        "source_sha256": "0" * 64,
        "plaque": 1,
        "region_id": "zone-1",
        "z_min_mm": 0.2,
        "z_max_mm": 2.0,
    }
    destination = prive / "retours-photo" / "candidate"
    rapport = retour_photo.creer_suggestion(
        photo, destination, prive=prive, archive_3mf=archive,
        declaration=declaration, plaque=1, region_id="zone-1",
    )
    assert rapport["liaison"]["statut"] == "NON VÉRIFIÉ"
    assert "SHA" in rapport["liaison"]["motif"]
    with pytest.raises(FileExistsError):
        retour_photo.creer_suggestion(photo, destination, prive=prive)


def test_confirmation_refuse_photo_alteree_apres_suggestion(tmp_path):
    prive = tmp_path / "prive"
    prive.mkdir()
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    dossier = prive / "candidat"
    retour_photo.creer_suggestion(photo, dossier, prive=prive)
    fiche = dossier / "suggestion.json"
    sha = hashlib.sha256(fiche.read_bytes()).hexdigest()
    (dossier / "photo-source.png").write_bytes(b"modifie")
    with pytest.raises(ValueError, match="différent de la suggestion"):
        retour_photo.confirmer_suggestion(fiche, sha, "observation", prive / "confirmation.json", prive=prive)
    assert not (prive / "confirmation.json").exists()


def test_route_cli_ne_cree_ni_base_sqlite_ni_mesure(tmp_path, capsys):
    from benchab.cli import main

    prive = tmp_path / "prive"
    prive.mkdir()
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    destination = prive / "retours-photo" / "cli"
    code = main([
        "--prive", str(prive), "visualisation", "retour-photo", "proposer",
        str(photo), "--destination", str(destination),
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["suggestion"] == "CANDIDAT"
    assert not (prive / "benchab.sqlite").exists()


def test_heic_synthetic_converti_localement_si_sips_disponible(tmp_path):
    import subprocess

    if not Path("/usr/bin/sips").is_file():
        pytest.skip("sips indisponible")
    jpg = tmp_path / "source.jpg"
    heic = tmp_path / "source.heic"
    Image.new("RGB", (8, 8), "green").save(jpg)
    conversion = subprocess.run(
        ["/usr/bin/sips", "-s", "format", "heic", str(jpg), "--out", str(heic)],
        capture_output=True, timeout=30, check=False,
    )
    if conversion.returncode != 0:
        pytest.skip("sips ne sait pas créer de témoin HEIC synthétique")
    prive = tmp_path / "prive"
    prive.mkdir()
    rapport = retour_photo.creer_suggestion(heic, prive / "candidate", prive=prive)
    assert rapport["photo"]["format_apercu"] == "JPEG"
    assert (prive / "candidate" / "photo-source.heic").read_bytes() == heic.read_bytes()

