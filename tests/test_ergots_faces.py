"""Tests du contrôle des ergots par face avec blocs accolés et clés carrées."""

import json
import tempfile
import unittest
from pathlib import Path

import trimesh as tm

from benchab.controles import ergots_faces as E


def blocs(dossier, cles, gouttiere=False):
    a = tm.creation.box([40, 40, 20])
    a.apply_translation([-20.1, 0, 10])  # jeu de 0,2 mm au plan x = 0
    b = tm.creation.box([40, 40, 20])
    b.apply_translation([20.1, 0, 10])
    outils = []
    for y in cles:
        k = tm.creation.box([14.7, 8.7, 8.7])
        k.apply_translation([0, y, 10])
        outils.append(k)
    if gouttiere:  # rainure qui débouche aux deux bords : ce n'est pas un ergot
        g = tm.creation.box([6, 50, 4])
        g.apply_translation([0, 0, 18])
        outils.append(g)
    if outils:
        o = tm.boolean.union(outils, engine="manifold")
        a = tm.boolean.difference([a, o], engine="manifold")
        b = tm.boolean.difference([b, o], engine="manifold")
    a.export(Path(dossier) / "A.stl")
    b.export(Path(dossier) / "B.stl")


class Ergots(unittest.TestCase):
    def lancer(self, cles, gouttiere=False, ecarts=None):
        with tempfile.TemporaryDirectory() as d:
            blocs(d, cles, gouttiere)
            args = [d, "--json", str(Path(d) / "r.json")]
            if ecarts:
                (Path(d) / "e.json").write_text(json.dumps(ecarts))
                args += ["--ecarts", str(Path(d) / "e.json")]
            code = E.main(args)
            return code, json.loads((Path(d) / "r.json").read_text())

    def test_deux_ergots_pass(self):
        code, r = self.lancer([-10, 10])
        self.assertEqual(code, 0)
        self.assertEqual([f["ergots"] for f in r["faces"]], [2])
        self.assertEqual(r["statut"], "PASS")

    def test_un_ergot_fail(self):
        code, r = self.lancer([0])
        self.assertEqual(code, 1)
        self.assertEqual(r["faces"][0]["ergots"], 1)
        self.assertEqual(r["statut"], "FAIL")

    def test_ecart_arbitre_warn(self):
        code, r = self.lancer([0], ecarts={"A|B": "pièce déjà imprimée"})
        self.assertEqual(code, 0)
        self.assertEqual(r["statut"], "WARN")

    def test_gouttiere_ne_compte_pas(self):
        code, r = self.lancer([-10], gouttiere=True)
        self.assertEqual(r["faces"][0]["ergots"], 1)

    def test_pieces_eloignees_non_applicable(self):
        with tempfile.TemporaryDirectory() as d:
            tm.creation.box([10, 10, 10]).export(Path(d) / "A.stl")
            q = tm.creation.box([10, 10, 10])
            q.apply_translation([50, 0, 0])
            q.export(Path(d) / "B.stl")
            self.assertEqual(E.main([d]), 0)


if __name__ == "__main__":
    unittest.main()
