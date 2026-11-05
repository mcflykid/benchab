"""Cas synthétiques du contrôle des colonnes isolées (arrosoir P09) ; aucune impression."""

import math
import tracemalloc
import unittest

import numpy as np

from benchab.controles.colonnes_isolees import cases, controler


def cases_historique(segs, o, forme, pas):
    """Boucle d'origine (jusqu'au 28/10/2026), conservée comme référence d'équivalence de la version vectorisée."""
    m = np.zeros(forme, bool)
    for x0, y0, x1, y1 in segs:
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / (pas / 2)) + 1)
        t = np.linspace(0, 1, n)
        i = np.clip(((x0 + (x1 - x0) * t - o[0]) / pas).astype(int), 0, forme[0] - 1)
        j = np.clip(((y0 + (y1 - y0) * t - o[1]) / pas).astype(int), 0, forme[1] - 1)
        m[i, j] = True
    return m


def carre(x, y, c):
    return [
        f"G1 X{x} Y{y}",
        f"G1 X{x + c} Y{y} E.5",
        f"G1 X{x + c} Y{y + c} E.5",
        f"G1 X{x} Y{y + c} E.5",
        f"G1 X{x} Y{y} E.5",
    ]


def gcode(couches=150, h=0.2, colonnes=((10, 10), (110, 10)), cote=8, relevage=0.4, corps=None, support=None):
    """Colonnes carrées de `cote` mm avec déplacement relevé de `relevage` mm.

    `corps` ajoute un carré large à chaque couche ; `support` ajoute un pilier
    carré de 4 mm sous le trajet.
    """
    L = ["G90", "M83"]
    for k in range(1, couches + 1):
        z = round(k * h, 3)
        L += [";LAYER_CHANGE", f";Z:{z}", f"G1 Z{z}"]
        if support:
            L += [";TYPE:Support"] + carre(*support, 4)
        if corps:
            L += [";TYPE:Outer wall"] + carre(*corps, 60)
        for i, (x, y) in enumerate(colonnes):
            L += [f"G1 Z{z + relevage}", f"G1 X{x} Y{y}", f"G1 Z{z}"]  # chaque déplacement vers une colonne est relevé
            L += [";TYPE:Outer wall"] + carre(x, y, cote)
    return "\n".join(L) + "\n"


class ColonnesIsolees(unittest.TestCase):
    def test_cas_arrosoir_deux_colonnes_eloignees_peu_relevees(self):
        r = controler(gcode())
        self.assertEqual(r["statut"], "WARN")
        s = r["series"][0]
        self.assertEqual(s["ilots_max"], 2)
        self.assertGreaterEqual(s["trajet_max_mm"], 90)
        self.assertEqual(s["relevage_mm"], [0.4])

    def test_relevage_suffisant(self):
        self.assertEqual(controler(gcode(relevage=2.0))["statut"], "PASS")

    def test_gros_volume_dans_la_couche(self):
        self.assertEqual(controler(gcode(corps=(30, 40)))["statut"], "PASS")

    def test_colonnes_trop_courtes_ou_trapues(self):
        self.assertEqual(controler(gcode(couches=20))["statut"], "PASS")  # 4 mm < 5 mm
        self.assertEqual(
            controler(gcode(couches=60, cote=25))["statut"], "PASS"
        )  # 12 mm < 2 × 28 mm (îlot de 25 mm, grille de 1 mm)

    def test_colonne_seule_au_dessus_de_supports_hauts(self):
        g = gcode(
            couches=250, colonnes=((110, 10),), support=(60, 12)
        )  # support haut de plus de 20 mm sur 30 mm de colonne
        r = controler(g)
        self.assertEqual(r["statut"], "WARN")
        self.assertGreaterEqual(r["series"][0]["support_survole_max_mm"], 20)
        self.assertEqual(
            controler(gcode(couches=250, colonnes=((110, 10),)))["statut"], "PASS"
        )  # sans support ni autre colonne

    def test_rien_a_analyser(self):
        self.assertEqual(controler("G90\nM83\n")["statut"], "NON VÉRIFIÉ")


class CasesVectorisees(unittest.TestCase):
    """Équivalence stricte avec la boucle historique (29/10/2026)."""

    def egal(self, segs, o=(-3.0, -3.0), forme=(60, 50), pas=1.0, **k):
        self.assertTrue(
            np.array_equal(cases(segs, np.array(o), forme, pas, **k), cases_historique(segs, np.array(o), forme, pas))
        )

    def test_aleatoire_identique(self):
        r = np.random.default_rng(29)
        for pas in (1.0, 0.5, 0.37):
            segs = [tuple(v) for v in r.uniform(-5, 55, (400, 4))]
            with self.subTest(pas=pas):
                self.egal(segs, pas=pas, forme=(int(70 / pas), int(70 / pas)))

    def test_degeneres_negatifs_et_bords(self):
        self.egal(
            [
                (3.0, 3.0, 3.0, 3.0),
                (-10.0, -10.0, -9.0, -9.0),
                (0.0, 0.0, 1e-9, 0.0),
                (80.0, 80.0, 90.0, 95.0),
                (-3.0, -3.0, 56.0, 46.0),
            ]
        )

    def test_vide(self):
        self.assertFalse(cases([], np.zeros(2), (5, 5), 1.0).any())
        self.assertFalse(cases(np.empty((0, 4)), np.zeros(2), (5, 5), 1.0).any())

    def test_paquets_identiques(self):
        segs = [tuple(v) for v in np.random.default_rng(1).uniform(0, 40, (300, 4))]
        self.assertTrue(
            np.array_equal(cases(segs, np.zeros(2), (45, 45), 1.0, paquet=7), cases(segs, np.zeros(2), (45, 45), 1.0))
        )

    def test_entrees_invalides(self):
        for segs in ([(1.0, 2.0, 3.0)], [(0.0, 0.0, float("nan"), 1.0)], [(0.0, 0.0, float("inf"), 1.0)]):
            with self.subTest(segs=segs), self.assertRaises(ValueError):
                cases(segs, np.zeros(2), (5, 5), 1.0)

    def test_un_segment_plus_long_que_paquet_reste_borne(self):
        from unittest.mock import patch

        original = np.arange
        tailles = []

        def observe(*args, **kwargs):
            out = original(*args, **kwargs)
            tailles.append(len(out))
            return out

        segs = [(0.0, 0.0, 100.0, 3.0), (1.0, 4.0, 2.0, 6.0)]
        with patch("benchab.controles.colonnes_isolees.np.arange", side_effect=observe):
            resultat = cases(segs, np.zeros(2), (101, 10), 0.1, paquet=7)
        self.assertLessEqual(max(tailles), 7)
        self.assertTrue(np.array_equal(resultat, cases_historique(segs, np.zeros(2), (101, 10), 0.1)))

    def test_pas_et_paquet_invalides_refuses(self):
        for pas in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(pas=pas), self.assertRaises(ValueError):
                cases([], np.zeros(2), (2, 2), pas)
        for paquet in (0, -1, True, 1.5):
            with self.subTest(paquet=paquet), self.assertRaises(ValueError):
                cases([], np.zeros(2), (2, 2), 1.0, paquet=paquet)

    def test_memoire_bornee(self):
        segs = [(0.0, 0.0, 400.0, 0.0)] * 3000  # 2,4 millions de points d'échantillonnage
        tracemalloc.start()
        cases(segs, np.zeros(2), (401, 2), 1.0, paquet=100_000)
        pic = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        self.assertLess(pic, 40e6)  # paquets de 100 000 points : quelques Mo, pas 2,4 millions × 8 tableaux


if __name__ == "__main__":
    unittest.main()
