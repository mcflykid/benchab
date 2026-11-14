"""Aides publiques complètes, sans atelier ni exécution des contrôles métier."""

import pytest

from benchab import cli


@pytest.mark.parametrize(
    "commande,attendus",
    [
        (["controle", "reglages"], ["placement.json", "sortie.json"]),
        (["controle", "debit-gcode"], ["source", "--plafonds", "--sortie"]),
        (["controle", "revue-locale"], ["--envoi", "--revue", "--appliquer"]),
        (["controle", "finition-parois"], ["analyser", "source", "--maitre", "--seuil-delta-mm-s"]),
        (["fabrication", "optimisation"], ["proposer", "comparer", "--projet", "--sans-tranchage"]),
        (["fabrication", "preparation"], ["creer", "--plaques", "--tranche"]),
    ],
)
@pytest.mark.parametrize("aide", ["--help", "-h"])
def test_aide_deleguee_decrit_route_sans_ouvrir_atelier(commande, attendus, aide, tmp_path, monkeypatch, capsys):
    """L’aide rend 0, expose le contrat métier et ne crée aucun atelier ni rapport."""
    def ouverture_interdite(*args, **kwargs):
        raise AssertionError("Une aide ne doit pas ouvrir un atelier")

    monkeypatch.setattr(cli, "Base", ouverture_interdite)
    assert cli.main([*commande, aide]) == 0
    texte = capsys.readouterr().out
    for attendu in attendus:
        assert attendu in texte
    assert not (tmp_path / "prive").exists()


def test_aide_sous_commande_deleguee_conserve_positionnels(capsys):
    """L’aide après une sous-commande expose le projet et rend 0 sans fabrication."""
    assert cli.main(["fabrication", "optimisation", "proposer", "--help"]) == 0
    assert "--projet" in capsys.readouterr().out
