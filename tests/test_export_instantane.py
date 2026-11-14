"""Vérifie que le réemploi local ne masque pas une divergence SQLite ↔ export."""

import hashlib
import json
import sqlite3

import pytest

from benchab.donnees.base import TABLES, Base


def test_transaction_reutilise_les_digests_et_repare_un_export_falsifie(tmp_path, monkeypatch):
    """Un manifeste altéré ne remplace jamais le SHA issu de l'instantané."""
    base = Base(tmp_path)
    try:
        dossier = tmp_path / "export"
        base.ecrire(
            "INSERT INTO connaissances(id,texte,categorie,source) VALUES(?,?,?,?)",
            ("preuve", "canonique", "numerique", "fixture"),
        )
        faux = dossier / "connaissances.jsonl"
        attendu = faux.read_bytes()
        alteration = attendu.replace(b"canonique", b"corrompue")
        assert alteration != attendu and len(alteration) == len(attendu)
        faux.write_bytes(alteration)
        manifeste = dossier / "manifest.json"
        donnees = json.loads(manifeste.read_bytes())
        donnees["sha256"]["connaissances"] = hashlib.sha256(faux.read_bytes()).hexdigest()
        manifeste.write_text(json.dumps(donnees))
        projet = dossier / "projets.jsonl"
        stables = dossier / "profils.jsonl"
        inode = stables.stat().st_ino
        lire = base._lignes_canoniques
        tables = []

        def mesurer(table):
            tables.append(table)
            return lire(table)

        monkeypatch.setattr(base, "_lignes_canoniques", mesurer)
        base.ecrire("INSERT INTO projets(id,nom) VALUES(?,?)", ("temoin", "Témoin"))
        assert tables == ["projets", "connaissances"]
        assert faux.read_bytes() == attendu
        assert json.loads(projet.read_text())["id"] == "temoin"
        assert stables.stat().st_ino == inode
    finally:
        base.fermer()


@pytest.mark.parametrize("journal", ["delete", "wal"])
def test_commit_externe_avant_export_invalide_tous_les_digests(tmp_path, monkeypatch, journal):
    """Un commit extérieur ne doit pas faire publier un manifeste périmé."""
    base = Base(tmp_path)
    try:
        assert base.connexion.execute(f"PRAGMA journal_mode={journal}").fetchone()[0] == journal
        exporter = base.exporter
        lire = base._lignes_canoniques
        tables = []

        def mesurer(table):
            tables.append(table)
            return lire(table)

        def commit_avant_export():
            with sqlite3.connect(tmp_path / "benchab.sqlite") as autre:
                autre.execute("UPDATE projets SET nom=? WHERE id=?", ("Externe", "temoin"))
            exporter()

        monkeypatch.setattr(base, "_lignes_canoniques", mesurer)
        monkeypatch.setattr(base, "exporter", commit_avant_export)
        base.ecrire("INSERT INTO projets(id,nom) VALUES(?,?)", ("temoin", "Interne"))
        assert set(tables) == set(TABLES)
        assert json.loads((tmp_path / "export/projets.jsonl").read_text())["nom"] == "Externe"
    finally:
        base.fermer()


def test_export_explicite_apres_transaction_recalcule_les_tables(tmp_path, monkeypatch):
    """L’empreinte de transaction n’est pas conservée pour une publication ultérieure."""
    base = Base(tmp_path)
    try:
        base.ecrire("INSERT INTO projets(id,nom) VALUES(?,?)", ("temoin", "Initial"))
        with sqlite3.connect(tmp_path / "benchab.sqlite") as autre:
            autre.execute("UPDATE projets SET nom=? WHERE id=?", ("Nouvelle valeur", "temoin"))
        lire = base._lignes_canoniques
        tables = []

        def mesurer(table):
            tables.append(table)
            return lire(table)

        monkeypatch.setattr(base, "_lignes_canoniques", mesurer)
        base.exporter()
        assert set(tables) == set(TABLES)
        assert json.loads((tmp_path / "export/projets.jsonl").read_text())["nom"] == "Nouvelle valeur"
    finally:
        base.fermer()


