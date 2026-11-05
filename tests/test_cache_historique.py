import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.qualite import tout as T


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Path(self.tmp.name)

    def stage(self):
        return {
            "id": "check",
            "entrees": ["input"],
            "commande": ["{python}", "-c", "pass"],
            "cache": {
                "dependances": [],
                "paquets": [],
                "environnement": ["ATELIER_VARIABLE"],
                "configuration": ["matiere"],
            },
        }

    def test_contrat_isole_config_et_environnement_sans_ignorer_necessaire(self):
        (self.r / "input").write_text("a")
        s = self.stage()
        a = T.empreinte(self.r, s, {"matiere": "PLA", "autre": 1})
        self.assertEqual(a, T.empreinte(self.r, s, {"matiere": "PLA", "autre": 2}))
        self.assertNotEqual(a, T.empreinte(self.r, s, {"matiere": "PETG"}))
        with patch.dict(os.environ, {"ATELIER_VARIABLE": "nouveau"}):
            self.assertNotEqual(a, T.empreinte(self.r, s, {"matiere": "PLA"}))

    def test_entree_modifiee_meme_taille_et_mtime_invalide(self):
        p = self.r / "input"
        p.write_text("a")
        st = p.stat()
        ctx = T.ContexteCache()
        s = self.stage()
        a = T.empreinte(self.r, s, contexte=ctx)
        n = ctx.octets_lus
        self.assertEqual(a, T.empreinte(self.r, s, contexte=ctx))
        self.assertEqual(n, ctx.octets_lus)
        p.write_text("b")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertNotEqual(a, T.empreinte(self.r, s, contexte=ctx))

    def test_import_local_du_script_invalide_le_cache(self):
        (self.r / "parametres.py").write_text("VALEUR=1")
        (self.r / "controle.py").write_text("import parametres")
        s = self.stage()
        s["commande"] = ["{python}", "controle.py"]
        a = T.empreinte(self.r, s)
        (self.r / "parametres.py").write_text("VALEUR=2")
        self.assertNotEqual(a, T.empreinte(self.r, s))

    def test_contrat_incomplet_et_traversal_refuses(self):
        for spec in ({"paquets": []}, {"dependances": ["../x"], "paquets": [], "environnement": []}):
            s = self.stage()
            s["cache"] = spec
            with self.assertRaises(ValueError):
                T.empreinte(self.r, s)

    def test_evenements_dedoublonnes_et_diagnostics_echec(self):
        cfg = {"manifeste": "absent.json"}
        step = {"id": "a", "statut": "PASS", "code": 0, "secondes": 1}
        first = T.etat(self.r, cfg, [step], {}, "PASS", [])
        step = dict(step, secondes=3, cache=True)
        T.etat(self.r, cfg, [step], first, "PASS", [])
        folder = self.r / "preuves/qualite"
        self.assertEqual(len(list((folder / "executions").glob("*.json"))), 1)
        T.etat(self.r, cfg, [step], first, "RUNNING", [])
        self.assertEqual(len(list((folder / "executions").glob("*.json"))), 1)
        (folder / "a.log").write_text("preuve échec")
        T.etat(self.r, cfg, [dict(step, statut="FAIL")], first, "FAIL", ["incident"])
        self.assertEqual(len(list((folder / "executions").glob("*.json"))), 2)
        self.assertTrue(list((folder / "echecs").glob("*.log")))


if __name__ == "__main__":
    unittest.main()
