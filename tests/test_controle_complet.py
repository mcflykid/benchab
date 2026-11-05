"""Tests déterministes de l'orchestration complète, sans lancer Ruff, pytest enfant ni slicer."""

import json
from pathlib import Path

import pytest

from benchab.qualite.complet import ORDRE, executer


def _racines(tmp_path):
    racine = tmp_path / "depot"
    prive = tmp_path / "prive"
    racine.mkdir()
    prive.mkdir()
    return racine, prive


def test_ordre_fixe_continue_apres_fail_et_mesure_chaque_etape(tmp_path):
    racine, prive = _racines(tmp_path)
    appels = []

    def faux_executeur(nom):
        appels.append(nom)
        return {"statut": "FAIL" if nom == "coherence" else "PASS", "erreurs": []}

    rapport = executer(racine, prive=prive, executeur=faux_executeur)

    assert appels == list(ORDRE)
    assert [etape["etape"] for etape in rapport["etapes"]] == list(ORDRE)
    assert rapport["statut"] == "FAIL"
    assert all(etape["duree_s"] >= 0 and etape["cpu_s"] >= 0 for etape in rapport["etapes"])
    assert all(
        "rss_max_kib_processus" in etape and "rss_max_kib_etape" in etape and "charge_avant" in etape
        for etape in rapport["etapes"]
    )
    assert all(etape["statut"] == "FAIL" for etape in rapport["etapes"] if etape["etape"] == "coherence")
    assert json.loads(Path(rapport["rapport"]).read_text())["statut"] == "FAIL"
    assert rapport["validation_physique"] is False


def test_non_verifie_reste_non_verifie(tmp_path):
    racine, prive = _racines(tmp_path)
    rapport = executer(
        racine,
        prive=prive,
        executeur=lambda nom: {"statut": "NON VÉRIFIÉ" if nom == "pytest" else "PASS"},
    )
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["etapes"][-1]["statut"] == "NON VÉRIFIÉ"


def test_temoins_exigent_profil_index_et_fixture_avant_creation_rapport(tmp_path):
    racine, prive = _racines(tmp_path)
    with pytest.raises(ValueError, match="profil privé, index filament et fixture"):
        executer(racine, prive=prive, temoins=True, executeur=lambda _nom: {"statut": "PASS"})
    assert not (prive / "qualite/complet").exists()


def test_temoins_sml_mockes_nappellent_aucun_binaire(tmp_path):
    racine, prive = _racines(tmp_path)
    profile = prive / "profil.3mf"
    fixture = prive / "fixture-envoi.json"
    profile.write_bytes("profil explicite réservé au test mock".encode())
    fixture.write_text("{}")
    appels = []

    def faux_executeur(nom):
        appels.append(nom)
        return {"statut": "PASS", "validation_physique": False}

    rapport = executer(
        racine,
        prive=prive,
        temoins=True,
        profil_canonique=profile,
        index_filament=1,
        declaration_envoi=fixture,
        executeur=faux_executeur,
    )

    assert appels == [*ORDRE, "temoin-S", "temoin-M", "temoin-L"]
    assert rapport["statut"] == "PASS"
    assert all(etape["validation_physique"] is False for etape in rapport["etapes"])


def test_statut_inconnu_devient_non_verifie_et_continue(tmp_path):
    racine, prive = _racines(tmp_path)
    appels = []

    def faux_executeur(nom):
        appels.append(nom)
        return {"statut": "INCONNU" if nom == "ruff" else "PASS"}

    rapport = executer(racine, prive=prive, executeur=faux_executeur)
    assert appels == list(ORDRE)
    assert rapport["etapes"][0]["statut"] == "NON VÉRIFIÉ"
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_cli_transmet_les_parametres_sans_lancer_de_machine(tmp_path, monkeypatch, atelier_initialise):
    """La commande publique transmet les chemins et options ; WARN reste code 1, sans slicer ni machine."""
    from benchab import cli
    from benchab.qualite import complet

    appels = []

    def faux(racine, **options):
        appels.append((racine, options))
        return {"statut": "WARN", "rapport": "fixture-rapport.json", "validation_physique": False}

    monkeypatch.setattr(complet, "executer", faux)
    prive = tmp_path / "prive"
    assert (
        cli.main(
            [
                "--prive",
                str(prive),
                "controle",
                "complet",
                "--racine",
                str(tmp_path),
                "--temoins",
                "--profil-canonique",
                str(prive / "profil.3mf"),
                "--index-filament",
                "1",
                "--declaration-envoi",
                str(prive / "fixture.json"),
            ]
        )
        == 1
    )
    assert len(appels) == 1
    assert appels[0][1]["prive"] == prive
    assert appels[0][1]["temoins"] is True


