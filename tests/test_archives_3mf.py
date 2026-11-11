"""Contrats de lecture bornée des archives 3MF."""

import hashlib
import os
import stat
import zipfile

import pytest

from benchab.archives_3mf import Archive3MF


def _zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)


def test_lecture_bornee_et_source_inchangee(tmp_path):
    path = tmp_path / "projet.3mf"
    _zip(path, [("Metadata/project_settings.config", b'{"layer_height": "0.2"}')])
    avant = hashlib.sha256(path.read_bytes()).hexdigest()
    with Archive3MF(path) as archive:
        assert archive.lire("Metadata/project_settings.config").startswith(b"{")
        assert archive.reglages().get("layer_height") == "0.2"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == avant


def test_repertoires_zip_sont_acceptes(tmp_path):
    path = tmp_path / "directories.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/", b"")
        archive.writestr("Metadata/x", b"x")
    with Archive3MF(path) as archive:
        assert archive.lire("Metadata/x") == b"x"


def test_deux_contextes_sequentiels_reinitialisent_index_et_budget(tmp_path):
    """Le même lecteur reconstruit l'index et son budget à chaque ouverture."""
    path = tmp_path / "reouverture.3mf"
    _zip(path, [("x", b"ABCD")])
    lecteur = Archive3MF(path, taille_max_totale=4)
    for _ in range(2):
        with lecteur as archive:
            assert archive.lire("x") == b"ABCD"


def test_reentree_refusee_ne_ferme_pas_le_flux_actif(tmp_path):
    """Le contexte extérieur conserve son flux après le refus d'une ouverture imbriquée."""
    path = tmp_path / "reentree.3mf"
    _zip(path, [("x", b"ABCD")])
    lecteur = Archive3MF(path)
    with lecteur, lecteur.ouvrir_flux("x") as flux:
        assert flux.read(2) == b"AB"
        with pytest.raises(RuntimeError, match="déjà ouverte"):
            with lecteur:
                pytest.fail("Un même objet ne doit pas ouvrir deux contextes imbriqués")
        assert flux.read() == b"CD"


def test_echec_index_initial_ne_pollue_pas_reouverture(tmp_path):
    """Après un refus d'index, une nouvelle ouverture examine le ZIP courant à neuf."""
    path = tmp_path / "index-refuse.3mf"
    with pytest.warns(UserWarning, match="Duplicate name"):
        _zip(path, [("x", b"avant"), ("x", b"doublon")])
    lecteur = Archive3MF(path)
    with pytest.raises(ValueError, match="dupliqué"):
        with lecteur:
            pytest.fail("L'index invalide ne doit pas être ouvert")
    _zip(path, [("x", b"apres")])
    with lecteur:
        assert lecteur.lire("x") == b"apres"


def test_source_refuse_parent_symbolique_avant_ouverture(tmp_path):
    """Un parent lié ne contourne pas la garde source ; archive réelle intacte."""
    dossier = tmp_path / "reel"
    dossier.mkdir()
    source = dossier / "source.3mf"
    _zip(source, [("x", b"original")])
    avant = source.read_bytes()
    lien = tmp_path / "alias"
    lien.symlink_to(dossier, target_is_directory=True)
    with pytest.raises(ValueError, match="Lien symbolique"):
        with Archive3MF(lien / "source.3mf"):
            pytest.fail("Le parent lié doit être refusé avant lecture")
    assert source.read_bytes() == avant


@pytest.mark.parametrize("noms", [("a", "a/"), ("a/", "a"), ("a", "a/b"), ("a/b", "a")])
def test_refuse_collision_fichier_repertoire(tmp_path, noms):
    """Un même chemin ne peut désigner un fichier et un répertoire implicite ou explicite."""
    source = tmp_path / "ambigu.3mf"
    _zip(source, [(nom, b"") for nom in noms])
    with pytest.raises(ValueError, match="ambigu"):
        with Archive3MF(source):
            pytest.fail("Index fichier/répertoire contradictoire")


