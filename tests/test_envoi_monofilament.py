"""Régression native S1 : un filament sans T0 explicite, sans masquer les cas ambigus."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from fixtures_communes import DECL, creer_3mf_test


class MonoNatif(unittest.TestCase):
    def controler(self, modifier=lambda ps, xml, g: (ps, xml, g), cap=32):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            f = creer_3mf_test(d / "base.3mf")
            with zipfile.ZipFile(f) as z:
                membres = {n: z.read(n) for n in z.namelist()}
            ps = json.loads(membres["Metadata/project_settings.config"])
            ps.update(printer_model="Anycubic Kobra S1", filament_type=["PLA"])
            xml = membres["Metadata/model_settings.config"].decode()
            g = membres["Metadata/plate_1.gcode"].decode().replace("T0\n", "")
            ps, xml, g = modifier(ps, xml, g)
            membres.update(
                {
                    "Metadata/project_settings.config": json.dumps(ps),
                    "Metadata/model_settings.config": xml,
                    "Metadata/plate_1.gcode": g,
                }
            )
            with zipfile.ZipFile(d / "essai.3mf", "w") as z:
                for n, data in membres.items():
                    z.writestr(n, data)
            dec = copy.deepcopy(DECL)
            dec["filaments"]["1"]["debit_max"] = cap
            (d / "dec.json").write_text(json.dumps(dec))
            r = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "benchab.controles.controle_envoi",
                    str(d / "essai.3mf"),
                    "--declaration",
                    str(d / "dec.json"),
                    "--json",
                    str(d / "r.json"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertTrue((d / "r.json").exists(), r.stderr)
            return r.returncode, json.loads((d / "r.json").read_text())["resultats"]

    def resultat(self, rs, nom):
        return next(x for x in rs if x["controle"] == nom)

    def test_implicite_identique_explicite_et_debit_non_nul(self):
        c, a = self.controler()
        d, b = self.controler(lambda p, x, g: (p, x, g.replace("M83", "T0\nM83")))
        self.assertEqual((c, d), (0, 0))
        r = self.resultat(a, "outils utilisés")
        self.assertIn("T0 implicite", r["detail"])
        self.assertEqual(r["statut"], "PASS")
        da = self.resultat(a, "débit commandé (filament 1)")
        db = self.resultat(b, "débit commandé (filament 1)")
        self.assertEqual(da, db)
        self.assertNotIn("sous 0.00", da["detail"])
        self.assertEqual(self.resultat(a, "chauffe de démarrage (filament 1)")["statut"], "PASS")

    def test_debit_excessif_refuse(self):
        c, a = self.controler(cap=0.01)
        self.assertEqual(c, 2)
        self.assertEqual(self.resultat(a, "débit commandé (filament 1)")["statut"], "FAIL")

    def test_temperature_demarrage_fausse_refusee(self):
        c, a = self.controler(lambda p, x, g: (p, x, g.replace("extruderTemp=220", "extruderTemp=250")))
        self.assertEqual(c, 2)
        self.assertEqual(self.resultat(a, "chauffe de démarrage (filament 1)")["statut"], "FAIL")

    def test_multifilament_sans_selection_refuse(self):
        c, a = self.controler(lambda p, x, g: (dict(p, filament_type=["PLA", "PLA"]), x, g))
        self.assertEqual(c, 2)
        self.assertEqual(self.resultat(a, "extrusions sans outil identifié")["statut"], "FAIL")

    def test_table_filament_incoherente_refusee(self):
        c, a = self.controler(lambda p, x, g: (dict(p, nozzle_temperature=["240", "220"]), x, g))
        self.assertEqual(c, 2)
        self.assertEqual(self.resultat(a, "outils utilisés")["statut"], "FAIL")

    def test_extrudeur_deux_sans_selection_refuse(self):
        c, a = self.controler(
            lambda p, x, g: (
                dict(
                    p,
                    filament_type=["PLA", "PLA"],
                    nozzle_temperature=["240", "220"],
                    nozzle_temperature_initial_layer=["220", "220"],
                    filament_max_volumetric_speed=["32", "16"],
                ),
                x.replace('key="extruder" value="1"', 'key="extruder" value="2"'),
                g,
            )
        )
        self.assertEqual(c, 2)
        self.assertEqual(self.resultat(a, "outils utilisés")["statut"], "FAIL")

    def test_autre_machine_refusee(self):
        c, a = self.controler(lambda p, x, g: (dict(p, printer_model="autre"), x, g))
        self.assertEqual(c, 2)

    def test_sans_macro_native_refuse(self):
        c, a = self.controler(lambda p, x, g: (p, x, g.replace("G9111", "G9112")))
        self.assertEqual(c, 2)

    def test_selection_explicite_erronee_refusee(self):
        c, a = self.controler(lambda p, x, g: (p, x, g.replace("M83", "T1\nM83")))
        self.assertEqual(c, 2)
        self.assertEqual(self.resultat(a, "outils utilisés")["statut"], "FAIL")

    def test_extrusion_avant_selection_explicite_refusee(self):
        c, a = self.controler(lambda p, x, g: (p, x, g + "T0\n"))
        self.assertEqual(c, 2)
        self.assertEqual(self.resultat(a, "extrusions sans outil identifié")["statut"], "FAIL")

    def test_pas_extrusion_pas_outil_implicite(self):
        c, a = self.controler(lambda p, x, g: (p, x, g.replace("E0.2", "E0")))
        self.assertEqual(c, 2)
        self.assertEqual(self.resultat(a, "outils utilisés")["statut"], "FAIL")


class AuditTemperatures(unittest.TestCase):
    controler = MonoNatif.controler
    resultat = MonoNatif.resultat

    def test_chauffe_premiere_couche_pas_comparee_a_la_suite(self):
        c, a = self.controler(lambda p, x, g: (p, x, g.replace(";TYPE:Outer wall", "M104 S220\n;TYPE:Outer wall")))
        self.assertEqual(self.resultat(a, "température après la 1re couche (filament 1)")["statut"], "PASS")

    def test_sans_commande_normale_ne_pas_inventer_temperature_du_profil(self):
        c, a = self.controler(lambda p, x, g: (p, x, g.replace("M104 S240", "; aucune baisse")))
        r = self.resultat(a, "température après la 1re couche (filament 1)")
        self.assertNotEqual(r["statut"], "PASS")

    def test_m109_apres_premiere_couche_pris_en_compte(self):
        c, a = self.controler(lambda p, x, g: (p, x, g.replace("M104 S240", "M109 S240")))
        self.assertEqual(self.resultat(a, "température après la 1re couche (filament 1)")["statut"], "PASS")


if __name__ == "__main__":
    unittest.main()
