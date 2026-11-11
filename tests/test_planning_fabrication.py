"""Régressions du planning sur des fiches et 3MF synthétiques."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from benchab.donnees.base import Base
from benchab.fabrication import planning
from benchab.fabrication.preparation import gcodes
from benchab.projets.envois import enregistrer as associer_envoi
from benchab.projets.preparations import enregistrer as inscrire_preparation


class PlanningFabricationTests(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.racine = Path(temporaire.name)
        self.base = Base(self.racine / "prive")
        self.addCleanup(self.base.fermer)
        self.projet = self.base.prive / "projets" / "essai"
        preparation = self.projet / "preparations" / "P02"
        preparation.mkdir(parents=True)
        self.fiche = preparation / "PREPARATION.json"
        self.fiche.write_text(
            json.dumps(
                {
                    "id": "P02",
                    "envoi": None,
                    "tranche_3mf": {"fichier": "preparations/P02/tranche.3mf"},
                }
            ),
            encoding="utf-8",
        )
        archive = self.projet / "preparations/P02/tranche.3mf"
        with zipfile.ZipFile(archive, "w") as paquet:
            paquet.writestr(
                "Metadata/model_settings.config",
                '<metadata key="plater_id" value="1" />\n<metadata key="plater_name" value="Plaque témoin" />',
            )
            paquet.writestr("Metadata/plate_1.gcode", "G1 X1 Y1 E1\n")
        donnees = json.loads(self.fiche.read_text(encoding="utf-8"))
        donnees["gcode"] = gcodes(archive)
        self.fiche.write_text(json.dumps(donnees), encoding="utf-8")
        (self.projet / "DECLARATION-ENVOI.json").write_text(
            json.dumps({"filaments": {"1": {"matiere": "PLA"}}}), encoding="utf-8"
        )

    def test_noms_plaques_refuse_les_entrees_ambiguës_sans_modifier_source(self):
        """Le lecteur commun refuse un index ZIP dupliqué et ne réécrit pas le tranchage."""
        archive = self.projet / "preparations/P02/tranche.3mf"
        with zipfile.ZipFile(archive, "w") as paquet:
            paquet.writestr("Metadata/model_settings.config", "<metadata key=\"plater_id\" value=\"1\" />")
            paquet.writestr("Metadata/plate_1.gcode", "G1 X1 Y1 E1\n")
            with self.assertWarns(UserWarning):
                paquet.writestr("Metadata/plate_1.gcode", "G1 X2 Y2 E1\n")
        avant = archive.read_bytes()
        with self.assertRaises(ValueError):
            planning._noms_plaques(archive)
        self.assertEqual(archive.read_bytes(), avant)

    def test_collecte_estimations_et_chemin_relatif_sans_envoi(self):
        lectures = [{"index": 1, "secondes": 7200, "grammes": 15.26}]
        with patch.object(planning, "resultats", return_value=lectures):
            plaques = planning.plaques_pretes(self.base.prive / "projets")
        self.assertEqual(len(plaques), 1)
        self.assertEqual(plaques[0]["nom"], "Plaque témoin")
        self.assertEqual(plaques[0]["fichier"], "essai/preparations/P02/tranche.3mf")
        self.assertEqual(plaques[0]["filament"], "PLA")
        self.assertEqual(plaques[0]["heures"], 2)
        self.assertEqual(plaques[0]["grammes"], 15.3)

    def test_derniere_preparation_avec_envoi_ecarte_le_projet(self):
        self.fiche.write_text(
            json.dumps({"id": "P02", "envoi": {"code": 1, "statut": "WARN"}}),
            encoding="utf-8",
        )
        with patch.object(planning, "resultats") as lire:
            self.assertEqual(planning.plaques_pretes(self.base.prive / "projets"), [])
        lire.assert_not_called()

    def test_evenement_envoi_ecarte_sans_modifier_la_preparation_figee(self):
        inscrire_preparation(self.base, self.fiche)
        source = self.base.prive / "export.gcode"
        source.write_text("G1 X1 Y1 E1 ; export synthétique\n", encoding="utf-8")
        avant = self.fiche.read_bytes()
        ligne_avant = tuple(self.base.connexion.execute("SELECT empreinte, donnees_json FROM preparations").fetchone())
        associer_envoi(self.base, self.projet, "P02", source)
        with patch.object(planning, "resultats") as lire:
            self.assertEqual(planning.plaques_pretes(self.base.prive / "projets", self.base), [])
        lire.assert_not_called()
        self.assertEqual(self.fiche.read_bytes(), avant)
        self.assertEqual(
            tuple(self.base.connexion.execute("SELECT empreinte, donnees_json FROM preparations").fetchone()),
            ligne_avant,
        )

    def test_creneaux_regroupent_par_filament_sans_depasser_la_limite(self):
        plaques = [
            {"filament": "PLA", "heures": 2},
            {"filament": "PLA", "heures": 1},
            {"filament": "PETG", "heures": 3},
        ]
        groupes = planning.creneaux(plaques, 3)
        self.assertEqual([[p["heures"] for p in groupe] for groupe in groupes], [[3], [2, 1]])

    def test_limite_invalide_refusee(self):
        for valeur in (0, -1, float("nan"), float("inf")):
            with self.subTest(valeur=valeur), self.assertRaises(ValueError):
                planning.creneaux([], valeur)

    def test_cli_ecrit_uniquement_dans_prive_et_refuse_sortie_exterieure(self):
        destination = self.base.prive / "rapports/planning.md"
        with patch.object(planning, "plaques_pretes", return_value=[]):
            code = planning.main(
                [
                    "--prive",
                    str(self.base.prive),
                    "--projets",
                    str(self.base.prive / "projets"),
                    "--sortie",
                    str(destination),
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("Aucune impression", destination.read_text(encoding="utf-8"))
        hors = Path.cwd() / ".planning-test-interdit.md"
        self.addCleanup(hors.unlink, missing_ok=True)
        with patch.object(planning, "plaques_pretes", return_value=[]), self.assertRaises(SystemExit):
            planning.main(
                [
                    "--prive",
                    str(self.base.prive),
                    "--projets",
                    str(self.base.prive / "projets"),
                    "--sortie",
                    str(hors),
                ]
            )
        self.assertFalse(hors.exists())


if __name__ == "__main__":
    unittest.main()
