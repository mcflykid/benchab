import unittest

from benchab.controles.contact_support import ContactSupport


def move(z, kind="Support", x=0):
    return {"deposited": 1, "deposition_start": [x, 0, z], "end": [x + 5, 0, z], "type": kind}


class Contact(unittest.TestCase):
    def test_ecart_et_projection_estimes(self):
        c = ContactSupport()
        c.ajouter(move(1), {"WIDTH": 1, "HEIGHT": 0.2})
        c.ajouter(move(1.4, "Outer wall"), {"WIDTH": 1, "HEIGHT": 0.2})
        r = c.resultat()
        self.assertEqual(r["ecart_z_local_mm"]["minimum"], 0.2)
        self.assertGreater(r["projection_interface_estimee_mm2"], 0)
        self.assertIsNone(r["surface_contact_physique_mm2"])

    def test_variation_locale(self):
        c = ContactSupport()
        for x, gap in [(0, 0.2), (10, 0.6)]:
            c.ajouter(move(1, x=x), {"WIDTH": 1, "HEIGHT": 0.2})
            c.ajouter(move(1.2 + gap, "Outer wall", x), {"WIDTH": 1, "HEIGHT": 0.2})
        r = c.resultat()
        self.assertEqual(r["ecart_z_local_mm"]["minimum"], 0.2)
        self.assertEqual(r["ecart_z_local_mm"]["maximum"], 0.6)

    def test_absence_height_n_invente_pas_nominal(self):
        c = ContactSupport()
        c.ajouter(move(1), {"WIDTH": 1})
        r = c.resultat()
        self.assertIsNone(r["projection_interface_estimee_mm2"])
        self.assertEqual(r["couverture"]["segments_sans_metadonnees_exploitables"], 1)

    def test_eloigne_et_sans_interface(self):
        c = ContactSupport()
        c.ajouter(move(1), {"WIDTH": 1, "HEIGHT": 0.2})
        c.ajouter(move(4, "Outer wall"), {"WIDTH": 1, "HEIGHT": 0.2})
        self.assertEqual(c.resultat()["statut"], "NON_MESURABLE")

    def test_type_auxiliaire_et_coordonnees_extremes_exclus(self):
        c = ContactSupport()
        c.ajouter(move(1, "Wipe tower"), {"WIDTH": 1, "HEIGHT": 0.2})
        c.ajouter(move(1, x=1e300), {"WIDTH": 1, "HEIGHT": 0.2})
        self.assertEqual(c.resultat()["couverture"]["segments_sans_metadonnees_exploitables"], 2)

    def test_budget_et_parseur_invalident_mesures(self):
        c = ContactSupport(max_echantillons=2)
        c.ajouter(move(1), {"WIDTH": 1, "HEIGHT": 0.2})
        self.assertEqual(c.resultat()["budgets"]["arret"], "budget_echantillons")
        self.assertIsNone(c.resultat()["ecart_z_local_mm"]["minimum"])
        with self.assertRaises(ValueError):
            ContactSupport(cellule=0)


if __name__ == "__main__":
    unittest.main()