def test_reimpression_controle_cycle_meme_sans_verrou(tmp_path, monkeypatch):
    """Un cycle incohérent reste FAIL sans verrou; aucune référence absente n'est inventée."""
    from benchab.qualite import complet

    prive = tmp_path / 'prive'
    projet = prive / 'projets' / 'piece-synthetique'
    projet.mkdir(parents=True)
    monkeypatch.setattr(complet.reimpression, 'verifier', lambda p: {'statut': 'NON APPLICABLE'})
    monkeypatch.setattr(
        complet.publication, 'verifier', lambda p: {'statut': 'FAIL', 'erreurs': ['référence figée absente']}
    )
    resultat = complet._verifier_reimpressions(prive)
    assert resultat['statut'] == 'FAIL'
    assert resultat['erreurs'] == ['référence figée absente']
    assert resultat['projets'][0]['verrou']['statut'] == 'NON APPLICABLE'


def test_runner_borne_enfants_et_garde_journal_complet(tmp_path):
    """Un délai en s échoue à 124 et arrête les descendants ; logs complets conservés sans toucher un service."""
    import sys

    from benchab.processus import _actif
    from benchab.qualite.complet import _lancer

    journal = tmp_path / "pytest.log"
    programme = ("import subprocess,sys,time;print('X'*100000,flush=True);"
                 "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);time.sleep(30)")
    r = _lancer("pytest", [sys.executable, "-B", "-c", programme], tmp_path, journal, delai=.5)
    assert r["statut"] == "FAIL" and r["code"] == 124
    d = json.loads(Path(r["diagnostic_processus"]).read_text())
    assert not _actif(d["pid"])
    assert journal.read_text().startswith("X" * 100000)
    assert "délai" in journal.read_text()
    assert r["delai_secondes"] == .5


def test_runner_code_cinq_ne_devient_pas_pass(tmp_path):
    """Le code pytest de découverte vide (5) reste FAIL ; stdout complet et code original conservés."""
    import sys

    from benchab.qualite.complet import _lancer

    r = _lancer("pytest", [sys.executable, "-B", "-c", "import sys;sys.exit(5)"],
                tmp_path, tmp_path / "pytest.log")
    assert r["statut"] == "FAIL" and r["code"] == r["code_processus"] == 5


def test_incident_initial_temoin_preserve_atelier_et_preuve_ancienne(tmp_path, monkeypatch):
    """L'exception de création reste FAIL avec journal exact et atelier conservé ; aucun moteur lancé."""
    from benchab import reglages
    from benchab.qualite import complet

    racine, prive = _racines(tmp_path)
    profil, declaration = prive / "profil.3mf", prive / "declaration.json"
    profil.write_bytes(b"profil synthetique")
    source = {
        "filament_ids": ["fixture-id"], "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"],
        "filament_settings_id": ["fixture"], "nozzle_temperature": ["220"],
        "nozzle_temperature_initial_layer": ["220"], "textured_plate_temp": ["55"],
        "filament_max_volumetric_speed": ["16"],
    }
    declaration.write_text(json.dumps({"filaments": {"1": {"id": "fixture-id", "type": "PLA", "color": "#FFFFFF",
        "settings_id": "fixture", "temp": 220, "temp_1re": 220, "plateau_c": 55, "debit_max": 16}}}))
    monkeypatch.setattr(reglages, "lire_3mf", lambda _: reglages.Reglages(source))
    ancien = prive / "qualite/complet/ancien/rapport.json"
    ancien.parent.mkdir(parents=True)
    ancien.write_bytes(b"preuve ancienne")

    def refuser(*_args, **_kwargs):
        raise OSError("incident avant résultat")

    monkeypatch.setattr(complet, "creer", refuser)
    atelier = prive / "temoin-defaut"

    def etape(nom):
        if nom == "temoin-S":
            return complet._temoin_niveau("S", prive, profil, 1, declaration, atelier)
        return {"statut": "PASS"}

    resultat = executer(racine, prive=prive, temoins=True, profil_canonique=profil,
                        index_filament=1, declaration_envoi=declaration, executeur=etape)
    echec = next(e for e in resultat["etapes"] if e["etape"] == "temoin-S")
    assert resultat["statut"] == echec["statut"] == "FAIL"
    assert "incident avant résultat" in Path(echec["journal"]).read_text()
    assert (atelier / "prive/benchab.sqlite").is_file()
    assert ancien.read_bytes() == b"preuve ancienne"
