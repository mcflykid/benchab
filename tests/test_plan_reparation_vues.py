"""Le plan de réparation des vues reste une comparaison passive."""

import hashlib
import json

import pytest

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
        assert actions[chemin]["cible_avant"]["statut"] == par_cible[chemin]["statut"]
        assert actions[chemin]["cible_avant"]["sha256"] == par_cible[chemin].get("sha256_actuel")
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


def test_base_etrangere_ne_peut_pas_servir_de_source(tmp_path):
    """Une table homonyme dans une base étrangère n'autorise aucun plan."""
    import sqlite3
    from contextlib import closing

    prive = _atelier(tmp_path)
    (prive / "vues/INDEX.md").unlink()
    with closing(sqlite3.connect(prive / "benchab.sqlite")) as db, db:
        db.execute("PRAGMA application_id = 0")
    avant = _empreintes(prive)
    rapport = plan_reparation.planifier(prive)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["actions"] == []
    assert _empreintes(prive) == avant


def test_plan_nouvre_pas_une_base_a_journal_wal(tmp_path, monkeypatch):
    """Le diagnostic évite les fichiers auxiliaires SQLite d'un journal non pris en charge."""
    import sqlite3
    from contextlib import closing

    prive = _atelier(tmp_path)
    with closing(sqlite3.connect(prive / "benchab.sqlite")) as db:
        assert db.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    avant = _empreintes(prive)

    def refus(*args, **kwargs):
        raise AssertionError("SQLite ne doit pas ouvrir cette base en WAL")

    monkeypatch.setattr(sqlite3, "connect", refus)
    rapport = plan_reparation.planifier(prive)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["actions"] == []
    assert _empreintes(prive) == avant


def test_appliquer_vue_absente_depuis_plan_conserve_sqlite(tmp_path):
    """Applique explicitement une vue absente depuis son rendu SQLite confirmé."""
    prive = _atelier(tmp_path)
    cible = prive / "vues/INDEX.md"
    cible.unlink()
    projet = prive / "projets/demo/source.3mf"
    projet.parent.mkdir(parents=True)
    projet.write_bytes("projet synthétique intact".encode("utf-8"))
    readme = prive / "README.md"
    readme.write_text("Note éditoriale hors marqueurs\n" + readme.read_text(encoding="utf-8"), encoding="utf-8")
    readme_avant = readme.read_bytes()
    projet_avant = projet.read_bytes()
    changelog_avant = (prive / "CHANGELOG.md").read_bytes()
    base_sha = hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest()
    rapport = plan_reparation.planifier(prive)

    resultat = plan_reparation.appliquer_vue(prive, rapport, "vues/INDEX.md")

    action = next(item for item in rapport["actions"] if item["cible"] == "vues/INDEX.md")
    assert resultat["statut"] == "PASS"
    assert hashlib.sha256(cible.read_bytes()).hexdigest() == action["sha256_attendu"]
    assert resultat["sauvegarde"] is None
    assert hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest() == base_sha
    assert readme.read_bytes() == readme_avant
    assert projet.read_bytes() == projet_avant
    assert (prive / "CHANGELOG.md").read_bytes() == changelog_avant


def test_appliquer_vue_divergente_sauvegarde_et_relit_les_octets(tmp_path):
    """La vue divergente est sauvegardée neuve et relue avant remplacement atomique."""
    prive = _atelier(tmp_path)
    cible = next((prive / "vues/etats").glob("*.md"))
    ancien = b"conserver exactement avant remplacement\n"
    cible.write_bytes(ancien)
    projet = prive / "projets/demo/source.3mf"
    projet.parent.mkdir(parents=True)
    projet.write_bytes("projet synthétique intact".encode("utf-8"))
    projet_avant = projet.read_bytes()
    base_sha = hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest()
    rapport = plan_reparation.planifier(prive)

    resultat = plan_reparation.appliquer_vue(prive, rapport, cible.relative_to(prive).as_posix())

    sauvegarde = prive / resultat["sauvegarde"]
    action = next(item for item in rapport["actions"] if item["cible"] == cible.relative_to(prive).as_posix())
    assert sauvegarde.is_file() and not sauvegarde.is_symlink()
    assert sauvegarde.read_bytes() == ancien
    assert resultat["sha256_sauvegarde"] == hashlib.sha256(ancien).hexdigest()
    assert hashlib.sha256(cible.read_bytes()).hexdigest() == action["sha256_attendu"]
    assert hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest() == base_sha
    assert projet.read_bytes() == projet_avant