def test_contexte_reutilise_une_ouverture_zip(tmp_path, monkeypatch):
    path = tmp_path / "one-open.3mf"
    _zip(path, [("a", b"A"), ("b", b"B")])
    original = zipfile.ZipFile
    appels = []

    def compter(*args, **kwargs):
        appels.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", compter)
    with Archive3MF(path) as archive:
        assert archive.lire("a") == b"A"
        assert archive.lire("b") == b"B"
    assert len(appels) == 1 and hasattr(appels[0], "read")


def test_flux_ouvert_est_ferme_apres_sortie_du_contexte(tmp_path):
    path = tmp_path / "stream.3mf"
    _zip(path, [("a", b"A")])
    with Archive3MF(path) as archive:
        with archive.ouvrir("a") as flux:
            echappe = flux
    assert echappe.closed


def test_archive_ferme_un_flux_dont_le_context_manager_a_fuite(tmp_path):
    path = tmp_path / "escaped-stream.3mf"
    _zip(path, [("a", b"A")])
    with Archive3MF(path) as archive:
        gestionnaire = archive.ouvrir("a")
        echappe = gestionnaire.__enter__()
    assert echappe.closed
    gestionnaire.__exit__(None, None, None)


def test_flux_lit_par_blocs_et_compte_les_relectures(tmp_path):
    path = tmp_path / "stream-bounded.3mf"
    _zip(path, [("gcode", b"abcdef")])
    with Archive3MF(path, taille_max_totale=8) as archive:
        with archive.ouvrir_flux("gcode") as flux:
            assert flux.read(0) == b""
            assert flux.read(2) == b"ab"
            assert flux.read(2) == b"cd"
            assert flux.read(2) == b"ef"
            assert flux.read(2) == b""
        with pytest.raises(ValueError, match="cumul"):
            with archive.ouvrir_flux("gcode") as flux:
                flux.read()


def test_flux_partiel_ne_force_pas_une_seconde_decompression(tmp_path):
    path = tmp_path / "partial.3mf"
    _zip(path, [("gcode", b"abcdef")])
    with Archive3MF(path, taille_max_totale=6) as archive:
        with archive.ouvrir_flux("gcode") as flux:
            assert flux.read(1) == b"a"
        with pytest.raises(ValueError, match="cumul"):
            with archive.ouvrir_flux("gcode"):
                pytest.fail("Le budget déclaré doit être réservé avant toute décompression")


def test_flux_verifie_crc_a_eof_et_se_ferme_apres_contexte(tmp_path):
    path = tmp_path / "crc.3mf"
    _zip(path, [("gcode", b"abcdef")])
    with Archive3MF(path) as archive:
        with archive.ouvrir_flux("gcode") as flux:
            echappe = flux
            assert flux.read() == b"abcdef"
            assert flux.read() == b""
    assert echappe.closed


def test_flux_corrompu_signale_le_crc_a_eof(tmp_path):
    path = tmp_path / "bad-crc.3mf"
    _zip(path, [("gcode", b"abcdef")])
    contenu = bytearray(path.read_bytes())
    debut_index = contenu.index(b"PK\x01\x02")
    contenu[debut_index + 16] ^= 1  # Altère le CRC indexé, sans toucher aux données.
    path.write_bytes(contenu)
    with Archive3MF(path) as archive:
        with archive.ouvrir_flux("gcode") as flux:
            with pytest.raises(zipfile.BadZipFile, match="CRC"):
                while flux.read(2):
                    pass


def test_flux_non_seekable_refuse_le_seek(tmp_path):
    path = tmp_path / "non-seekable.3mf"
    _zip(path, [("gcode", b"abcdef")])
    with Archive3MF(path) as archive:
        with archive.ouvrir_flux("gcode") as flux:
            with pytest.raises((AttributeError, OSError)):
                flux.seek(0)


