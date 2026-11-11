"""Le niveau 3 refuse les métriques absentes/non finies avant le reste des contrôles."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from benchab.qualite import qualite as Q


class Resultats(unittest.TestCase):
    def test_metriques_invalides_et_gcode_vide(self):
        for value, gcode in [("nan", "G1"), ("0", "G1"), ("-1", "G1"), ("1", "")]:
            with self.subTest(value=value, gcode=gcode), tempfile.TemporaryDirectory() as tmp:
                r = Path(tmp)
                p = r / "tranche.3mf"
                with ZipFile(p, "w") as z:
                    z.writestr(
                        "Metadata/slice_info.config",
                        '<config><plate><metadata key="index" value="1"/>'
                        f'<metadata key="prediction" value="{value}"/>'
                        '<metadata key="weight" value="1"/></plate></config>',
                    )
                    z.writestr("Metadata/plate_1.gcode", gcode)
                config = r / "qualite.json"
                config.write_text(json.dumps({"tranche_3mf": p.name, "placement": "absent.json"}))
                self.assertEqual(Q.niveau3(config), Q.KO)
                data = json.loads((r / "preuves/qualite/niveau3.json").read_text())
                self.assertEqual(data["statut_global"], Q.KO)


class Delais(unittest.TestCase):
    def test_timeout_garde_sorties_partielles_et_echec(self):
        with patch.object(
            Q.subprocess, "run", side_effect=subprocess.TimeoutExpired(["temoin"], 0.1, b"preuve", b"erreur")
        ):
            r = Q.executer_borne(["temoin"], delai=0.1)
        self.assertEqual(r.returncode, 124)
        self.assertIn("preuve", r.stdout)
        self.assertIn("erreur", r.stderr)

    def test_vrai_processus_borne(self):
        """Vérifie l’arrêt du processus même si Python n’a pas fini de démarrer."""
        r = Q.executer_borne([sys.executable, "-c", "import time; time.sleep(5)"], delai=0.1)
        self.assertEqual(r.returncode, 124)
        self.assertIn("Délai du contrôle dépassé", r.stderr)

    def test_delais_invalides(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Q.executer_borne(["temoin"], value)


if __name__ == "__main__":
    unittest.main()
