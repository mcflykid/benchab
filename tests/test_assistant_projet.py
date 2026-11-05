import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from benchab.conception import assistant_projet as outil


class ConceptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projet = Path(self.tmp.name)
        (self.projet / "candidats").mkdir()
        self.data = {
            "objectif": "Un support",
            "type_forme": "mixte",
            "dimensions_mm": {"hauteur": 40},
            "reference": {"mode": "generee"},
        }

    def sauver(self):
        (self.projet / "CONCEPTION.json").write_text(json.dumps(self.data))

    def test_reference_demandee_absente_bloque(self):
        self.sauver()
        self.assertEqual(outil.verifier(self.projet)["statut"], "NON VÉRIFIÉ")

    def test_pas_de_reference_ne_fabrique_pas_de_pass(self):
        self.data["reference"]["mode"] = "aucune"
        self.sauver()
        self.assertEqual(outil.verifier(self.projet)["statut"], "NON APPLICABLE")

    def test_brief_trace_besoin_et_conception(self):
        self.sauver()
        (self.projet / "BESOIN.md").write_text("Hauteur 40 mm")
        avant = outil.brief(self.projet)
        self.assertIn("interfaces cotées", avant["parcours"])
        (self.projet / "BESOIN.md").write_text("Hauteur 50 mm")
        self.assertNotEqual(avant["besoin_sha256"], outil.brief(self.projet)["besoin_sha256"])

    def test_dimension_invalide_refusee(self):
        for valeur in (-1, True, float("nan"), "40"):
            self.data["dimensions_mm"]["hauteur"] = valeur
            self.sauver()
            with self.assertRaises(ValueError):
                outil.lire_conception(self.projet)

    def test_collection_dimensions_mal_typee_refusee_sans_traceback(self):
        for value in ([], None, "hauteur=40", 1, True):
            with self.subTest(value=value):
                self.data["dimensions_mm"] = value
                self.sauver()
                with self.assertRaises(ValueError):
                    outil.lire_conception(self.projet)
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-m",
                        "benchab.conception.assistant_projet",
                        "brief",
                        "--projet",
                        str(self.projet),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["statut"], "FAIL")
                self.assertIn("dimensions_mm", result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_brief_sans_besoin_refuse(self):
        self.data["objectif"] = ""
        self.sauver()
        with self.assertRaises(ValueError):
            outil.brief(self.projet)

    def evaluation(self, concept=False, rendu=True):
        p = self.projet
        image = Image.new("RGB", (64, 64), "black")
        ImageDraw.Draw(image).rectangle((16, 16, 48, 48), fill="white")
        image.save(p / "reference.png")
        image.save(p / "rendu.png")
        (p / "candidats/modele.stl").write_text("source synthétique pour empreintes")
        camera = {"position": [0, -100, 0], "target": [0, 0, 0], "ortho_scale": 50}
        camera_spec = {
            "meshes": [{"path": "candidats/modele.stl", "units": "mm"}],
            "resolution": [64, 64],
            "output_dir": ".",
            "views": {"face": camera},
        }
        (p / "camera.json").write_text(json.dumps(camera_spec))
        self.sauver()
        spec = {
            "besoin": "Support",
            "vues_obligatoires": ["face"],
            "seuils": {"iou_min": 0.99, "centre_max_px": 0, "bbox_max_px": 0},
            "vues": {
                "face": {
                    "image": "reference.png",
                    "mode": "clair",
                    "seuil_masque": 127,
                    "origine": "generee",
                    "provenance": "Fixture synthétique de test",
                    "projection": "concept" if concept else "orthographique",
                }
            },
        }
        (p / "spec.json").write_text(json.dumps(spec))
        module = outil.module_references()
        module.figer(p / "spec.json", p / "REFERENCES-VISUELLES.json")
        manifest = {
            "schema": 1,
            "sources": [
                {
                    "path": str(p / "candidats/modele.stl"),
                    "sha256": outil.sha(p / "candidats/modele.stl"),
                    "units": "mm",
                }
            ],
            "resolution": [64, 64],
            "spec_path": str(p / "camera.json"),
            "spec_sha256": outil.sha(p / "camera.json"),
            "views": {"face": {"image": "rendu.png", "sha256": outil.sha(p / "rendu.png"), "camera": camera}}
            if rendu
            else {},
        }
        (p / "manifest.json").write_text(json.dumps(manifest))
        module.evaluer(p / "REFERENCES-VISUELLES.json", p / "manifest.json", p / "evaluation")
        return p / "evaluation/rapport.json"

    def test_revue_metrique_et_invalidation_modele(self):
        rapport = self.evaluation()
        revue = outil.retenir(self.projet, rapport, "test", "Identité des silhouettes")
        (self.projet / "REVUE-VISUELLE.json").write_text(json.dumps(revue))
        self.assertEqual(outil.verifier(self.projet)["statut"], "PASS")
        (self.projet / "candidats/modele.stl").write_text("géométrie modifiée")
        self.assertEqual(outil.verifier(self.projet)["statut"], "FAIL")

    def test_concept_revu_distinct_du_pass(self):
        rapport = self.evaluation(concept=True)
        with self.assertRaises(ValueError):
            outil.retenir(self.projet, rapport, "test", "Revue sans choix explicite")
        revue = outil.retenir(self.projet, rapport, "test", "Proportions examinées, profondeur hypothétique", True)
        (self.projet / "REVUE-VISUELLE.json").write_text(json.dumps(revue))
        self.assertEqual(outil.verifier(self.projet)["statut"], "REVU")
        self.data["dimensions_mm"]["hauteur"] = 50
        self.sauver()
        self.assertEqual(outil.verifier(self.projet)["statut"], "FAIL")

    def test_concept_sans_rendu_non_retenable(self):
        with self.assertRaises(ValueError):
            rapport = self.evaluation(concept=True, rendu=False)
            outil.retenir(self.projet, rapport, "test", "Sans preuve", True)

    def test_piece_ajoutee_invalide_revue(self):
        rapport = self.evaluation()
        revue = outil.retenir(self.projet, rapport, "test", "Une pièce examinée")
        (self.projet / "REVUE-VISUELLE.json").write_text(json.dumps(revue))
        (self.projet / "candidats/Autre.stl").write_text("Pièce non rendue")
        self.assertEqual(outil.verifier(self.projet)["statut"], "FAIL")

    def test_aucun_candidat_ne_permet_pas_revue_concept(self):
        rapport = self.evaluation(concept=True)
        # Le manifest reste valide mais les fichiers ne sont plus dans candidats.
        source = self.projet / "candidats/modele.stl"
        source.rename(self.projet / "hors-candidats.stl")
        with self.assertRaises(ValueError):
            outil.retenir(self.projet, rapport, "test", "Sans candidat", True)

    @unittest.skipUnless(os.environ.get("TEST_BLENDER") == "1", "essai réel explicite")
    def test_parcours_reel_reference_modele_revue_et_variation(self):
        import trimesh

        blender = shutil.which("blender")
        if not blender:
            self.skipTest("Blender absent")
        p = self.projet
        self.sauver()
        trimesh.creation.box(extents=[20, 10, 30]).export(p / "candidats/piece.stl")
        from benchab.references_visuelles import rendre_silhouettes

        renderer = Path(rendre_silhouettes.__file__)
        vues = {
            n: {"position": pos, "target": [0, 0, 0], "ortho_scale": 50}
            for n, pos in {"face": [0, -100, 0], "profil": [100, 0, 0], "dessus": [0, 0, 100]}.items()
        }

        def rendre(numero):
            spec = {
                "meshes": [{"path": "candidats/piece.stl", "units": "mm"}],
                "resolution": [96, 96],
                "output_dir": f"rendus-{numero}",
                "views": vues,
            }
            source = p / f"camera-{numero}.json"
            source.write_text(json.dumps(spec))
            run = subprocess.run(
                [blender, "-b", "--factory-startup", "--python", str(renderer), "--", str(source)],
                text=True,
                capture_output=True,
                timeout=90,
                env=dict(
                    os.environ,
                    HOME=str(p),
                    TMPDIR=str(p),
                    XDG_CACHE_HOME=str(p / "cache"),
                    BLENDER_USER_CONFIG=str(p / "blender-config"),
                ),
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            return p / f"rendus-{numero}/manifest.json"

        manifest = rendre(1)
        ref = {
            "besoin": "Témoin de comparaison 20 × 10 × 30 mm",
            "vues_obligatoires": list(vues),
            "seuils": {"iou_min": 0.99, "centre_max_px": 0, "bbox_max_px": 0},
            "vues": {
                n: {
                    "image": f"rendus-1/{n}.png",
                    "mode": "clair",
                    "seuil_masque": 127,
                    "origine": "fournie",
                    "projection": "orthographique",
                    "provenance": "Rendu synthétique témoin, pas image générée par IA",
                }
                for n in vues
            },
        }
        (p / "ref-spec.json").write_text(json.dumps(ref))
        module = outil.module_references()
        module.figer(p / "ref-spec.json", p / "REFERENCES-VISUELLES.json")
        original = outil.sha(p / "REFERENCES-VISUELLES.json")
        module.evaluer(p / "REFERENCES-VISUELLES.json", manifest, p / "evaluation-1")
        revue = outil.retenir(p, p / "evaluation-1/rapport.json", "test", "Trois vues identiques")
        (p / "REVUE-VISUELLE.json").write_text(json.dumps(revue))
        self.assertEqual(outil.verifier(p)["statut"], "PASS")
        trimesh.creation.box(extents=[35, 10, 30]).export(p / "candidats/piece.stl")
        self.assertEqual(outil.verifier(p)["statut"], "FAIL")
        nouveau = module.evaluer(p / "REFERENCES-VISUELLES.json", rendre(2), p / "evaluation-2")
        self.assertEqual(nouveau["statut"], "FAIL")
        self.assertEqual(original, outil.sha(p / "REFERENCES-VISUELLES.json"))


if __name__ == "__main__":
    unittest.main()
