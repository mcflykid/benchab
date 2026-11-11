"""Tests de l'aperçu du G-code (vitesses, déplacements à vide, alertes, page autonome) ; aucun réseau."""

import base64
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from benchab.visualisation import apercu_gcode as A
from benchab.visualisation import hors_ligne as H

G = (
    "G90\nM83\n;Z:0.2\n;TYPE:Outer wall\nG1 X0 Y0 F600\nG1 X10 Y0 E1\nG1 X10"
    " Y10 E1 F1200\n;TYPE:Support\nG1 Z0.6\nG1 X110 Y10\nG1 Z0.2\nG1 X120 Y"
    "10 E1\n"
)


class Apercu(unittest.TestCase):
    def test_titre_litteral_peut_contenir_le_marqueur_des_donnees(self):
        """Le titre reste littéral et les données de la page autonome restent un JSON valide."""
        import re

        titre = "Témoin __DONNEES__"
        with tempfile.TemporaryDirectory() as dossier:
            root = Path(dossier)
            source = root / "p.gcode"
            source.write_text(G)
            sortie = root / "page.html"
            A.page(source, sortie, titre=titre)
            html = sortie.read_text()

        self.assertTrue("<h1>Témoin __DONNEES__</h1>" in html, "le titre contenant le marqueur reste littéral")
        donnees = re.search(r"const D = (.*?);\nconst cv =", html, re.S).group(1)
        self.assertEqual(json.loads(donnees)["titre"], titre)
        self.assertIn("data:text/javascript;base64,", html)
        self.assertNotIn('<script src=', html)

    def test_scripts_lisibles_sont_inclus_sans_urls_externes(self):
        """Le document autonome injecte ses scripts empaquetés et garde ses commandes DOM."""
        from importlib.resources import files

        ressources = files("benchab.visualisation").joinpath("ressources")
        script_2d = ressources.joinpath("apercu_gcode_2d.js").read_text()
        script_3d = ressources.joinpath("apercu_gcode_3d.js").read_text()
        brut = Path(A.__file__).with_suffix(".html").read_text()
        self.assertIn("__SCRIPT_2D__", brut)
        self.assertIn("__SCRIPT_3D__", brut)
        self.assertGreater(len(script_2d.splitlines()), 20)
        self.assertGreater(len(script_3d.splitlines()), 20)
        self.assertLess(max(map(len, script_2d.splitlines())), 180)
        self.assertLess(max(map(len, script_3d.splitlines())), 180)
        with tempfile.TemporaryDirectory() as dossier:
            source = Path(dossier) / "p.gcode"
            source.write_text(G)
            sortie = Path(dossier) / "page.html"
            A.page(source, sortie, titre="Test")
            final = sortie.read_text()
        for code in ("window.apercu =", "window.plaque3d =", 'id="o2d"', 'id="o3d"'):
            self.assertIn(code, final)
        self.assertNotIn("__SCRIPT_", final)
        self.assertNotIn('<script src=', final)
        self.assertNotIn("cdn.jsdelivr.net", final)
        self.assertIn("data:text/javascript;base64,", final)
        self.assertIn('type="importmap"', final)

    def test_vitesses_et_deplacements(self):
        c = A.couches(G)[0]
        d = np.frombuffer(base64.b64decode(c["d"]), np.int16)
        v = np.frombuffer(base64.b64decode(c["v"]), np.uint8)
        tr = np.frombuffer(base64.b64decode(c["tr"]), np.int16)
        self.assertEqual(list(v), [10, 20, 20])
        self.assertEqual(list(d[:2]), [A.TYPES.index("Outer wall"), 1])
        self.assertEqual(list(tr), [200, 200, 2200, 200, 8])

    def test_absolute_retraction_g92(self):
        c = A.couches("M82\nG92 E0\n;Z:0.2\n;TYPE:Support\nG1 X10 E5 F600\nG1 X30 E4\nG92 E0\nG1 X50 E1\nG1 X60 E2\n")[
            0
        ]
        data = np.frombuffer(base64.b64decode(c["d"]), np.int16).tolist()
        self.assertEqual(
            data, [A.TYPES.index("Support"), 1, 0, 0, 200, 0, A.TYPES.index("Support"), 1, 1000, 0, 1200, 0]
        )

    def test_relative_xyz_and_invalid_mode(self):
        c = A.couches("M83\nG91\n;Z:0.2\nG1 X10 E1 F600\nG1 X10 E1\n")[0]
        self.assertEqual(np.frombuffer(base64.b64decode(c["d"]), np.int16).tolist()[-2:], [400, 0])
        with self.assertRaises(ValueError):
            A.couches(";Z:0.2\nG1 X10 E1\n")

    def test_titre_et_rapport_ne_peuvent_injecter_html_ou_script(self):
        import re
        from html.parser import HTMLParser
        from importlib.resources import files

        class Scripts(HTMLParser):
            def __init__(self):
                super().__init__()
                self.count = 0

            def handle_starttag(self, tag, attrs):
                self.count += tag == "script"

        with tempfile.TemporaryDirectory() as dossier:
            root = Path(dossier)
            source = root / "p.gcode"
            source.write_text(G)
            titre = '</script><script>globalThis.INJECTE=1</script><img onerror="x">'
            A.page(source, root / "page.html", titre=titre)
            html = (root / "page.html").read_text()
            parser = Scripts()
            parser.feed(html)
            self.assertEqual(parser.count, 3)
            self.assertNotIn("<script>globalThis.INJECTE=1</script>", html)
            self.assertIn("&lt;img onerror=", html)
            donnees = re.search(r"const D = (.*?);\nconst cv =", html, re.S).group(1)
            self.assertEqual(json.loads(donnees)["titre"], titre)
        import subprocess

        source_js = files("benchab.visualisation").joinpath("ressources/apercu_gcode_2d.js").read_text()
        script = re.search(r"const echapper = (.*?);\nconst D =", source_js, re.S).group(1)
        js = "const echapper = " + script + '; process.stdout.write(echapper("<img onerror=1>&"));'
        resultat = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
        self.assertEqual(resultat.stdout, "&lt;img onerror=1&gt;&amp;")
        script_3d = files("benchab.visualisation").joinpath("ressources/apercu_gcode_3d.js").read_text()
        self.assertIn("echapper(a.texte)", script_3d)
        self.assertIn("echapper(", source_js)

    def test_alertes_et_page_autonome(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            g = t / "p.gcode"
            g.write_text(G)
            (t / "z.json").write_text(
                json.dumps(
                    {
                        "zones_locales": {
                            "zones": [
                                {
                                    "plaque": 1,
                                    "type": "Overhang wall",
                                    "z_min_mm": 0.2,
                                    "z_max_mm": 0.2,
                                    "bbox_xy_mm": [0, 0, 10, 10],
                                },
                                {"id": "geometrie:x", "type": "surplombs"},
                            ]
                        }
                    }
                )
            )
            (t / "c.json").write_text(
                json.dumps(
                    {
                        "plaques": {
                            "plaque 1": {
                                "series": [
                                    {"z_mm": [0.2, 0.2], "alerte": True, "trajet_max_mm": 100, "relevage_mm": [0.4]},
                                    {"z_mm": [1, 2], "alerte": False},
                                ]
                            }
                        }
                    }
                )
            )
            A.page(g, t / "a.html", zones=t / "z.json", colonnes=t / "c.json")
            html = (t / "a.html").read_text()
            for dist in H.DISTANTS:
                self.assertNotIn(dist, html)
            self.assertIn("window.apercu3d", html)
            self.assertEqual(len(A.alertes(t / "z.json", t / "c.json")[1]), 2)


if __name__ == "__main__":
    unittest.main()
