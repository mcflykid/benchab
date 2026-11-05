"""Contrat de sortie commun des verdicts, sans lancement du slicer."""

import json

import pytest

from benchab import cli
from benchab.controles import controle_envoi, envoi


@pytest.mark.parametrize(("statut", "attendu"), [("PASS", 0), ("WARN", 1), ("FAIL", 2), ("NON VÉRIFIÉ", 2)])
def test_code_envoi_est_celui_du_rapport(statut, attendu, atelier_initialise, tmp_path, monkeypatch, capsys):
    """Exerce l’agrégateur et la CLI d’envoi avec les quatre verdicts, sans accès machine."""
    monkeypatch.setattr(envoi, "identifiants", lambda _: {"statut": statut})
    monkeypatch.setattr(envoi, "commandes_machine", lambda _: {"statut": "PASS"})
    monkeypatch.setattr(envoi, "membres_gcode_3mf", lambda _: [])
    monkeypatch.setattr(envoi, "continuite_paroi", lambda _: {"statut": "PASS"})
    monkeypatch.setattr(controle_envoi, "controler", lambda *args, **kwargs: {"statut": "PASS", "resultats": []})
    code = cli.main([
        "--prive", str(atelier_initialise), "controle", "envoi", str(tmp_path / "fixture.3mf"),
        "--declaration", str(tmp_path / "declaration.json"),
    ])
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["statut"] == statut
    assert code == rapport["code"] == attendu
