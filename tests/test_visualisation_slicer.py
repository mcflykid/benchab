"""Le superviseur conserve le verrou pendant toute la vie du moteur graphique."""

import errno
import fcntl
import json
import struct
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import pytest

from benchab.fabrication import trancher as T


def archive(path, tranche=True):
    with ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", '<model unit="millimeter"/>')
        z.writestr("Metadata/model_settings.config", "<config><plate/></config>")
        if tranche:
            z.writestr("Metadata/plate_1.gcode", "; fixture générée\nG1 X1 Y1 E1\n")
    return path


def test_superviseur_garde_verrou_et_configuration_isolee(tmp_path, monkeypatch):
    prive = tmp_path / "prive"
    prive.mkdir()
    source = archive(prive / "piece.3mf")
    avant = source.read_bytes()
    accuse = prive / "accuse.json"
    monkeypatch.setattr(T, "ECHANGE", tmp_path / "echange")
    commandes = []

    class Moteur:
        pid = 12345

        def __init__(self, commande, **options):
            commandes.append(commande)
            assert "--datadir" in commande and "--outputdir" in commande
            assert Path(commande[-1]).read_bytes() == avant
            for cle in ("HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
                assert Path(options["env"][cle]).is_relative_to("/private/tmp")
                assert Path(options["env"][cle]).is_dir()
            assert Path(commande[commande.index("--datadir") + 1]).is_dir()

        def wait(self):
            assert json.loads(accuse.read_text())["ouverture"] == "DEMANDEE"
            with (T.ECHANGE / "execution.lock").open("a") as autre:
                with pytest.raises(OSError) as erreur:
                    fcntl.flock(autre, fcntl.LOCK_EX | fcntl.LOCK_NB)
                assert erreur.value.errno in (errno.EAGAIN, errno.EACCES)
            return 0

    monkeypatch.setattr(T.subprocess, "Popen", Moteur)
    assert (
        T.main(
            ["--visualiser", str(source), "--prive", str(prive), "--accuse", str(accuse), "--sha256", T.sha256(source)]
        )
        == 0
    )
    assert len(commandes) == 1
    assert source.read_bytes() == avant
    assert json.loads(accuse.with_suffix(".fin.json").read_text())["modifications"] == []
    with (T.ECHANGE / "execution.lock").open("a") as libre:
        fcntl.flock(libre, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_ouvrir_refuse_fichier_non_tranche_ou_change(tmp_path, monkeypatch):
    prive = tmp_path / "prive"
    prive.mkdir()
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: pytest.fail("Aucun lancement attendu"))
    brut = archive(prive / "brut.3mf", False)
    with pytest.raises(ValueError, match="déjà tranché"):
        T.demander_ouverture(brut, prive=prive)
    tranche = archive(prive / "tranche.3mf")
    with pytest.raises(ValueError, match="préparation contrôlée"):
        T.demander_ouverture(tranche, prive=prive, sha_attendu="0" * 64)


def test_visualisation_refuse_alias_numeriques_de_plaque(tmp_path):
    """Deux graphies pour le même numéro ne doivent pas passer pour deux plaques valides."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = prive / "alias.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/model_settings.config", "<config><plate/></config>")
        archive.writestr("Metadata/plate_01.gcode", "G90\n")
        archive.writestr("Metadata/plate_1.gcode", "G91\n")

    with pytest.raises(ValueError, match="numéro de plaque dupliqué"):
        T._fichier_visualisable(source, prive)


def test_visualisation_accepte_une_graphie_rembourree_unique(tmp_path):
    """Un seul G-code rembourré conserve son numéro numérique conventionnel."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = prive / "rembourree.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/model_settings.config", "<config><plate/></config>")
        archive.writestr("Metadata/plate_01.gcode", "G90\n")

    assert T._fichier_visualisable(source, prive) == source


@pytest.mark.parametrize(
    "noms_gcode",
    [
        ("Metadata/plate_1.gcode",),
        ("Metadata/plate_1.gcode", "Metadata/plate_3.gcode"),
    ],
)
def test_visualisation_refuse_compte_ou_indices_incoherents(tmp_path, noms_gcode):
    """Les G-code doivent couvrir exactement les plaques XML de 1 à N."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = prive / "incoherente.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/model_settings.config", "<config><plate/><plate/></config>")
        for nom in noms_gcode:
            archive.writestr(nom, "G90\n")

    with pytest.raises(ValueError, match="G-code et plaques déclarées incohérents"):
        T._fichier_visualisable(source, prive)


def test_visualisation_refuse_plus_de_30_gcodes(tmp_path):
    """La limite s’applique aussi au nombre de G-code, avant l’ouverture du moteur."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = prive / "trente-et-un-gcodes.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/model_settings.config", "<config><plate/></config>")
        for numero in range(1, 32):
            archive.writestr(f"Metadata/plate_{numero}.gcode", "G90\n")

    with pytest.raises(ValueError, match="entre 1 et 30 plaques"):
        T._fichier_visualisable(source, prive)


@pytest.mark.parametrize(
    "configuration",
    [
        '<!DOCTYPE config [<!ENTITY p "x">]><config><plate/></config>',
        "<autre><plate/></autre>",
    ],
)
def test_visualisation_exige_xml_config_protege(tmp_path, configuration):
    """Les plate-nodes d’un XML non conforme ou à entité ne suffisent pas."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = prive / "xml-invalide.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/model_settings.config", configuration)
        archive.writestr("Metadata/plate_1.gcode", "G90\n")

    with pytest.raises(ValueError, match="Structure des plaques absente ou invalide"):
        T._fichier_visualisable(source, prive)


def _corrompre_crc(path, membre_cible):
    """Corrompt le CRC central d’un membre ciblé sans changer son contenu décompressé."""
    brut = bytearray(path.read_bytes())
    position = 0
    while (position := brut.find(b"PK\x01\x02", position)) >= 0:
        longueur_nom, longueur_extra, longueur_commentaire = struct.unpack_from("<HHH", brut, position + 28)
        debut = position + 46
        nom = bytes(brut[debut:debut + longueur_nom]).decode("ascii")
        if nom == membre_cible:
            crc = struct.unpack_from("<I", brut, position + 16)[0]
            struct.pack_into("<I", brut, position + 16, crc ^ 1)
            path.write_bytes(brut)
            return
        position = debut + longueur_nom + longueur_extra + longueur_commentaire
    raise AssertionError(f"Membre absent : {membre_cible}")


@pytest.mark.parametrize("membre", ["Metadata/plate_1.gcode", "Metadata/ignored.bin"])
def test_visualisation_verifie_crc_gcode_et_membres_ignores(tmp_path, membre):
    """La validation consomme aussi les G-code et membres non exploités jusqu’à EOF."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = prive / "crc.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/model_settings.config", "<config><plate/></config>")
        archive.writestr("Metadata/plate_1.gcode", "G90\n")
        archive.writestr("Metadata/ignored.bin", b"membre conserve")
    _corrompre_crc(source, membre)

    with pytest.raises(BadZipFile):
        T._fichier_visualisable(source, prive)


def test_visualisation_ne_melange_pas_les_inconnues_de_sources_distinctes(tmp_path, monkeypatch):
    """Les échecs inconnus de visualisation ne bloquent que la même source répétée trois fois."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = archive(prive / "tranche.3mf")
    source_sha = T.sha256(source)
    dossiers = prive / "mesures/echecs"
    for indice in range(3):
        incident = dossiers / str(indice) / "incident.json"
        incident.parent.mkdir(parents=True)
        incident.write_text(json.dumps({
            "mode": "visualisation", "code": -11, "source_sha256": f"{indice + 1:064x}",
            "diagnostic": {"cause": "inconnue"},
        }), encoding="utf-8")
    monkeypatch.setattr(T, "ECHANGE", tmp_path / "echange")
    accuse = prive / "accuse.json"
    moteurs = []

    class Moteur:
        pid = 12345

        def __init__(self, *args, **kwargs):
            moteurs.append(args)

        def wait(self):
            return 0

    monkeypatch.setattr(T.subprocess, "Popen", Moteur)
    assert T.main([
        "--visualiser", str(source), "--prive", str(prive), "--accuse", str(accuse), "--sha256", source_sha,
    ]) == 0
    assert len(moteurs) == 1
    assert source.exists()


def test_visualisation_bloque_trois_inconnues_de_la_meme_source(tmp_path, monkeypatch):
    """Trois incidents inconnus au même SHA refusent la visualisation avant le moteur."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = archive(prive / "tranche.3mf")
    source_sha = T.sha256(source)
    dossiers = prive / "mesures/echecs"
    for indice in range(3):
        incident = dossiers / str(indice) / "incident.json"
        incident.parent.mkdir(parents=True)
        incident.write_text(json.dumps({
            "mode": "visualisation", "code": -11, "source_sha256": source_sha,
            "diagnostic": {"cause": "inconnue"},
        }), encoding="utf-8")
    monkeypatch.setattr(T, "ECHANGE", tmp_path / "echange")
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **k: pytest.fail("Aucun lancement attendu"))
    accuse = prive / "accuse.json"

    assert T.main([
        "--visualiser", str(source), "--prive", str(prive), "--accuse", str(accuse), "--sha256", source_sha,
    ]) == 2
    assert json.loads(accuse.read_text())["code"] == 2


