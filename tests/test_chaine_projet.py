"""La chaîne de fabrication conserve les barrières avant tout lancement du moteur."""

import json
from pathlib import Path
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
        return __import__("subprocess").CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(chaine.environnement, "collecter", lambda _: {"code": 0})
    monkeypatch.setattr(chaine.subprocess, "run", construire)
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
    resultat = __import__("subprocess").CompletedProcess([], 1, "", erreur)
    mock = Mock(return_value=resultat)
    monkeypatch.setattr(chaine.subprocess, "run", mock)
    rapport = chaine.executer(projet, prive)
    assert rapport["statut"] == "FAIL" and rapport["etape_bloquante"] == "build"
    assert erreur in Path(rapport["etapes"][-1]["journal"]).read_text()
    assert mock.call_count == 1


def test_coherence_invalide_bloque_tout_script(tmp_path, monkeypatch):
    """Un export natif incohérent donne FAIL sans exécution ; aucune réparation implicite ni unité physique."""
    prive, projet = _projet(tmp_path)
    (prive / "export/projets.jsonl").write_text("altéré")
    mock = Mock(side_effect=AssertionError("aucun programme ne doit être exécuté"))
    monkeypatch.setattr(chaine.subprocess, "run", mock)
    rapport = chaine.executer(projet, prive)
    assert rapport["statut"] == "FAIL" and "Cohérence native" in rapport["erreur"]
    assert not mock.called
