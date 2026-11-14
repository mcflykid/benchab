"""Régressions des délais sur le pipeline ancien et les consultations Git locales."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from benchab import processus
from benchab.connaissances import memoire
from benchab.qualite import frontiere, tout


def test_pipeline_sans_delai_explicite_est_borne(tmp_path, monkeypatch):
    """Une étape historique sans délai doit rendre 124 et expliquer le dépassement en secondes."""
    monkeypatch.setattr(tout, "DELAI_ETAPE_SECONDES", 0.07)
    journal = tmp_path / "etape.log"
    commande = [sys.executable, "-c", "import time; time.sleep(30)"]
    assert tout.executer_commande(commande, tmp_path, journal, {"id": "fixture"}) == 124
    assert "Délai de l’étape dépassé" in journal.read_text(encoding="utf-8")


def test_index_git_bloque_refuse_la_frontiere(tmp_path, monkeypatch):
    """Un délai de Git rend un défaut de frontière, sans lancer Git ni consulter un dépôt distant."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(processus, "executer", lambda *a, **kw: SimpleNamespace(ok=False, code_controle=124))
    erreurs = frontiere.verifier(tmp_path)
    assert erreurs == ["Délai de lecture de l’index Git dépassé : frontière non vérifiée"]


def test_git_blob_ne_bloque_pas_la_lecture(tmp_path, monkeypatch):
    """Un enfant silencieux est interrompu au délai global, aucune lecture Git réelle."""
    popen = subprocess.Popen
    enfants = []

    def demarrer(commande, *args, **options):
        if commande[0] == "git":
            p = popen([sys.executable, "-c", "import time; time.sleep(30)"], *args, **options)
            enfants.append(p)
            return p
        return popen(commande, *args, **options)

    monkeypatch.setattr(memoire.subprocess, "Popen", demarrer)
    monkeypatch.setattr(memoire, "DELAI_BLOB_GIT_SECONDES", 0.07)
    with pytest.raises(subprocess.TimeoutExpired):
        memoire._empreinte_blob_git(tmp_path, "fixture", {})
    assert len(enfants) == 1 and enfants[0].poll() is not None
