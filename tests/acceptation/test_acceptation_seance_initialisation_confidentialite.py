"""Socle installé, données temporaires, mesures réelles et frontière publique."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from benchab.cli import main
from benchab.donnees.base import Base
from benchab.machine.configuration import charger
from benchab.qualite.frontiere import appels_interdits, verifier


def cli(tmp_path, *arguments, jeton=None):
    """Lance la CLI en atelier temporaire ; jeton transmis hors argv, aucune machine."""
    prive = tmp_path / "prive"
    environnement = dict(os.environ, BENCHAB_PRIVE=str(prive), PYTHONDONTWRITEBYTECODE="1")
    environnement.pop("BENCHAB_JETON_SEANCE", None)
    if jeton is not None:
        environnement["BENCHAB_JETON_SEANCE"] = jeton
    return subprocess.run(
        [sys.executable, "-m", "benchab.cli", *arguments],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        check=False,
    )


def test_init_help_et_commande_mesuree_dans_un_vrai_sous_processus(tmp_path):
    """Aide sans écriture puis trois commandes mesurées en s/octets ; aucune machine touchée."""
    assert cli(tmp_path, "--help").returncode == 0
    assert not (tmp_path / "prive").exists()
    assert cli(tmp_path, "init").returncode == 0
    assert cli(tmp_path, "configuration").returncode == 0
    resultat = cli(tmp_path, "etat")
    assert resultat.returncode == 0, resultat.stderr
    assert json.loads(resultat.stdout)["projets"] == 0
    with sqlite3.connect(tmp_path / "prive/benchab.sqlite") as connexion:
        mesures = connexion.execute("SELECT commande, duree_s, memoire_max_octets, code FROM mesures").fetchall()
    assert len(mesures) == 3
    assert {m[0] for m in mesures} == {"configuration", "init", "etat"}
    assert all(m[1] >= 0 and m[2] > 0 and m[3] == 0 for m in mesures)
    assert (tmp_path / "prive/export/mesures.jsonl").is_file()
    assert len((tmp_path / "prive/export/mesures.jsonl").read_text().splitlines()) == 3


def test_init_conserve_donnees_et_refuse_base_etrangere(tmp_path):
    base = Base(tmp_path / "prive")
    base.ecrire("INSERT INTO projets (id, nom) VALUES ('temoin', 'Pièce témoin')")
    base.fermer()
    assert cli(tmp_path, "init").returncode == 0
    base = Base(tmp_path / "prive")
    assert base.connexion.execute("SELECT nom FROM projets").fetchone()[0] == "Pièce témoin"
    base.fermer()
    etranger = tmp_path / "autre"
    etranger.mkdir()
    with sqlite3.connect(etranger / "benchab.sqlite") as connexion:
        connexion.execute("CREATE TABLE ne_pas_toucher (valeur TEXT)")
    with pytest.raises(ValueError, match="étrangère"):
        Base(etranger)


def test_deux_seances_simultanees_refusees_et_journal_exporte(tmp_path, atelier_initialise):
    """Deux séances simultanées refusées sur atelier initialisé ; journal conservé, aucune machine touchée."""
    debut = cli(tmp_path, "seance", "debut", "--lot", "fixture")
    assert debut.returncode == 0
    jeton = debut.stdout.split("Jeton de séance : ", 1)[1].strip()
    assert cli(tmp_path, "seance", "debut").returncode == 1
    assert cli(tmp_path, "seance", "fin", "--candidates", "2", "--temps-vote", "1.2", jeton=jeton).returncode == 0
    with sqlite3.connect(tmp_path / "prive/benchab.sqlite") as connexion:
        ligne = connexion.execute("SELECT fin, candidates, temps_vote_s FROM seances").fetchone()
    assert ligne[0] is not None
    assert ligne[1:] == (2, 1.2)
    assert (tmp_path / "prive/export/seances.jsonl").read_text().strip()


def test_frontiere_bloque_donnees_sensibles_sans_les_afficher(tmp_path):
    adresse = "personne" + "@" + "example.invalid"
    chemin = "/".join(["", "Users", "fixture", "document"])
    jeton = "sk-" + "a" * 32
    (tmp_path / "public.md").write_text(f"{adresse}\n{chemin}\n{jeton}")
    erreurs = verifier(tmp_path)
    assert len(erreurs) == 3
    assert all(adresse not in e and chemin not in e and jeton not in e for e in erreurs)
    prive = tmp_path / "prive"
    prive.mkdir()
    (prive / "donnees.md").write_text(adresse)
    (tmp_path / "public.md").write_text("Texte général sourcé.")
    assert verifier(tmp_path) == []
    (tmp_path / "lien").symlink_to(prive / "donnees.md")
    assert any("lien public" in e for e in verifier(tmp_path))


def test_interpreteur_et_port_machine():
    assert appels_interdits("subprocess.run(['python3', 'outil.py'])", True)
    assert appels_interdits("subprocess.run([sys.executable, 'outil.py'])", True) == []
    assert appels_interdits("#!/usr/bin/env python3\nprint('fixture')", True)
    assert charger()["visionneuse"]["port"] == 8771


def test_secret_argument_refuse_et_absent_des_mesures(tmp_path, atelier_initialise):
    """Secret synthétique refusé et expurgé des mesures ; aucune donnée réelle ni commande machine."""
    secret = "valeur-confidentielle-" + "x" * 32
    resultat = cli(tmp_path, "etat", "--token", secret)
    assert resultat.returncode == 1
    assert secret not in resultat.stdout + resultat.stderr
    with sqlite3.connect(tmp_path / "prive/benchab.sqlite") as connexion:
        commande, code = connexion.execute("SELECT commande, code FROM mesures").fetchone()
    assert secret not in commande
    assert "<masqué>" in commande
    assert code == 1


def test_identifiant_machine_et_base_publique_refuses(tmp_path):
    (tmp_path / "machine.json").write_text(json.dumps({"printer" + "_id": "fixture-machine"}))
    assert any("identifiant" in erreur for erreur in verifier(tmp_path))
    (tmp_path / "machine.json").unlink()
    (tmp_path / "base.sqlite").write_bytes(b"fixture")
    assert any("base de données" in erreur for erreur in verifier(tmp_path))


def test_python_du_venv_autorise_et_port_personnalise(tmp_path, monkeypatch):
    assert appels_interdits("subprocess.run(['.venv/bin/python3', 'outil.py'])", True) == []
    config = tmp_path / "machine.toml"
    config.write_text("[visionneuse]\nport = 9411\n")
    monkeypatch.setenv("BENCHAB_MACHINE", str(config))
    assert charger()["visionneuse"]["port"] == 9411


def test_temps_de_controle_et_mesure_de_fin_restent_dans_la_seance(tmp_path, atelier_initialise):
    """Temps de contrôle en s conservé dans la séance de fixture ; aucune commande machine."""
    debut = cli(tmp_path, "seance", "debut")
    assert debut.returncode == 0
    jeton = debut.stdout.split("Jeton de séance : ", 1)[1].strip()
    assert cli(tmp_path, "controle", "frontiere", "--racine", str(tmp_path)).returncode == 0
    assert cli(tmp_path, "seance", "fin", jeton=jeton).returncode == 0
    with sqlite3.connect(tmp_path / "prive/benchab.sqlite") as connexion:
        seance, duree = connexion.execute("SELECT id, temps_controles_s FROM seances").fetchone()
        rattachement = connexion.execute("SELECT seance_id FROM mesures WHERE commande = 'seance fin'").fetchone()[0]
    assert duree > 0
    assert rattachement == seance


def test_export_coherent_et_transaction_refusee_sans_perte(tmp_path):
    base = Base(tmp_path / "prive")
    base.ecrire("INSERT INTO projets (id, nom) VALUES ('temoin', 'Pièce témoin')")
    export = tmp_path / "prive/export"
    manifeste = json.loads((export / "manifest.json").read_text())
    for table, empreinte in manifeste["sha256"].items():
        assert hashlib.sha256((export / f"{table}.jsonl").read_bytes()).hexdigest() == empreinte
    contenu = (export / "projets.jsonl").read_bytes()
    with pytest.raises(sqlite3.IntegrityError):
        with base.transaction() as connexion:
            connexion.execute("INSERT INTO projets (id, nom) VALUES ('annule', 'Non conservé')")
            connexion.execute("INSERT INTO preparations (id, projet_id, nom) VALUES ('p', 'absent', 'P01')")
    assert base.connexion.execute("SELECT count(*) FROM projets").fetchone()[0] == 1
    assert (export / "projets.jsonl").read_bytes() == contenu
    base.fermer()


@pytest.mark.parametrize("option", ["--temps-vote", "--temps-controles"])
@pytest.mark.parametrize("valeur", ["inf", "nan", "-inf"])
def test_duree_non_finie_refusee_sans_fermer_ni_corrompre(tmp_path, option, valeur, atelier_initialise):
    """Durées non finies FAIL sans fermer la séance ; assertions de données et de code conservées."""
    assert cli(tmp_path, "seance", "debut").returncode == 0
    resultat = cli(tmp_path, "seance", "fin", f"{option}={valeur}")
    assert resultat.returncode == 2
    with sqlite3.connect(tmp_path / "prive/benchab.sqlite") as connexion:
        assert connexion.execute("SELECT fin FROM seances").fetchone()[0] is None
    assert cli(tmp_path, "etat").returncode == 0


def test_base_refuse_non_fini_avant_commit(tmp_path):
    base = Base(tmp_path / "prive")
    with pytest.raises(ValueError):
        base.ecrire("INSERT INTO seances (id, debut, temps_vote_s) VALUES ('s', 'fixture', ?)", (float("inf"),))
    assert base.connexion.execute("SELECT count(*) FROM seances").fetchone()[0] == 0
    base.fermer()


def test_frontiere_refuse_racine_inexistante(tmp_path):
    assert verifier(tmp_path / "racine-absente")


@pytest.mark.parametrize("format_fichier", ["toml", "yaml"])
def test_frontiere_couvre_identifiants_toml_yaml(tmp_path, format_fichier):
    cle = "printer" + "_id"
    contenu = f'{cle} = "fixture-machine"' if format_fichier == "toml" else f"{cle}: fixture-machine"
    (tmp_path / f"machine.{format_fichier}").write_text(contenu)
    assert any("identifiant" in e for e in verifier(tmp_path))


def test_scripts_sans_extension_et_enveloppe_env_examines(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "check-all").write_text("#!/bin/sh\npython3 outil.py\n")
    assert any("Python système" in e for e in verifier(tmp_path))
    assert appels_interdits("subprocess.run(['/usr/bin/env', 'python3', 'outil.py'])", True)


def test_echec_export_ne_conserve_pas_une_mesure_de_succes(tmp_path, monkeypatch):
    prive = tmp_path / "prive"
    Base(prive).fermer()

    def export_refuse(_base):
        raise OSError("Échec d'export de fixture")

    monkeypatch.setattr(Base, "exporter", export_refuse)
    assert main(["--prive", str(prive), "etat"]) == 1
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT code FROM mesures").fetchone()[0] == 1


def test_note_de_seance_sans_et_avec_verrou(atelier_initialise, capsys):
    """Une note sans séance FAIL ; avec séance elle est relue/exportée, sans inférer de preuve physique."""
    argv = ["--prive", str(atelier_initialise), "seance"]
    assert main([*argv, "note", "Fixture"]) == 1
    assert main([*argv, "debut", "--lot", "fixture"]) == 0
    assert main([*argv, "note", "Étape numérique vérifiée"]) == 0
    capsys.readouterr()
    assert main([*argv, "etat"]) == 0
    assert json.loads(capsys.readouterr().out)["derniere_note"]["texte"] == "Étape numérique vérifiée"
    with sqlite3.connect(atelier_initialise / "benchab.sqlite") as db:
        nombre = db.execute(
            "SELECT count(*) FROM decisions WHERE json_extract(donnees_json,'$.type')='note_seance'"
        ).fetchone()[0]
        assert nombre == 1


def test_seance_fin_par_un_tiers_est_refusee(tmp_path, atelier_initialise):
    """Une autre invocation sans jeton ne ferme pas la séance, et aucun jeton n'entre dans les exports."""
    debut = cli(tmp_path, "seance", "debut")
    assert debut.returncode == 0
    refus = cli(tmp_path, "seance", "fin")
    assert refus.returncode == 1 and "Jeton de séance requis" in refus.stderr
    jeton = debut.stdout.split("Jeton de séance : ", 1)[1].strip()
    assert cli(tmp_path, "seance", "fin", jeton=jeton).returncode == 0
    for nom in ("decisions", "seances", "mesures"):
        assert jeton not in (atelier_initialise / f"export/{nom}.jsonl").read_text(encoding="utf-8")


