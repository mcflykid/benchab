"""Les rapports de supports et de réglages ne remplacent aucune entrée."""

import json
from zipfile import ZipFile

import pytest

from benchab.controles import supports_requis, verifier_reglages


def test_rapport_supports_identique_a_source_refuse_avant_tranchage(tmp_path, monkeypatch):
    """Le chemin est vérifié avant l’appel au moteur, même pour un verdict PASS."""
    source = tmp_path / "support.3mf"
    source.write_bytes(b"source")
    appels = []

    def mesurer(*args):
        appels.append(args)
        return {"plaques": [], "statut": "PASS"}

    monkeypatch.setattr(supports_requis, "mesurer", mesurer)
    assert supports_requis.main([str(source), str(source)]) == 2
    assert source.read_bytes() == b"source"
    assert not appels


def test_reglages_protege_3mf_et_placement_et_ecrit_une_preuve_neuve(tmp_path):
    """Les deux entrées sont préservées et le format JSON du rapport reste exploitable."""
    source = tmp_path / "reglages.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", "{}")
    placement = tmp_path / "placement.json"
    placement.write_text('{"plates":[],"objects":[]}', encoding="utf-8")
    avant = [p.read_bytes() for p in (source, placement)]

    for destination in (source, placement):
        assert verifier_reglages.main([str(source), str(placement), str(destination)]) == 2
        assert [p.read_bytes() for p in (source, placement)] == avant

    preuve = tmp_path / "rapport.json"
    assert verifier_reglages.main([str(source), str(placement), str(preuve)]) == 1
    rapport = json.loads(preuve.read_bytes())
    assert rapport["source"] == source.name and rapport["tout_conforme"] is False
    assert [p.read_bytes() for p in (source, placement)] == avant


def test_gcode_de_supports_protege_toutes_les_preuves_avant_publication(tmp_path):
    """Une plaque existante bloque le lot neuf sans toucher aux autres plaques."""
    source = tmp_path / "support.3mf"
    source.write_bytes(b"source")
    travail = tmp_path / "gcode"
    travail.mkdir()
    plaques = [travail / f"plaque-{i}.gcode" for i in (1, 2)]
    for plaque in plaques:
        plaque.write_text("G1 X1\n", encoding="utf-8")
    garde = tmp_path / "garde"
    garde.mkdir()
    ancien = garde / plaques[1].name
    ancien.write_text("preuve existante", encoding="utf-8")

    with pytest.raises(FileExistsError):
        supports_requis._publier_gcodes(source, garde, plaques)

    assert ancien.read_text(encoding="utf-8") == "preuve existante"
    assert not (garde / plaques[0].name).exists()

    garde_neuve = tmp_path / "garde-neuve"
    supports_requis._publier_gcodes(source, garde_neuve, plaques)
    assert [(garde_neuve / p.name).read_bytes() for p in plaques] == [p.read_bytes() for p in plaques]
    assert source.read_bytes() == b"source"
