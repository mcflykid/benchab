"""La provenance BENCHAB ajoute un témoin neutre sans altérer les autres membres 3MF."""

import hashlib
import json
from zipfile import ZipFile

import pytest

from benchab.fabrication import tracer_3mf

CONTENUS_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '</Types>'
)


def _archive(chemin, *, noms_extra=()):
    """Crée un petit 3MF XML/G-code déterministe pour tester le clone privé."""
    with ZipFile(chemin, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENUS_TYPES)
        archive.writestr("Metadata/project_settings.config", "{}")
        archive.writestr("Metadata/model_settings.config", "<config/>\n")
        archive.writestr("Metadata/plate_1.gcode", "G1 X1 Y1\n")
        for nom in noms_extra:
            archive.writestr(nom, "signature")


def test_trace_cree_une_copie_privee_avec_seules_differences_autorisees(tmp_path):
    """Ajoute la provenance fixe, sans relation ni mutation des données source."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = tmp_path / "source.3mf"
    sortie = prive / "trace.3mf"
    _archive(source)
    source_avant = source.read_bytes()
    with ZipFile(source) as archive:
        hashes = {n: hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist()}

    resultat = tracer_3mf.tracer(source, sortie, prive=prive)

    assert source.read_bytes() == source_avant
    assert resultat["statut"] == "PASS"
    assert resultat["source_sha256"] == hashlib.sha256(source_avant).hexdigest()
    with ZipFile(sortie) as archive:
        assert set(archive.namelist()) == {*hashes, "Metadata/benchab-provenance.json"}
        for nom, attendu in hashes.items():
            if nom != "[Content_Types].xml":
                assert hashlib.sha256(archive.read(nom)).hexdigest() == attendu
        donnees = json.loads(archive.read("Metadata/benchab-provenance.json"))
        assert donnees == {
            "schema": 1, "outil": "BENCHAB", "source_sha256": hashlib.sha256(source_avant).hexdigest()
        }
        contenu_types = archive.read("[Content_Types].xml").decode()
        assert (
            '<Override PartName="/Metadata/benchab-provenance.json" ContentType="application/json"/>'
            in contenu_types
        )
        source_contenus_types = CONTENUS_TYPES.encode()
        assert archive.read("[Content_Types].xml").replace(
            b'<Override PartName="/Metadata/benchab-provenance.json" ContentType="application/json"/>', b""
        ) == source_contenus_types
        assert "benchab-provenance.json.rels" not in archive.namelist()


@pytest.mark.parametrize("membres", [
    ["Metadata/benchab-provenance.json"],
    ["_xmlsignatures/origin.sigs"],
])
def test_refuse_archive_deja_marquee_ou_signee_sans_sortie(tmp_path, membres):
    """Une provenance existante ou une signature ne peut pas être invalidée par le clone."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = tmp_path / "source.3mf"
    sortie = prive / "trace.3mf"
    _archive(source, noms_extra=membres)

    with pytest.raises(ValueError):
        tracer_3mf.tracer(source, sortie, prive=prive)
    assert not sortie.exists()


def test_commande_est_raccordee_au_routeur_fabrication():
    """Expose une commande explicite avec source et sortie obligatoire."""
    from benchab.cli import parser

    arguments = parser().parse_args([
        "fabrication", "tracer-3mf", "source.3mf", "--sortie", "trace.3mf"
    ])
    assert arguments.action == "tracer-3mf"
    assert arguments.arguments == ["source.3mf", "--sortie", "trace.3mf"]


def test_refuse_sortie_existante_sans_la_remplacer(tmp_path):
    """Une sortie préexistante reste byte pour byte intacte."""
    prive = tmp_path / "prive"
    prive.mkdir()
    source = tmp_path / "source.3mf"
    sortie = prive / "trace.3mf"
    _archive(source)
    sortie.write_bytes(b"conserver")

    with pytest.raises(FileExistsError):
        tracer_3mf.tracer(source, sortie, prive=prive)
    assert sortie.read_bytes() == b"conserver"
