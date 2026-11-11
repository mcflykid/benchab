"""Contrats de lecture bornée des références de fabrication sur archives jetables."""

import hashlib
import json
from zipfile import ZipFile

import pytest

from benchab.qualite import references_or
from benchab.qualite.references_or import relever


def _archive(chemin, membres, *, reglages=b"{}"):
    """Écrit un ZIP temporaire sans données personnelles."""
    with ZipFile(chemin, "w") as archive:
        archive.writestr("Metadata/project_settings.config", reglages)
        for nom, contenu in membres:
            archive.writestr(nom, contenu)


def test_relever_hache_les_commandes_sans_commentaires_et_garde_le_format(tmp_path):
    """Le hash garde le contrat historique : lignes de code nettoyées puis terminées par LF."""
    source = tmp_path / "reference.3mf"
    gcode = b"G90 ; absolu\r\n; commentaire\nM83\n  G1 X1 E1 ; depot\r\n\n"
    _archive(
        source,
        [("Metadata/plate_2.gcode", gcode)],
        reglages=json.dumps({
            "printer_model": "Anycubic Kobra S1",
            "machine_max_speed_x": ["600", "300", "780"],
        }).encode("utf-8"),
    )

    resultat = relever(source)

    assert resultat["commandes"] == {
        "Metadata/plate_2.gcode": {
            "sha256": hashlib.sha256(b"G90\nM83\nG1 X1 E1\n").hexdigest(),
            "nombre": 3,
        }
    }
    assert resultat["reglages"]["projet/machine_max_speed_x"] == ["600", "300", "780"]


def test_relever_refuse_un_membre_gcode_duplique(tmp_path):
    """Une archive avec le même nom G-code deux fois ne peut pas écraser la première entrée."""
    source = tmp_path / "doublon.3mf"
    with pytest.warns(UserWarning, match="Duplicate name"):
        _archive(source, [
            ("Metadata/plate_1.gcode", b"G1 X1\n"),
            ("Metadata/plate_1.gcode", b"G1 X2\n"),
        ])

    with pytest.raises(ValueError, match="dupliqué"):
        relever(source)


def test_relever_refuse_indices_de_plaque_ambigus(tmp_path):
    """Les graphies plate_1 et plate_01 ne sont pas fusionnées sous un index unique."""
    source = tmp_path / "indices.3mf"
    _archive(source, [
        ("Metadata/plate_1.gcode", b"G1 X1\n"),
        ("Metadata/plate_01.gcode", b"G1 X1\n"),
    ])

    with pytest.raises(ValueError, match="numéro de plaque dupliqué"):
        relever(source)


def test_relever_refuse_source_changee_pendant_lecture_secondaire(tmp_path, monkeypatch):
    """La source reste liée au premier contexte quand les réglages sont relus par une autre fonction."""
    source = tmp_path / "mutation.3mf"
    _archive(source, [])

    def muter(chemin):
        chemin.write_bytes(chemin.read_bytes() + b"modification concurrente")
        return {}

    monkeypatch.setattr(references_or, "instantane", muter)

    with pytest.raises(ValueError, match="modifiée|différente|changé"):
        relever(source)


def test_relever_refuse_ligne_gcode_au_dela_du_budget(tmp_path):
    """Une ligne G-code supérieure à un Mio est refusée sans changer le hachage des lignes courtes."""
    source = tmp_path / "ligne-longue.3mf"
    gcode = b"G1 X" + b"1" * (1024 * 1024) + b"\n"
    _archive(source, [("Metadata/plate_1.gcode", gcode)])

    with pytest.raises(ValueError, match="ligne G-code dépasse le budget"):
        relever(source)
