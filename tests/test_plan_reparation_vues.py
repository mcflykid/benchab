"""Le plan de réparation des vues reste une comparaison passive."""

import hashlib
import json
import sqlite3
import threading

import pytest

from benchab.donnees.base import Base
from benchab.donnees.vues import generer
from benchab.qualite import plan_reparation
from benchab.qualite.ecritures import PublicationNeuve


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
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["propositions_liens"] == []
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


def test_plan_refuse_bascule_wal_apres_lecture_initiale_entete(tmp_path, monkeypatch):
    """Le mode journal effectif est vérifié après l'ouverture de l'instantané SQLite."""
    prive = _atelier(tmp_path)
    entete_originale = plan_reparation._entete_sqlite
    bascule_effectuee = False

    def entete_puis_bascule(chemin):
        nonlocal bascule_effectuee
        entete = entete_originale(chemin)
        if not bascule_effectuee:
            bascule_effectuee = True
            with sqlite3.connect(chemin) as connexion:
                assert connexion.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        return entete

    monkeypatch.setattr(plan_reparation, "_entete_sqlite", entete_puis_bascule)

    rapport = plan_reparation.planifier(prive)

    assert bascule_effectuee
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["actions"] == []
    assert "journal" in rapport["detail"].lower()


def test_appliquer_refuse_wal_effectif_avant_preuve(tmp_path, monkeypatch):
    """Un passage WAL après l'en-tête initial est refusé avant preuve ou copie."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    cible = prive / "vues/recuperation/INDEX.md"
    cible.parent.mkdir()
    index.unlink()
    index.symlink_to("recuperation/INDEX.md")
    candidat = prive / "candidat.md"
    candidat.write_bytes(attendu)
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    entete_originale = plan_reparation._entete_sqlite
    bascule_effectuee = False

    def entete_puis_bascule(chemin):
        nonlocal bascule_effectuee
        entete = entete_originale(chemin)
        if not bascule_effectuee:
            bascule_effectuee = True
            with sqlite3.connect(chemin) as connexion:
                assert connexion.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        return entete

    monkeypatch.setattr(plan_reparation, "_entete_sqlite", entete_puis_bascule)

    with pytest.raises(ValueError, match="journal"):
        plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")

    assert bascule_effectuee
    assert not cible.exists()
    assert not list((prive / "sauvegardes/vues").glob("reprise-lien-*.json"))
    assert index.is_symlink() and candidat.read_bytes() == attendu


def test_appliquer_refuse_base_remplacee_apres_preuve(tmp_path, monkeypatch):
    """Le chemin de base garde la même identité jusqu'à la publication de la copie."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    cible = prive / "vues/recuperation/INDEX.md"
    cible.parent.mkdir()
    index.unlink()
    index.symlink_to("recuperation/INDEX.md")
    candidat = prive / "candidat.md"
    candidat.write_bytes(attendu)
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    base = prive / "benchab.sqlite"
    publieur = PublicationNeuve.publier

    def remplacer_base_apres_preuve(publication):
        resultat = publieur(publication)
        if any(chemin.name.startswith("reprise-lien-") for chemin in publication.sorties):
            remplacement = tmp_path / "base-remplacement.sqlite"
            remplacement.write_bytes(base.read_bytes())
            remplacement.replace(base)
        return resultat

    monkeypatch.setattr(PublicationNeuve, "publier", remplacer_base_apres_preuve)

    with pytest.raises(ValueError, match="Identité du chemin SQLite modifiée"):
        plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")

    assert not cible.exists()
    assert index.is_symlink() and candidat.read_bytes() == attendu
    assert list((prive / "sauvegardes/vues").glob("reprise-lien-*.json"))


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


