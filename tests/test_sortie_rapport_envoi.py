"""Contrat de résumé et de conservation du rapport complet d'envoi."""

import json
from zipfile import ZipFile

from benchab import cli
from benchab.controles import envoi


def _archive(path):
    """Crée un témoin 3MF minimal, sans trancheuse ni réglage personnel."""
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "Metadata/slice_info.config",
            '<config><header><header_item key="X-BBL-Client-Type" value="slicer"/>'
            '</header><plate><metadata key="index" value="1"/></plate></config>',
        )
    return path


def _rapport(monkeypatch):
    """Injecte des diagnostics fixes avec un message exact et un chemin privé."""
    complet = {
        "statut": "FAIL",
        "code": 2,
        "resultats": [
            {"controle": "diagnostic avertissement", "statut": "WARN", "detail": "warning privé exact"},
            {"controle": "diagnostic échec", "statut": "FAIL", "plaque": 2, "detail": "échec exact dans /privé/projet"},
            {"controle": "diagnostic inconnu", "statut": "NON VÉRIFIÉ", "detail": "cause exacte privée"},
        ],
        "fichier": "/privé/projet/source.3mf",
        "source_sha256": "a" * 64,
    }
    monkeypatch.setattr(envoi, "controler", lambda *args, **kwargs: complet)
    return complet


def test_sortie_enregistre_le_complet_et_stdout_priorise_sans_details(
    atelier_initialise, tmp_path, monkeypatch, capsys
):
    complet = _rapport(monkeypatch)
    archive = _archive(tmp_path / "source.3mf")
    sortie = atelier_initialise / "rapports/envoi-complet.json"

    code = cli.main([
        "--prive", str(atelier_initialise), "controle", "envoi", str(archive), "--sortie", str(sortie),
    ])

    stdout = capsys.readouterr().out
    resume = json.loads(stdout)
    assert code == resume["code"] == 2
    assert resume["rapport"] == "rapports/envoi-complet.json"
    assert resume["diagnostics_prioritaires"][0]["plaque"] == 2
    assert [diagnostic["controle"] for diagnostic in resume["diagnostics_prioritaires"]] == [
        "diagnostic échec", "diagnostic inconnu", "diagnostic avertissement",
    ]
    assert "échec exact" not in stdout and "/privé/projet" not in stdout
    assert json.loads(sortie.read_text(encoding="utf-8")) == complet
    assert len(stdout.encode()) < len(sortie.read_bytes())


def test_detail_imprime_le_rapport_complet_et_mode_historique_reste_complet(
    atelier_initialise, tmp_path, monkeypatch, capsys
):
    complet = _rapport(monkeypatch)
    archive = _archive(tmp_path / "source.3mf")
    commun = ["--prive", str(atelier_initialise), "controle", "envoi", str(archive)]

    assert cli.main([*commun, "--detail"]) == 2
    detail = json.loads(capsys.readouterr().out)
    assert detail == complet

    sortie = atelier_initialise / "rapports/envoi-detail.json"
    assert cli.main([*commun, "--sortie", str(sortie), "--detail"]) == 2
    detail_avec_sortie = json.loads(capsys.readouterr().out)
    assert detail_avec_sortie == complet
    assert json.loads(sortie.read_text(encoding="utf-8")) == complet

    assert cli.main(commun) == 2
    historique = json.loads(capsys.readouterr().out)
    assert historique == complet


def test_sortie_existante_est_refusee_sans_ecrasement(atelier_initialise, tmp_path, monkeypatch, capsys):
    _rapport(monkeypatch)
    archive = _archive(tmp_path / "source.3mf")
    sortie = atelier_initialise / "rapports/existant.json"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text("garder", encoding="utf-8")

    code = cli.main([
        "--prive", str(atelier_initialise), "controle", "envoi", str(archive), "--sortie", str(sortie),
    ])
    assert code != 0
    assert sortie.read_text(encoding="utf-8") == "garder"


def test_sortie_hors_espace_prive_est_refusee(atelier_initialise, tmp_path, monkeypatch):
    _rapport(monkeypatch)
    archive = _archive(tmp_path / "source.3mf")
    sortie_publique = tmp_path / "rapport-public.json"

    code = cli.main([
        "--prive", str(atelier_initialise), "controle", "envoi", str(archive), "--sortie", str(sortie_publique),
    ])

    assert code != 0
    assert not sortie_publique.exists()
