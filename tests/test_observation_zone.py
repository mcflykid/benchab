"""Contrat commun d’observation privée par zone, avec données entièrement synthétiques."""

import hashlib
import json
from zipfile import ZipFile

import pytest
from PIL import Image

from benchab import cli
from benchab.projets import retour_photo_assiste
from benchab.projets.observation_zone import construire


def _candidate():
    return {
        "schema": 1,
        "suggestion": "CANDIDAT",
        "photo": {
            "source_fichier": "photo-source.png",
            "source_sha256": hashlib.sha256(b"image fictive").hexdigest(),
            "apercu_fichier": "apercu.jpg",
            "apercu_sha256": hashlib.sha256(b"apercu fictif").hexdigest(),
            "format_apercu": "JPEG",
        },
        "liaison": {
            "statut": "PASS",
            "source_3mf_sha256": "a" * 64,
            "plaque": 1,
            "region_id_declaree": "zone-fictive-7",
            "plage_z_mm_declaree": [1.25, 3.5],
            "gcode_membre": "Metadata/plate_01.gcode",
            "gcode_sha256": "b" * 64,
            "reglages_membre": "Metadata/project_settings.config",
            "reglages_sha256": "c" * 64,
            "origine_region": "déclaration fictive",
        },
    }


@pytest.mark.parametrize("origine", ["photo_importee", "image_deja_disponible"])
def test_origines_media_partagent_le_meme_contrat_et_gardent_la_zone(origine):
    observation = construire(
        _candidate(),
        sha_suggestion="d" * 64,
        texte_declare="Exemple fictif : surface irrégulière dans la zone déclarée.",
        origine_media=origine,
    )

    assert observation["schema"] == 1
    assert observation["nature"] == "OBSERVATION_ZONE"
    assert observation["statut"] == "CONFIRMATION_HUMAINE"
    assert observation["portee"] == {
        "hypothese": "NON ÉVALUÉE", "mesure_physique": "NON VÉRIFIÉE", "reglage_manuel": "NON APPLIQUÉ",
    }
    assert observation["media"]["origine_declaree"] == origine
    assert observation["media"]["source_sha256"] == _candidate()["photo"]["source_sha256"]
    assert observation["zone"] == {
        "statut": "PASS",
        "source_3mf_sha256": "a" * 64,
        "plaque": 1,
        "region_id_declaree": "zone-fictive-7",
        "plage_z_mm_declaree": [1.25, 3.5],
        "gcode_membre": "Metadata/plate_01.gcode",
        "gcode_sha256": "b" * 64,
        "reglages_membre": "Metadata/project_settings.config",
        "reglages_sha256": "c" * 64,
    }
    assert observation["confirmation"]["suggestion_sha256"] == "d" * 64


def test_zone_non_qualifiee_reste_non_verifiee_et_sans_inventer_de_zone():
    candidate = _candidate()
    candidate["liaison"] = {"statut": "NON VÉRIFIÉ", "motif": "source absente"}

    observation = construire(
        candidate,
        sha_suggestion="d" * 64,
        texte_declare="Observation fictive.",
        origine_media="image_deja_disponible",
    )

    assert observation["zone"] == {"statut": "NON VÉRIFIÉ", "motif": "source absente"}
    assert observation["statut"] == "CONFIRMATION_HUMAINE"


def test_liaison_pass_incomplete_ne_devient_pas_une_observation():
    candidate = _candidate()
    del candidate["liaison"]["gcode_sha256"]

    with pytest.raises(ValueError, match="Liaison de zone PASS"):
        construire(
            candidate,
            sha_suggestion="d" * 64,
            texte_declare="Observation fictive.",
            origine_media="photo_importee",
        )


def test_contrat_refuse_origine_inconnue_ou_confirmation_absente():
    with pytest.raises(ValueError):
        construire(
            _candidate(), sha_suggestion="d" * 64,
            texte_declare="Observation.", origine_media="camera",
        )