def test_plan_propose_candidat_prive_identique_pour_lien_sans_ecriture(tmp_path):
    """Un SHA exact sur candidat privé permet une proposition passive pour un lien cassé."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    candidat = prive / "vues-recuperation/INDEX.md"
    candidat.parent.mkdir()
    candidat.write_bytes(attendu)
    index.unlink()
    index.symlink_to("cible-deplacee-absente.md")
    avant = _empreintes(prive)

    rapport = plan_reparation.planifier(prive, candidats=["vues-recuperation/INDEX.md"])

    assert _empreintes(prive) == avant
    entree = next(ligne for ligne in rapport["vues"] if ligne["cible"] == "vues/INDEX.md")
    assert entree["statut"] == "LIEN_A_VERIFIER"
    proposition = next(item for item in rapport["propositions_liens"] if item["cible"] == "vues/INDEX.md")
    assert proposition["action"] == "PROPOSER_LIEN_CANDIDAT_SHA_EXACT"
    assert proposition["candidat"] == "vues-recuperation/INDEX.md"
    assert proposition["sha256"] == hashlib.sha256(attendu).hexdigest()
    assert proposition["application_effectuee"] is False
    assert rapport["statut"] == "WARN"


def test_plan_refuse_candidats_ambigus_ou_non_prouves(tmp_path):
    """Deux copies exactes restent ambiguës et un contenu différent ne prouve rien."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    index.unlink()
    index.symlink_to("cible-deplacee-absente.md")
    premier = prive / "candidats/un.md"
    second = prive / "candidats/deux.md"
    different = prive / "candidats/different.md"
    premier.parent.mkdir()
    premier.write_bytes(attendu)
    second.write_bytes(attendu)
    different.write_bytes("octets différents".encode("utf-8"))

    ambigu = plan_reparation.planifier(
        prive, candidats=["candidats/un.md", "candidats/deux.md"]
    )
    non_prouve = plan_reparation.planifier(prive, candidats=["candidats/different.md"])

    assert ambigu["statut"] == "NON VÉRIFIÉ"
    assert ambigu["propositions_liens"] == []
    assert non_prouve["statut"] == "NON VÉRIFIÉ"
    assert non_prouve["propositions_liens"] == []


def test_plan_ne_propose_pas_de_remplacement_pour_un_lien_vivant(tmp_path):
    """Le cas pris en charge est un lien relatif cassé, pas un lien déjà fonctionnel."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    candidat = prive / "vues/candidat.md"
    candidat.write_bytes(attendu)
    index.unlink()
    index.symlink_to(candidat.name)

    rapport = plan_reparation.planifier(prive, candidats=["vues/candidat.md"])

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["propositions_liens"] == []


def test_plan_rejette_candidat_hors_prive_et_candidat_lie(tmp_path):
    """Les candidats hors privé ou symboliques ne sont jamais lus comme preuves."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    index.unlink()
    index.symlink_to("cible-deplacee-absente.md")
    dehors = tmp_path / "candidat-externe.md"
    dehors.write_bytes(attendu)
    lien = prive / "lien.md"
    lien.symlink_to(dehors)

    rapport = plan_reparation.planifier(
        prive, candidats=["../candidat-externe.md", "lien.md"]
    )

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["propositions_liens"] == []


def test_cli_plan_reparation_accepte_candidat_explicitement(tmp_path, capsys):
    """La CLI transmet le chemin candidat privé au plan passif."""
    from benchab import cli

    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    candidat = prive / "candidat.md"
    candidat.write_bytes(index.read_bytes())
    index.unlink()
    index.symlink_to("deplace-absent.md")

    code = cli.main([
        "--prive", str(prive), "controle", "plan-reparation",
        "--candidat-lien", "candidat.md",
    ])

    rapport = json.loads(capsys.readouterr().out)
    assert code == 1
    assert rapport["propositions_liens"][0]["candidat"] == "candidat.md"
    assert rapport["application_effectuee"] is False


def test_plan_lien_vivant_resout_la_cible_depuis_le_dossier_du_lien(tmp_path):
    """Un homonyme à la racine ne masque pas une cible vivante sous vues/."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    index.unlink()
    cible_vivante = prive / "vues/autre.md"
    cible_vivante.write_bytes(attendu)
    (prive / "autre.md").unlink(missing_ok=True)
    index.symlink_to("autre.md")
    candidat = prive / "candidat.md"
    candidat.write_bytes(attendu)

    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])

    assert rapport["propositions_liens"] == []
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_plan_lien_casse_ignore_un_homonyme_a_la_racine(tmp_path):
    """Un homonyme à la racine ne rend pas vivante la cible absente sous vues/."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    index.unlink()
    (prive / "autre.md").write_bytes(attendu)
    index.symlink_to("autre.md")
    candidat = prive / "candidat.md"
    candidat.write_bytes(attendu)

    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])

    proposition = next(item for item in rapport["propositions_liens"] if item["cible"] == "vues/INDEX.md")
    assert proposition["candidat"] == "candidat.md"
    assert rapport["statut"] == "WARN"


