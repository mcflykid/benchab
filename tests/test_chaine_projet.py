"""La chaîne de fabrication conserve les barrières avant tout lancement du moteur."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from benchab.donnees.base import Base
from benchab.fabrication import chaine


def _projet(tmp_path):
    prive = tmp_path / "prive"
    base = Base(prive)
    base.fermer()
    projet = prive / "projets/demo"
    projet.mkdir(parents=True)
    return prive, projet


def test_echec_geometrique_arrete_avant_preparer_et_trancher(tmp_path, monkeypatch):
    """Injecte FAIL géométrique, retour FAIL sans slicer ; chemins temporaires, aucune unité modifiée."""
    prive, projet = _projet(tmp_path)
    commandes = []

    def construire(cmd, **_options):
        commandes.append(cmd)
        (projet / "candidats").mkdir()
        (projet / "candidats/piece.stl").write_text("fixture non lue par le contrôle injecté")
        return SimpleNamespace(returncode=0, stdout="", stderr="", diagnostic=None, raison="TERMINE", resultat="PASS")

    monkeypatch.setattr(chaine.environnement, "collecter", lambda _: {"code": 0})
    monkeypatch.setattr(chaine.processus, "executer", construire)
    monkeypatch.setattr(chaine.controles_piece, "main", lambda _: 2)
    rapport = chaine.executer(projet, prive)
    assert rapport["statut"] == "FAIL" and rapport["etape_bloquante"] == "geometrie"
    assert [c[-1] for c in commandes] == ["src.construire"]
    assert json.loads(Path(rapport["rapport"]).read_text()) == rapport


def test_exception_script_code_un_est_fail_pas_warn(tmp_path, monkeypatch):
    """Un script en erreur code 1 reste FAIL ; garde l’erreur exacte en log, sans slicer ni changement."""
    prive, projet = _projet(tmp_path)
    erreur = "ValueError: dimension invalide à src/modele.py:12"
    monkeypatch.setattr(chaine.environnement, "collecter", lambda _: {"code": 0})
    resultat = SimpleNamespace(
        returncode=1, stdout="", stderr=erreur, diagnostic=None, raison="TERMINE", resultat="FAIL",
    )
    mock = Mock(return_value=resultat)
    monkeypatch.setattr(chaine.processus, "executer", mock)
    rapport = chaine.executer(projet, prive)
    assert rapport["statut"] == "FAIL" and rapport["etape_bloquante"] == "build"
    assert erreur in Path(rapport["etapes"][-1]["journal"]).read_text()
    assert mock.call_count == 1


def test_coherence_invalide_bloque_tout_script(tmp_path, monkeypatch):
    """Un export natif incohérent donne FAIL sans exécution ; aucune réparation implicite ni unité physique."""
    prive, projet = _projet(tmp_path)
    (prive / "export/projets.jsonl").write_text("altéré")
    mock = Mock(side_effect=AssertionError("aucun programme ne doit être exécuté"))
    monkeypatch.setattr(chaine.processus, "executer", mock)
    rapport = chaine.executer(projet, prive)
    assert rapport["statut"] == "FAIL" and "Cohérence native" in rapport["erreur"]
    assert not mock.called

def test_delai_gabarit_arrete_la_chaine_non_verifie(tmp_path, monkeypatch):
    """Un gabarit bloqué est interrompu sans lancer les étapes suivantes."""
    import sys

    prive, projet = _projet(tmp_path)
    original = chaine.processus.executer
    commandes = []

    def dormir(cmd, **options):
        commandes.append(cmd[-1])
        assert options["delai"] == 0.05
        return original([sys.executable, "-c", "import time; time.sleep(10)"], **options)

    monkeypatch.setattr(chaine.environnement, "collecter", lambda _: {"code": 0})
    monkeypatch.setattr(chaine, "DELAI_SCRIPT_S", 0.05)
    monkeypatch.setattr(chaine.processus, "executer", dormir)
    rapport = chaine.executer(projet, prive)
    assert rapport["statut"] == "NON VÉRIFIÉ" and rapport["etape_bloquante"] == "build"
    assert rapport["etapes"][-1]["statut"] == "NON VÉRIFIÉ"
    assert commandes == ["src.construire"]
