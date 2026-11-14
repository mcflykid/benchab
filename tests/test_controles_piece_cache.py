"""Cache géométrique : entrées artificielles, dossiers temporaires, aucun slicer."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.controles import controles_piece as C


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = Path(self.tmp.name)
        self.f = self.r / "piece.stl"
        self.f.write_text("entrée")
        self.outil = self.r / "outil.py"
        self.outil.write_text("version=1")
        self.p = C.O.Projet(self.r)
        self.params = {"couche": 0.2}
        self.rapport = [{"controle": "test", "statut": "PASS", "detail": "calcul synthétique"}]
        self.mock = patch.object(C, "controler", return_value=self.rapport).start()

    def test_rapport_cree_le_repertoire_au_besoin(self):
        cible = self.r / "preuves/nouveau/controle.json"
        with (
            patch.object(C, "controler", return_value=self.rapport),
        ):
            code = C.main(["--sans-cache", str(self.f), "--json", str(cible)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(cible.read_text())["fichiers"][self.f.name], self.rapport)

    def test_erreur_logicielle_cli_bloquante(self):
        with patch.object(C, "main", side_effect=OSError("sortie inaccessible")):
            self.assertEqual(C.cli(), 2)

    def test_main_parse_argv_explicit_sans_dependre_de_sys_argv(self):
        with patch("sys.argv", ["controles_piece.py", "--help"]):
            with self.assertRaises(SystemExit) as exit_code:
                C.main(["--option-invalide"])
        self.assertEqual(exit_code.exception.code, 2)

    def tearDown(self):
        patch.stopall()
        self.tmp.cleanup()

    def calcul(self, params=None):
        return C.controler_cache(self.f, self.p, params or self.params, [str(self.outil)])

    def test_modification_entree_force_recalcul(self):
        self.calcul()
        self.calcul()
        self.assertEqual(self.mock.call_count, 1)
        self.f.write_text("entrée modifiée")
        self.calcul()
        self.assertEqual(self.mock.call_count, 2)

    def test_modification_outil_force_recalcul(self):
        self.calcul()
        self.calcul()
        self.assertEqual(self.mock.call_count, 1)
        self.outil.write_text("version=2")
        self.calcul()
        self.assertEqual(self.mock.call_count, 2)

    def test_parametres_et_liste_outils_invalident(self):
        self.calcul()
        self.calcul({"couche": 0.3})
        self.assertEqual(self.mock.call_count, 2)
        C.controler_cache(self.f, self.p, {"couche": 0.3}, [])
        self.assertEqual(self.mock.call_count, 3)

    def test_warn_restitue_integralement_fail_recalcule(self):
        self.rapport[0]["statut"] = "WARN"
        self.assertEqual(self.calcul(), self.rapport)
        self.assertEqual(self.calcul(), self.rapport)
        self.assertEqual(self.mock.call_count, 1)
        self.f.write_text("autre")
        self.rapport[0]["statut"] = "FAIL"
        self.calcul()
        self.calcul()
        self.assertEqual(self.mock.call_count, 3)

    def test_cache_corrompu_ou_rapport_altéré_recalcule(self):
        self.calcul()
        p = self.p.d / "cache.json"
        d = json.loads(p.read_text())
        next(iter(d.values()))["conclusion"]["rapport"][0]["detail"] = "altéré"
        p.write_text(json.dumps(d))
        self.calcul()
        self.assertEqual(self.mock.call_count, 2)
        p.write_text("{")
        self.calcul()
        self.assertEqual(self.mock.call_count, 3)

    def test_statut_inconnu_pas_reutilise(self):
        self.rapport[0]["statut"] = "INCONNU"
        self.calcul()
        self.calcul()
        self.assertEqual(self.mock.call_count, 2)

    def test_doute_de_calcul_non_reutilise(self):
        self.rapport[0].update(statut="WARN", detail="sortie illisible : échec du sous-outil")
        self.calcul()
        self.calcul()
        self.assertEqual(self.mock.call_count, 2)
        self.rapport[0].update(statut="NON APPLICABLE", detail="raison manquante")
        self.calcul()
        self.calcul()
        self.assertEqual(self.mock.call_count, 4)

    def test_cli_warn_reste_visible_et_code_identique(self):
        import trimesh

        f = self.r / "cube.stl"
        m = trimesh.creation.box((10, 10, 4))
        m.apply_translation(-m.bounds[0])
        m.export(f)
        cmd = [
            sys.executable,
            str(Path(C.__file__)),
            str(f),
            "--epaisseur-min",
            "100",
            "--json",
            str(self.r / "rapport.json"),
        ]
        a = subprocess.run(cmd, cwd=self.r, capture_output=True, text=True)
        rapport = (self.r / "rapport.json").read_bytes()
        commande_2 = [*cmd]
        commande_2[-1] = str(self.r / "rapport-2.json")
        b = subprocess.run(commande_2, cwd=self.r, capture_output=True, text=True)
        self.assertEqual(a.returncode, 1, a.stderr)
        self.assertEqual(b.returncode, 1, b.stderr)
        self.assertIn("WARN", b.stdout)
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(rapport, (self.r / "rapport-2.json").read_bytes())


class ControlesPieceRegressions(unittest.TestCase):
    def test_cube_ferme_et_non_applicable_ne_devient_pas_pass(self):
        import trimesh

        with tempfile.TemporaryDirectory() as dossier:
            source = Path(dossier) / "cube.stl"
            mesh = trimesh.creation.box((20, 20, 10))
            mesh.apply_translation(-mesh.bounds[0])
            mesh.export(source)
            rapport = C.controler(source, non_applicable={"couches courtes": "pièce massive"})
        statuts = {item["controle"]: item["statut"] for item in rapport}
        self.assertEqual(statuts["maillage fermé"], "PASS")
        self.assertEqual(statuts["couches courtes"], "NON APPLICABLE")

    def test_maillage_ouvert_est_fail(self):
        import trimesh

        with tempfile.TemporaryDirectory() as dossier:
            source = Path(dossier) / "ouvert.stl"
            mesh = trimesh.creation.box((20, 20, 10))
            mesh.apply_translation(-mesh.bounds[0])
            mesh.update_faces(list(range(len(mesh.faces) - 2)))
            mesh.export(source)
            rapport = C.controler(source)
        fermeture = next(item for item in rapport if item["controle"] == "maillage fermé")
        self.assertEqual(fermeture["statut"], "FAIL")

    def test_surplomb_reste_signale(self):
        import trimesh

        with tempfile.TemporaryDirectory() as dossier:
            source = Path(dossier) / "surplomb.stl"
            colonne = trimesh.creation.box((10, 10, 20))
            surplomb = trimesh.creation.box((30, 10, 4))
            surplomb.apply_translation((10, 0, 12))
            mesh = trimesh.boolean.union([colonne, surplomb], engine="manifold")
            mesh.apply_translation(-mesh.bounds[0])
            mesh.export(source)
            rapport = C.controler(source)
        controle = next(item for item in rapport if item["controle"] == "surplombs")
        self.assertEqual(controle["statut"], "WARN")


if __name__ == "__main__":
    unittest.main()


def test_cache_dependance_modifiee_absente_et_cycles_sont_invalides(tmp_path):
    """Invalide un résultat dérivé sur SHA modifié, dépendance absente ou cycle ; aucune preuve physique."""
    from benchab.controles.cache_geometrie import Projet, cache_lire, cache_poser

    projet = Projet(tmp_path)
    fichier = tmp_path / "source.json"
    fichier.write_text("version1")
    cache_poser(projet, "source", "source", ["source.json"], "PASS", "calculé")
    cache_poser(projet, "derive", "derive", [], "PASS", "calculé", dependances=["source"])
    assert cache_lire(projet, "derive")["statut"] == "VALIDE"
    fichier.write_text("version2")
    assert cache_lire(projet, "derive")["dependances_invalides"] == ["source"]
    assert cache_lire(projet, "derive")["statut"] == "INVALIDE"
    cache_poser(projet, "absent", "derive", [], "PASS", "calculé", dependances=["inexistant"])
    assert cache_lire(projet, "absent")["statut"] == "INVALIDE"
    cache_poser(projet, "a", "derive", [], "PASS", "calculé", dependances=["b"])
    cache_poser(projet, "b", "derive", [], "PASS", "calculé", dependances=["a"])
    avant = (tmp_path / "cache.json").read_bytes()
    assert cache_lire(projet, "a")["statut"] == "INVALIDE"
    assert (tmp_path / "cache.json").read_bytes() == avant
