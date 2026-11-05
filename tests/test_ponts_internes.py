"""Tests du contrôle des ponts internes sous le sommet (27/10/2026) : G-code synthétique, aucun tranchage."""

import unittest

from benchab.controles import ponts_internes as PI


def gcode(z_pont, longueur, z_haut=10.0):
    return "\n".join(
        [
            ";Z:2",
            ";TYPE:Outer wall",
            "G1 X0 Y0 E1",
            f";Z:{z_pont}",
            ";TYPE:Internal Bridge",
            "G1 X0 Y0",
            f"G1 X{longueur} Y0 E1",
            f";Z:{z_haut}",
            ";TYPE:Top surface",
            "G1 X5 Y5 E1",
        ]
    )


class Ponts(unittest.TestCase):
    def test_long_pont_sous_sommet(self):
        m = PI.mesurer(gcode(9.0, 14.0))
        self.assertEqual(m["longs_sous_sommet"], 1)
        self.assertEqual(m["portee_max_sous_sommet_mm"], 14.0)

    def test_pont_court_ignore(self):
        self.assertEqual(PI.mesurer(gcode(9.0, 6.0))["longs_sous_sommet"], 0)

    def test_pont_bas_ignore(self):
        self.assertEqual(PI.mesurer(gcode(5.0, 14.0))["longs_sous_sommet"], 0)

    def test_deplacement_non_compte(self):
        m = PI.mesurer(";Z:9\n;TYPE:Internal Bridge\nG1 X0 Y0\nG1 X20 Y0\n;Z:10\n;TYPE:Top surface\nG1 X1 Y1 E1")
        self.assertEqual(m["ponts_internes"], 0)


if __name__ == "__main__":
    unittest.main()
