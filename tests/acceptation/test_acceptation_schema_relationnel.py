"""Contraintes relationnelles, mesures et valeurs physiques dans une base temporaire uniquement."""

import sqlite3
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture
def base(tmp_path):
    with sqlite3.connect(tmp_path / "benchab.sqlite") as connexion:
        connexion.executescript((RACINE / "benchab/donnees/schema.sql").read_text())
        yield connexion


def test_relations_refusent_orphelins_et_suppression_parent(base):
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("INSERT INTO preparations (id, projet_id, nom) VALUES ('p1', 'absent', 'P01')")
    base.execute("INSERT INTO projets (id, nom) VALUES ('projet', 'Pièce témoin')")
    base.execute("INSERT INTO preparations (id, projet_id, nom) VALUES ('p1', 'projet', 'P01')")
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("DELETE FROM projets WHERE id = 'projet'")
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("INSERT INTO preparations (id, projet_id, nom) VALUES ('p2', 'projet', 'P01')")


def test_masse_inconnue_et_masse_negative(base):
    base.execute("INSERT INTO filaments (id, nom, matiere) VALUES ('pla', 'Témoin', 'PLA')")
    base.execute("INSERT INTO bobines (id, filament_id) VALUES ('bobine', 'pla')")
    assert base.execute("SELECT masse_restante_g FROM bobines").fetchone()[0] is None
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("UPDATE bobines SET masse_restante_g = -1")


def test_faits_prives_par_defaut_et_json_invalide_refuse(base):
    base.execute(
        "INSERT INTO connaissances (id, texte, categorie, source) VALUES (?, ?, ?, ?)",
        ("fait", "Observation du témoin", "observation", "fixture"),
    )
    assert base.execute("SELECT public FROM connaissances").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("UPDATE connaissances SET donnees_json = '{invalide'")
    with pytest.raises(sqlite3.IntegrityError):
        base.execute("UPDATE connaissances SET categorie = 'pret-a-imprimer'")


def test_historique_impressions_pas_de_succes_implicite(base):
    base.execute("INSERT INTO projets (id, nom) VALUES ('projet', 'Pièce témoin')")
    base.execute("INSERT INTO preparations (id, projet_id, nom) VALUES ('p1', 'projet', 'P01')")
    base.execute("INSERT INTO impressions (id, preparation_id) VALUES ('i1', 'p1')")
    assert base.execute("SELECT resultat FROM impressions").fetchone()[0] == "inconnu"
    base.execute(
        "INSERT INTO retours (id, impression_id, texte, source) VALUES ('r1', 'i1', 'À examiner', 'fixture')"
    )
    assert base.execute("SELECT validation_physique FROM retours").fetchone()[0] == 0
