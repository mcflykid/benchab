"""Règles de décollement évaluées sur des maillages synthétiques."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import trimesh

from benchab.controles import decollement as D


class Decollement(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        self.racine = Path(temporaire.name)

    def mesh(self, extents, name="piece.stl"):
        mesh = trimesh.creation.box(extents=extents)
        mesh.apply_translation(-mesh.bounds[0])
        path = self.racine / name
        mesh.export(path)
        return path

    def test_regles_bordure_oreilles_et_aucune(self):
        petite = D.evaluer(trimesh.load_mesh(self.mesh((5, 5, 20))))
        longue = D.evaluer(trimesh.load_mesh(self.mesh((130, 20, 5))))
        stable = D.evaluer(trimesh.load_mesh(self.mesh((30, 30, 10))))
        self.assertEqual(petite["recommandation"], "bordure")
        self.assertEqual(longue["recommandation"], "oreilles")
        self.assertGreater(longue["coins_vifs"], 0)
        self.assertEqual(stable["recommandation"], "aucune")

    def projet(self, *, brim="no_brim", override=None):
        mesh_path = self.mesh((130, 20, 5), "Longue.stl")
        candidates = self.racine / "candidats"
        candidates.mkdir(exist_ok=True)
        mesh_path.replace(candidates / "Longue.stl")
        archive = self.racine / "plaques.3mf"
        with zipfile.ZipFile(archive, "w") as container:
            container.writestr("Metadata/project_settings.config", json.dumps({"brim_type": brim}))
        placement = self.racine / "placement.json"
        placement.write_text(
            json.dumps({"objects": [{"name": "Longue", "model": "Longue", "reglages": {"brim_type": override}}]})
        )
        return archive, placement, candidates

    def test_reglage_objet_precede_reglage_projet_et_auto_reste_non_verifie(self):
        archive, placement, candidates = self.projet(override="brim_ears")
        resultats, code = D.controler([archive], placement=placement, candidats=candidates)
        self.assertEqual(code, 0)
        self.assertEqual(resultats["Longue #1"]["statut"], "PASS")
        archive, placement, candidates = self.projet(brim="auto_brim")
        resultats, code = D.controler([archive], placement=placement, candidats=candidates)
        self.assertEqual(code, 0)
        self.assertEqual(resultats["Longue #1"]["statut"], "NON VÉRIFIÉ")

    def test_main_respecte_argv_et_conserve_les_mesures_independantes(self):
        source = self.mesh((30, 30, 10))
        rapport = self.racine / "rapport.json"
        with patch("sys.argv", ["decollement.py", "--help"]):
            self.assertEqual(D.main([str(source), "--json", str(rapport)]), 0)
        contenu = rapport.read_bytes()
        self.assertEqual(D.main([str(source), "--json", str(rapport)]), 0)
        self.assertEqual(rapport.read_bytes(), contenu)
        self.assertIn(source.stem, json.loads(contenu))


if __name__ == "__main__":
    unittest.main()