def test_appliquer_candidat_recree_la_cible_du_lien_sans_remplacer_le_lien(tmp_path):
    """La reprise copie une preuve SHA exacte vers une cible absente et garde le lien."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    cible = prive / "vues/recuperation/INDEX.md"
    cible.parent.mkdir()
    index.unlink()
    index.symlink_to("recuperation/INDEX.md")
    candidat = prive / "candidats/INDEX.md"
    candidat.parent.mkdir()
    candidat.write_bytes(attendu)
    candidat_avant = candidat.read_bytes()
    lien_avant = index.readlink()
    base_sha = hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest()
    rapport = plan_reparation.planifier(prive, candidats=["candidats/INDEX.md"])

    resultat = plan_reparation.appliquer_candidat_lien(
        prive, rapport, "vues/INDEX.md", "candidats/INDEX.md"
    )

    preuve = prive / resultat["preuve"]
    preuve_json = json.loads(preuve.read_text(encoding="utf-8"))
    assert resultat["statut"] == "PASS"
    assert cible.read_bytes() == attendu
    assert index.is_symlink() and index.readlink() == lien_avant
    assert candidat.read_bytes() == candidat_avant
    assert preuve_json["lien_relatif"] == "recuperation/INDEX.md"
    assert preuve_json["cible_avant"] == "ABSENTE"
    assert preuve_json["candidat_sha256"] == hashlib.sha256(attendu).hexdigest()
    assert hashlib.sha256((prive / "benchab.sqlite").read_bytes()).hexdigest() == base_sha


def test_ecriture_sqlite_concurrente_ne_committe_pas_entre_preuve_et_copie(tmp_path, monkeypatch):
    """La transaction RO garde le rendu source fixe jusqu'à la copie du candidat."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    cible = prive / "vues/recuperation/INDEX.md"
    cible.parent.mkdir()
    attendu = index.read_bytes()
    index.unlink()
    index.symlink_to("recuperation/INDEX.md")
    candidat = prive / "candidat.md"
    candidat.write_bytes(attendu)
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    base = prive / "benchab.sqlite"
    with sqlite3.connect(base) as connexion:
        assert connexion.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
    tentatives = []
    publier = PublicationNeuve.publier

    def tenter_ecriture_apres_preuve(publication):
        resultat = publier(publication)
        if not any(path.name.startswith("reprise-lien-") for path in publication.sorties):
            return resultat

        def ecrire():
            try:
                with sqlite3.connect(base, timeout=0.15) as connexion:
                    connexion.execute(
                        "UPDATE projets SET nom=? WHERE id=?", ("Projet concurrent", "demo")
                    )
                tentatives.append("COMMIT")
            except sqlite3.OperationalError as erreur:
                raison = str(erreur).lower()
                tentatives.append("BLOQUÉE" if "locked" in raison or "busy" in raison else "ERREUR")

        ecrivain = threading.Thread(target=ecrire)
        ecrivain.start()
        ecrivain.join(timeout=2)
        assert not ecrivain.is_alive(), "L'écrivain doit terminer ou échouer avant la copie."
        return resultat

    monkeypatch.setattr(PublicationNeuve, "publier", tenter_ecriture_apres_preuve)

    resultat = plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")

    assert resultat["statut"] == "PASS"
    assert tentatives == ["BLOQUÉE"]
    assert cible.read_bytes() == attendu
    with sqlite3.connect(base) as connexion:
        nom = connexion.execute("SELECT nom FROM projets WHERE id='demo'").fetchone()[0]
    assert nom == "Projet témoin"


