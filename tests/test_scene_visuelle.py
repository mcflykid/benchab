"""Scène réelle sur fixture, intégrité et poses d'inspection explicites."""

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

import trimesh

from benchab.visualisation.catalogue_visuel import scene


class SceneVisuelle(unittest.TestCase):
    def test_independantes_separees_sans_recriture_et_poses_declarees_preservees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "projets/boite-ete"
            (p / "candidats").mkdir(parents=True)
            (p / "qualite.json").write_text(json.dumps({"suivi": {"titre": "Boîte été", "statut": "EN_PREPARATION"}}))
            for n in ("A", "B"):
                trimesh.creation.box((20, 30, 10)).export(p / "candidats" / (n + ".stl"))
            m = p / "candidats/assemblage.json"
            data = {"models": {"A": {}, "B": {}}, "parts": [{"name": n, "model": n} for n in ("A", "B")]}
            m.write_text(json.dumps(data))
            avant = {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob("*") if f.is_file()}

            def lire():
                html = scene(root, p.name).decode()
                return json.loads(re.search('id="donnees">(.*?)</script>', html, re.S)[1])

            out = lire()
            self.assertIn("indépendantes", out["source"]["disposition"])
            self.assertNotEqual(out["pieces"][0]["matrice"], out["pieces"][1]["matrice"])
            self.assertEqual(
                avant, {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob("*") if f.is_file()}
            )
            matrix = [[1, 0, 0, 42], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
            data["parts"][0]["matrix"] = matrix
            m.write_text(json.dumps(data))
            out = lire()
            self.assertNotIn("disposition", out["source"])
            self.assertEqual(out["pieces"][0]["matrice"][3], 42)


if __name__ == "__main__":
    unittest.main()
