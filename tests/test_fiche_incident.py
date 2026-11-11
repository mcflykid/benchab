"""Contrôle les liens de non-régression avec un dépôt Git synthétique."""

import hashlib
import json
import subprocess

import pytest

from benchab.cli import main as cli
from benchab.connaissances import incidents
from benchab.donnees.base import Base


@pytest.fixture
def incident(tmp_path):
    """Crée une révision et une preuve déclarative sans accès réseau ni atelier réel."""
    depot, prive = tmp_path / "depot", tmp_path / "atelier"
    depot.mkdir()
    prive.mkdir()
    (prive / "rapports").mkdir()
    (depot / "tests").mkdir()
    chemin = "tests/test_regression.py"
    contenu = b"def test_exemple():\n    assert True\n"
    (depot / chemin).write_bytes(contenu)
    subprocess.run(["git", "init", "-q", str(depot)], check=True)
    subprocess.run(["git", "-C", str(depot), "add", chemin], check=True)
    subprocess.run(
        ["git", "-C", str(depot), "-c", "user.name=Fixture",
         "-c", "user.email=fixture-synthetique", "commit", "-qm", "fixture"],
        check=True,
    )
    revision = subprocess.check_output(["git", "-C", str(depot), "rev-parse", "HEAD"], text=True).strip()
    preuve = prive / "rapports/verdict.json"
    preuve.write_bytes(b'{"statut":"PASS"}\n')
    declaration = {
        "schema": 1, "issue": 119, "revision": revision,
        "symptome": "Un lien privé restait cassé après diagnostic.",
        "conditions": ["Atelier de test ; fichier régulier absent."],
        "limites": ["Aucun original retrouvé ; aucun essai physique."],
        "tests": [chemin],
        "preuve_execution": {
            "chemin": "rapports/verdict.json",
            "sha256": hashlib.sha256(preuve.read_bytes()).hexdigest(),
        },
    }
    return depot, prive, declaration, preuve, contenu


def test_liaison_du_blob_independante_de_la_copie_de_travail(incident):
    """L’empreinte vient de la révision déclarée ; un JSON PASS seul ne prouve aucune exécution."""
    depot, prive, declaration, preuve, contenu = incident
    (depot / declaration["tests"][0]).write_text("contenu de travail différent\n")
    rapport = incidents.fiche(declaration, depot=depot, prive=prive)
    assert rapport["statut_lien"] == "PASS"
    assert rapport["tests"] == [{
        "chemin": declaration["tests"][0], "sha256": hashlib.sha256(contenu).hexdigest(),
    }]
    assert rapport["execution"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["execution"]["sha256"] == hashlib.sha256(preuve.read_bytes()).hexdigest()
    assert rapport["validation_physique"] is False


@pytest.mark.parametrize("revision", ["HEAD", "0" * 40])
def test_revision_mobile_ou_absente_refusee(incident, revision):
    depot, prive, declaration, _, _ = incident
    declaration["revision"] = revision
    with pytest.raises(ValueError, match="révision|blob"):
        incidents.fiche(declaration, depot=depot, prive=prive)


@pytest.mark.parametrize("chemin", ["tests/../source.py", "benchab/test_autre.py", "tests/absent.py"])
def test_regression_ambiguë_ou_absente_refusee(incident, chemin):
    depot, prive, declaration, _, _ = incident
    declaration["tests"] = [chemin]
    with pytest.raises(ValueError, match="test|blob"):
        incidents.fiche(declaration, depot=depot, prive=prive)


def test_lien_git_ne_compte_pas_comme_fichier_de_test(incident):
    """Un blob de lien Git n’est pas le contenu d’un test régulier."""
    depot, prive, declaration, _, _ = incident
    chemin = declaration["tests"][0]
    (depot / chemin).unlink()
    (depot / chemin).symlink_to("note.txt")
    subprocess.run(["git", "-C", str(depot), "add", chemin], check=True)
    subprocess.run(
        ["git", "-C", str(depot), "-c", "user.name=Fixture",
         "-c", "user.email=fixture-synthetique", "commit", "-qm", "lien"],
        check=True,
    )
    declaration["revision"] = subprocess.check_output(
        ["git", "-C", str(depot), "rev-parse", "HEAD"], text=True,
    ).strip()
    with pytest.raises(ValueError, match="test régulier"):
        incidents.fiche(declaration, depot=depot, prive=prive)


def test_preuve_locale_changee_refusee(incident):
    depot, prive, declaration, preuve, _ = incident
    preuve.write_bytes(b'{"statut":"FAIL"}\n')
    with pytest.raises(ValueError, match="empreinte"):
        incidents.fiche(declaration, depot=depot, prive=prive)


def test_cli_cree_une_fiche_sans_ecraser_la_preuve(incident, capsys):
    depot, prive, declaration, preuve, _ = incident
    fichier = prive / "declaration.json"
    fichier.write_text(json.dumps(declaration), encoding="utf-8")
    commande = ["--depot", str(depot), str(fichier)]
    avant = preuve.read_bytes()
    assert incidents.main([*commande, "--sortie", str(preuve)], racine=prive) == 2
    assert preuve.read_bytes() == avant
    assert "BENCHAB" in capsys.readouterr().err
    sortie = prive / "rapports/incident-119.json"
    assert incidents.main([*commande, "--sortie", str(sortie)], racine=prive) == 0
    resultat = json.loads(capsys.readouterr().out)
    assert resultat["rapport"] == str(sortie)
    assert json.loads(sortie.read_bytes())["statut_lien"] == "PASS"



def test_route_cli_transmet_atelier_prive(incident, capsys):
    """La commande publique transmet l’atelier choisi et n’ajoute aucun fait à la base."""
    depot, prive, declaration, _, _ = incident
    base = Base(prive)
    try:
        assert base.connexion.execute("SELECT COUNT(*) FROM connaissances").fetchone()[0] == 0
    finally:
        base.fermer()
    fichier = prive / "declaration.json"
    fichier.write_text(json.dumps(declaration), encoding="utf-8")
    sortie = prive / "rapports/fiche.json"

    assert cli([
        "--prive", str(prive), "connaissances", "incidents", str(fichier),
        "--depot", str(depot), "--sortie", str(sortie),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["rapport"] == str(sortie)
    base = Base(prive, creer=False)
    try:
        assert base.connexion.execute("SELECT COUNT(*) FROM connaissances").fetchone()[0] == 0
    finally:
        base.fermer()