@pytest.mark.parametrize("origine", ["photo_importee", "image_deja_disponible"])
def test_confirmation_publie_le_meme_format_commum_pour_les_deux_origines(tmp_path, origine):
    """Deux provenances déclarées produisent un même contrat depuis des médias synthétiques."""
    prive = tmp_path / "prive"
    prive.mkdir()
    image = (prive / "images" / "fictive.png") if origine == "image_deja_disponible" else tmp_path / "fictive.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "blue").save(image)
    dossier = prive / origine
    proposition = retour_photo_assiste.creer_suggestion(image, dossier, prive=prive, origine_media=origine)
    confirmation_path = prive / f"{origine}-confirmation.json"

    capture = retour_photo_assiste.confirmer_suggestion(
        dossier / "suggestion.json",
        proposition["suggestion_sha256"],
        "Exemple fictif : marque visible dans une zone déclarée.",
        confirmation_path,
        prive=prive,
    )
    publie = json.loads(confirmation_path.read_text(encoding="utf-8"))

    assert capture["observation_zone"] == publie["observation_zone"]
    assert publie["observation_zone"]["nature"] == "OBSERVATION_ZONE"
    assert publie["observation_zone"]["media"]["origine_declaree"] == origine
    assert publie["observation_zone"]["zone"]["statut"] == "NON VÉRIFIÉ"
    assert publie["statut"] == "PASS"


def test_confirmation_lie_le_contrat_a_une_archive_synthetique_exacte(tmp_path):
    """La zone commune garde plaque, membre, SHA et intervalle issus d’un 3MF fictif."""
    prive = tmp_path / "prive"
    prive.mkdir()
    image = tmp_path / "image-fictive.png"
    Image.new("RGB", (8, 8), "green").save(image)
    archive = tmp_path / "archive-fictive.3mf"
    with ZipFile(archive, "w") as paquet:
        paquet.writestr("Metadata/project_settings.config", json.dumps({
            "printer_model": "MODELE-FICTIF",
            "filament_settings_id": ["FILAMENT-FICTIF"],
            "filament_colour": ["#123456"],
            "filament_type": ["MATERIAU-FICTIF"],
        }))
        paquet.writestr("Metadata/plate_01.gcode", "G90\nM83\n")
    sha_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    declaration = {
        "schema": 1, "source_sha256": sha_archive, "plaque": 1,
        "region_id": "zone-fictive-7", "z_min_mm": 1.25, "z_max_mm": 3.5,
    }
    proposition = retour_photo_assiste.creer_suggestion(
        image, prive / "candidate", prive=prive, archive_3mf=archive, declaration=declaration,
        plaque=1, region_id="zone-fictive-7", origine_media="photo_importee",
    )
    capture = retour_photo_assiste.confirmer_suggestion(
        prive / "candidate" / "suggestion.json", proposition["suggestion_sha256"],
        "Exemple fictif : observation dans la zone indiquée.", prive / "confirmation.json", prive=prive,
    )

    zone = capture["observation_zone"]["zone"]
    assert zone["statut"] == "PASS"
    assert zone["source_3mf_sha256"] == sha_archive
    assert zone["gcode_membre"] == "Metadata/plate_01.gcode"
    assert zone["region_id_declaree"] == "zone-fictive-7"
    assert zone["plage_z_mm_declaree"] == [1.25, 3.5]


def test_cli_expose_origine_declaree_sans_creer_sqlite(tmp_path, monkeypatch, capsys):
    """Le parcours commun via CLI n’ouvre aucun atelier pour une image synthétique."""
    prive = tmp_path / "prive"
    prive.mkdir()
    image = tmp_path / "fictive.png"
    Image.new("RGB", (8, 8), "red").save(image)
    monkeypatch.setattr(cli, "Base", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Base()")))
    monkeypatch.setattr(cli, "Mesure", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Mesure()")))

    code = cli.main([
        "--prive", str(prive), "visualisation", "retour-photo", "proposer", str(image),
        "--destination", str(prive / "candidate"), "--origine-image", "photo_importee",
    ])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["origine_media_declaree"] == "photo_importee"
    assert not (prive / "benchab.sqlite").exists()
