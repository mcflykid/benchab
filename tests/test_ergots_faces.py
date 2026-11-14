"""Tests du contrôle des ergots par face avec blocs accolés et clés carrées."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


def test_resume_ergots_localise_la_face_en_defaut(tmp_path, capsys):
    """Le résumé conserve le refus, la localisation et le rapport intégral."""
    blocs(tmp_path, [0])
    detail = tmp_path / "detail.json"
    rapport = tmp_path / "resume.json"
    assert E.main([str(tmp_path), "--json", str(detail)]) == 1
    capsys.readouterr()
    assert E.main([str(tmp_path), "--resume"]) == 2
    assert E.main([str(tmp_path), "--json", str(rapport), "--resume"]) == 1
    court = json.loads(capsys.readouterr().out)
    assert court["statut"] == "FAIL" and court["code"] == 1
    assert court["points"] == [{"pieces": ["A", "B"], "centre": [-0.1, 0, 10],
                                "ergots": 1, "statut": "FAIL"}]
    assert court["rapport"] == str(rapport)
    assert detail.read_bytes() == rapport.read_bytes()


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

    def test_json_ne_peut_pas_remplacer_un_maillage_source(self):
        """Le rapport ne peut pas remplacer une STL analysée."""
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "A.stl"
            tm.creation.box([10, 10, 10]).export(source)
            avant = source.read_bytes()

            code = E.main([d, "--json", str(source)])

            self.assertEqual(code, 2)
            self.assertEqual(source.read_bytes(), avant)

    def test_sortie_json_protege_les_maillages_et_le_fichier_ecarts(self):
        """La liste protégée contient toutes les STL et les écarts lus."""
        with tempfile.TemporaryDirectory() as d:
            dossier = Path(d)
            for nom in ("A.stl", "B.stl"):
                tm.creation.box([10, 10, 10]).export(dossier / nom)
            ecarts = dossier / "ecarts.json"
            ecarts.write_text("{}", encoding="utf-8")
            sortie = dossier / "rapport.json"
            with patch.object(E, "faces_contact", return_value=[]), patch.object(E, "ecrire_json_neuf") as ecrire:
                code = E.main([d, "--ecarts", str(ecarts), "--json", str(sortie)])
            self.assertEqual(code, 0)
            self.assertEqual(set(ecrire.call_args.kwargs["entrees"]), {dossier / "A.stl", dossier / "B.stl", ecarts})


if __name__ == "__main__":
    unittest.main()
