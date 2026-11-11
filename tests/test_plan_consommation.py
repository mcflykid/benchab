"""Preuves synthétiques du plan numérique #77 ; aucun stock ni appareil touché."""

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from benchab.donnees.base import Base
from benchab.projets.plan_consommation import estimer_retour


class PlanConsommation(unittest.TestCase):
    def setUp(self):
        """Crée un atelier jetable avec retour, préparation et envoi liés par leurs SHA."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.racine = Path(self.temp.name).resolve()
        self.base = Base(self.racine)
        self.addCleanup(self.base.fermer)
        self.projet_id = "projets/fixture"
        self.preparation_id = "preparation-fixture"
        self.retour_id = "retour-fixture"
        self.projet = self.racine / self.projet_id
        self.fiche = self.projet / "preparations/P01/PREPARATION.json"
        self.fiche.parent.mkdir(parents=True)
        fiche = {"id": "P01", "gcode": {"1": {}, "2": {}}}
        octets_fiche = json.dumps(fiche, separators=(",", ":")).encode()
        self.fiche.write_bytes(octets_fiche)
        self.fiche_sha = hashlib.sha256(octets_fiche).hexdigest()
        with zipfile.ZipFile(self.racine / "envoi.gcode.3mf", "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({"filament_ids": ["f1", "f2"]}))
            archive.writestr("Metadata/plate_1.gcode", "; filament used [g] = 0, 12.5\nT1\nG1 X1 E1\n")
            archive.writestr("Metadata/plate_2.gcode", "; filament used [g] = 3.25, 0\nT0\nG1 X2 E1\n")
        self.envoi = self.racine / "envoi.gcode.3mf"
        self.envoi_sha = hashlib.sha256(self.envoi.read_bytes()).hexdigest()
        self.chemin_envoi = "archives/envois/envoi.gcode.3mf"
        self.archive_prive = self.racine / self.chemin_envoi
        self.archive_prive.parent.mkdir(parents=True)
        self.archive_prive.write_bytes(self.envoi.read_bytes())
        with self.base.transaction() as connexion:
            connexion.execute(
                "INSERT INTO filaments(id,nom,matiere) VALUES(?,?,?)",
                ("filament-fixture", "Bobine fixture", "PLA"),
            )
            connexion.execute(
                "INSERT INTO bobines(id,filament_id,masse_initiale_g,masse_restante_g) VALUES(?,?,?,?)",
                ("bobine-fixture", "filament-fixture", 100.0, 80.0),
            )
            connexion.execute(
                "INSERT INTO projets(id,nom,origine,donnees_json) VALUES(?,?,?,?)",
                (self.projet_id, "fixture", self.projet_id, "{}"),
            )
            connexion.execute(
                "INSERT INTO preparations(id,projet_id,nom,empreinte,donnees_json) VALUES(?,?,?,?,?)",
                (self.preparation_id, self.projet_id, "P01", self.fiche_sha, json.dumps(fiche)),
            )
            association = {
                "type": "association_fichier_preparation",
                "preparation_id": self.preparation_id,
                "fiche_sha256": self.fiche_sha,
                "envoi": {"fichier": self.chemin_envoi, "sha256": self.envoi_sha},
            }
            connexion.execute(
                "INSERT INTO decisions(id,projet_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
                ("association-fixture", self.projet_id, "2026-11-11", "association", self.chemin_envoi,
                 json.dumps(association)),
            )
            retour = {
                "id": self.retour_id,
                "type": "retour_physique",
                "preparation": "P01",
                "resultat": "reussie",
                "portee": "ENSEMBLE",
                "envois_identifies": [{"chemin": self.chemin_envoi, "sha256": self.envoi_sha}],
                "preuve_statut": "DECLARATION_HUMAINE",
            }
            connexion.execute(
                "INSERT INTO impressions(id,preparation_id,resultat,donnees_json) VALUES(?,?,?,?)",
                (self.retour_id, self.preparation_id, "reussie", json.dumps(retour)),
            )
            connexion.execute(
                "INSERT INTO retours(id,impression_id,texte,source,donnees_json) VALUES(?,?,?,?,?)",
                (self.retour_id, self.retour_id, "fixture", "declaration_humaine", json.dumps(retour)),
            )

    def test_plan_lie_estimation_par_plaque_et_index_outil_sans_imputation(self):
        """Le plan expose les grammes du slicer, tout en laissant la consommation NV."""
        avant = tuple(self.base.connexion.execute("SELECT id,masse_restante_g FROM bobines"))
        changements_avant = self.base.connexion.total_changes
        sha_archive_avant = hashlib.sha256(self.archive_prive.read_bytes()).hexdigest()

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertEqual(resultat["estimation"]["nature"], "ESTIMATION_SLICER")
        plaques = resultat["estimation"]["plaques"]
        self.assertEqual([plaque["numero"] for plaque in plaques], [1, 2])
        self.assertEqual(plaques[0]["outils"][1]["grammes_estimes"], 12.5)
        self.assertEqual(plaques[1]["outils"][0]["grammes_estimes"], 3.25)
        self.assertIsNone(resultat["affectation_bobine"])
        apres = tuple(self.base.connexion.execute("SELECT id,masse_restante_g FROM bobines"))
        self.assertEqual(apres, avant)
        self.assertEqual(self.base.connexion.total_changes, changements_avant)
        self.assertEqual(self.envoi_sha, hashlib.sha256(self.envoi.read_bytes()).hexdigest())
        self.assertEqual(sha_archive_avant, hashlib.sha256(self.archive_prive.read_bytes()).hexdigest())

    def test_retour_interrompu_est_non_verifie_sans_masses_utilisables(self):
        """Un retour interrompu n'est jamais transformé en quantité imputable."""
        retour = json.loads(self.base.connexion.execute(
            "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,)
        ).fetchone()[0])
        retour["resultat"] = "arretee"
        with self.base.transaction() as connexion:
            connexion.execute("UPDATE retours SET donnees_json=? WHERE id=?", (json.dumps(retour), self.retour_id))
            connexion.execute("UPDATE impressions SET donnees_json=? WHERE id=?", (json.dumps(retour), self.retour_id))
            connexion.execute("UPDATE impressions SET resultat='interrompue' WHERE id=?", (self.retour_id,))

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("retour non réussi ou portée incomplète", resultat["motifs"])

    def test_envoi_sans_association_ne_fournit_pas_d_estimation(self):
        """Un envoi non rattaché à cette préparation ne fournit aucune estimation."""
        retour = json.loads(self.base.connexion.execute(
            "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,)
        ).fetchone()[0])
        retour["envois_identifies"][0]["sha256"] = "0" * 64
        with self.base.transaction() as connexion:
            connexion.execute("UPDATE retours SET donnees_json=? WHERE id=?", (json.dumps(retour), self.retour_id))
            connexion.execute("UPDATE impressions SET donnees_json=? WHERE id=?", (json.dumps(retour), self.retour_id))

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("association d’envoi absente ou ambiguë", resultat["motifs"])

    def test_envoi_modifie_apres_association_ne_fournit_pas_d_estimation(self):
        """Un changement d'octets après l'association invalide son SHA enregistré."""
        chemin = self.racine / self.chemin_envoi
        chemin.write_bytes(b"archive modifiee")

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("envoi absent, ambigu ou empreinte divergente", resultat["motifs"])

    def test_masse_absente_sur_une_plaque_invalide_le_plan_entier(self):
        """Une plaque incomplète ne laisse pas passer une estimation partielle."""
        incomplet = self.racine / "envoi-incomplet.3mf"
        with zipfile.ZipFile(incomplet, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({"filament_ids": ["f1", "f2"]}))
            archive.writestr("Metadata/plate_1.gcode", "T1\nG1 X1 E1\n")
            archive.writestr("Metadata/plate_2.gcode", "; filament used [g] = 3.25, 0\nT0\nG1 X2 E1\n")
        nouveau_sha = hashlib.sha256(incomplet.read_bytes()).hexdigest()
        (self.racine / self.chemin_envoi).write_bytes(incomplet.read_bytes())
        with self.base.transaction() as connexion:
            association = json.loads(connexion.execute(
                "SELECT donnees_json FROM decisions WHERE id='association-fixture'"
            ).fetchone()[0])
            association["envoi"]["sha256"] = nouveau_sha
            connexion.execute(
                "UPDATE decisions SET donnees_json=? WHERE id='association-fixture'",
                (json.dumps(association),),
            )
            retour = json.loads(connexion.execute(
                "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,)
            ).fetchone()[0])
            retour["envois_identifies"][0]["sha256"] = nouveau_sha
            contenu = json.dumps(retour)
            connexion.execute("UPDATE retours SET donnees_json=? WHERE id=?", (contenu, self.retour_id))
            connexion.execute("UPDATE impressions SET donnees_json=? WHERE id=?", (contenu, self.retour_id))

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("masse ou identité d’une plaque absente", resultat["motifs"])

    def test_associations_dupliquees_restent_non_verifiees(self):
        """Deux associations compatibles ne permettent pas de choisir une source par intuition."""
        with self.base.transaction() as connexion:
            original = connexion.execute(
                "SELECT projet_id,date,texte,source,donnees_json FROM decisions WHERE id='association-fixture'"
            ).fetchone()
            connexion.execute(
                "INSERT INTO decisions(id,projet_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
                ("association-fixture-2", *tuple(original)),
            )

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("association d’envoi absente ou ambiguë", resultat["motifs"])


if __name__ == "__main__":
    unittest.main()
