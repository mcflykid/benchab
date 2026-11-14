"""Règles de décollement évaluées sur des maillages synthétiques."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import trimesh

from benchab.controles import decollement as D


def test_resume_decollement_conserve_le_motif_auto(tmp_path, capsys, monkeypatch):
    """La décision laissée au slicer reste NON VÉRIFIÉE avec le même code historique."""
    source = tmp_path / "tall.stl"
    source.touch()
    rapport = tmp_path / "detail.json"
    objet = {"statut": "NON VÉRIFIÉ", "recommandation": "bordure",
             "raison": "petite emprise", "detail": "auto_brim : décision du slicer", "aire_mm2": 25}
    monkeypatch.setattr(D, "controler", lambda *_args, **_kw: ({"Témoin": objet}, 0))
    assert D.main([str(source), "--resume"]) == 2
    assert D.main([str(source), "--json", str(rapport), "--resume"]) == 0
    resume = json.loads(capsys.readouterr().out)
    assert resume == {"statut": "NON VÉRIFIÉ", "code": 0, "objets": 1, "rapport": str(rapport),
                      "points": [{"objet": "Témoin", **{k: v for k, v in objet.items() if k != "aire_mm2"}}]}
    assert json.loads(rapport.read_text()) == {"Témoin": objet}


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
        """Une nouvelle sortie JSON réussit et un second passage refuse l’écrasement."""
        source = self.mesh((30, 30, 10))
        rapport = self.racine / "rapport.json"
        with patch("sys.argv", ["decollement.py", "--help"]):
            self.assertEqual(D.main([str(source), "--json", str(rapport)]), 0)
        contenu = rapport.read_bytes()
        self.assertEqual(D.main([str(source), "--json", str(rapport)]), 2)
        self.assertEqual(rapport.read_bytes(), contenu)
        self.assertIn(source.stem, json.loads(contenu))

    def test_json_ne_peut_pas_remplacer_une_entree(self):
        """Le rapport ne peut pas écraser son STL source."""
        source = self.mesh((30, 30, 10))
        avant = source.read_bytes()

        code = D.main([str(source), "--json", str(source)])

        self.assertEqual(code, 2)
        self.assertEqual(source.read_bytes(), avant)

    def test_sortie_json_protege_toutes_les_entrees_du_projet(self):
        """La publication connaît l’archive, le placement et les STL effectivement lus."""
        projet = self.racine / "projet"
        candidats = projet / "candidats"
        preuves = projet / "preuves"
        candidats.mkdir(parents=True)
        preuves.mkdir()
        source = candidats / "plaques.3mf"
        source.write_bytes(b"fixture 3MF")
        maillage = candidats / "A.stl"
        maillage.write_bytes(b"fixture STL")
        placement = preuves / "placement.json"
        placement.write_text(json.dumps({"objects": [{"name": "A", "model": "A"}]}), encoding="utf-8")
        sortie = self.racine / "rapport.json"
        avec = {"A #1": {"statut": "PASS", "recommandation": "aucune", "raison": "fixture"}}
        with patch.object(D, "controler", return_value=(avec, 0)), patch.object(D, "ecrire_json_neuf") as ecrire:
            code = D.main([
                str(source), "--placement", str(placement), "--candidats", str(candidats), "--json", str(sortie),
            ])
        self.assertEqual(code, 0)
        self.assertEqual(set(ecrire.call_args.kwargs["entrees"]), {source, placement, maillage})


if __name__ == "__main__":
    unittest.main()