def test_appliquer_candidat_refuse_candidat_change_et_cible_apparue(tmp_path):
    """La reprise replanifie sous verrou et refuse une preuve modifiée ou une cible occupée."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    attendu = index.read_bytes()
    cible = prive / "vues/recuperation/INDEX.md"
    cible.parent.mkdir()
    index.unlink()
    index.symlink_to("recuperation/INDEX.md")
    candidat = prive / "candidat.md"
    candidat.write_bytes(attendu)
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    candidat.write_bytes(b"preuve modifiee")

    with pytest.raises((ValueError, RuntimeError)):
        plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")
    assert not cible.exists()
    assert index.is_symlink()

    candidat.write_bytes(attendu)
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    deplace = prive / "deplace/candidat.md"
    deplace.parent.mkdir()
    candidat.rename(deplace)
    with pytest.raises((ValueError, RuntimeError)):
        plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")
    assert not cible.exists()
    assert deplace.read_bytes() == attendu

    deplace.rename(candidat)
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    cible.write_bytes(b"octets apparus apres le plan")
    avant = cible.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")
    assert cible.read_bytes() == avant
    assert index.is_symlink()


def test_appliquer_candidat_refuse_lien_change_depuis_plan(tmp_path):
    """Le SHA et l'identité du lien du plan empêchent de suivre une cible remplacée."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    candidat = prive / "candidat.md"
    candidat.write_bytes(index.read_bytes())
    index.unlink()
    index.symlink_to("restauration/premiere.md")
    (prive / "vues/restauration").mkdir()
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    index.unlink()
    index.symlink_to("restauration/seconde.md")

    with pytest.raises(ValueError, match="modifié depuis le plan"):
        plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")

    assert not (prive / "vues/restauration/premiere.md").exists()
    assert not (prive / "vues/restauration/seconde.md").exists()


def test_plan_ne_propose_pas_de_cible_sous_parent_lie(tmp_path):
    """Un parent de destination symbolique reste NON VÉRIFIÉ et n'est jamais copié."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    candidat = prive / "candidat.md"
    candidat.write_bytes(index.read_bytes())
    index.unlink()
    cible_reelle = prive / "vues/reelle"
    cible_reelle.mkdir()
    (prive / "vues/liee").symlink_to(cible_reelle, target_is_directory=True)
    index.symlink_to("liee/INDEX.md")

    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["propositions_liens"] == []
    assert not (cible_reelle / "INDEX.md").exists()


def test_plan_refuse_une_cible_de_lien_avec_composant_point(tmp_path):
    """Un chemin de lien non canonique reste NON VÉRIFIÉ même s'il résout dans vues/."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    candidat = prive / "candidat.md"
    candidat.write_bytes(index.read_bytes())
    index.unlink()
    (prive / "vues/recuperation").mkdir()
    index.symlink_to("./recuperation/INDEX.md")

    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["propositions_liens"] == []
    assert not (prive / "vues/recuperation/INDEX.md").exists()


def test_appliquer_candidat_refuse_manifeste_et_vue_canonique(tmp_path):
    """Une cible de lien égale au manifeste ou à une autre vue n'est jamais copiée."""
    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    candidat = prive / "candidat.md"
    candidat.write_bytes(index.read_bytes())
    manifeste = prive / "vues/manifest.json"
    manifeste.unlink()
    index.unlink()
    index.symlink_to("manifest.json")
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    with pytest.raises(ValueError):
        plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")
    assert not manifeste.exists()
    assert index.is_symlink()

    index.unlink()
    etat = next((prive / "vues/etats").glob("*.md"))
    relatif_etat = etat.relative_to(prive).as_posix()
    etat.unlink()
    index.symlink_to("etats/" + etat.name)
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    with pytest.raises(ValueError):
        plan_reparation.appliquer_candidat_lien(prive, rapport, "vues/INDEX.md", "candidat.md")
    assert not etat.exists()
    assert index.is_symlink()
    assert relatif_etat.startswith("vues/etats/")


def test_cli_applique_candidat_de_lien_explicitement(tmp_path, capsys):
    """La CLI copie un candidat uniquement après plan explicite et option dédiée."""
    from benchab import cli

    prive = _atelier(tmp_path)
    index = prive / "vues/INDEX.md"
    candidat = prive / "candidat.md"
    candidat.write_bytes(index.read_bytes())
    index.unlink()
    (prive / "vues/recuperation").mkdir()
    index.symlink_to("recuperation/INDEX.md")
    rapport = plan_reparation.planifier(prive, candidats=["candidat.md"])
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(rapport), encoding="utf-8")

    code = cli.main([
        "--prive", str(prive), "controle", "appliquer-reparation-vue",
        "--plan", str(plan), "--cible", "vues/INDEX.md", "--candidat", "candidat.md",
    ])

    resultat = json.loads(capsys.readouterr().out)
    assert code == 0
    assert resultat["statut"] == "PASS"
    assert (prive / "vues/recuperation/INDEX.md").read_bytes() == candidat.read_bytes()
    assert index.is_symlink()
