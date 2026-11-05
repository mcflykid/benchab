"""Enregistre les fiches de préparation dans une base privée isolée."""

import json
import tempfile
import unittest
from pathlib import Path

from benchab.donnees.base import Base
from benchab.projets.preparations import enregistrer


class PreparationRegistryTests(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.racine = Path(temporaire.name)
        self.base = Base(self.racine / "prive")
        self.addCleanup(self.base.fermer)
        self.dossier = self.base.prive / "projets" / "essai" / "preparations" / "P01"
        self.dossier.mkdir(parents=True)
        self.fichier = self.dossier / "PREPARATION.json"
        self.fiche = {
            "id": "P01",
            "date": "2026-11-04T12:00:00+01:00",
            "statut": "préparation candidate — NON imprimée",
            "envoi": None,
            "retour_physique": None,
            "gcode": {"1": {"sha256": "a" * 64}},
        }
        self.ecrire()

    def ecrire(self):
        self.fichier.write_text(json.dumps(self.fiche, ensure_ascii=False))

    def test_fiche_inseree_et_doublon_identique_idempotent(self):
        premier = enregistrer(self.base, self.fichier)
        second = enregistrer(self.base, self.fichier)
        self.assertFalse(premier["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(premier["projet_id"], "projets/essai")
        row = self.base.connexion.execute("SELECT * FROM preparations").fetchone()
        self.assertEqual(row["id"], premier["preparation_id"])
        self.assertEqual(json.loads(row["donnees_json"]), self.fiche)
        self.assertEqual(self.base.connexion.execute("SELECT count(*) FROM preparations").fetchone()[0], 1)
        self.assertTrue((self.base.prive / "export/preparations.jsonl").is_file())

    def test_fiche_modifiee_refuse_de_remplacer_enregistrement_fige(self):
        enregistrer(self.base, self.fichier)
        self.fiche["statut"] = "envoi déclaré — résultat physique non vérifié"
        self.fiche["envoi"] = {"code": 1, "statut": "WARN"}
        self.ecrire()
        with self.assertRaisesRegex(ValueError, "contenu différent"):
            enregistrer(self.base, self.fichier)
        row = self.base.connexion.execute("SELECT donnees_json FROM preparations").fetchone()
        self.assertIsNone(json.loads(row[0])["envoi"])

    def test_codes_et_statuts_d_envoi_sont_conserves(self):
        self.fiche["envoi"] = {
            "code": 1,
            "statut": "WARN",
            "controle": {"code": 2, "statut": "FAIL"},
        }
        self.ecrire()
        enregistrer(self.base, self.fichier, {"code": 1, "statut": "WARN"})
        row = self.base.connexion.execute("SELECT donnees_json FROM preparations").fetchone()
        donnees = json.loads(row[0])
        self.assertEqual(donnees["envoi"]["controle"], {"code": 2, "statut": "FAIL"})
        self.assertEqual(donnees["enregistrement_envoi"], {"code": 1, "statut": "WARN"})

    def test_fiche_hors_base_prive_refusee(self):
        hors = self.racine / "PREPARATION.json"
        hors.write_text(json.dumps(self.fiche))
        with self.assertRaisesRegex(ValueError, "base.prive"):
            enregistrer(self.base, hors)

    def test_id_defaut_depuis_chemin_relatif_et_id_explicite(self):
        premier = enregistrer(self.base, self.fichier)
        fiche_explicitement_id = dict(self.fiche, id="P02", projet_id="atelier-nord")
        second_path = self.dossier.parent / "P02" / "PREPARATION.json"
        second_path.parent.mkdir()
        second_path.write_text(json.dumps(fiche_explicitement_id))
        second = enregistrer(self.base, second_path)
        self.assertEqual(premier["projet_id"], "projets/essai")
        self.assertEqual(second["projet_id"], "atelier-nord")
        self.assertNotEqual(premier["preparation_id"], second["preparation_id"])

    def test_chemin_absolu_de_base_ne_participe_pas_a_l_identifiant(self):
        autre = Base(self.racine / "prive-reconstruit")
        self.addCleanup(autre.fermer)
        fichier_autre = autre.prive / "projets" / "essai" / "preparations" / "P01" / "PREPARATION.json"
        fichier_autre.parent.mkdir(parents=True)
        fichier_autre.write_text(json.dumps(self.fiche, ensure_ascii=False), encoding="utf-8")
        original = enregistrer(self.base, self.fichier)
        reconstruit = enregistrer(autre, fichier_autre)
        self.assertEqual(original["projet_id"], reconstruit["projet_id"])
        self.assertEqual(original["preparation_id"], reconstruit["preparation_id"])

    def test_projet_id_explicite_vide_refuse(self):
        with self.assertRaisesRegex(ValueError, "projet_id"):
            enregistrer(self.base, self.fichier, projet_id="")


if __name__ == "__main__":
    unittest.main()