def test_visualisation_ne_compte_pas_les_inconnues_de_tranchage(tmp_path, monkeypatch):
    """Le même SHA ne mélange pas une opération de tranchage avec une ouverture graphique."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = archive(prive / "tranche.3mf")
    source_sha = T.sha256(source)
    dossiers = prive / "mesures/echecs"
    for indice in range(3):
        incident = dossiers / str(indice) / "incident.json"
        incident.parent.mkdir(parents=True)
        incident.write_text(json.dumps({
            "incident": {"operation": "tranchage"}, "code": 250, "source_sha256": source_sha,
            "diagnostic": {"cause": "inconnue"},
        }), encoding="utf-8")
    monkeypatch.setattr(T, "ECHANGE", tmp_path / "echange")
    accuse = prive / "accuse.json"
    moteurs = []

    class Moteur:
        pid = 12345

        def __init__(self, *args, **kwargs):
            moteurs.append(args)

        def wait(self):
            return 0

    monkeypatch.setattr(T.subprocess, "Popen", Moteur)
    assert T.main([
        "--visualiser", str(source), "--prive", str(prive), "--accuse", str(accuse), "--sha256", source_sha,
    ]) == 0
    assert len(moteurs) == 1


def test_aucune_session_installee_lue_implicitement(monkeypatch):
    from benchab.machine import cloud_lecture as C

    monkeypatch.setattr(C, "_local_json", lambda *a: pytest.fail("Aucune lecture implicite"))
    for fonction in (C.session_slicer, C.token_slicer):
        with pytest.raises(C.CloudErreur, match="COPIE_SESSION_EXPLICITE_REQUISE"):
            fonction()


def test_31_plaques_xml_refusees_meme_avec_un_seul_gcode(tmp_path, monkeypatch):
    prive = tmp_path / "prive"
    prive.mkdir()
    fichier = prive / "trop-de-plaques.3mf"
    with ZipFile(fichier, "w") as z:
        z.writestr("Metadata/plate_1.gcode", "; fixture")
        z.writestr("Metadata/model_settings.config", "<config>" + "<plate/>" * 31 + "</config>")
    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: pytest.fail("Aucun lancement attendu"))
    with pytest.raises(ValueError, match="30 plaques déclarées"):
        T.demander_ouverture(fichier, prive=prive)
