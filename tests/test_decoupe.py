"""Tests de la découpe avec goupilles (pièces numériques, dossiers temporaires)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from benchab.conception import decoupe as D

PLAN = {"origine": [20, 20, 20], "normale": [0, 0, 1]}


def cube(c=40):
    m = trimesh.creation.box((c, c, c))
    m.apply_translation((c / 2, c / 2, c / 2))
    return m


def statuts(r, debut):
    return [c["statut"] for c in r["controles"] if c["controle"].startswith(debut)]


class Decoupe(unittest.TestCase):
    def test_cube_pass_volume_et_dimensions(self):
        parties, pions, r = D.decouper(cube(), PLAN)
        self.assertEqual(r["statut"], "PASS")
        self.assertEqual(len(pions), 2)
        v = r["volumes_mm3"]
        self.assertAlmostEqual(v["dessus"] + v["dessous"] + v["logements"], 64000, delta=0.1)
        self.assertLess(parties["dessus"].bounds[0][2], 20 + 1e-6)
        self.assertGreater(parties["dessous"].bounds[1][2], 20 - 1e-6)

    def test_jeu_radial_et_profondeur(self):
        _, pions, r = D.decouper(cube(), PLAN, {"nombre": 1, "jeu": 0.25})
        logement = r["volumes_mm3"]["logements"]
        g = r["goupilles"]
        attendu = np.pi * (g["diametre"] / 2 + 0.25) ** 2 * (g["longueur"] + 2 * g["fond"])
        self.assertAlmostEqual(logement / attendu, 1, delta=0.01)  # polygone à 48 côtés
        self.assertAlmostEqual(pions[0].extents[2], g["longueur"], places=4)

    def test_goupilles_espacees_dans_la_section(self):
        _, _, r = D.decouper(cube(), PLAN, {"nombre": 2})
        a, b = map(np.array, r["goupilles"]["positions"])
        self.assertGreater(np.linalg.norm(a - b), 40)
        self.assertTrue(np.allclose([a[2], b[2]], 20))

    def test_plan_oblique(self):
        _, _, r = D.decouper(cube(), {"origine": [20, 20, 20], "normale": [1, 0, 1]}, {"nombre": 1})
        self.assertEqual(r["statut"], "PASS")

    def test_coupe_seule(self):
        _, pions, r = D.decouper(cube(), PLAN, {"nombre": 0})
        self.assertEqual(pions, [])
        self.assertEqual(r["volumes_mm3"]["logements"], 0)

    def test_positions_explicites_cardinalite_coherente(self):
        for nombre, positions in ((0, [[20, 20, 20]]), (2, [[20, 20, 20]]), (1, [[10, 10, 20], [30, 30, 20]])):
            with self.subTest(nombre=nombre), self.assertRaises(ValueError):
                D.decouper(cube(), PLAN, {"nombre": nombre, "positions": positions})

    def test_deux_logements_superposes_ne_peuvent_pas_passer(self):
        _, _, r = D.decouper(cube(), PLAN, {"nombre": 2, "positions": [[20, 20, 20], [22, 20, 20]]})
        self.assertEqual(r["statut"], "FAIL")
        self.assertEqual(statuts(r, "espacement"), ["FAIL"])
        petit = trimesh.creation.box((10, 10, 40))
        petit.apply_translation((5, 5, 20))
        _, pions, r = D.decouper(petit, {"origine": [5, 5, 20], "normale": [0, 0, 1]})
        self.assertLess(len(pions), 2)
        self.assertEqual(r["statut"], "FAIL")
        self.assertEqual(statuts(r, "place"), ["FAIL"])

    def test_positions_allongement_extreme_allocations_bornees(self):
        from shapely.geometry import box

        grille = D.np.meshgrid
        produit = D.np.einsum
        allocations = []

        def grille_bornee(x, y):
            allocations.append(len(x) * len(y))
            self.assertLessEqual(len(x) * len(y), D.MAX_CANDIDATS)
            return grille(x, y)

        def paires_bornees(signature, a, b):
            self.assertLessEqual(a.shape[0] * a.shape[1], D.MAX_PAIRES)
            return produit(signature, a, b)

        with (
            patch.object(D.np, "meshgrid", side_effect=grille_bornee),
            patch.object(D.np, "einsum", side_effect=paires_bornees),
        ):
            for longueur in (1000, 100000):
                points = D.positions_auto([box(0, 0, longueur, 10)], np.eye(4), 3.9, 2)
                self.assertEqual(len(points), 2)
                self.assertGreater(np.linalg.norm(points[1] - points[0]), longueur * 0.9)
        self.assertTrue(allocations)

    def test_piece_trop_fine_refusee(self):
        plaque = trimesh.creation.box((40, 40, 6))
        plaque.apply_translation((20, 20, 3))
        _, _, r = D.decouper(plaque, {"origine": [20, 20, 3], "normale": [0, 0, 1]})
        self.assertEqual(r["statut"], "FAIL")
        self.assertIn("FAIL", statuts(r, "goupille"))

    def test_section_etroite_place_insuffisante(self):
        barre = trimesh.creation.box((6, 6, 40))
        barre.apply_translation((3, 3, 20))
        _, _, r = D.decouper(barre, PLAN | {"origine": [3, 3, 20]}, {"nombre": 2})
        self.assertEqual(statuts(r, "place"), ["FAIL"])

    def test_position_imposee_au_bord_refusee(self):
        _, _, r = D.decouper(cube(), PLAN, {"nombre": 1, "positions": [[1.5, 20, 25]]})
        self.assertEqual(r["goupilles"]["positions"][0][2], 20.0)
        self.assertEqual(statuts(r, "goupille 1"), ["FAIL"])

    def test_zone_protegee_traversee_ou_entamee(self):
        traversee = {"nom": "axe", "forme": "boite", "min": [30, 30, 15], "max": [35, 35, 25]}
        _, _, r = D.decouper(cube(), PLAN, {"nombre": 0}, {"protege": [traversee]})
        self.assertEqual(statuts(r, "zone protégée"), ["FAIL"])
        entamee = {"nom": "clé", "forme": "cylindre", "centre": [20, 20, 23], "rayon": 2, "hauteur": 2}
        _, _, r = D.decouper(cube(), PLAN, {"nombre": 1, "positions": [[20, 20, 20]]}, {"protege": [entamee]})
        self.assertEqual(statuts(r, "zone protégée"), ["FAIL"])
        loin = {"nom": "loin", "forme": "boite", "min": [1, 1, 30], "max": [3, 3, 35]}
        _, _, r = D.decouper(cube(), PLAN, {"nombre": 1}, {"protege": [loin]})
        self.assertEqual(statuts(r, "zone protégée"), ["PASS"])

    def test_sommets_confondus_restent_fermes(self):
        """Vérifie que la conversion garde fermés les blocs joints par une arête."""
        import manifold3d as mf

        u = mf.Manifold.cube((20, 20, 40)) + mf.Manifold.cube((20, 20, 40)).translate(
            (20, 20, 0)
        )  # blocs joints par une arête
        m = u.to_mesh64()
        piece = trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts), process=False)
        parties, _, r = D.decouper(piece, {"origine": [20, 20, 20], "normale": [0, 0, 1]}, {"nombre": 0})
        self.assertTrue(all(p.is_watertight for p in parties.values()))
        self.assertEqual(statuts(r, "partie"), ["WARN", "WARN"])

    def test_ilots_signales(self):
        u = trimesh.boolean.difference(
            [
                cube(),
                trimesh.creation.box((10, 50, 30), transform=trimesh.transformations.translation_matrix((20, 20, 30))),
            ],
            engine="manifold",
        )
        parties, _, r = D.decouper(u, {"origine": [20, 20, 30], "normale": [0, 0, 1]}, {"nombre": 0})
        self.assertEqual(statuts(r, "partie dessus"), ["WARN"])
        self.assertEqual(len(parties["dessus"].split(only_watertight=False)), 2)

    def test_entrees_invalides(self):
        for plan, g in [
            ({"origine": [0, 0, 0], "normale": [0, 0, 0]}, {}),
            (PLAN, {"diametre": 1}),
            (PLAN, {"nombre": 9}),
            (PLAN, {"jeu": float("nan")}),
            (PLAN, {"inconnu": 1}),
            ({"origine": [0, 0, 99], "normale": [0, 0, 1]}, {}),
        ]:
            with self.subTest(plan=plan, g=g), self.assertRaises(ValueError):
                D.decouper(cube(), plan, g)

    def test_original_intact_et_jamais_ecrase(self):
        m = cube()
        avant = m.vertices.copy()
        parties, pions, r = D.decouper(m, PLAN)
        self.assertTrue(np.array_equal(avant, m.vertices))
        with tempfile.TemporaryDirectory() as t:
            s = D.ecrire(parties, pions, r, Path(t) / "sortie")
            self.assertEqual(
                sorted(p.name for p in s.iterdir()),
                ["decoupe.json", "goupille-1.stl", "goupille-2.stl", "partie-dessous.stl", "partie-dessus.stl"],
            )
            with self.assertRaises(FileExistsError):
                D.ecrire(parties, pions, r, s)

    def test_cli_unites(self):
        with tempfile.TemporaryDirectory() as t:
            src = Path(t) / "cube-cm.stl"
            c = cube(4)
            c.export(src)  # 4 cm → 40 mm
            self.assertEqual(
                D.main(
                    [
                        "decouper",
                        str(src),
                        "--unites",
                        "cm",
                        "--plan",
                        "20,20,20,0,0,1",
                        "--sortie",
                        str(Path(t) / "out"),
                    ]
                ),
                0,
            )
            self.assertAlmostEqual(trimesh.load(Path(t) / "out/partie-dessous.stl").extents[0], 40, places=3)


if __name__ == "__main__":
    unittest.main()