def test_appliquer_vue_refuse_reprise_cible_stale_et_rendu_stale(tmp_path):
    """Une reprise ou une cible/source modifiée depuis le plan ne touche aucun original."""
    prive = _atelier(tmp_path)
    cible = prive / "vues/INDEX.md"
    cible.unlink()
    rapport = plan_reparation.planifier(prive)
    plan_reparation.appliquer_vue(prive, rapport, "vues/INDEX.md")
    contenu_applique = cible.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        plan_reparation.appliquer_vue(prive, rapport, "vues/INDEX.md")
    assert cible.read_bytes() == contenu_applique
    assert (prive / "benchab.sqlite").is_file()

    cible.unlink()
    rapport_source = plan_reparation.planifier(prive)
    base = Base(prive)
    base.ecrire("UPDATE projets SET nom=? WHERE id=?", ("Source modifiée après plan", "demo"))
    base.fermer()
    with pytest.raises((ValueError, RuntimeError)):
        plan_reparation.appliquer_vue(prive, rapport_source, "vues/INDEX.md")
    assert not cible.exists()

    rapport_cible = plan_reparation.planifier(prive)
    cible.write_text("édition postérieure au plan\n", encoding="utf-8")
    avant = cible.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        plan_reparation.appliquer_vue(prive, rapport_cible, "vues/INDEX.md")
    assert cible.read_bytes() == avant


def test_appliquer_vue_refuse_champ_inattendu_sans_ecriture(tmp_path):
    """Les plans enrichis de champs non reconnus ne sont jamais exécutés."""
    prive = _atelier(tmp_path)
    cible = prive / "vues/INDEX.md"
    cible.unlink()
    rapport = plan_reparation.planifier(prive)
    rapport["actions"][0]["autoriser_autre_fichier"] = True
    base_sha = hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest()

    with pytest.raises(ValueError):
        plan_reparation.appliquer_vue(prive, rapport, "vues/INDEX.md")

    assert not cible.exists()
    assert hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest() == base_sha


def test_appliquer_vue_refuse_identite_sqlite_modifiee(tmp_path):
    """Une identité de schéma modifiée après le plan bloque la réparation sans toucher la cible."""
    import sqlite3
    from contextlib import closing

    prive = _atelier(tmp_path)
    cible = prive / "vues/INDEX.md"
    cible.unlink()
    rapport = plan_reparation.planifier(prive)
    with closing(sqlite3.connect(prive / "benchab.sqlite")) as connexion:
        connexion.execute("PRAGMA user_version = 2")
        connexion.commit()
    base_sha = hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest()

    with pytest.raises(ValueError):
        plan_reparation.appliquer_vue(prive, rapport, "vues/INDEX.md")

    assert not cible.exists()
    assert hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest() == base_sha


def test_appliquer_vue_refuse_cible_liee_apres_plan(tmp_path):
    """Une cible remplacée par un lien après plan n'est jamais suivie ni écrasée."""
    prive = _atelier(tmp_path)
    cible = prive / "vues/INDEX.md"
    cible.unlink()
    rapport = plan_reparation.planifier(prive)
    externe = tmp_path / "externe.md"
    externe.write_text("conserver hors atelier\n", encoding="utf-8")
    cible.symlink_to(externe)
    avant = externe.read_bytes()

    with pytest.raises((ValueError, OSError)):
        plan_reparation.appliquer_vue(prive, rapport, "vues/INDEX.md")

    assert cible.is_symlink()
    assert externe.read_bytes() == avant


def test_commande_applique_une_vue_explicitement_sans_base_ni_mesure(tmp_path, monkeypatch, capsys):
    """La commande applique uniquement la cible choisie, sans Base écrivable ni mesure SQLite."""
    from benchab import cli

    prive = _atelier(tmp_path)
    (prive / "vues/INDEX.md").unlink()
    rapport = plan_reparation.planifier(prive)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(rapport), encoding="utf-8")

    def interdit(*_args, **_kwargs):
        raise AssertionError("la réparation ne doit ouvrir ni Base ni mesure")

    monkeypatch.setattr(cli, "Base", interdit)
    monkeypatch.setattr(cli, "Mesure", interdit)
    code = cli.main([
        "--prive",
        str(prive),
        "controle",
        "appliquer-reparation-vue",
        "--plan",
        str(plan),
        "--cible",
        "vues/INDEX.md",
    ])

    resultat = json.loads(capsys.readouterr().out)
    assert code == 0
    assert resultat["cible"] == "vues/INDEX.md"
    assert resultat["application_effectuee"] is True
