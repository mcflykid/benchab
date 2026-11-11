"""Vue d'impression (27/10/2026) : sur « Assemblage », les pièces des plaques sont cachées (elles
s'affichaient à côté de l'assemblage) ;
plateau marqué pour la grille ; GLB compressé et sans normales."""

import base64
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from benchab.visualisation.visionneuse import Visionneuse


class VueImpression(unittest.TestCase):
    def test_supports_de_deux_plaques_ouvrent_un_seul_contexte(self):
        """Les supports des deux plaques sont lus une fois par 3MF, sans modifier l'archive."""
        from unittest.mock import patch
        from zipfile import ZipFile

        from benchab.archives_3mf import Archive3MF

        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            trimesh.creation.box([10, 10, 10]).export(d / "Bloc.stl")
            objets = [{"name": "Bloc", "model": "Bloc", "plate": i, "xy_interface": [15, 25]} for i in (1, 2)]
            tranche = d / "tranche.3mf"
            with ZipFile(tranche, "w") as z:
                for i in (1, 2):
                    z.writestr(f"Metadata/plate_{i}.gcode", "M83\nG1 X10 Y10 Z.2\n;TYPE:Support\nG1 X20 E1 F600\n")
            original = tranche.read_bytes()
            entrer = Archive3MF.__enter__
            ouvertures = []

            def observer(archive):
                """Compte les vraies ouvertures de la source, sans changer le lecteur."""
                ouvertures.append(archive.chemin)
                return entrer(archive)

            v = Visionneuse("Deux plaques")
            with patch.object(Archive3MF, "__enter__", observer):
                v.vue_impression({"objects": objets}, d, tranche, allegement=0)
            self.assertEqual(ouvertures, [tranche])
            self.assertEqual(
                [v.source[f"supports_plaque_{i}"]["segments_support_lus"] for i in (1, 2)], [1, 1]
            )
            self.assertEqual(tranche.read_bytes(), original)

    def test_budget_support_conserve_piece_plateau_et_trace_limite(self):
        from unittest.mock import patch
        from zipfile import ZipFile

        from benchab.visualisation import visionneuse

        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            trimesh.creation.box([10, 10, 10]).export(d / "Bloc.stl")
            pl = {"objects": [{"name": "Bloc", "model": "Bloc", "plate": 1, "xy_interface": [15, 25]}]}
            with ZipFile(d / "tranche.3mf", "w") as z:
                z.writestr("Metadata/plate_1.gcode", "M83\nG1 X10 Y10 Z.2\n;TYPE:Support\nG1 X100 E5 F600\n")
            v = Visionneuse("Budget")
            with patch.object(visionneuse, "MAX_SUPPORT_CELLS", 1):
                v.vue_impression(pl, d, d / "tranche.3mf", allegement=0)
            self.assertTrue(any((p["nom"].startswith("Bloc") for p in v.pieces)))
            self.assertTrue(any((p["nom"].startswith("Plateau") for p in v.pieces)))
            self.assertEqual(v.source["supports_plaque_1"]["statut"], "PARTIELLE_BUDGET")

    def test_pose_impression_normalise_minima_et_rotation(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            mesh = trimesh.creation.box([10, 20, 30])
            mesh.apply_translation([40, 50, 60])
            mesh.export(d / "Bloc.stl")
            (d / "placement.json").write_text(
                json.dumps(
                    {
                        "objects": [
                            {"name": "Bloc", "model": "Bloc", "plate": 1, "xy_interface": [15, 25], "rot_z_deg": 90}
                        ]
                    }
                )
            )
            v = Visionneuse("Pose")
            v.vue_impression(d / "placement.json", d, allegement=0)
            piece = next((x for x in v.pieces if x["nom"].startswith("Bloc")))
            m = v.modeles[piece["modele"]]["maillage"]
            np.testing.assert_allclose(m.bounds[0], [0, 0, 0], atol=1e-07)
            np.testing.assert_allclose(m.extents, [20, 10, 30], atol=1e-07)
            self.assertEqual(v.source["objets_impression"][0]["xy_plaque_mm"], [15, 25])

    def test_assemblage_cache_les_plaques_et_page_compressee(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "candidats").mkdir()
            trimesh.creation.box([20, 20, 10]).export(d / "candidats/Bloc.stl")
            (d / "placement.json").write_text(
                json.dumps({"objects": [{"name": "Bloc", "model": "Bloc", "plate": 1, "xy_interface": [125, 125]}]})
            )
            v = Visionneuse("Essai")
            v.modele("Bloc", d / "candidats/Bloc.stl")
            v.piece("Bloc", "Bloc")
            v.vue_impression(d / "placement.json", d / "candidats")
            m = v.mecanismes[-1]
            self.assertEqual(m["id"], "impression")
            self.assertEqual(m["valeurs"], ["Assemblage", "Plaque 1"])
            cach = np.frombuffer(base64.b64decode(m["cachees"]), np.uint8).reshape(2, len(m["pieces"]))
            noms = [v.pieces[i]["nom"] for i in m["pieces"]]
            plaque = [j for j, n in enumerate(noms) if "plaque" in n.lower() or n.startswith("Plateau")]
            self.assertTrue(plaque and cach[0, plaque].all(), "Assemblage : toutes les pièces de plaque cachées")
            self.assertFalse(cach[1, plaque].any())
            self.assertTrue(cach[1, noms.index("Bloc")])
            self.assertTrue(any((x.get("plateau") == [250.0, 250.0] for x in v.modeles.values())))
            v.ecrire(d / "page.html")
            html = (d / "page.html").read_text()
            D = json.loads(html.split('id="donnees"')[1].split(">", 1)[1].split("</script>")[0].replace("<\\/", "</"))
            self.assertTrue(D["glb_gzip"])
            glb = gzip.decompress(base64.b64decode(D["glb"]))
            self.assertNotIn(b'"NORMAL"', glb[:20000])


if __name__ == "__main__":
    unittest.main()
