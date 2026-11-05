"""Contrat normalisé et compatibilité des rapports historiques."""

import unittest

from benchab.qualite import validation as V


class Statuts(unittest.TestCase):
    def rapport(self, **kw):
        args = dict(
            execution="PASS",
            resultat="PASS",
            gravite="INFO",
            applicabilite="APPLICABLE",
            preuve="NUMERIQUE",
            origine="fixture explicite",
            entree_sha256="a" * 64,
            outil_version="fixture-1",
            conditions=["copie synthétique"],
        )
        args.update(kw)
        return V.rapport_controle(**args)

    def test_conversions_et_compatibilite(self):
        for value, expected in [
            ("WARN", "WARNING"),
            ("NON VÉRIFIÉ", "NON_VERIFIE"),
            ("NON APPLICABLE", "NON_APPLICABLE"),
            ("ERROR", "ERREUR_EXECUTION"),
            ("ATTENTE_HUMAINE", "EN_ATTENTE_HUMAINE"),
            ("inventé", "NON_VERIFIE"),
        ]:
            self.assertEqual(V.normaliser_statut(value), expected)
        self.assertEqual(V.agregation_statut({"statut": "WARN"}), "WARNING")
        self.assertTrue(V.mauvais({"statut": "PASS", "detail": {"ok": False}}))

    def test_dimensions_distinctes(self):
        q = self.rapport()
        q["etat_projet"] = "EN_CONCEPTION"
        q["metadonnees"] = {"status": "non pertinent"}
        self.assertFalse(V.mauvais(q))
        self.assertEqual(q["niveau_preuve"], "NUMERIQUE")
        self.assertTrue(V.mauvais(self.rapport(execution="ERREUR_EXECUTION")))
        self.assertTrue(V.mauvais(self.rapport(decision="EN_ATTENTE_HUMAINE")))
        self.assertTrue(V.mauvais(self.rapport(resultat="NON_APPLICABLE", applicabilite="NON_APPLICABLE")))
        self.assertEqual(V.agregation_statut(self.rapport(resultat="WARNING")), "WARNING")
        warning = self.rapport(resultat="WARNING")
        self.assertTrue(V.mauvais(warning))
        self.assertFalse(V.mauvais(warning, ("PASS", "WARNING")))
        self.assertTrue(V.mauvais(self.rapport(resultat="FAIL"), ("FAIL",)))
        self.assertTrue(V.mauvais({}))

    def test_schema_incomplet_et_contradiction(self):
        self.assertTrue(V.mauvais({"schema_controle": 1, "resultat": "PASS"}))
        with self.assertRaises(ValueError):
            self.rapport(resultat="NON_APPLICABLE")
        with self.assertRaises(ValueError):
            self.rapport(entree_sha256="absente")
        with self.assertRaises(ValueError):
            self.rapport(conditions=[])


if __name__ == "__main__":
    unittest.main()
