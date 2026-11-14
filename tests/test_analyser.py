import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from benchab.references_visuelles import analyser as outil


class AnalyseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spec = {"tolerance_relative": 0.03, "vues": {}}
        for nom, w, h in [("face", 40, 80), ("profil", 20, 80), ("dessus", 40, 20)]:
            self.image(nom + ".png", w, h)
            self.spec["vues"][nom] = {
                "image": nom + ".png",
                "mode": "clair",
                "seuil_masque": 127,
                "projection": "orthographique",
                "mm_par_pixel": 0.5,
            }

    def image(self, nom, w, h, x=10, y=10):
        image = Image.new("L", (128, 128))
        ImageDraw.Draw(image).rectangle((x, y, x + w - 1, y + h - 1), fill=255)
        image.save(self.root / nom)

    def analyser(self):
        (self.root / "spec.json").write_text(json.dumps(self.spec))
        return outil.analyser(self.root / "spec.json")

    def test_coherence_ratios_cotes_calibrees(self):
        r = self.analyser()
        self.assertEqual(r["statut"], "PASS")
        self.assertEqual(r["coherence_ratios"]["W_sur_D"], 2)
        self.assertEqual(r["vues"]["face"]["cotes_mm"], {"X": 20, "Z": 40})
        self.assertTrue(all(v["statut"] == "PASS" for v in r["coherence_cotes_mm"].values()))

    def test_ratios_contradictoires(self):
        self.image("dessus.png", 60, 20)
        self.assertEqual(self.analyser()["coherence_ratios"]["statut"], "FAIL")

    def test_calibrations_contradictoires(self):
        self.spec["vues"]["dessus"]["mm_par_pixel"] = 1
        r = self.analyser()
        self.assertEqual(r["coherence_ratios"]["statut"], "PASS")
        self.assertEqual(r["coherence_cotes_mm"]["X"]["statut"], "FAIL")
        self.assertEqual(r["statut"], "FAIL")

    def test_calibration_absente_explicitement_non_verifiee(self):
        for v in self.spec["vues"].values():
            v.pop("mm_par_pixel")
        r = self.analyser()
        self.assertEqual(r["coherence_ratios"]["statut"], "PASS")
        self.assertTrue(all(v["statut"] == outil.NON_VERIFIE for v in r["coherence_cotes_mm"].values()))

    def test_vue_absente_non_verifiee(self):
        del self.spec["vues"]["profil"]
        r = self.analyser()
        self.assertEqual(r["statut"], outil.NON_VERIFIE)
        self.assertIn("profil", r["coherence_ratios"]["vues"])

    def test_concept_pas_de_calibration_inventee(self):
        self.spec["vues"]["face"]["projection"] = "concept"
        r = self.analyser()
        self.assertEqual(r["statut"], outil.NON_VERIFIE)
        self.assertEqual(r["vues"]["face"]["cotes_mm"]["statut"], outil.NON_VERIFIE)

    def test_composantes_parties_reperes(self):
        p = self.root / "face.png"
        image = Image.open(p)
        ImageDraw.Draw(image).rectangle((90, 90, 95, 95), fill=255)
        image.save(p)
        self.spec["vues"]["face"].update(
            nombre_composantes_attendu=2,
            parties={"corps": [15, 15], "accessoire": [92, 92]},
            reperes={"centre": [30, 50]},
        )
        v = self.analyser()["vues"]["face"]
        self.assertEqual(len(v["composantes"]), 2)
        self.assertNotEqual(v["parties"]["corps"]["composante_id"], v["parties"]["accessoire"]["composante_id"])
        self.assertEqual(v["reperes"]["centre"]["statut"], "PASS")

    def test_repere_hors_silhouette(self):
        self.spec["vues"]["face"]["reperes"] = {"trou": [0, 0]}
        r = self.analyser()
        self.assertEqual(r["vues"]["face"]["reperes"]["trou"]["statut"], "FAIL")
        self.assertEqual(r["statut"], "FAIL")

    def test_nombre_composantes_attendu(self):
        self.spec["vues"]["face"]["nombre_composantes_attendu"] = 2
        self.assertEqual(self.analyser()["vues"]["face"]["statut"], "FAIL")

    def test_diagnostic_signe_sans_autoalign(self):
        self.image("rendu.png", 35, 75, x=15, y=18)
        self.spec["vues"]["face"]["rendu"] = "rendu.png"
        r = self.analyser()["vues"]["face"]["diagnostic_rendu"]
        d = r["correction_image_reference_moins_rendu"]
        self.assertLess(d["dx_px"], 0)
        self.assertLess(d["dy_px"], 0)
        self.assertEqual(d["largeur_delta_px"], 5)
        self.assertEqual(d["hauteur_delta_px"], 5)
        self.assertEqual(r["statut"], "FAIL")

    def test_diagnostic_concept_non_mesurable(self):
        self.spec["vues"]["face"].update(projection="concept", rendu="face.png")
        r = self.analyser()["vues"]["face"]["diagnostic_rendu"]
        self.assertEqual(r["statut"], outil.NON_VERIFIE)
        self.assertNotIn("correction_image_reference_moins_rendu", r)

    def test_pas_ecrasement_et_sources_intactes(self):
        before = outil._helpers.empreinte(self.root / "face.png")
        self.analyser()
        outil.analyser(self.root / "spec.json", self.root / "rapport.json")
        with self.assertRaises(FileExistsError):
            outil.analyser(self.root / "spec.json", self.root / "rapport.json")
        self.assertEqual(before, outil._helpers.empreinte(self.root / "face.png"))

    def test_tolerance_explicitement_requise(self):
        del self.spec["tolerance_relative"]
        with self.assertRaises(ValueError):
            self.analyser()

    def test_calibration_resolutions_differentes(self):
        self.image("dessus.png", 80, 40)
        self.spec["vues"]["dessus"]["mm_par_pixel"] = 0.25
        r = self.analyser()
        self.assertEqual(r["statut"], "PASS")
        self.assertEqual(r["coherence_cotes_mm"]["X"]["mesures"]["dessus"], 20)

    def test_dos_contradictoire_sans_calibration(self):
        self.image("dos.png", 80, 80)
        self.spec["vues"]["dos"] = {
            "image": "dos.png",
            "mode": "clair",
            "seuil_masque": 127,
            "projection": "orthographique",
        }
        self.assertEqual(self.analyser()["coherence_dos"]["statut"], "FAIL")

    def test_calibration_invalide(self):
        self.spec["vues"]["face"]["mm_par_pixel"] = 0
        with self.assertRaises(ValueError):
            self.analyser()


if __name__ == "__main__":
    unittest.main()
