"""Une exportation périmée bloque le diagnostic de l'atelier."""

from benchab.donnees.base import Base
from benchab.donnees.vues import generer
from benchab.qualite.coherence import verifier


def test_corruption_export_detectee_sans_reparation(tmp_path):
    base = Base(tmp_path / "prive")
    try:
        assert verifier(base.prive) == []
        fichier = base.prive / "export/projets.jsonl"
        fichier.write_text("{}\n")
        assert any("projets" in e for e in verifier(base.prive))
        assert fichier.read_text() == "{}\n"
        base.exporter()
        assert verifier(base.prive) == []
    finally:
        base.fermer()


def test_catalogue_historique_sans_base_ne_passe_pas(tmp_path):
    (tmp_path / "BASE.json").write_text('{"journal":"absent.jsonl"}')
    assert verifier(tmp_path)
    assert not (tmp_path / "benchab.sqlite").exists()


def test_vues_alterees_bloquent_coherence_sans_reparation(tmp_path):
    """Contrôle les vues générées et garde les zones éditoriales hors marqueurs intactes."""
    base = Base(tmp_path / "prive")
    try:
        # Base vierge : aucune génération n'est encore déclarée ni exigée.
        assert verifier(base.prive) == []
        base.ecrire(
            "INSERT INTO projets(id,nom,etat,donnees_json) VALUES (?,?,?,?)",
            ("fixture", "Projet fixture", "actif", "{}"),
        )
        manuel = base.prive / "README.md"
        manuel.write_text("Texte manuel conservé.\n", encoding="utf-8")
        generer(base)
        with manuel.open("a", encoding="utf-8") as flux:
            flux.write("\nAjout éditorial conservé.\n")
        assert verifier(base.prive) == []
        index = base.prive / "vues/INDEX.md"
        index.write_text(index.read_text(encoding="utf-8") + "altération\n", encoding="utf-8")
        erreurs = verifier(base.prive)
        assert any("Vue générée périmée ou altérée : vues/INDEX.md" in erreur for erreur in erreurs), erreurs
        assert "altération" in index.read_text(encoding="utf-8")
        assert "Ajout éditorial conservé." in manuel.read_text(encoding="utf-8")
    finally:
        base.fermer()


def test_vue_index_supprimee_apres_generation_echoue_en_lecture_seule(tmp_path):
    """Détecte un INDEX supprimé sans le recréer ni modifier le contenu manquant."""
    base = Base(tmp_path / "prive")
    try:
        generer(base)
        index = base.prive / "vues/INDEX.md"
        index.unlink()
        erreurs = verifier(base.prive)
        assert any("Vue générée absente : vues/INDEX.md" in erreur for erreur in erreurs), erreurs
        assert not index.exists()
    finally:
        base.fermer()


def test_sources_profils_et_preuves_sont_relues_sans_reparation(tmp_path):
    """Un SHA périmé ou un fichier disparu donne FAIL ; la copie gelée reste inchangée."""
    import hashlib
    import json

    base = Base(tmp_path / "prive")
    try:
        source = base.prive / "archive/impression-3d/profils/sources-figees/fixture.json"
        source.parent.mkdir(parents=True)
        source.write_bytes(b'{"fixture":"profil"}')
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        relatif = "profils/sources-figees/fixture.json"
        base.ecrire(
            "INSERT INTO filaments(id,nom,matiere,donnees_json) VALUES (?,?,?,?)",
            ("fixture", "Filament fixture", "PLA", json.dumps({"sources_profils": [
                {"chemin": relatif, "sha256": sha, "chemin_origine": "/origine/interdite"}
            ]})),
        )
        base.ecrire(
            "INSERT INTO profils(id,nom,source,empreinte) VALUES (?,?,?,?)",
            ("profil-fixture", "Profil fixture", relatif, sha),
        )
        assert verifier(base.prive) == []
        source.write_bytes(b'profil altere')
        erreurs = verifier(base.prive)
        assert sum("empreinte de source différente" in e for e in erreurs) == 2
        assert source.read_bytes() == b'profil altere'
        source.unlink()
        assert any("source ou preuve absente" in e for e in verifier(base.prive))
        assert not source.exists()
    finally:
        base.fermer()


def test_contrat_fiche_sans_pointeur_ne_disparait_pas_du_controle(tmp_path):
    """Un contrat natif sans son chemin donne FAIL ; aucune omission silencieuse ni fichier créé."""
    import json

    base = Base(tmp_path / "prive")
    try:
        base.ecrire("INSERT INTO filaments(id,nom,matiere,donnees_json) VALUES (?,?,?,?)", (
            "fixture", "Filament fixture", "PLA", json.dumps({"fiche_contrat": "recherche-sources-v1"}),
        ))
        assert any("fiche filament non conforme" in e for e in verifier(base.prive))
        assert not (base.prive / "docs").exists()
    finally:
        base.fermer()


def test_bloc_fiche_historique_fin_avant_debut_est_refuse(tmp_path):
    """Les marqueurs inversés ne prouvent pas un bloc complet ; FAIL sans modifier la fiche synthétique."""
    import json

    from benchab.connaissances.bobine import reference_fiche

    base = Base(tmp_path / "prive")
    try:
        donnees = {"id": "fixture", "nom": "Filament fixture", "fiche": "docs/reference/filaments/fixture/fixture.md",
                   "date_controle": "date synthétique", "profil": {}, "fabricant": "Fixture",
                   "observation": "Aucun essai physique", "sources_profils": []}
        fiche = base.prive / donnees["fiche"]
        fiche.parent.mkdir(parents=True)
        contenu = "<!-- AUTO:fixture:FIN -->\n<!-- AUTO:fixture:DEBUT -->\n" + reference_fiche(donnees) + "\n"
        fiche.write_text(contenu)
        base.ecrire("INSERT INTO filaments(id,nom,matiere,donnees_json) VALUES (?,?,?,?)", (
            "fixture", "Filament fixture", "PLA", json.dumps(donnees),
        ))
        assert any("ordre invalide" in e for e in verifier(base.prive))
        assert fiche.read_text() == contenu
    finally:
        base.fermer()


def test_regeneration_fiche_moderne_depuis_sqlite_sans_archive(tmp_path):
    """Une fiche moderne périmée est régénérable ; l'archive historique reste identique, sans adoption de profil."""
    import json

    from benchab.connaissances.bobine import importer
    from benchab.donnees.base import Base
    from benchab.donnees.vues import generer

    base = Base(tmp_path / "prive")
    try:
        rel = importer(base.prive, {"id": "fixture", "marque": "Test", "nom": "Filament fixture",
            "possession": "fixture synthétique", "sources": {cle: {"statut": "NON VÉRIFIÉ"}
            for cle in ("fabricant", "orca", "3dfilamentprofiles")}})
        archive = base.prive / "archive/impression-3d" / rel
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"archive immuable")
        row = base.connexion.execute("SELECT donnees_json FROM filaments WHERE id='fixture'").fetchone()
        d = json.loads(row[0])
        d["observation"] = "Retour synthétique modifié"
        base.ecrire("UPDATE filaments SET donnees_json=? WHERE id='fixture'", (json.dumps(d),))
        assert any("fiche filament non conforme" in e for e in verifier(base.prive))
        generer(base)
        assert "Retour synthétique modifié" in (base.prive / rel).read_text()
        assert archive.read_bytes() == b"archive immuable"
        assert not verifier(base.prive)
    finally:
        base.fermer()
