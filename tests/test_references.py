import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from benchab.references_visuelles import references as ref


class ReferencesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.image("face.png")
        self.image("rendu.png")
        (self.root / "modele.py").write_text("longueur = 40\n")
        self.spec = {
            "besoin": "Pièce de test",
            "vues_obligatoires": ["face"],
            "sources_modele": ["modele.py"],
            "seuils": {"iou_min": 0.95, "centre_max_px": 1, "bbox_max_px": 1},
            "vues": {
                "face": {
                    "image": "face.png",
                    "rendu": "rendu.png",
                    "origine": "generee",
                    "provenance": "image synthétique de test",
                    "projection": "orthographique",
                    "mode": "clair",
                    "seuil_masque": 127,
                }
            },
        }

    def image(self, nom, box=(10, 10, 30, 30), size=(50, 50)):
        image = Image.new("L", size)
        if box:
            ImageDraw.Draw(image).rectangle(box, fill=255)
        image.save(self.root / nom)

    def figer(self):
        (self.root / "spec.json").write_text(json.dumps(self.spec))
        ref.figer(self.root / "spec.json", self.root / "contrat.json")
        return self.root / "contrat.json"

    def comparer(self):
        return ref.comparer(self.figer(), {"face": self.root / "rendu.png"}, self.root / "preuve")

    def test_identique_et_overlay(self):
        rapport = self.comparer()
        self.assertEqual(rapport["statut"], "PASS")
        self.assertEqual(rapport["vues"]["face"]["iou"], 1)
        self.assertTrue((self.root / "preuve/vue-00-superposition.png").is_file())

    def test_decalage_non_masque(self):
        self.image("rendu.png", (15, 10, 35, 30))
        self.assertEqual(self.comparer()["statut"], "FAIL")

    def test_tailles_differentes_refusees(self):
        self.image("rendu.png", size=(100, 100))
        self.assertEqual(self.comparer()["statut"], "FAIL")

    def test_rendu_vide(self):
        self.image("rendu.png", box=None)
        self.assertEqual(self.comparer()["statut"], "FAIL")

    def test_reference_vide_refusee(self):
        self.image("face.png", box=None)
        with self.assertRaises(ValueError):
            self.figer()

    def test_reference_modifiee_invalide(self):
        contrat = self.figer()
        self.image("face.png", (12, 12, 32, 32))
        self.assertEqual(ref.audit_contrat(contrat)["statut"], "FAIL")

    def test_rendu_modifie_invalide(self):
        contrat = self.figer()
        self.image("rendu.png", (12, 12, 32, 32))
        self.assertEqual(ref.audit_contrat(contrat)["statut"], "FAIL")

    def test_modele_modifie_invalide(self):
        contrat = self.figer()
        (self.root / "modele.py").write_text("longueur=80\n")
        self.assertEqual(ref.audit_contrat(contrat)["statut"], "FAIL")

    def test_vue_obligatoire_manquante(self):
        self.spec["vues_obligatoires"].append("dos")
        self.assertEqual(self.comparer()["statut"], ref.NON_VERIFIE)

    def test_rendu_manquant(self):
        contrat = self.figer()
        self.assertEqual(ref.comparer(contrat, {}, self.root / "preuve")["statut"], ref.NON_VERIFIE)

    def test_concept_non_mesurable(self):
        self.spec["vues"]["face"]["projection"] = "concept"
        self.assertEqual(self.comparer()["statut"], ref.NON_VERIFIE)

    def test_seuils_obligatoires(self):
        del self.spec["seuils"]
        with self.assertRaises(ValueError):
            self.figer()

    def test_pas_ecrasement_contrat(self):
        self.figer()
        with self.assertRaises(FileExistsError):
            self.figer()

    def test_audit_complet(self):
        self.assertEqual(ref.audit_contrat(self.figer())["statut"], "PASS")

    def test_comparaison_sources_modifiees(self):
        contrat = self.figer()
        (self.root / "modele.py").write_text("longueur=80\n")
        self.assertEqual(
            ref.comparer(contrat, {"face": self.root / "rendu.png"}, self.root / "preuve")["statut"], "FAIL"
        )

    def test_contrat_vide_refuse(self):
        contrat = self.figer()
        data = json.loads(contrat.read_text())
        data["vues_obligatoires"] = []
        contrat.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ref.audit_contrat(contrat)

    def manifest(self):
        (self.root / "modele.stl").write_text("solid test\nendsolid test\n")
        camera = {"position": [0, -100, 0], "target": [0, 0, 0], "ortho_scale": 50}
        spec = {
            "meshes": [{"path": "modele.stl", "units": "mm"}],
            "output_dir": ".",
            "resolution": [50, 50],
            "views": {"face": camera},
        }
        (self.root / "camera.json").write_text(json.dumps(spec))
        data = {
            "schema": 1,
            "spec_path": "camera.json",
            "spec_sha256": ref.empreinte(self.root / "camera.json"),
            "sources": [{"path": "modele.stl", "sha256": ref.empreinte(self.root / "modele.stl"), "units": "mm"}],
            "resolution": [50, 50],
            "views": {
                "face": {"image": "rendu.png", "sha256": ref.empreinte(self.root / "rendu.png"), "camera": camera}
            },
        }
        (self.root / "manifest.json").write_text(json.dumps(data))
        return self.root / "manifest.json"

    def test_iteration_sans_regeler_reference(self):
        self.spec["sources_modele"] = []
        self.spec["vues"]["face"].pop("rendu")
        contrat = self.figer()
        initial = ref.empreinte(contrat)
        (self.root / "modele.py").write_text("longueur = 80\n")
        rapport = ref.evaluer(contrat, self.manifest(), self.root / "evaluation")
        self.assertEqual(rapport["statut"], "PASS")
        self.assertEqual(initial, ref.empreinte(contrat))
        self.assertEqual(ref.audit_contrat(contrat, self.root / "evaluation/rapport.json")["statut"], "PASS")
        (self.root / "modele.stl").write_text("solid modifie\nendsolid modifie\n")
        self.assertEqual(ref.audit_contrat(contrat, self.root / "evaluation/rapport.json")["statut"], "FAIL")

    def test_evaluation_rendu_perime(self):
        contrat = self.figer()
        manifest = self.manifest()
        self.image("rendu.png", (15, 15, 30, 30))
        with self.assertRaises(ValueError):
            ref.evaluer(contrat, manifest)

    def test_evaluation_camera_perimee(self):
        contrat = self.figer()
        manifest = self.manifest()
        (self.root / "camera.json").write_text("{}")
        with self.assertRaises(ValueError):
            ref.evaluer(contrat, manifest)

    def test_evaluation_concept_non_metrique(self):
        self.spec["vues"]["face"]["projection"] = "concept"
        contrat = self.figer()
        manifest = self.manifest()
        resultat = ref.evaluer(contrat, manifest)
        self.assertEqual(resultat["statut"], ref.NON_VERIFIE)
        self.assertFalse(resultat["vues"]["face"]["metrique_orthographique"])
        self.assertEqual(resultat["liaison_modele"], "PASS")

    def test_concept_sans_rendu_non_eligible_revue(self):
        self.spec["vues"]["face"]["projection"] = "concept"
        contrat = self.figer()
        resultat = ref.comparer(contrat, {}, self.root / "evaluation")
        ligne = resultat["vues"]["face"]
        self.assertEqual(ligne["statut"], ref.NON_VERIFIE)
        self.assertEqual(ligne["raison"], "rendu absent")
        self.assertNotIn("rendu_sha256", ligne)
        self.assertNotIn("projection", ligne)

    def test_manifest_vues_absentes_refuse(self):
        contrat = self.figer()
        manifest = self.manifest()
        data = json.loads(manifest.read_text())
        data["views"] = {}
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ref.evaluer(contrat, manifest)

    def test_manifest_camera_differente_refuse(self):
        contrat = self.figer()
        manifest = self.manifest()
        data = json.loads(manifest.read_text())
        data["views"]["face"]["camera"]["ortho_scale"] = 90
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ref.evaluer(contrat, manifest)

    def test_manifest_unites_differentes_refuse(self):
        contrat = self.figer()
        manifest = self.manifest()
        data = json.loads(manifest.read_text())
        data["sources"][0]["units"] = "m"
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ref.evaluer(contrat, manifest)

    def test_manifest_resolution_differente_refuse(self):
        contrat = self.figer()
        manifest = self.manifest()
        data = json.loads(manifest.read_text())
        data["resolution"] = [100, 100]
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ref.evaluer(contrat, manifest)

    def test_pas_liaison_modele(self):
        self.spec["sources_modele"] = []
        self.assertEqual(ref.audit_contrat(self.figer())["statut"], ref.NON_VERIFIE)


if __name__ == "__main__":
    unittest.main()
