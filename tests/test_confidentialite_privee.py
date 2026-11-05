"""Le filtre vient de la base privée, jamais d'une liste de projets livrée."""

import json
import zipfile

from benchab.donnees.base import Base
from benchab.qualite import frontiere
from benchab.qualite.edition import verifier as publication
from benchab.qualite.frontiere import verifier


def _racine_privee_fixture(racine):
    """Crée uniquement des alias fictifs de fixture et renvoie le dossier public."""
    base = Base(racine / "prive")
    try:
        base.ecrire(
            "INSERT INTO projets(id,nom,donnees_json) VALUES (?,?,?)",
            ("projet-secret-fixture", "Projet Secret Fixture", json.dumps({"alias": ["Alias Modèle Fictif"]})),
        )
    finally:
        base.fermer()
    return racine


def test_commande_utilise_base_privee_externe_au_depot(tmp_path, capsys):
    """Vérifie --prive séparé du dépôt ; FAIL sur alias fictif, aucune donnée personnelle publiée."""
    from benchab import cli

    prive = _racine_privee_fixture(tmp_path / "donnees") / "prive"
    public = tmp_path / "public"
    public.mkdir()
    (public / "texte.txt").write_text("Alias Modèle Fictif")
    code = cli.main(["--prive", str(prive), "controle", "frontiere", "--racine", str(public)])
    assert code == 1
    assert "nom ou identifiant privé" in capsys.readouterr().err


def test_projet_alias_et_identifiants_refuses_dans_tout_fichier_public(tmp_path):
    """Injecte des noms privés fictifs, attend FAIL ; retour None, aucune unité ni donnée réelle publiée."""
    racine = tmp_path / "atelier"
    base = Base(racine / "prive")
    try:
        base.ecrire(
            "INSERT INTO projets(id,nom,donnees_json) VALUES (?,?,?)",
            (
                "projet-secret-fixture",
                "Projet Secret Fixture",
                json.dumps({"alias": ["Alias Modèle Fictif"], "source": {"nom_modele_tiers": "Modèle Tiers Fixture"}}),
            ),
        )
        base.ecrire(
            "INSERT INTO decisions(id,texte,source,donnees_json) VALUES (?,?,?,?)",
            (
                "identite-fixture",
                "Protection",
                "fixture synthétique",
                json.dumps(
                    {
                        "confidentialite": {
                            "identifiants_github": ["compte-fictif-fixture"],
                            "emails_locaux": ["courriel.fictif.fixture"],
                        }
                    }
                ),
            ),
        )
    finally:
        base.fermer()
    cible = racine / "donnee.bin"
    for terme in (
        "PROJET_SECRET_FIXTURE",
        "Alias Modèle Fictif",
        "Modèle Tiers Fixture",
        "compte-fictif-fixture",
        "courriel.fictif.fixture",
    ):
        cible.write_bytes(b"\x00" + terme.encode())
        erreurs = verifier(racine)
        assert any("nom ou identifiant privé" in e for e in erreurs), erreurs
        assert all(terme not in e for e in erreurs)
        assert any("nom ou identifiant privé" in e for e in publication(racine, lecteur_url=lambda _: None))
    cible.write_text("Texte technique sans identifiant de projet.")
    assert not any("nom ou identifiant privé" in e for e in verifier(racine))


def test_alias_decompresse_et_nom_membre_zip_bloquent_frontiere_et_publication(tmp_path):
    """Détecte un alias uniquement après décompression ; aucun nom privé ne revient dans l’erreur."""
    racine = _racine_privee_fixture(tmp_path / "atelier")
    archive = racine / "export.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as sortie:
        sortie.writestr("notes/rapport.txt", "alias modèle fictif aperçu")
    for erreurs in (verifier(racine), publication(racine, lecteur_url=lambda _: None)):
        trouvee = [e for e in erreurs if "export.zip" in e and "membre d’archive" in e]
        assert trouvee, erreurs
        assert all("Alias Modèle Fictif" not in erreur for erreur in erreurs)
    nom = racine / "membre.zip"
    with zipfile.ZipFile(nom, "w", compression=zipfile.ZIP_DEFLATED) as sortie:
        sortie.writestr("Alias Modèle Fictif/notes.txt", "texte propre")
    erreurs = verifier(racine)
    assert any("membre.zip" in erreur and "nom privé dans un membre" in erreur for erreur in erreurs), erreurs
    assert all("Alias Modèle Fictif" not in erreur for erreur in erreurs)


def test_archive_zip_propre_ne_cree_pas_de_faux_positif(tmp_path):
    """Accepte un ZIP synthétique propre, sans motif privé dans son nom ou son contenu."""
    racine = _racine_privee_fixture(tmp_path / "atelier")
    with zipfile.ZipFile(racine / "export.zip", "w", compression=zipfile.ZIP_DEFLATED) as sortie:
        sortie.writestr("notes/lecture.txt", "Contenu public sans identifiant.")
    erreurs = verifier(racine)
    assert not any(
        "nom privé dans un membre" in erreur or "identifiant privé dans un membre" in erreur for erreur in erreurs
    ), erreurs


def test_zip_hors_budget_et_archive_incomplete_echouent_avec_diagnostic_public(tmp_path, monkeypatch):
    """Refuse le dépassement plafonné et l’archive tronquée, sans nom de membre dans le diagnostic."""
    racine = _racine_privee_fixture(tmp_path / "atelier")
    trop_gros = racine / "budget.zip"
    with zipfile.ZipFile(trop_gros, "w") as sortie:
        sortie.writestr("notes/gros.txt", "123456789")
    monkeypatch.setattr(frontiere, "MAX_PUBLIC_ZIP_MEMBER", 4)
    erreurs = verifier(racine)
    assert any("budget.zip" in erreur and "hors budget" in erreur for erreur in erreurs), erreurs
    assert all("gros.txt" not in erreur for erreur in erreurs)
    (racine / "tronque.3mf").write_bytes(b"PK\x03\x04incomplet")
    erreurs = verifier(racine)
    assert any("tronque.3mf" in erreur and "archive ZIP illisible" in erreur for erreur in erreurs), erreurs


def test_index_refuse_atelier_prive_deplace_dans_le_depot(tmp_path):
    """La racine privée résolue et le préfixe historique restent exclus du suivi Git."""
    import subprocess

    from benchab.donnees.base import Base

    depot = tmp_path / "depot"
    depot.mkdir()
    subprocess.run(["git", "init", "-q", str(depot)], check=True)
    prive = depot / "ateliers/donnees"
    base = Base(prive)
    base.fermer()
    modele = prive / "fixture.stl"
    modele.write_bytes(b"fixture jetable")
    public = depot / "ateliers/donnees-guide.md"
    public.write_text("Guide de fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(depot), "add", str(public)], check=True)
    subprocess.run(["git", "-C", str(depot), "add", str(modele)], check=True)
    assert verifier(depot, prive=prive) == ["Un fichier privé est suivi par Git"]
    subprocess.run(["git", "-C", str(depot), "rm", "--cached", "-q", str(modele)], check=True)
    assert verifier(depot, prive=prive) == []
    assert modele.is_file()
