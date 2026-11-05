"""Paquet autonome, Unicode, empreintes, absence et ressources locales vérifiées."""

import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import trimesh
from test_serveur import S, lancer

from benchab.visualisation.catalogue_visuel import couches, scene
from benchab.visualisation.hors_ligne import VENDOR, partagee
from benchab.visualisation.paquet_inspection import paquet, securiser_couches


class Paquet(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="inspection été ")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name).resolve()
        self.p = self.root / "projets/boite-ete"
        (self.p / "candidats").mkdir(parents=True)
        self.titre = "Boîte été </script> & source"
        q = {
            "suivi": {
                "titre": self.titre,
                "statut": "HISTORIQUE",
                "references_imprimees": [{"fichiers": [{"chemin": "reference/envoi.gcode"}]}],
            }
        }
        (self.p / "qualite.json").write_text(json.dumps(q))
        (self.p / "candidats/assemblage.json").write_text(
            json.dumps({"models": {"Cube": {}}, "parts": [{"name": "Cube", "model": "Cube"}]})
        )
        trimesh.creation.box((10, 20, 30)).export(self.p / "candidats/Cube.stl")

    def couches(self):
        (self.p / "reference").mkdir()
        (self.p / "reference/envoi.gcode").write_text(
            "G90\nM83\n;Z:0.2\nG1 Z0.2\nG1 X0 Y0 F1200\nG1 X10 Y0 E1\n;Z:0.4\nG1 Z0.4\nG1 X10 Y10 E1\n"
        )

    def test_paquet_complet_sources_intactes_et_donnees_echappees(self):
        self.couches()
        avant = {f: f.read_bytes() for f in self.p.rglob("*") if f.is_file()}
        with ZipFile(io.BytesIO(paquet(self.root, "boite-ete"))) as z:
            self.assertEqual(
                set(z.namelist()),
                {"inspection.html", "couches.html", "licence-three.txt", "manifeste.json", "lire-moi.txt"},
            )
            man = json.loads(z.read("manifeste.json"))
            for nom, sha in man["fichiers"].items():
                self.assertEqual(hashlib.sha256(z.read(nom)).hexdigest(), sha)
            h = z.read("inspection.html").decode()
            d = json.loads(re.search('id="donnees">(.*?)</script>', h, re.S)[1])
            self.assertEqual(d["titre"], self.titre)
            self.assertEqual(d["source"]["couches_url"], "couches.html")
            self.assertNotIn("paquet_url", d["source"])
            self.assertNotIn("cdn.jsdelivr.net", h)
            self.assertIn("Content-Security-Policy", z.read("couches.html").decode())
        self.assertEqual(avant, {f: f.read_bytes() for f in self.p.rglob("*") if f.is_file()})

    def test_absence_couches_explicite_et_partage_verifie(self):
        with ZipFile(io.BytesIO(paquet(self.root, "boite-ete"))) as z:
            self.assertNotIn("couches.html", z.namelist())
            self.assertIn("Aucune couche", z.read("lire-moi.txt").decode())
        h = scene(self.root, "boite-ete", ressources_partagees=True).decode()
        self.assertNotIn("cdn.jsdelivr.net", h)
        self.assertNotIn("data:text/javascript;base64,", h)
        self.assertIn("/benchab/visualisation/vendor/three-0.160.0/build/three.module.js", h)
        with self.assertRaises(ValueError):
            securiser_couches("document sans entête")

    def test_vendor_altere_refuse_par_la_generation_partagee(self):
        v = self.root / "vendor"
        shutil.copytree(VENDOR, v)
        (v / "build/three.module.js").write_text("altéré")
        with self.assertRaises(ValueError):
            partagee('<script type="importmap">{}</script>', v)

    def test_source_gcode_modifiee_pendant_generation_refusee(self):
        self.couches()

        def produire(source, out, **kw):
            out.write_text("<html><head></head><body>ancien résultat</body></html>")
            source.write_text("G90\nG1 X99\n")

        with patch("benchab.visualisation.apercu_gcode.page", side_effect=produire):
            with self.assertRaisesRegex(ValueError, "modifiée pendant"):
                couches(self.root, "boite-ete")

    def test_http_module_verifie_cache_et_refus_tamper_get_head(self):
        v = self.root / "benchab/visualisation/vendor/three-0.160.0"
        shutil.copytree(VENDOR, v)
        ancien = S.RACINE
        S.RACINE = self.root
        srv = lancer(S.Gestion)
        try:
            url = f"http://127.0.0.1:{srv.server_port}/benchab/visualisation/vendor/three-0.160.0/build/three.module.js"
            with urllib.request.urlopen(url) as r:
                self.assertIn("immutable", r.headers["Cache-Control"])
                self.assertEqual(
                    hashlib.sha256(r.read()).hexdigest(),
                    hashlib.sha256((v / "build/three.module.js").read_bytes()).hexdigest(),
                )
            (v / "build/three.module.js").write_text("altéré")
            for method in ("GET", "HEAD"):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(urllib.request.Request(url, method=method))
                self.assertEqual(e.exception.code, 403)
        finally:
            srv.shutdown()
            srv.server_close()
            S.RACINE = ancien


if __name__ == "__main__":
    unittest.main()


def test_fixtures_refusent_traversee_et_parent_lie_avant_creation(tmp_path, monkeypatch):
    """Refuse .. et parents liés avant STL/JSON/HTML ; FAIL sans écrire le dossier cible."""
    import pytest

    from benchab.visualisation.fixtures_inspection import creer

    prive = tmp_path / "prive"
    prive.mkdir()
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    cible = tmp_path / "ailleurs"
    with pytest.raises(ValueError, match="Traversée"):
        creer(prive / ".." / "ailleurs")
    assert not cible.exists()
    (prive / "lien").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Lien symbolique"):
        creer(prive / "lien/ailleurs")
    assert not cible.exists()