def test_export_qualifie_ne_laisse_pas_committer_un_tiers_pendant_wal(tmp_path, monkeypatch):
    """Le snapshot WAL ne peut pas devenir obsolète avant la publication."""
    base = Base(tmp_path)
    try:
        assert base.connexion.execute("PRAGMA journal_mode=wal").fetchone()[0] == "wal"
        relever = base._version_export
        appels = []

        def apres_releve():
            marqueur = relever()
            appels.append(marqueur)
            if len(appels) == 2:
                with sqlite3.connect(tmp_path / "benchab.sqlite", timeout=0) as autre:
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        autre.execute("UPDATE projets SET nom=? WHERE id=?", ("Externe", "temoin"))
            return marqueur

        monkeypatch.setattr(base, "_version_export", apres_releve)
        base.ecrire("INSERT INTO projets(id,nom) VALUES(?,?)", ("temoin", "Initial"))
        assert len(appels) == 2
        assert json.loads((tmp_path / "export/projets.jsonl").read_text())["nom"] == "Initial"
        with sqlite3.connect(tmp_path / "benchab.sqlite", timeout=0) as autre:
            autre.execute("UPDATE projets SET nom=? WHERE id=?", ("Après", "temoin"))
    finally:
        base.fermer()


@pytest.mark.parametrize("echec", [False, True])
def test_export_direct_wal_libere_le_verrou_apres_publication(tmp_path, monkeypatch, echec):
    """La voie directe réserve le snapshot, puis libère SQLite même sur exception."""
    base = Base(tmp_path)
    try:
        assert base.connexion.execute("PRAGMA journal_mode=wal").fetchone()[0] == "wal"
        base.ecrire("INSERT INTO projets(id,nom) VALUES(?,?)", ("temoin", "Initial"))
        publier = base._exporter_tables

        def pendant_publication():
            with sqlite3.connect(tmp_path / "benchab.sqlite", timeout=0) as autre:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    autre.execute("UPDATE projets SET nom=? WHERE id=?", ("Externe", "temoin"))
            if echec:
                raise RuntimeError("publication interrompue")
            return publier()

        monkeypatch.setattr(base, "_exporter_tables", pendant_publication)
        if echec:
            with pytest.raises(RuntimeError, match="publication interrompue"):
                base.exporter()
        else:
            base.exporter()
        assert not base.connexion.in_transaction
        with sqlite3.connect(tmp_path / "benchab.sqlite", timeout=0) as autre:
            autre.execute("UPDATE projets SET nom=? WHERE id=?", ("Après", "temoin"))
    finally:
        base.fermer()


def test_transaction_evite_de_relire_une_grande_table_sans_changement(tmp_path):
    """Une écriture de filament réemploie les empreintes des projets inchangés."""
    base = Base(tmp_path)
    try:
        base.ecrire("INSERT INTO projets(id,nom) VALUES('projet','Test')")
        requetes = []
        base.connexion.set_trace_callback(requetes.append)
        base.ecrire("INSERT INTO filaments(id,nom,matiere) VALUES('f','Témoin','PLA')")
        assert not any('SELECT * FROM main."projets"' in sql for sql in requetes)
        dossier = tmp_path / "export"
        manifeste = json.loads((dossier / "manifest.json").read_bytes())
        for table in TABLES:
            donnees = (dossier / f"{table}.jsonl").read_bytes()
            lignes = [dict(ligne) for ligne in base.connexion.execute(
                f'SELECT * FROM "{table}" ORDER BY id'
            )]
            assert [json.loads(ligne) for ligne in donnees.splitlines()] == lignes
            assert manifeste["sha256"][table] == hashlib.sha256(donnees).hexdigest()
    finally:
        base.fermer()


