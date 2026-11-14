"""Tests de retour.py (chantier K, lot 6) : racine, envois et préparation synthétiques ; aucun fichier réel touché."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from benchab.donnees.base import Base
from benchab.fabrication import preparation as PR
from benchab.projets.envois import lire_fiche
from benchab.projets.preparations import enregistrer as inscrire

REGLAGES = json.dumps(
    {
        "filament_settings_id": ["Glow"],
        "nozzle_type": "brass",
        "nozzle_temperature_initial_layer_BRASS": ["210"],
        "nozzle_temperature_BRASS": ["190"],
        "layer_height": "0.2",
    }
)


def tranche(p, gcode):
    with ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/plate_1.gcode", gcode)
        z.writestr("Metadata/project_settings.config", REGLAGES)


class Retour(unittest.TestCase):
    def setUp(self):
        """Fige une préparation synthétique dans un atelier jetable, sans lancer le slicer."""
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name).resolve()
        self.base = Base(self.r)
        self.ident = "P01"
        self.p = self.r / "projets/lampe"
        self.p.mkdir(parents=True)
        (self.r / "suivi/journal").mkdir(parents=True, exist_ok=True)
        self.src = self.r / "tmp-slicer/1"
        self.src.mkdir(parents=True)
        self.arch = self.r / "archive"
        (self.p / "DECLARATION-ENVOI.json").write_text(
            json.dumps(
                {"filaments": {"1": {"matiere": "PLA Glow", "emplacement_ace": 4, "temp_1re": 210, "temp": 190}}}
            )
        )
        tranche(self.r / "tranche.3mf", "G1 X1 Y1 E1\n")
        tranche(self.r / "plaques.3mf", "")
        PR.creer(
            self.base,
            self.p,
            "P01",
            self.r / "plaques.3mf",
            self.r / "tranche.3mf",
            sources=[self.p / "DECLARATION-ENVOI.json"],
            # Le retour compare des archives synthétiques. La qualification du
            # poste est séparée (docs/reference/controles.md).
            environnement={
                "code": 0,
                "mesures": {"python": {"version": "3.13"}},
                "resultats": [{"controle": "Fixture de retour", "statut": "PASS"}],
            },
        )

    def tearDown(self):
        self.base.fermer()
        self.t.cleanup()

    def lancer(self, *a):
        r = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "benchab.fabrication.retour",
                "--prive",
                str(self.r),
                "--projet",
                str(self.p),
                "--id",
                self.ident,
                "--source",
                str(self.src.parent),
                "--archive",
                str(self.arch),
                *a,
            ],
            capture_output=True,
            text=True,
            env=dict(os.environ, RETOUR_RACINE=str(self.r)),
        )
        return r.returncode, r.stdout + r.stderr

    def fiche(self):
        return lire_fiche(self.base, self.p, self.ident)

    def test_envoi_identique_enregistre_et_journal_cree_une_fois(self):
        tranche(
            self.src / "1027-0310-Lampe(1)_a.gcode.3mf", "G1 X1 Y1 E1 ; commentaire différent\n"
        )  # mêmes commandes, commentaires ignorés
        c, o = self.lancer()
        self.assertEqual(c, 0, o)
        self.assertIn("enregistré", o)
        self.assertIn("identiques", json.dumps(self.fiche()["envoi"]["comparaison"], ensure_ascii=False))
        j = next((self.r / "suivi/journal").rglob("*-impression-lampe-p01.md"))
        t = j.read_text()
        for attendu in (
            "Type : impression",
            "Niveau de preuve : À VÉRIFIER",
            "À COMPLÉTER",
            "PLA Glow, ACE 4",
            "buse appliquée 210 puis 190 °C",
            "| 1 | À COMPLÉTER",
        ):
            self.assertIn(attendu, t)
        j.write_text("notes de l’utilisateur")
        c, o = self.lancer()
        self.assertEqual(c, 0, o)
        self.assertIn("déjà enregistrés", o)
        self.assertEqual(j.read_text(), "notes de l’utilisateur")

    def test_envoi_retranche_jamais_devine_puis_designe(self):
        tranche(self.src / "1027-0311-Lampe(1)_b.gcode.3mf", "G1 X9 Y9 E1\n")
        c, o = self.lancer()
        self.assertEqual(c, 1, o)
        self.assertIn("AUCUN envoi archivé", o)
        self.assertIsNone(self.fiche()["envoi"])
        c, o = self.lancer("--fichier", str(self.arch / "1027-0311-Lampe(1)_b.gcode.3mf"))
        self.assertEqual(c, 0, o)
        self.assertIn("DIFFÉRENTES", json.dumps(self.fiche()["envoi"]["comparaison"], ensure_ascii=False))

    def test_commandes_identiques_sur_deux_plaques_pas_d_association_auto(self):
        d = self.fiche()
        d["id"] = "P02"
        d["gcode"]["2"] = dict(d["gcode"]["1"])
        self.ident = "P02"
        f = self.p / "preparations/P02/PREPARATION.json"
        f.parent.mkdir()
        f.write_text(json.dumps(d))
        inscrire(self.base, f)
        tranche(self.src / "ambigue.gcode.3mf", "G1 X1 Y1 E1\n")
        c, o = self.lancer()
        self.assertEqual(c, 1, o)
        self.assertIsNone(self.fiche()["envoi"])

    def test_plaque_inconnue_dans_archive_pas_d_association_partielle(self):
        f = self.src / "mixte.gcode.3mf"
        tranche(f, "G1 X1 Y1 E1\n")
        with ZipFile(f, "a") as z:
            z.writestr("Metadata/plate_2.gcode", "G1 X99\n")
        c, o = self.lancer()
        self.assertEqual(c, 1, o)
        self.assertIsNone(self.fiche()["envoi"])


if __name__ == "__main__":
    unittest.main()
