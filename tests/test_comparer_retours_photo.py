"""Comparaisons numériques de retours déclarés, sans données personnelles."""

import hashlib
import json
from zipfile import ZipFile

import pytest
from PIL import Image

from benchab.projets import retour_photo_assiste as retour
from benchab.projets.retour_photo_comparaison import comparer


def _archive(chemin, *, matiere="PLA", z=0.2):
    """Crée une plaque synthétique avec un seul profil de matière."""
    with ZipFile(chemin, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/project_settings.config", json.dumps({
            "printer_model": "Anycubic Kobra S1",
            "filament_settings_id": ["Témoin"], "filament_colour": ["#ffffff"], "filament_type": [matiere],
        }))
        archive.writestr("Metadata/plate_1.gcode", f"M83\nG1 X1 Y2 Z{z} E1\n")
    return chemin


def _preuve(prive, nom, archive, *, zone="haut"):
    """Établit une déclaration et une confirmation distinctes pour le test."""
    image = prive / f"{nom}.png"
    Image.new("RGB", (6, 6), "blue").save(image)
    destination = prive / nom
    plan = {"schema": 1, "source_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "plaque": 1, "region_id": zone, "z_min_mm": 0, "z_max_mm": 2}
    rapport = retour.creer_suggestion(
        image, destination, prive=prive, archive_3mf=archive, declaration=plan, plaque=1, region_id=zone,
    )
    confirmation = prive / f"{nom}-confirmation.json"
    fiche = destination / "suggestion.json"
    capture = retour.confirmer_suggestion(
        fiche, rapport["suggestion_sha256"], f"Observation privée {nom}", confirmation, prive=prive,
    )
    assert capture["confirmation_sha256"] == hashlib.sha256(confirmation.read_bytes()).hexdigest()
    return {
        "suggestion": fiche.relative_to(prive).as_posix(),
        "confirmation": confirmation.relative_to(prive).as_posix(),
        "confirmation_sha256": capture["confirmation_sha256"],
        "archive": archive.relative_to(prive).as_posix(),
    }


def test_meme_archive_et_meme_zone_declarees_sont_comparables(tmp_path):
    """La source et la zone déclarées coïncident, sans prouver une cause physique."""
    archive = _archive(tmp_path / "source.3mf")
    sources = [_preuve(tmp_path, nom, archive) for nom in ("a", "b")]
    rapport = comparer({"schema": 1, "sources": sources}, prive=tmp_path)
    assert rapport["statut"] == "PASS"
    assert rapport["portee"] == "MEME_SOURCE_ET_ZONE_DECLAREE"
    assert rapport["validation_physique"] is False
    assert "Observation privée" not in json.dumps(rapport, ensure_ascii=False)


def test_archives_differentes_ne_comparent_que_machine_et_matiere(tmp_path):
    """Une autre archive ne devient pas une comparaison géométrique."""
    a = _preuve(tmp_path, "a", _archive(tmp_path / "a.3mf", z=0.2))
    b = _preuve(tmp_path, "b", _archive(tmp_path / "b.3mf", z=0.4))
    rapport = comparer({"schema": 1, "sources": [a, b]}, prive=tmp_path)
    assert rapport["statut"] == "PASS"
    assert rapport["portee"] == "MACHINE_ET_MATIERE_DECLAREES"
    assert rapport["comparaison_geometrique"] is False


def test_matiere_differente_et_source_distincte_restent_non_verifiees(tmp_path):
    """Deux observations confirmées n’autorisent pas une comparaison hors contexte."""
    a = _preuve(tmp_path, "a", _archive(tmp_path / "a.3mf", matiere="PLA"))
    b = _preuve(tmp_path, "b", _archive(tmp_path / "b.3mf", matiere="PETG"))
    rapport = comparer({"schema": 1, "sources": [a, b]}, prive=tmp_path)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["portee"] == "AUCUNE"


@pytest.mark.parametrize("element", ["confirmation", "archive", "apercu"])
def test_comparaison_refuse_un_lien_ou_une_preuve_modifiee(tmp_path, element):
    """La comparaison revérifie les octets déclarés de chaque paire."""
    archive = _archive(tmp_path / "source.3mf")
    a = _preuve(tmp_path, "a", archive)
    b = _preuve(tmp_path, "b", archive)
    cible = tmp_path / (a[element] if element != "apercu" else "a/apercu.jpg")
    cible.write_bytes(b"autres octets")
    with pytest.raises(ValueError):
        comparer({"schema": 1, "sources": [a, b]}, prive=tmp_path)


def test_cli_comparaison_sans_base_et_sortie_privee_neuve(tmp_path, monkeypatch, capsys):
    """La route commune respecte --prive et conserve un rapport sans SQLite."""
    from benchab import cli

    prive = tmp_path / "atelier"
    prive.mkdir()
    monkeypatch.setenv("BENCHAB_PRIVE", str(tmp_path / "autre"))
    monkeypatch.setattr(cli, "Base", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Base()")))
    archive = _archive(prive / "source.3mf")
    declaration = prive / "paire.json"
    declaration.write_text(json.dumps({
        "schema": 1, "sources": [_preuve(prive, nom, archive) for nom in ("a", "b")],
    }), encoding="utf-8")
    destination = prive / "comparaison.json"
    commande = ["--prive", str(prive), "visualisation", "retour-photo", "comparer",
                str(declaration), "--sortie", str(destination)]
    assert cli.main(commande) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["statut"] == "PASS"
    assert json.loads(destination.read_bytes())["portee"] == rapport["portee"]
    assert cli.main(commande) == 2
    assert not (prive / "benchab.sqlite").exists()
