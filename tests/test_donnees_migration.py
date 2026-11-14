"""Reconstruction, recherches physiques et frontière des vues sur données synthétiques."""

import hashlib
import json
import sqlite3

import pytest

from benchab.cli import main
from benchab.donnees.base import TABLES, Base
from benchab.donnees.reconstruction import reconstruire
from benchab.donnees.requetes import impressions
from benchab.donnees.vues import DEBUT, FIN, generer


@pytest.fixture
def base(tmp_path):
    base = Base(tmp_path / "atelier")
    with base.transaction() as c:
        c.execute("INSERT INTO projets VALUES (?,?,?,?,?)", (
            "projets/modele-secret", "Projet privé exemple", "archive/source", "terminé",
            json.dumps({"suivi": {"physique": {"resultat": "inconnu", "portee": "PARTIELLE"}}}),
        ))
        c.execute("INSERT INTO preparations VALUES (?,?,?,?,?,?)", (
            "p1", "projets/modele-secret", "P01", "2026-01-01", "a" * 64, "{}",
        ))
        c.execute("INSERT INTO filaments VALUES (?,?,?,?,?)", ("f1", "Filament exemple", "PLA+", "Exemple", "{}"))
        c.execute("INSERT INTO bobines VALUES (?,?,?,?,?)", ("b1", "f1", 1000, 400, "{}"))
        c.execute("INSERT INTO profils VALUES (?,?,?,?,?)", ("pr1", "Profil exemple", "archive/profil", "b" * 64, "{}"))
        c.execute("INSERT INTO impressions VALUES (?,?,?,?,?)", (
            "i1", "p1", "2026-01-02", "reussie", json.dumps({
                "filaments": [{"matiere": "PLA+"}], "ponts": {"present": True, "source": "observation synthétique"},
            }),
        ))
        c.execute("INSERT INTO retours VALUES (?,?,?,?,?,?,?)", (
            "r1", "i1", "2026-01-02", "Déclaration synthétique explicite", "fixture", 1, "{}",
        ))
        c.execute("INSERT INTO connaissances VALUES (?,?,?,?,?,?,?)", (
            "k1", "Connaissance privée exemple", "observation", "fixture", "2026-01-02", 0, "{}",
        ))
        c.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?)", (
            "d1", "projets/modele-secret", "2026-01-02", "Décision privée", "fixture", "{}",
        ))
        c.execute("INSERT INTO votes VALUES (?,?,?,?,?,?)", ("v1", "p1", "2026-01-02", "1", "abstention", "{}"))
        c.execute("INSERT INTO seances VALUES (?,?,?,?,?,?,?)", ("s1", "2026-01-01", "2026-01-02", "5", 1, 0, 0))
        c.execute("INSERT INTO mesures VALUES (?,?,?,?,?,?,?,?,?)", ("m1", "s1", "2026-01-01", "etat", 1, 0, 0, 10, 0))
        c.execute("INSERT INTO versions VALUES (?,?,?,?,?)", (
            "ver1", "0.0.1", "2026-01-02", "Changement privé à ne pas exposer", "source privée",
        ))
    try:
        yield base
    finally:
        base.fermer()


def test_reconstruction_identique_toutes_tables_et_export(base, tmp_path):
    destination = Base(tmp_path / "reconstruite")
    try:
        rapport = reconstruire(destination, base.prive / "export")
        assert rapport["lignes"] == dict.fromkeys(TABLES, 1)
        for table in TABLES:
            assert (destination.prive / f"export/{table}.jsonl").read_bytes() == (
                base.prive / f"export/{table}.jsonl"
            ).read_bytes()
        assert (destination.prive / "export/manifest.json").read_bytes() == (
            base.prive / "export/manifest.json"
        ).read_bytes()
        with pytest.raises(ValueError, match="pas vide"):
            reconstruire(destination, base.prive / "export")
    finally:
        destination.fermer()


def test_export_corrompu_est_refuse_avant_insertion(base, tmp_path):
    (base.prive / "export/connaissances.jsonl").write_text("{}\n")
    destination = Base(tmp_path / "reconstruite")
    try:
        with pytest.raises(ValueError, match="Empreinte"):
            reconstruire(destination, base.prive / "export")
        assert destination.connexion.execute("SELECT count(*) FROM projets").fetchone()[0] == 0
    finally:
        destination.fermer()


def test_export_fk_invalide_annule_la_transaction_entiere(base, tmp_path):
    fichier = base.prive / "export/preparations.jsonl"
    ligne = json.loads(fichier.read_text())
    ligne["projet_id"] = "projet-absent"
    fichier.write_text(json.dumps(ligne, ensure_ascii=False, sort_keys=True) + "\n")
    manifeste = base.prive / "export/manifest.json"
    contrat = json.loads(manifeste.read_text())
    contrat["sha256"]["preparations"] = hashlib.sha256(fichier.read_bytes()).hexdigest()
    manifeste.write_text(json.dumps(contrat))
    destination = Base(tmp_path / "reconstruite")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            reconstruire(destination, base.prive / "export")
        assert destination.connexion.execute("SELECT count(*) FROM projets").fetchone()[0] == 0
    finally:
        destination.fermer()