def test_identite_fichier_reverifiee_a_la_fermeture(tmp_path):
    path = tmp_path / "changed.3mf"
    _zip(path, [("a", b"A")])
    with pytest.raises(ValueError, match="modifiée"):
        with Archive3MF(path):
            path.write_bytes(path.read_bytes() + b"x")


def test_refuse_fifo_et_lien_symbolique_comme_source(tmp_path):
    regular = tmp_path / "source.3mf"
    _zip(regular, [("a", b"A")])
    symlink = tmp_path / "alias.3mf"
    symlink.symlink_to(regular)
    with pytest.raises((OSError, ValueError)):
        Archive3MF(symlink).__enter__()
    fifo = tmp_path / "pipe.3mf"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="fichier régulier"):
        Archive3MF(fifo).__enter__()


@pytest.mark.parametrize("budget", [0, -1, 1.5, True])
def test_budget_doit_etre_entier_positif(budget, tmp_path):
    path = tmp_path / "budget.3mf"
    _zip(path, [("a", b"A")])
    with pytest.raises(ValueError, match="budget"):
        Archive3MF(path, taille_max_membre=budget).__enter__()


@pytest.mark.parametrize("name", ["../x", "/absolute", "C:/x", "a\\..\\x"])
def test_refuse_les_noms_de_membres_non_surs(tmp_path, name):
    path = tmp_path / "unsafe.3mf"
    _zip(path, [(name, b"data")])
    with pytest.raises(ValueError, match="membre"):
        Archive3MF(path).__enter__()


def test_refuse_doublon_et_lien_symbolique(tmp_path):
    duplicate = tmp_path / "duplicate.3mf"
    with pytest.warns(UserWarning, match="Duplicate name"):
        _zip(duplicate, [("x", b"1"), ("x", b"2")])
    with pytest.raises(ValueError, match="dupliqué"):
        Archive3MF(duplicate).__enter__()

    link = tmp_path / "link.3mf"
    with zipfile.ZipFile(link, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")
    with pytest.raises(ValueError, match="lien"):
        Archive3MF(link).__enter__()


def test_refuse_budget_declare_et_flux_reel(tmp_path):
    path = tmp_path / "large.3mf"
    _zip(path, [("payload", b"12345")])
    with pytest.raises(ValueError, match="budget"):
        Archive3MF(path, taille_max_membre=4).__enter__()


def test_refuse_cumul_de_lectures_repetitives(tmp_path):
    path = tmp_path / "repeat.3mf"
    _zip(path, [("payload", b"1234")])
    with Archive3MF(path, taille_max_membre=8, taille_max_totale=6) as archive:
        assert archive.lire("payload") == b"1234"
        with pytest.raises(ValueError, match="cumul"):
            archive.lire("payload")


def test_xml_et_reglages_ont_un_plafond_specifique(tmp_path):
    path = tmp_path / "xml-limit.3mf"
    _zip(path, [("xml", b"<x/>")])
    with Archive3MF(path, taille_max_xml=3) as archive:
        with pytest.raises(ValueError, match="XML"):
            archive.xml("xml")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_refuse_doctype_et_entites_xml_multiencodage(tmp_path, encoding):
    path = tmp_path / f"entity-{encoding}.3mf"
    xml = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'
    _zip(path, [("Metadata/model_settings.config", xml.encode(encoding))])
    with Archive3MF(path) as archive:
        with pytest.raises(ValueError, match="DOCTYPE|ENTITY"):
            archive.xml("Metadata/model_settings.config")


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_refuse_doctype_xml_multiencodage_sans_bom(tmp_path, encoding):
    path = tmp_path / f"entity-no-bom-{encoding}.3mf"
    xml = '<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'
    _zip(path, [("xml", xml.encode(encoding))])
    with Archive3MF(path) as archive:
        with pytest.raises(ValueError, match="DOCTYPE|ENTITY"):
            archive.xml("xml")
