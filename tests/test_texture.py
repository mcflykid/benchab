"""Textures de surface (chantier H) : géométrie seulement, aucun tranchage ni impression."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh
from PIL import Image

from benchab.conception import texture as T


def cube(c=20):
    m = trimesh.creation.box(extents=(c, c, c))
    m.apply_translation((c / 2, c / 2, c / 2))
    return m


MOL = {"motif": "moletage", "periode": 3.0, "amplitude": 0.5, "arete_max": 1.0}


def statut(rapport, debut):
    return [c["statut"] for c in rapport["controles"] if c["controle"].startswith(debut)]


class Textures(unittest.TestCase):
    def test_plateau_et_dessous_intacts_solide_valide(self):
        var, r = T.appliquer(cube(), MOL)
        self.assertEqual(statut(r, "solide"), ["PASS"])
        self.assertEqual(statut(r, "plateau"), ["PASS"])
        self.assertAlmostEqual(var.bounds[0][2], 0, places=6)
        self.assertLess(abs(r["volume_texture_mm3"] / r["volume_reference_mm3"] - 1), 0.03)  # relief symétrique

    def test_region_protegee_inchangee(self):
        zone = {
            "nom": "clé",
            "type": "interface",
            "forme": "boite",
            "min": [-1, -1, 5],
            "max": [21, 21, 12],
            "marge": 0.5,
        }
        var, r = T.appliquer(cube(), MOL, {"protege": [zone]})
        ref, _ = T.appliquer(cube(), dict(MOL, amplitude=1e-9))
        dans = (ref.vertices[:, 2] > 4.5) & (ref.vertices[:, 2] < 12.5)
        self.assertLess(np.abs(var.vertices[dans] - ref.vertices[dans]).max(), 1e-6)
        self.assertEqual(statut(r, "plateau"), ["PASS"])

    def test_frontieres_boite_et_cylindre_reellement_immobiles(self):
        piece = cube(4)
        original = piece.vertices.copy()
        texture = {"motif": "cannelures", "amplitude": 0.2, "periode": 3, "arete_max": 1}
        v, f = trimesh.remesh.subdivide_to_size(piece.vertices, piece.faces, max_edge=1, max_iter=12)
        reference = trimesh.Trimesh(v, f, process=True)
        libre, _ = T.appliquer(piece, texture)
        zones = [
            (
                {"forme": "boite", "min": [-0.1, -0.1, -0.1], "max": [2, 4.1, 4.1]},
                np.isclose(reference.vertices[:, 0], 2) & (reference.vertices[:, 2] > 0),
            ),
            (
                {"forme": "cylindre", "centre": [2, 2, 2], "rayon": 2, "hauteur": 4},
                np.all(np.isclose(reference.vertices, [4, 2, 2]), axis=1)
                | np.all(np.isclose(reference.vertices, [2, 2, 4]), axis=1),
            ),
        ]
        for zone, frontiere in zones:
            with self.subTest(forme=zone["forme"]):
                self.assertTrue(frontiere.any())
                self.assertGreater(
                    np.linalg.norm(libre.vertices[frontiere] - reference.vertices[frontiere], axis=1).max(), 1e-3
                )
                protegee, rapport = T.appliquer(piece, texture, {"protege": [zone]})
                np.testing.assert_array_equal(protegee.vertices[frontiere], reference.vertices[frontiere])
                self.assertEqual(statut(rapport, "plateau"), ["PASS"])
        np.testing.assert_array_equal(piece.vertices, original)

    def test_motif_trop_fin_refuse(self):
        self.assertEqual(statut(T.appliquer(cube(), dict(MOL, periode=1.0))[1], "motif"), ["FAIL"])

    def test_amplitude_excessive_signalee(self):
        self.assertEqual(statut(T.appliquer(cube(), dict(MOL, amplitude=1.5))[1], "amplitude"), ["WARN"])

    def test_projection_cylindrique(self):
        cyl = trimesh.creation.cylinder(radius=10, height=20, sections=64)
        cyl.apply_translation((0, 0, 10))
        var, r = T.appliquer(cyl, dict(MOL, motif="cannelures", projection="cylindrique", axe="z"))
        self.assertEqual(statut(r, "solide"), ["PASS"])
        self.assertAlmostEqual(var.bounds[0][2], 0, places=6)

    def test_parametres_invalides(self):
        for t in [dict(MOL, motif="inconnu"), dict(MOL, amplitude=0), dict(MOL, periode=-1)]:
            with self.subTest(t=t), self.assertRaises(ValueError):
                T.appliquer(cube(), t)

    def test_zone_hors_surface_ne_change_rien(self):
        t = dict(MOL, zone={"min": [30, 30, 30], "max": [40, 40, 40]}, transition_mm=2)
        var, r = T.appliquer(cube(), t)
        self.assertEqual(r["part_texturee"], 0)
        self.assertAlmostEqual(var.volume, 8000, places=5)

    def test_transition_et_protection_prioritaire(self):
        zone = {"min": [-1, -1, 4], "max": [21, 21, 16]}
        t = dict(MOL, zone=zone, transition_mm=3)
        var, r = T.appliquer(cube(), t)
        ref, _ = T.appliquer(cube(), dict(MOL, amplitude=1e-9))
        hors = (ref.vertices[:, 2] <= 4) | (ref.vertices[:, 2] >= 16)
        self.assertLess(np.abs(var.vertices[hors] - ref.vertices[hors]).max(), 1e-6)
        self.assertEqual(statut(r, "solide"), ["PASS"])
        protect = {"nom": "interface", "forme": "boite", "min": [-2, -2, 8], "max": [22, 22, 12]}
        var, _ = T.appliquer(cube(), t, {"protege": [protect]})
        dedans = (ref.vertices[:, 2] > 8) & (ref.vertices[:, 2] < 12)
        self.assertLess(np.abs(var.vertices[dedans] - ref.vertices[dedans]).max(), 1e-6)

    def test_transition_reduit_saut_de_rellef(self):
        t = dict(MOL, zone={"min": [-1, -1, 4], "max": [21, 21, 16]})
        dur, _ = T.appliquer(cube(), t)
        doux, _ = T.appliquer(cube(), dict(t, transition_mm=3))
        ref, _ = T.appliquer(cube(), dict(MOL, amplitude=1e-9))
        bande = (ref.vertices[:, 2] > 4) & (ref.vertices[:, 2] < 5)
        self.assertLess(
            np.linalg.norm(doux.vertices[bande] - ref.vertices[bande], axis=1).max(),
            np.linalg.norm(dur.vertices[bande] - ref.vertices[bande], axis=1).max(),
        )

    def test_zone_invalide_et_non_fini(self):
        for extra in [
            {"transition_mm": 1},
            {"transition_mm": float("nan")},
            {"zone": {"min": [0, 0, 0], "max": [0, 1, 1]}},
            {"zone": {"min": [0, 0, 0], "max": [1, 1, 1]}, "transition_mm": 2},
        ]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                T.appliquer(cube(), dict(MOL, **extra))


class ValidationPrealable(unittest.TestCase):
    def test_B26_numeriques_avant_subdivision(self):
        for key in ["amplitude", "periode", "arete_max", "angle_bas", "angle_haut"]:
            for value in [float("nan"), float("inf"), float("-inf")] + (
                [0, -1] if key in ["amplitude", "periode", "arete_max"] else []
            ):
                with (
                    self.subTest(key=key, value=value),
                    patch.object(
                        T.trimesh.remesh, "subdivide_to_size", side_effect=AssertionError("subdivision atteinte")
                    ),
                    self.assertRaises(ValueError),
                ):
                    T.appliquer(cube(), dict(MOL, **{key: value}))

    def test_B26_projection_axe_centre_et_angles(self):
        for extra in [
            {"projection": "inconnue"},
            {"projection": "cylindrique", "axe": "q"},
            {"projection": "cylindrique", "centre": [float("nan"), 0]},
            {"projection": "cylindrique", "centre": [0, 0, 0]},
            {"angle_bas": -1},
            {"angle_haut": 181},
        ]:
            with (
                self.subTest(extra=extra),
                patch.object(T.trimesh.remesh, "subdivide_to_size", side_effect=AssertionError("subdivision atteinte")),
                self.assertRaises(ValueError),
            ):
                T.appliquer(cube(), dict(MOL, **extra))

    def test_B26_budget_avant_subdivision(self):
        with (
            patch.object(T.trimesh.remesh, "subdivide_to_size", side_effect=AssertionError("allocation dangereuse")),
            self.assertRaises(ValueError),
        ):
            T.appliquer(cube(), dict(MOL, arete_max=1e-12))

    def test_budget_faces_refuse_avant_allocation(self):
        with (
            patch.object(T, "FACES_MAX", 100),
            patch.object(T.trimesh.remesh, "subdivide_to_size", side_effect=AssertionError("allocation dangereuse")),
            self.assertRaises(ValueError),
        ):
            T.appliquer(cube(), MOL)

    def test_original_preserve(self):
        m = cube()
        v = m.vertices.copy()
        f = m.faces.copy()
        T.appliquer(m, MOL)
        self.assertTrue(np.array_equal(m.vertices, v))
        self.assertTrue(np.array_equal(m.faces, f))

    def test_image_motif_est_lue_et_son_chemin_n_est_pas_publie(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            image = Path(temporaire) / "motif.png"
            Image.new("L", (4, 4), color=180).save(image)
            _, rapport = T.appliquer(cube(4), {**MOL, "motif": str(image), "arete_max": 2.0})
        self.assertEqual(rapport["texture"]["motif"], "motif.png")


if __name__ == "__main__":
    unittest.main()