def test_requete_ne_confond_pas_prose_reglages_et_faits_physiques(base):
    leurres = [
        ("i2", "reussie", {"texte": "impression réussie en PLA+ avec ponts"}),
        ("i3", "echec", {"filaments": [{"matiere": "PLA+"}], "ponts": {"present": True, "source": "fixture"}}),
        ("i4", "reussie", {"filaments": [{"matiere": "PLA+"}], "ponts": {"present": True}}),
        ("i5", "reussie", {"filaments": [{"matiere": "PLA"}], "ponts": {"present": True, "source": "fixture"}}),
        ("i6", "reussie", {"filaments": [{"matiere": "PLA+", "confirmation_bobine_physique": False}],
                           "ponts": {"present": True, "source": "fixture"}}),
    ]
    with base.transaction() as c:
        for ident, resultat, donnees in leurres:
            c.execute("INSERT INTO impressions VALUES (?,?,?,?,?)", (
                ident, "p1", "2026-01-03", resultat, json.dumps(donnees),
            ))
    assert [r["id"] for r in impressions(base, resultat="reussie", matiere="PLA+", ponts=True)] == ["i1"]


def test_recherche_preserve_observation_sans_preparation_et_dedoublonne(base):
    donnees = {"retours_physiques": [
        {"id": "sans-prepa", "resultat": "reussie", "source": "déclaration synthétique", "date": "2026-01-03"},
        {"id": "i1", "resultat": "reussie", "source": "même événement que la table impressions"},
    ]}
    base.ecrire("UPDATE projets SET donnees_json=?", (json.dumps(donnees),))
    resultat = impressions(base, resultat="reussie")
    assert [r["id"] for r in resultat] == ["i1", "sans-prepa"]
    assert resultat[1]["preparation_id"] is None
    assert len(impressions(base, resultat="reussie", matiere="PLA+", ponts=True)) == 1
    assert base.connexion.execute("SELECT count(*) FROM preparations").fetchone()[0] == 1


def test_vues_privees_reproductibles_sans_projet_ou_changement_prive_public(base, tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (public / "README.md").write_text("# Présentation\n\nTexte éditorial conservé.\n")
    changelog_manuel = "# Changements\n\n## v1.0.1\n\nHistorique manuel conservé.\n"
    (public / "CHANGELOG.md").write_text(changelog_manuel)
    generer(base, racine_publique=public)
    premier = {p.name: p.read_bytes() for p in public.iterdir()}
    generer(base, racine_publique=public)
    assert premier == {p.name: p.read_bytes() for p in public.iterdir()}
    texte = "\n".join(p.read_text() for p in public.iterdir())
    assert "Texte éditorial conservé." in texte
    assert "aucune version livrée" not in texte
    assert "0.0.1" not in texte
    assert "privé" not in texte
    assert "modele-secret" not in texte
    assert "Projet privé exemple" in (base.prive / "vues/INDEX.md").read_text()
    assert "Changement privé" in (base.prive / "CHANGELOG.md").read_text()
    assert (public / "CHANGELOG.md").read_text() == changelog_manuel
    etat = next((base.prive / "vues/etats").glob("*.md")).read_text()
    assert "inconnu" in etat and "PARTIELLE" in etat


def test_generation_publique_sans_base_de_versions_reste_alignee_et_preserve_historique(tmp_path):
    """Une base vide n'efface pas l'historique manuel et affiche la version du paquet."""
    from benchab import __version__

    base = Base(tmp_path / "atelier-vide")
    public = tmp_path / "public"
    public.mkdir()
    readme = public / "README.md"
    readme.write_text("# BENCHAB\n\nPrésentation manuelle.\n")
    changelog = public / "CHANGELOG.md"
    historique = "# Changements\n\nHistorique maintenu dans le dépôt.\n"
    changelog.write_text(historique)
    try:
        generer(base, racine_publique=public)
        section = readme.read_text()
        assert __version__ in section
        assert "aucune version livrée" not in section
        assert "Présentation manuelle." in section
        assert changelog.read_text() == historique
    finally:
        base.fermer()


def test_vue_refuse_lien_public_et_marqueurs_ambigus(base, tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    document = public / "README.md"
    document.write_text(DEBUT + DEBUT + FIN)
    with pytest.raises(ValueError, match="Marqueurs"):
        generer(base, racine_publique=public)
    document.unlink()
    autre = tmp_path / "autre.md"
    autre.write_text("À conserver")
    document.symlink_to(autre)
    with pytest.raises(ValueError, match="Lien symbolique"):
        generer(base, racine_publique=public)
    assert autre.read_text() == "À conserver"


def test_cli_recherche_et_generation_mesurees(base, capsys):
    assert main(["--prive", str(base.prive), "donnees", "impressions", "--resultat", "reussie",
                 "--matiere", "PLA+", "--ponts"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "i1"
    assert main(["--prive", str(base.prive), "donnees", "vues"]) == 0
    assert json.loads(capsys.readouterr().out)["projets_publics"] == 0
    assert base.connexion.execute("SELECT count(*) FROM mesures WHERE commande LIKE 'donnees %'").fetchone()[0] == 2


def test_cli_reconstruction_identique_mesuree_dans_atelier_origine(base, tmp_path, capsys):
    attendu = {p.name: p.read_bytes() for p in (base.prive / "export").iterdir()}
    cible = tmp_path / "restauree"
    assert main(["--prive", str(base.prive), "donnees", "reconstruire", str(base.prive / "export"),
                 "--destination", str(cible)]) == 0
    assert json.loads(capsys.readouterr().out)["statut"] == "PASS"
    assert {p.name: p.read_bytes() for p in (cible / "export").iterdir()} == attendu
    assert base.connexion.execute("SELECT count(*) FROM mesures").fetchone()[0] == 2
