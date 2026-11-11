"""Contrats privés du retour photo, sur images et archives synthétiques."""

import hashlib
import json
import os
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
    assert rapport["suggestion_sha256"] == hashlib.sha256(
        (destination / "suggestion.json").read_bytes()
    ).hexdigest()
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


def test_confirmation_ne_relit_pas_un_apercu_hors_budget(tmp_path, monkeypatch):
    """Une référence d’image hors budget n’est pas hachée ni confirmée."""
    prive = tmp_path / "prive"
    prive.mkdir()
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    dossier = prive / "candidat"
    rapport = retour_photo.creer_suggestion(photo, dossier, prive=prive)
    monkeypatch.setattr(retour_photo, "_MAX_APERCU_OCTETS", 32)
    with pytest.raises(ValueError, match="hors budget"):
        retour_photo.confirmer_suggestion(
            dossier / "suggestion.json", rapport["suggestion_sha256"], "observation",
            prive / "confirmation.json", prive=prive,
        )
    assert not (prive / "confirmation.json").exists()


def test_apercu_ne_diffuse_pas_les_metadonnees_de_la_photo_originale(tmp_path):
    """L’orientation est appliquée, les métadonnées restent dans l’original privé."""
    prive = tmp_path / "prive"
    prive.mkdir()
    photo = tmp_path / "photo.jpg"
    image = Image.new("RGB", (20, 12), "white")
    exif = image.getexif()
    exif[274] = 6
    exif[270] = "Texte privé de la fixture"
    image.save(photo, exif=exif)
    dossier = prive / "candidat"
    retour_photo.creer_suggestion(photo, dossier, prive=prive)
    assert (dossier / "photo-source.jpg").read_bytes() == photo.read_bytes()
    with Image.open(dossier / "apercu.jpg") as apercu:
        assert apercu.size == (12, 20)
        assert not apercu.getexif()


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


def test_route_cli_transmet_le_prive_meme_si_la_variable_env_pointe_ailleurs(tmp_path, monkeypatch, capsys):
    from benchab.cli import main

    choisi = tmp_path / "atelier-choisi"
    choisi.mkdir()
    autre = tmp_path / "atelier-env"
    autre.mkdir()
    monkeypatch.setenv("BENCHAB_PRIVE", str(autre))
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    sortie = choisi / "retours-photo" / "choisi"
    assert main([
        "--prive", str(choisi), "visualisation", "retour-photo", "proposer",
        str(photo), "--destination", str(sortie),
    ]) == 0
    capsys.readouterr()
    assert (sortie / "suggestion.json").is_file()
    assert not (autre / "retours-photo").exists()


def test_refus_secret_du_retour_photo_ne_cree_pas_de_mesure(tmp_path, monkeypatch, capsys):
    from benchab.cli import main
    from benchab.donnees.base import Base

    prive = tmp_path / "prive"
    base = Base(prive)
    try:
        avant = base.connexion.execute("SELECT count(*) FROM mesures").fetchone()[0]
        monkeypatch.setenv("BENCHAB_PRIVE", str(tmp_path / "ailleurs"))
        code = main([
            "--prive", str(prive), "visualisation", "retour-photo", "proposer",
            "photo.png", "--password", "secret-fixture",
        ])
        assert code == 1
        capsys.readouterr()
        assert base.connexion.execute("SELECT count(*) FROM mesures").fetchone()[0] == avant
    finally:
        base.fermer()