def test_transaction_revalide_un_chemin_qui_est_devenu_un_lien(tmp_path):
    """Un SHA SQLite stable ne suffit pas lorsque le chemin a changé sur disque."""
    base = Base(tmp_path)
    try:
        fichier = tmp_path / "rapport.txt"
        fichier.write_text("Initial")
        base.ecrire("INSERT INTO projets(id,nom,donnees_json) VALUES(?,?,?)",
                    ("projet", "Test", json.dumps({"rapport": str(fichier)})))
        avant = (tmp_path / "export/filaments.jsonl").read_bytes()
        fichier.unlink()
        fichier.symlink_to(tmp_path / "autre")
        with pytest.raises(ValueError, match="Lien symbolique"):
            base.ecrire("INSERT INTO filaments(id,nom,matiere) VALUES('f','Témoin','PLA')")
        assert not base.connexion.execute("SELECT 1 FROM filaments").fetchall()
        assert (tmp_path / "export/filaments.jsonl").read_bytes() == avant
    finally:
        base.fermer()


def test_transaction_suit_les_ecritures_declenchees_par_sqlite(tmp_path):
    """Un trigger métier local fait partie des tables à recalculer."""
    base = Base(tmp_path)
    try:
        base.connexion.execute("""
            CREATE TRIGGER maj_projet AFTER INSERT ON filaments
            BEGIN UPDATE projets SET nom=NEW.nom; END
        """)
        base.ecrire("INSERT INTO projets(id,nom) VALUES('projet','Initial')")
        base.ecrire("INSERT INTO filaments(id,nom,matiere) VALUES('f','Témoin','PLA')")
        projet = json.loads((tmp_path / "export/projets.jsonl").read_bytes())
        assert projet["nom"] == "Témoin"
    finally:
        base.fermer()


def test_transaction_recalcule_apres_un_acces_direct_a_sqlite(tmp_path):
    """Un commit via la connexion publique invalide les empreintes de la dernière publication."""
    base = Base(tmp_path)
    try:
        base.ecrire("INSERT INTO projets(id,nom) VALUES('projet','Initial')")
        with base.connexion:
            base.connexion.execute("UPDATE projets SET nom='Externe'")
        base.ecrire("INSERT INTO filaments(id,nom,matiere) VALUES('f','Témoin','PLA')")
        projet = json.loads((tmp_path / "export/projets.jsonl").read_bytes())
        assert projet["nom"] == "Externe"
    finally:
        base.fermer()


def test_transaction_reprend_apres_un_export_interrompu(tmp_path, monkeypatch):
    """Le commit SQLite survit à l'erreur d'export ; la transaction suivante répare."""
    base = Base(tmp_path)
    try:
        base.ecrire("INSERT INTO projets(id,nom) VALUES('projet','Initial')")
        with monkeypatch.context() as patch:
            def interrompre(*args):
                raise OSError("publication interrompue")

            patch.setattr(base, "_publier_table", interrompre)
            with pytest.raises(OSError, match="publication interrompue"):
                base.ecrire("UPDATE projets SET nom='Après erreur'")
        fichier = tmp_path / "export/projets.jsonl"
        assert json.loads(fichier.read_bytes())["nom"] == "Initial"
        assert base.connexion.execute("SELECT nom FROM projets").fetchone()[0] == "Après erreur"
        base.ecrire("INSERT INTO filaments(id,nom,matiere) VALUES('f','Témoin','PLA')")
        assert json.loads(fichier.read_bytes())["nom"] == "Après erreur"
        manifeste = json.loads((tmp_path / "export/manifest.json").read_bytes())
        assert manifeste["sha256"]["projets"] == hashlib.sha256(fichier.read_bytes()).hexdigest()
    finally:
        base.fermer()


def test_acces_blob_direct_invalide_le_reemploi(tmp_path):
    """Une écriture BLOB peut changer un champ texte SQL sans incrémenter total_changes."""
    base = Base(tmp_path)
    try:
        base.ecrire("INSERT INTO projets(id,nom) VALUES('projet','ABC')")
        rowid = base.connexion.execute("SELECT rowid FROM projets").fetchone()[0]
        avant = base.connexion.total_changes
        with base.connexion.blobopen("projets", "nom", rowid) as flux:
            flux.write(b"DEF")
        assert base.connexion.total_changes == avant
        base.ecrire("INSERT INTO filaments(id,nom,matiere) VALUES('f','Témoin','PLA')")
        fichier = tmp_path / "export/projets.jsonl"
        assert json.loads(fichier.read_bytes())["nom"] == "DEF"
    finally:
        base.fermer()
