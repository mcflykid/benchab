"""Études retrouvables et bornées, sans réseau ni installation."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchab.connaissances import etudes as E
from benchab.connaissances import memoire as M


class Etudes(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.r = Path(t.name).resolve()
        (self.r / "donnees/connaissances").mkdir(parents=True)
        source = self.r / "source.md"
        source.write_text("documentation ciblée")
        self.item = {
            "id": "ET-1",
            "type": "ETUDE_TECHNIQUE",
            "titre": "Moteur X",
            "domaine": "capacites",
            "conditions": "lecture ciblée du README au commit exact",
            "exclusions": "ni code audité ni essai physique",
            "niveau_preuve": "DOCUMENTATION_LUE",
            "validite": "contextuelle",
            "sources": [{"chemin": "source.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
            "etude": {
                "depot": "exemple/moteur",
                "commit": "a" * 40,
                "version": "1.0",
                "perimetre": "README introduction",
                "couverture": "DOCUMENTATION_LUE",
                "integration": "REPORTE",
                "licence": "MIT déclarée, dépendances non auditées",
                "fichiers_lus": [
                    {
                        "url": "https://github.com/exemple/moteur/blob/" + ("a" * 40) + "/README.md",
                        "portee": "introduction",
                        "sha256": "b" * 64,
                    }
                ],
                "non_lu": ["code et dépendances"],
                "decision": "reporter",
                "raison": "fonction déjà couverte",
                "reexaminer_si": "une API de distances exacte manque",
                "declencheurs_capacites": ["distance-exacte"],
                "capacites": {"declarees": ["distance-exacte"], "testees": [], "integrees": []},
            },
        }
        self.write([self.item])

    def write(self, items):
        self.data = {"connaissances": items, "journaux": {}}
        (self.r / "donnees/connaissances/registre.json").write_text(json.dumps(self.data))

    def test_refus_retrouve_avec_sources_limites_et_declencheur(self):
        self.assertEqual(E.verifier(self.data), [])
        q = M.chercher(self.r, "Moteur X")[0]
        self.assertEqual(q["etude"]["raison"], "fonction déjà couverte")
        self.assertIn("manque", q["etude"]["reexaminer_si"])
        self.assertTrue(q["sources"])
        self.assertTrue(q["exclusions"])

    def test_non_etudie_ne_devient_pas_lu_ou_essaye(self):
        e = self.item["etude"]
        e["couverture"] = "NON_ETUDIE"
        self.assertTrue(E.verifier(self.data))
        e["fichiers_lus"] = []
        self.assertEqual(E.verifier(self.data), [])
        e["integration"] = "ESSAYE"
        self.assertTrue(E.verifier(self.data))

    def test_readme_ne_devient_pas_audit_code(self):
        self.item["etude"]["code_audite"] = ["tout"]
        self.assertTrue(E.verifier(self.data))
        self.item["etude"]["couverture"] = "AUDIT_EXHAUSTIF"
        self.assertTrue(E.verifier(self.data))

    def test_synthetique_ne_devient_pas_physique(self):
        self.item["etude"]["couverture"] = "TEST_SYNTHETIQUE"
        self.item["niveau_preuve"] = "MESURE_PHYSIQUE"
        self.assertTrue(E.verifier(self.data))
        self.item["niveau_preuve"] = "NUMERIQUE"
        self.assertEqual(E.verifier(self.data), [])

    def test_release_seule_ne_relance_pas_etude(self):
        snapshot = {"exemple/moteur": {"commit": "c" * 40, "version": "2.0", "url": "https://example.com/release"}}
        q = E.proposer(self.data, snapshot)[0]
        self.assertEqual(q["action"], "INFORMATION_SEULE")
        self.assertFalse(q["installation"])
        snapshot["exemple/moteur"]["capacites_demontrees"] = ["autre-fonction"]
        self.assertEqual(E.proposer(self.data, snapshot)[0]["action"], "INFORMATION_SEULE")

    def test_nouvelle_capacite_est_proposition_pas_integration(self):
        snapshot = {
            "exemple/moteur": {
                "commit": "c" * 40,
                "capacites_demontrees": ["distance-exacte"],
                "url": "https://example.com/commit",
            }
        }
        before = copy.deepcopy(self.data)
        q = E.proposer(self.data, snapshot)[0]
        self.assertEqual(q["action"], "PROPOSER_ETUDE")
        self.assertFalse(q["installation"])
        self.assertEqual(self.data, before)
        snapshot["exemple/moteur"]["commit"] = "a" * 40
        self.assertEqual(E.proposer(self.data, snapshot)[0]["action"], "INFORMATION_SEULE")

    def test_source_changee_invalide_conclusion_dependante(self):
        self.assertEqual(M.verifier(self.r), [])
        (self.r / "source.md").write_text("source modifiée")
        self.assertTrue(M.verifier(self.r))

    def test_doublon_meme_commit_perimetre_refuse(self):
        autre = copy.deepcopy(self.item)
        autre["id"] = "ET-2"
        self.write([self.item, autre])
        self.assertTrue(E.verifier(self.data))
        autre["etude"]["perimetre"] = "API distances"
        self.assertEqual(E.verifier(self.data), [])

    def test_remplacee_conservee_historique(self):
        self.item["validite"] = "remplacee"
        self.write([self.item])
        self.assertEqual(M.chercher(self.r, "Moteur"), [])
        self.assertEqual(len(M.chercher(self.r, "Moteur", {"historique": True})), 1)
        self.assertEqual(E.proposer(self.data, {"exemple/moteur": {"commit": "c" * 40}}), [])

    def test_conditions_fabrication_distinctes_et_contradiction_signalee(self):
        autre = copy.deepcopy(self.item)
        autre["id"] = "ET-2"
        self.item.update(comparabilite="contexte-A", faits={"precision": 1})
        autre.update(comparabilite="contexte-B", faits={"precision": 2})
        self.write([self.item, autre])
        self.assertEqual(M.contradictions(self.r), [])
        autre["comparabilite"] = "contexte-A"
        self.write([self.item, autre])
        self.assertEqual(len(M.contradictions(self.r)), 1)

    def test_pas_de_capacite_integree_sans_essai(self):
        e = self.item["etude"]
        e["integration"] = "INTEGRE"
        e["capacites"]["integrees"] = ["distance-exacte"]
        self.assertTrue(E.verifier(self.data))
        e["capacites"]["testees"] = ["distance-exacte"]
        e["preuves_essais"] = ["source.md"]
        self.assertTrue(E.verifier(self.data))
        e["couverture"] = "TEST_SYNTHETIQUE"
        self.item["niveau_preuve"] = "NUMERIQUE"
        self.assertEqual(E.verifier(self.data), [])

    def test_malforme_refuse_sans_exception(self):
        self.item["etude"] = None
        self.assertTrue(E.verifier(self.data))


if __name__ == "__main__":
    unittest.main()
