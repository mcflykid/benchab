"""Le registre historique devient un import ; la base reste canonique ensuite."""

import json

import pytest

from benchab.connaissances import bobine, memoire
from benchab.donnees.base import Base
from benchab.donnees.reference import importer


def test_import_initial_prive_conserve_contexte_puis_ignore_changements_json(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        registre = base.prive / "donnees/connaissances/registre.json"
        registre.parent.mkdir(parents=True)
        item = {
            "id": "exemple", "titre": "Jeu traversant", "conditions": "axe vertical",
            "exclusions": "pas un trou borgne", "niveau_preuve": "NUMERIQUE", "validite": "contextuelle",
            "sources": [{"chemin": "fixture.md"}],
        }
        registre.write_text(json.dumps({"connaissances": [item], "journaux": {}}))
        avant = registre.read_bytes()
        assert memoire.chercher(base.prive, "traversant") == [item]
        assert registre.read_bytes() == avant
        assert base.connexion.execute("SELECT public FROM connaissances").fetchone()[0] == 0
        registre.write_text('{"connaissances": [], "journaux": {}}')
        assert memoire.chercher(base.prive, "traversant") == [item]
        donnees = memoire.charger(base.prive)
        donnees["journaux"] = {"exemple.md": {"sha256": "a" * 64, "connaissances": [], "statut": "À EXAMINER"}}
        memoire.sauver(base.prive, donnees)
        assert memoire.charger(base.prive)["journaux"] == donnees["journaux"]
        assert json.loads(registre.read_text())["journaux"] == {}
        assert base.connexion.execute("SELECT count(*) FROM impressions").fetchone()[0] == 0
        export = (base.prive / "export/decisions.jsonl").read_text()
        assert "exemple.md" in export
    finally:
        base.fermer()


def test_reference_generale_idempotente_preserve_faits_prives_et_import_historique(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        from benchab.donnees.reference import _lignes
        nombre_reference = len(_lignes())
        assert nombre_reference >= 2
        assert importer(base) == nombre_reference
        assert importer(base) == 0
        assert memoire.verifier(base.prive) == []
        registre = base.prive / "donnees/connaissances/registre.json"
        registre.parent.mkdir(parents=True)
        registre.write_text(json.dumps({"connaissances": [{
            "id": "ancien", "titre": "Contexte privé", "sources": [], "niveau_preuve": "NUMERIQUE",
        }]}))
        assert len(memoire.chercher(base.prive, "Contexte privé")) == 1
        assert base.connexion.execute("SELECT count(*) FROM connaissances").fetchone()[0] == nombre_reference + 1
        base.ecrire("UPDATE connaissances SET texte='Valeur locale à conserver' WHERE id=?", (
            "savoir-asn-2003-couture-candidats",
        ))
        with pytest.raises(ValueError, match="existante différente"):
            importer(base)
        assert base.connexion.execute("SELECT texte FROM connaissances WHERE id=?", (
            "savoir-asn-2003-couture-candidats",
        )).fetchone()[0] == "Valeur locale à conserver"
    finally:
        base.fermer()


def test_index_refuse_mutation_concurrente_des_connaissances(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        donnees = memoire.charger(base.prive)
        base.ecrire("INSERT INTO connaissances VALUES (?,?,?,?,?,?,?)", (
            "nouvelle", "Nouvelle entrée", "decision", "fixture", None, 0, "{}",
        ))
        with pytest.raises(ValueError, match="concurremment"):
            memoire.sauver(base.prive, donnees)
        assert base.connexion.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
    finally:
        base.fermer()


def test_bobine_native_sans_registre_json_ne_cree_pas_calibration_ou_impression(tmp_path):
    base = Base(tmp_path / "atelier")
    try:
        donnees = {
            "id": "exemple-pla", "marque": "Exemple", "nom": "Filament exemple", "matiere": "PLA",
            "possession": "confirmée explicitement pour ce scénario synthétique",
            "sources": {s: {"statut": "NON VÉRIFIÉ"} for s in ("fabricant", "orca", "3dfilamentprofiles")},
        }
        relatif = bobine.importer(base.prive, donnees)
        assert (base.prive / relatif).is_file()
        assert not (base.prive / "donnees/filaments/references.json").exists()
        assert base.connexion.execute("SELECT matiere FROM filaments").fetchone()[0] == "PLA"
        assert tuple(base.connexion.execute("SELECT masse_initiale_g,masse_restante_g FROM bobines").fetchone()) == (
            None, None,
        )
        assert base.connexion.execute("SELECT count(*) FROM impressions").fetchone()[0] == 0
        assert base.connexion.execute("SELECT count(*) FROM connaissances").fetchone()[0] == 0
        assert "exemple-pla" in (base.prive / "export/bobines.jsonl").read_text()
        with pytest.raises(ValueError, match="déjà présente"):
            bobine.importer(base.prive, donnees)
    finally:
        base.fermer()
