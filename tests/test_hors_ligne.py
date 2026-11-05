"""Tests de la page autonome hors ligne (vendor vérifié, aucune ressource distante) ; aucun réseau."""

import base64
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import trimesh

from benchab.visualisation import hors_ligne as H
from benchab.visualisation.visionneuse import Visionneuse

ICI = Path(__file__).resolve().parent
VISU = ICI.parent / "benchab/visualisation"


def page(hors_ligne):
    v = Visionneuse("Test", axe_haut="Z")
    v.modele("Cube", trimesh.creation.box((10, 10, 10)))
    v.piece("Cube", "Cube")
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "p.html"
        v.ecrire(f, hors_ligne=hors_ligne)
        return f.read_text()


class HorsLigne(unittest.TestCase):
    def test_vendor_conforme(self):
        self.assertEqual(H.verifier_vendor(), "0.160.0")

    def test_vendor_altere_refuse(self):
        with tempfile.TemporaryDirectory() as t:
            v = Path(t) / "v"
            shutil.copytree(H.VENDOR, v)
            f = v / "examples/jsm/controls/OrbitControls.js"
            f.write_text(f.read_text() + "\n// modifié")
            with self.assertRaises(ValueError):
                H.verifier_vendor(v)

    def test_page_sans_ressource_distante(self):
        html = page(True)
        for d in H.DISTANTS:
            self.assertNotIn(d, html)
        carte = json.loads(re.search('<script type="importmap">(.*?)</script>', html, re.S).group(1))["imports"]
        self.assertEqual(set(carte), set(H.MODULES))
        self.assertTrue(all((u.startswith("data:text/javascript;base64,") for u in carte.values())))
        gltf = base64.b64decode(carte["three/addons/loaders/GLTFLoader.js"].split(",", 1)[1]).decode()
        self.assertIn("from 'three/addons/utils/BufferGeometryUtils.js'", gltf)
        self.assertNotIn("from '../", gltf)

    def test_tous_les_imports_du_gabarit_sont_fournis(self):
        imports = set(re.findall("(?:from |import\\()'([^']+)'", (VISU / "gabarit.html").read_text()))
        self.assertTrue(imports <= set(H.MODULES), imports - set(H.MODULES))

    def test_ancienne_page_cdn_sur_demande(self):
        with self.assertRaisesRegex(ValueError, "autonome hors ligne"):
            page(False)


if __name__ == "__main__":
    unittest.main()


def test_module_hors_ligne_convertit_une_page_neuve_sans_modifier_source(tmp_path):
    """Le point d'entrée -m convertit réellement ; code0, source intacte, aucun navigateur ni ressource distante."""
    import os
    import subprocess
    import sys

    source = tmp_path / "source.html"
    source.write_text(page(True))
    avant = source.read_bytes()
    sortie = tmp_path / "copie-autonome.html"
    execution = subprocess.run(
        [sys.executable, "-B", "-m", "benchab.visualisation.hors_ligne", str(source), "--sortie", str(sortie)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, check=False,
    )
    assert execution.returncode == 0, execution.stderr
    assert source.read_bytes() == avant
    assert sortie.is_file()
    assert all(d not in sortie.read_text() for d in H.DISTANTS)