def test_seance_orpheline_reprise_et_fin_forcee(tmp_path, atelier_initialise):
    """Une séance ancienne est reprise sans rattachement erroné, puis la fermeture forcée laisse une trace."""
    with sqlite3.connect(atelier_initialise / "benchab.sqlite") as connexion:
        connexion.execute("INSERT INTO seances (id,debut) VALUES (?,?)", ("fixture-orpheline", "2000-01-01T00:00:00Z"))
    refus = cli(tmp_path, "seance", "debut")
    assert refus.returncode == 1 and "seance reprendre" in refus.stderr
    assert "IntegrityError" not in refus.stderr and "UNIQUE" not in refus.stderr
    assert cli(tmp_path, "etat").returncode == 0
    with sqlite3.connect(atelier_initialise / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT seance_id FROM mesures WHERE commande='etat'").fetchone()[0] is None
    reprise = cli(tmp_path, "seance", "reprendre")
    assert reprise.returncode == 0 and "Jeton de séance" in reprise.stdout
    assert cli(tmp_path, "seance", "debut").returncode == 1
    assert cli(tmp_path, "seance", "fin", "--forcer").returncode == 0
    with sqlite3.connect(atelier_initialise / "benchab.sqlite") as connexion:
        assert connexion.execute("SELECT fin FROM seances WHERE id='fixture-orpheline'").fetchone()[0]
        journal = connexion.execute(
            "SELECT texte FROM decisions WHERE json_extract(donnees_json,'$.type')='verrou_seance' ORDER BY rowid"
        ).fetchall()
    assert journal == [("Verrou de séance : reprise",), ("Verrou de séance : fin forcée",)]


def test_expiration_du_verrou_de_seance(tmp_path, atelier_initialise):
    """Un jeton expiré ne ferme plus la séance et peut être remplacé sans perdre l'identifiant."""
    debut = cli(tmp_path, "seance", "debut")
    assert debut.returncode == 0
    ancien = debut.stdout.split("Jeton de séance : ", 1)[1].strip()
    with sqlite3.connect(atelier_initialise / "benchab.sqlite") as connexion:
        connexion.execute(
            "UPDATE decisions SET donnees_json=json_set(donnees_json,'$.expire',?) "
            "WHERE json_extract(donnees_json,'$.type')='verrou_seance'",
            ("2000-01-01T00:00:00Z",),
        )
    etat = cli(tmp_path, "seance", "etat")
    assert json.loads(etat.stdout)["verrou"]["statut"] == "ORPHELIN"
    assert cli(tmp_path, "seance", "fin", jeton=ancien).returncode == 1
    reprise = cli(tmp_path, "seance", "reprendre")
    nouveau = reprise.stdout.split("Jeton de séance : ", 1)[1].strip()
    assert reprise.returncode == 0 and nouveau != ancien
    assert cli(tmp_path, "seance", "fin", jeton=ancien).returncode == 1
    assert cli(tmp_path, "seance", "fin", jeton=nouveau).returncode == 0
