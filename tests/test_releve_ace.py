"""Un relevé ACE reste une estimation séparée du stock pesé."""

import json

import pytest

from benchab.cli import main
from benchab.donnees import releve_ace


def _fiche(**extra):
    """Construit un relevé utilisateur de 24 % sans inventer de masse nominale."""
    return {"position_ace": 1, "matiere": "PLA", "restant_pct": 24, "date": "2026-11-13T15:00:00+01:00",
            "source": "observation_utilisateur", **extra}


def test_releve_ace_sans_masse_nominale():
    assert releve_ace.estimer(_fiche())["masse_estimee_g"] is None


def test_releve_ace_marge_faible_conserve_incertitude_et_stock(tmp_path):
    entree = tmp_path / "ace.json"
    entree.write_text(json.dumps(_fiche(masse_nominale_g=1000)))
    sortie = tmp_path / "estimation.json"
    assert main(["donnees", "releve-ace", str(entree), "--requis-g", "230", "--marge-g", "25",
                 "--sortie", str(sortie)]) == 1
    r = json.loads(sortie.read_text())
    assert r["statut"] == "WARN"
    assert r["masse_estimee_g"] == 240 and r["marge_estimee_g"] == 10
    assert r["incertitude"] == "NON VÉRIFIÉE"
    assert not (tmp_path / "prive" / "benchab.sqlite").exists()


@pytest.mark.parametrize("valeur", [True, -1, 101, float("nan")])
def test_releve_ace_refuse_pourcentage_invalide(valeur):
    with pytest.raises(ValueError):
        releve_ace.estimer(_fiche(restant_pct=valeur))
