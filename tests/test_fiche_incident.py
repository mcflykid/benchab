"""Contrôle les liens de non-régression avec un dépôt Git synthétique."""

import hashlib
import json
import os
import subprocess
import sys

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
    assert "rapport_junit" not in rapport["execution"]
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


def test_junit_opt_in_rapporte_les_cas_declares_sans_authentifier_execution(incident):
    depot, prive, declaration, preuve, _ = incident
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_regression.py",
            "classe": "tests.test_regression",
            "nom": "test_exemple",
        }],
    })
    preuve.write_text(
        '<testsuite><testcase file="tests/test_regression.py" '
        'classname="tests.test_regression" name="test_exemple"/></testsuite>',
        encoding="utf-8",
    )
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()

    rapport = incidents.fiche(declaration, depot=depot, prive=prive)

    assert rapport["execution"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["execution"]["rapport_junit"]["statut"] == "CONTENU RAPPORTÉ : TOUS LES CAS SANS ÉCHEC"
    assert rapport["execution"]["rapport_junit"]["cas_attendus"] == 1


def test_junit_retrouve_le_cas_du_rapport_pytest_xunit1(incident):
    """L’export xunit1 porte `file` ; xunit2 sans ce champ reste manquant."""
    depot, prive, declaration, preuve, _ = incident
    execution = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-o", "junit_family=xunit1",
            "--junitxml=" + str(preuve), "tests/test_regression.py",
        ],
        cwd=depot,
        capture_output=True,
        text=True,
        check=False,
    )
    assert execution.returncode == 0, execution.stderr
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_regression.py",
            "classe": "tests.test_regression",
            "nom": "test_exemple",
        }],
    })
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()

    rapport = incidents.fiche(declaration, depot=depot, prive=prive)

    assert rapport["execution"]["rapport_junit"]["cas_trouves"] == 1
    assert rapport["execution"]["statut"] == "NON VÉRIFIÉ"

    execution_defaut = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-o", "junit_family=xunit2",
            "--junitxml=" + str(preuve), "tests/test_regression.py",
        ],
        cwd=depot,
        capture_output=True,
        text=True,
        check=False,
    )
    assert execution_defaut.returncode == 0, execution_defaut.stderr
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()
    rapport_defaut = incidents.fiche(declaration, depot=depot, prive=prive)
    assert rapport_defaut["execution"]["rapport_junit"]["statut"] == "CONTENU RAPPORTÉ : CAS MANQUANT"


def test_ouverture_junit_est_non_bloquante(incident, monkeypatch):
    depot, prive, declaration, preuve, _ = incident
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_regression.py",
            "classe": "tests.test_regression",
            "nom": "test_exemple",
        }],
    })
    preuve.write_text(
        '<testsuite><testcase file="tests/test_regression.py" '
        'classname="tests.test_regression" name="test_exemple"/></testsuite>',
        encoding="utf-8",
    )
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()
    ouvert = os.open
    drapeaux = []

    def ouverture_surveillee(chemin, flags, *args, **kwargs):
        drapeaux.append(flags)
        return ouvert(chemin, flags, *args, **kwargs)

    monkeypatch.setattr(incidents.os, "open", ouverture_surveillee)

    incidents.fiche(declaration, depot=depot, prive=prive)

    assert drapeaux[-1] & os.O_NONBLOCK


def test_junit_signale_un_cas_attendu_absent(incident):
    depot, prive, declaration, preuve, _ = incident
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_regression.py",
            "classe": "tests.test_regression",
            "nom": "test_absent",
        }],
    })
    preuve.write_text("<testsuite/>", encoding="utf-8")
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()

    rapport = incidents.fiche(declaration, depot=depot, prive=prive)

    assert rapport["execution"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["execution"]["rapport_junit"]["statut"] == "CONTENU RAPPORTÉ : CAS MANQUANT"


def test_junit_signale_echec_ou_cas_ignore(incident):
    depot, prive, declaration, preuve, _ = incident
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_regression.py",
            "classe": "tests.test_regression",
            "nom": "test_exemple",
        }],
    })
    preuve.write_text(
        '<testsuite><testcase file="tests/test_regression.py" '
        'classname="tests.test_regression" name="test_exemple"><failure/></testcase></testsuite>',
        encoding="utf-8",
    )
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()

    rapport = incidents.fiche(declaration, depot=depot, prive=prive)

    assert rapport["execution"]["rapport_junit"]["statut"] == "CONTENU RAPPORTÉ : ÉCHEC"
    assert rapport["execution"]["statut"] == "NON VÉRIFIÉ"


@pytest.mark.parametrize("enfant,statut", [
    ("<skipped/>", "CONTENU RAPPORTÉ : CAS IGNORÉ"),
    ("<error/>", "CONTENU RAPPORTÉ : ÉCHEC"),
])
def test_junit_distingue_cas_ignore_et_erreur(incident, enfant, statut):
    depot, prive, declaration, preuve, _ = incident
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_regression.py",
            "classe": "tests.test_regression",
            "nom": "test_exemple",
        }],
    })
    preuve.write_text(
        '<testsuite><testcase file="tests/test_regression.py" '
        f'classname="tests.test_regression" name="test_exemple">{enfant}</testcase></testsuite>',
        encoding="utf-8",
    )
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()

    rapport = incidents.fiche(declaration, depot=depot, prive=prive)

    assert rapport["execution"]["rapport_junit"]["statut"] == statut
    assert rapport["execution"]["statut"] == "NON VÉRIFIÉ"


def test_junit_refuse_identite_dupliquee_et_entites(incident):
    depot, prive, declaration, preuve, _ = incident
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_regression.py",
            "classe": "tests.test_regression",
            "nom": "test_exemple",
        }],
    })
    testcase = '<testcase file="tests/test_regression.py" classname="tests.test_regression" name="test_exemple"/>'
    preuve.write_text(f"<testsuite>{testcase}{testcase}</testsuite>", encoding="utf-8")
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="dupliquée"):
        incidents.fiche(declaration, depot=depot, prive=prive)

    preuve.write_text(
        '<!DOCTYPE testsuite [<!ENTITY x "contenu">]><testsuite>&x;</testsuite>', encoding="utf-8",
    )
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="DOCTYPE|ENTITY"):
        incidents.fiche(declaration, depot=depot, prive=prive)


def test_junit_xml_invalide_est_refuse_sans_sortie_brute(incident):
    depot, prive, declaration, preuve, _ = incident
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_regression.py",
            "classe": "tests.test_regression",
            "nom": "test_exemple",
        }],
    })
    preuve.write_text("<testsuite>", encoding="utf-8")
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="XML JUnit invalide"):
        incidents.fiche(declaration, depot=depot, prive=prive)


def test_junit_refuse_cas_non_declare_et_depassement_de_taille(incident):
    depot, prive, declaration, preuve, _ = incident
    declaration["preuve_execution"].update({
        "format": "junit",
        "cas_attendus": [{
            "chemin": "tests/test_absent.py",
            "classe": "tests.test_absent",
            "nom": "test_exemple",
        }],
    })
    preuve.write_text("<testsuite/>", encoding="utf-8")
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="déclaré"):
        incidents.fiche(declaration, depot=depot, prive=prive)

    declaration["preuve_execution"]["cas_attendus"][0]["chemin"] = "tests/test_regression.py"
    preuve.write_bytes(b" " * (incidents._MAX_JUNIT + 1))
    declaration["preuve_execution"]["sha256"] = hashlib.sha256(preuve.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="8 Mio"):
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
