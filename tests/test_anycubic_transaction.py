"""Régressions transactionnelles du tranchage avec moteur simulé."""

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest

from benchab.fabrication import anycubic
from benchab.fabrication import trancher as lanceur


def _entree(tmp_path):
    source = tmp_path / "source.3mf"
    source.write_bytes("archive d'entrée synthétique".encode("utf-8"))
    return source


def _sortie(prive):
    return prive / "projets" / "piece" / "preuves" / "tranche.3mf"


def _reponse(code=0, **extras):
    return {"code": code, "duree_s": 0.25, "modifications": [], "diagnostic": {}, **extras}


def test_echec_moteur_ne_publie_aucune_sortie(tmp_path, monkeypatch):
    """Un échec simulé conserve la source et ne crée pas le 3MF final."""
    source = _entree(tmp_path)
    prive = tmp_path / "prive"
    destination = _sortie(prive)
    avant = source.read_bytes()
    appels = []

    def moteur(demande):
        appels.append(demande)
        journal = tmp_path / "slicer.log"
        journal.write_text("moteur simulé en échec", encoding="utf-8")
        return _reponse(1, journal_natif=str(journal))

    monkeypatch.setattr(lanceur, "ECHANGE", tmp_path / "echange")
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    monkeypatch.setattr(lanceur, "_executer", moteur)

    with pytest.raises(RuntimeError, match="Slicer en échec"):
        anycubic.trancher(source, destination.parent, destination.name, prive=prive)

    assert len(appels) == 1
    assert not destination.exists()
    assert source.read_bytes() == avant
    assert list((prive / "mesures/echecs").glob("*/incident.json"))
    rapports = list((prive / "mesures/tranchages").glob("*.json"))
    assert len(rapports) == 1
    assert json.loads(rapports[0].read_text())["code"] == 1


def test_sortie_absente_apres_code_zero_est_refusee(tmp_path, monkeypatch):
    """Un code zéro sans fichier de sortie ne devient pas un succès publiable."""
    source = _entree(tmp_path)
    prive = tmp_path / "prive"
    destination = _sortie(prive)

    monkeypatch.setattr(lanceur, "ECHANGE", tmp_path / "echange")
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    def moteur(demande):
        journal = tmp_path / "slicer.log"
        journal.write_text("aucune sortie", encoding="utf-8")
        return _reponse(journal_natif=str(journal))

    monkeypatch.setattr(lanceur, "_executer", moteur)

    with pytest.raises(RuntimeError, match="Slicer en échec"):
        anycubic.trancher(source, destination.parent, destination.name, prive=prive)

    assert not destination.exists()
    assert source.read_bytes() == "archive d'entrée synthétique".encode("utf-8")


def test_collision_protegee_refusee_avant_moteur(tmp_path, monkeypatch):
    """Une cible livrable existante reste intacte et le moteur ne démarre pas."""
    source = _entree(tmp_path)
    prive = tmp_path / "prive"
    destination = _sortie(prive)
    destination.parent.mkdir(parents=True)
    destination.write_bytes("livrable protégé".encode("utf-8"))
    (destination.parent.parent / "LIVRABLES.json").write_text(
        '{"fichiers":[{"chemin":"preuves/tranche.3mf"}]}', encoding="utf-8"
    )
    appels = []

    monkeypatch.setattr(lanceur, "ECHANGE", tmp_path / "echange")
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    monkeypatch.setattr(lanceur, "_executer", lambda demande: appels.append(demande))

    with pytest.raises(ValueError, match="Destination existante"):
        anycubic.trancher(source, destination.parent, destination.name, prive=prive)

    assert appels == []
    assert destination.read_bytes() == "livrable protégé".encode("utf-8")


def test_mutation_externe_pendant_le_moteur_n_est_pas_ecrasee(tmp_path, monkeypatch):
    """La publication exclusive préserve une cible créée par une autre session."""
    source = _entree(tmp_path)
    prive = tmp_path / "prive"
    destination = _sortie(prive)

    def moteur(demande):
        Path(demande["destination"]).write_bytes(b"sortie temporaire")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"version concurrente")
        journal = tmp_path / "slicer.log"
        journal.write_text("sortie simulée", encoding="utf-8")
        return _reponse(journal_natif=str(journal))

    monkeypatch.setattr(lanceur, "ECHANGE", tmp_path / "echange")
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    monkeypatch.setattr(lanceur, "_executer", moteur)

    with pytest.raises(FileExistsError):
        anycubic.trancher(source, destination.parent, destination.name, prive=prive)

    assert destination.read_bytes() == b"version concurrente"
    rapports = list((prive / "mesures/tranchages").glob("*.json"))
    assert len(rapports) == 1
    rapport = json.loads(rapports[0].read_text())
    assert rapport["code"] == 2
    assert rapport["publication_sortie"]["statut"] == "FAIL"
    assert list((prive / "mesures/echecs").glob("*/sortie-refusee.3mf"))


def test_deux_appels_meme_destination_ne_lancent_qu_un_moteur(tmp_path, monkeypatch):
    """La collision entre deux précontrôles ne doit autoriser qu’un lancement."""
    source = _entree(tmp_path)
    prive = tmp_path / "prive"
    destination = _sortie(prive)
    barriere = Barrier(2)
    verrou = Lock()
    appels = []
    copie = shutil.copy2

    def copie_apres_precontrole(source_fichier, destination_fichier, *args, **kwargs):
        if Path(destination_fichier).name == "source.3mf":
            barriere.wait(timeout=5)
        return copie(source_fichier, destination_fichier, *args, **kwargs)

    def moteur(demande):
        with verrou:
            appels.append(demande)
        Path(demande["destination"]).write_bytes(b"sortie temporaire")
        journal = tmp_path / f"slicer-{len(appels)}.log"
        journal.write_text("sortie simulée", encoding="utf-8")
        return _reponse(journal_natif=str(journal))

    monkeypatch.setattr(lanceur, "ECHANGE", tmp_path / "echange")
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    monkeypatch.setattr(lanceur.shutil, "copy2", copie_apres_precontrole)
    monkeypatch.setattr(lanceur, "_executer", moteur)

    def lancer():
        try:
            return anycubic.trancher(source, destination.parent, destination.name, prive=prive)
        except ValueError as erreur:
            if "Destination apparue avant le moteur" in str(erreur):
                return "collision"
            raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultats = list(pool.map(lambda _: lancer(), range(2)))

    assert sum(resultat != "collision" for resultat in resultats) == 1
    assert len(appels) == 1
    assert destination.read_bytes() == b"sortie temporaire"
    rapports = list((prive / "mesures/tranchages").glob("*.json"))
    assert len(rapports) == 1
    assert json.loads(rapports[0].read_text())["publication_sortie"]["statut"] == "PASS"
