"""Sortie de capacités : le résumé ne remplace pas la preuve complète."""
import json

import pytest

from benchab import cli
from benchab.controles import capacites_machine


def test_cli_resume_capacites_exige_sortie_et_preserve_verdict(tmp_path, capsys, monkeypatch):
    """Un FAIL conserve son code, le détail et sa première localisation."""
    prive = tmp_path / "atelier"
    prive.mkdir()
    source = prive / "fixture.3mf"
    profil = prive / "maitre.json"
    source.write_bytes(b"fixture source")
    profil.write_text("{}")
    resultat = {
        "statut": "FAIL", "decision": "NO GO",
        "entrees": {"tranche": str(source), "tranche_sha256": "a" * 64},
        "plaques": [{"plaque": 1}], "metriques": {"groupes": [{"outil": 0}]},
        "contraintes": [{"statut": "FAIL", "controle": "débit strict", "detail": {"depassements": list(range(500))},
                         "plaque": 1, "lignes": [12, 15], "occurrences": 2, "obligatoire": True}],
    }

    def analyser(archive, *, maitre):
        assert archive == source and maitre == profil
        return resultat

    monkeypatch.setattr(capacites_machine, "analyser", analyser)
    monkeypatch.setattr(cli, "Base", lambda *_a, **_k: pytest.fail("Atelier ouvert"))
    cmd = ["--prive", str(prive), "controle", "capacites-machine", str(source), "--maitre", str(profil)]
    assert cli.main([*cmd, "--resume"]) == 2
    assert "--sortie" in capsys.readouterr().err
    rapport = prive / "capacites.json"
    assert cli.main([*cmd, "--resume", "--sortie", str(rapport)]) == 2
    affichage = capsys.readouterr().out
    resume = json.loads(affichage)
    assert resume["statut"] == "FAIL" and resume["code"] == 2
    assert resume["rapport"] == str(rapport) and resume["sha256"] == "a" * 64
    assert resume["priorites"][0] == {"statut": "FAIL", "controle": "débit strict", "plaque": 1, "ligne": 12}
    assert json.loads(rapport.read_bytes()) == resultat
    assert len(affichage.encode()) < rapport.stat().st_size
    assert not (prive / "benchab.sqlite").exists()
