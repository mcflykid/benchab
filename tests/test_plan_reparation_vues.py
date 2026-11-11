"""Le plan de réparation des vues reste une comparaison passive."""

import hashlib
import json

from benchab.donnees.base import Base
from benchab.donnees.vues import generer
from benchab.qualite import plan_reparation


def _atelier(racine):
    """Prépare un atelier synthétique isolé pour les vues."""
    base = Base(racine / "prive")
    base.ecrire(
        "INSERT INTO projets (id, nom, etat, donnees_json) VALUES (?, ?, ?, ?)",
        ("demo", "Projet témoin", "inconnu", json.dumps({"suivi": {"physique": {"resultat": "inconnu"}}})),
    )
    generer(base)
    base.fermer()
    return racine / "prive"


def _empreintes(racine):
    return {
        chemin.relative_to(racine).as_posix(): hashlib.sha256(chemin.read_bytes()).hexdigest()
        for chemin in sorted(racine.rglob("*")) if chemin.is_file() and not chemin.is_symlink()
    }


def test_plan_vue_absente_et_divergente_sans_ecriture(tmp_path):
    """Le plan distingue absence et divergence, prévoit les SHA, sans modifier les données."""
    prive = _atelier(tmp_path)
    (prive / "vues/INDEX.md").unlink()
    cible = next((prive / "vues/etats").glob("*.md"))
    cible.write_text("édition divergente\n", encoding="utf-8")
    avant = _empreintes(prive)
    rapport = plan_reparation.planifier(prive)

    apres = _empreintes(prive)
    assert apres == avant
    assert rapport["statut"] == "WARN"
    par_cible = {ligne["cible"]: ligne for ligne in rapport["vues"]}
    assert par_cible["vues/INDEX.md"]["statut"] == "ABSENTE"
    assert par_cible[cible.relative_to(prive).as_posix()]["statut"] == "DIVERGENTE"
    assert all(ligne["statut"] != "FALSIFIEE" for ligne in rapport["vues"])
    actions = {action["cible"]: action for action in rapport["actions"]}
    for chemin in ("vues/INDEX.md", cible.relative_to(prive).as_posix()):
        assert actions[chemin]["sha256_attendu"]
        assert actions[chemin]["source"]["sha256"]
        assert actions[chemin]["preconditions_requises"]["sauvegarde_verifiee_avant"] is True
        assert actions[chemin]["preconditions_requises"]["controle_apres_application"] is True


def test_vue_lien_est_signalee_sans_suivre(tmp_path):
    """Un lien de vue reste à vérifier et sa cible extérieure n'est jamais lue."""
    prive = _atelier(tmp_path)
    cible = tmp_path / "hors-prive.md"
    cible.write_text("ne pas suivre\n", encoding="utf-8")
    index = prive / "vues/INDEX.md"
    index.unlink()
    index.symlink_to(cible)
    empreinte = hashlib.sha256(cible.read_bytes()).hexdigest()

    rapport = plan_reparation.planifier(prive)

    entree = next(ligne for ligne in rapport["vues"] if ligne["cible"] == "vues/INDEX.md")
    assert entree["statut"] == "LIEN_A_VERIFIER"
    assert hashlib.sha256(cible.read_bytes()).hexdigest() == empreinte
    assert all(action["cible"] != "vues/INDEX.md" for action in rapport["actions"])


def test_base_absente_reste_non_verifiee_sans_creation(tmp_path):
    """Une base absente n'est ni créée ni réparée par le diagnostic."""
    prive = tmp_path / "atelier"
    prive.mkdir()

    rapport = plan_reparation.planifier(prive)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert not (prive / "benchab.sqlite").exists()


def test_marqueurs_readme_multiples_a_verifier(tmp_path):
    """Une section README ambiguë ne devient pas une action automatique."""
    prive = _atelier(tmp_path)
    readme = prive / "README.md"
    readme.write_text("<!-- BENCHAB:BASE:DEBUT -->x<!-- BENCHAB:BASE:DEBUT -->", encoding="utf-8")

    rapport = plan_reparation.planifier(prive)

    entree = next(ligne for ligne in rapport["vues"] if ligne["cible"] == "README.md#BENCHAB:BASE")
    assert entree["statut"] == "A_VERIFIER"
    assert all(action["cible"] != "README.md#BENCHAB:BASE" for action in rapport["actions"])


def test_commande_est_passive_et_ne_renvoie_que_les_hash(tmp_path, monkeypatch, capsys):
    """La commande n'instancie ni Base ni Mesure et retourne un plan JSON sans noms privés."""
    from benchab import cli
    from benchab.donnees import base as module_base

    prive = _atelier(tmp_path)
    def interdit(*_args, **_kwargs):
        raise AssertionError("l'ouverture d'une Base ou d'une mesure est interdite")

    monkeypatch.setattr(module_base, "Base", interdit)
    monkeypatch.setattr(cli, "Mesure", interdit)

    code = cli.main(["--prive", str(prive), "controle", "plan-reparation"])

    rapport = json.loads(capsys.readouterr().out)
    assert code == 0
    assert rapport["application_effectuee"] is False
    assert "Projet témoin" not in json.dumps(rapport, ensure_ascii=False)
    assert rapport["source"]["sha256_rendu_attendu"]


def test_plan_ne_suit_pas_lien_parent(tmp_path):
    """Un répertoire privé contenant un parent lié reste entièrement à vérifier."""
    prive = _atelier(tmp_path)
    original = prive / "vues/etats"
    copie = prive / "vues/etats-original"
    original.rename(copie)
    original.symlink_to(copie, target_is_directory=True)

    rapport = plan_reparation.planifier(prive)

    ligne = next(item for item in rapport["vues"] if item["cible"].startswith("vues/etats/"))
    assert ligne["statut"] == "LIEN_A_VERIFIER"
    assert all(action["cible"] != ligne["cible"] for action in rapport["actions"])


def test_refus_cli_reste_sans_mesure(tmp_path, monkeypatch):
    """Même une commande de plan invalide ne crée pas de mesure archivée."""
    from benchab import cli

    def interdit(*_args, **_kwargs):
        raise AssertionError("un refus de commande passive ne doit pas être mesuré")

    prive = _atelier(tmp_path)
    monkeypatch.setattr(cli, "Base", interdit)
    monkeypatch.setattr(cli, "Mesure", interdit)
    code = cli.main(["--prive", str(prive), "controle", "plan-reparation", "--inconnu"])
    assert code == 2