def test_apercu_est_decode_depuis_la_copie_privee(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    prive = tmp_path / "prive"
    prive.mkdir()
    appels = []
    apercu_reel = retour_photo._apercu

    def verifier_copie(source, destination):
        appels.append(source)
        assert source.name == "photo-source.png"
        assert source.read_bytes() == photo.read_bytes()
        apercu_reel(source, destination)

    monkeypatch.setattr(retour_photo, "_apercu", verifier_copie)
    retour_photo.creer_suggestion(photo, prive / "candidate", prive=prive)
    assert len(appels) == 1


def test_confirmation_refuse_manifest_trop_grand(tmp_path):
    prive = tmp_path / "prive"
    prive.mkdir()
    fiche = prive / "suggestion.json"
    fiche.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    sha = hashlib.sha256(fiche.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="taille|budget|Mio"):
        retour_photo.confirmer_suggestion(fiche, sha, "observation", prive / "confirmation.json", prive=prive)


def test_confirmation_parse_les_octets_meme_empreintes_sans_read_text(tmp_path, monkeypatch):
    prive = tmp_path / "prive"
    prive.mkdir()
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    dossier = prive / "candidate"
    retour_photo.creer_suggestion(photo, dossier, prive=prive)
    fiche = dossier / "suggestion.json"
    sha = hashlib.sha256(fiche.read_bytes()).hexdigest()
    lecture = Path.read_text

    def lecture_interdite(self, *args, **kwargs):
        if self == fiche:
            raise AssertionError("la fiche ne doit pas être rouverte après son SHA")
        return lecture(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", lecture_interdite)
    resultat = retour_photo.confirmer_suggestion(
        fiche, sha, "observation explicite", prive / "confirmation.json", prive=prive
    )
    assert resultat["suggestion_sha256"] == sha


def test_confirmation_exige_fichiers_references_distincts(tmp_path):
    prive = tmp_path / "prive"
    prive.mkdir()
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    dossier = prive / "candidate"
    retour_photo.creer_suggestion(photo, dossier, prive=prive)
    fiche = dossier / "suggestion.json"
    rapport = json.loads(fiche.read_text())
    rapport["photo"]["apercu_fichier"] = "photo-source.png"
    fiche.write_text(json.dumps(rapport))
    sha = hashlib.sha256(fiche.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="distincts"):
        retour_photo.confirmer_suggestion(fiche, sha, "observation", prive / "confirmation.json", prive=prive)


def test_apercu_ninclut_pas_les_metadonnees_exif(tmp_path):
    from PIL import Image

    photo = tmp_path / "photo.jpg"
    image = Image.new("RGB", (8, 8), "white")
    exif = Image.Exif()
    exif[270] = "métadonnée synthétique privée"
    image.save(photo, exif=exif)
    prive = tmp_path / "prive"
    prive.mkdir()
    retour_photo.creer_suggestion(photo, prive / "candidate", prive=prive)
    with Image.open(prive / "candidate" / "apercu.jpg") as apercu:
        assert not apercu.getexif()


def test_image_geante_est_refusee_sans_tenter_de_decodage_complet(tmp_path):
    import struct
    import zlib

    photo = tmp_path / "geante.png"
    Image.new("RGB", (1, 1), "white").save(photo)
    brut = bytearray(photo.read_bytes())
    brut[16:24] = struct.pack(">II", 50_000, 1_000)
    brut[29:33] = struct.pack(">I", zlib.crc32(brut[12:29]) & 0xFFFFFFFF)
    photo.write_bytes(brut)
    with pytest.raises(ValueError, match="40 mégapixels"):
        retour_photo._apercu(photo, tmp_path / "apercu.jpg")


def test_heic_surdimensionne_est_refuse_avant_conversion(tmp_path, monkeypatch):
    from types import SimpleNamespace

    heic = tmp_path / "grand.heic"
    heic.write_bytes(b"temoin")
    appels = []

    def sips(arguments, **kwargs):
        appels.append(arguments)
        return SimpleNamespace(returncode=0, stdout=f"{heic}:\n  pixelWidth: 10000\n  pixelHeight: 5000\n", stderr="")

    monkeypatch.setattr(retour_photo.subprocess, "run", sips)
    with pytest.raises(ValueError, match="40 mégapixels"):
        retour_photo._apercu(heic, tmp_path / "apercu.jpg")
    assert len(appels) == 1


def test_timeout_heic_devient_refus_metier(tmp_path, monkeypatch):
    import subprocess

    heic = tmp_path / "photo.heic"
    heic.write_bytes(b"temoin")
    def expire(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)
    monkeypatch.setattr(retour_photo.subprocess, "run", expire)
    with pytest.raises(ValueError, match="expirée"):
        retour_photo._apercu(heic, tmp_path / "apercu.jpg")


def test_echec_renommage_ne_supprime_pas_un_dossier_remplace(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    prive = tmp_path / "prive"
    prive.mkdir()
    victime = {}

    def remplacer_stage(stage, destination, *, src_dir_fd, dst_dir_fd):
        archive = "stage-cree-par-nous"
        os.rename(stage, archive, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        os.mkdir(stage, dir_fd=src_dir_fd)
        with os.fdopen(
            os.open(f"{stage}/tiers.txt", os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600, dir_fd=src_dir_fd),
            "w",
        ) as f:
            f.write("ne pas supprimer")
        victime["archive"] = archive
        victime["remplace"] = prive / stage
        raise FileExistsError("destination apparue")

    monkeypatch.setattr(retour_photo, "renommer_exclusif", remplacer_stage)
    with pytest.raises(FileExistsError):
        retour_photo.creer_suggestion(photo, prive / "candidate", prive=prive)
    assert (victime["remplace"] / "tiers.txt").read_text() == "ne pas supprimer"
    assert (prive / victime["archive"] / "suggestion.json").is_file()


@pytest.mark.parametrize("membre", ["photo-source.png", "apercu.jpg", "suggestion.json"])
def test_verification_apres_renommage_refuse_membre_altere_sans_supprimer_sortie(tmp_path, monkeypatch, membre):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    prive = tmp_path / "prive"
    prive.mkdir()
    destination = prive / "candidate"
    renommer_reel = retour_photo.renommer_exclusif
    altered = {}

    def alterer_apres_renommage(stage, cible, *, src_dir_fd, dst_dir_fd):
        renommer_reel(stage, cible, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        fichier = prive / cible / membre
        original = fichier.read_bytes()
        altered["bytes"] = b"x" * len(original)
        fichier.write_bytes(altered["bytes"])

    monkeypatch.setattr(retour_photo, "renommer_exclusif", alterer_apres_renommage)
    with pytest.raises(ValueError, match="SHA|empreinte|diffère|remplacé"):
        retour_photo.creer_suggestion(photo, destination, prive=prive)
    assert destination.is_dir()
    assert (destination / membre).read_bytes() == altered["bytes"]


def test_erreur_avant_renommage_conserve_stage_prive_de_diagnostic(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    prive = tmp_path / "prive"
    prive.mkdir()

    def refus(stage, destination, *, src_dir_fd, dst_dir_fd):
        raise FileExistsError("destination apparue")

    monkeypatch.setattr(retour_photo, "renommer_exclusif", refus)
    with pytest.raises(FileExistsError):
        retour_photo.creer_suggestion(photo, prive / "candidate", prive=prive)
    stages = list(prive.glob(".retour-photo-*"))
    assert len(stages) == 1
    assert (stages[0] / "photo-source.png").is_file()
    assert (stages[0] / "suggestion.json").is_file()


def test_photo_source_remplacee_avant_copie_est_refusee(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (6, 6), "white").save(photo)
    prive = tmp_path / "prive"
    prive.mkdir()
    lier_reel = retour_photo._lier_archive

    def remplacer_source(*args, **kwargs):
        photo.rename(tmp_path / "ancienne-photo.png")
        Image.new("RGB", (6, 6), "black").save(photo)
        return lier_reel(*args, **kwargs)

    monkeypatch.setattr(retour_photo, "_lier_archive", remplacer_source)
    with pytest.raises(ValueError, match="remplacée|modifiée|source"):
        retour_photo.creer_suggestion(photo, prive / "candidate", prive=prive)
    assert not (prive / "candidate").exists()
    assert list(prive.glob(".retour-photo-*"))


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
