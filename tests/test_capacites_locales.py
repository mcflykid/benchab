"""Inventaire local ; refus des anciens runtimes externes conformément au Python unique."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchab.conception import capacites_locales as C


class Capacites(unittest.TestCase):
    def test_runtime_cao_integre_par_defaut(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(C.cad_python(), Path(sys.executable))

    def test_runtime_cao_override_expansion_refuse(self):
        with mock.patch.dict(os.environ, {"BENCHAB_CAD_PYTHON": "~/runtime-personnel/bin/python"}):
            with self.assertRaisesRegex(ValueError, "Python unique"):
                C.cad_python()

    def test_runtime_externe_existant_refuse_sans_lancement(self):
        """Un autre chemin Python est refusé avant tout appel du lanceur commun."""
        with tempfile.TemporaryDirectory() as dossier:
            autre = Path(dossier) / "python"
            autre.symlink_to(sys.executable)
            with mock.patch.dict(os.environ, {"BENCHAB_CAD_PYTHON": str(autre)}):
                with self.assertRaisesRegex(ValueError, "Python unique"):
                    C.cad_python()
                interdit = AssertionError("aucun runtime externe")
                with mock.patch.object(C.processus, "executer", side_effect=interdit) as appel:
                    disponible, raison = C.importer(autre, ["json"])
                    self.assertFalse(disponible)
                    self.assertIn("Python unique", raison)
                    self.assertEqual(C.versions(autre, ["json"]), {"json": None})
                    appel.assert_not_called()

    def test_ports_incomplets_jamais_declares_disponibles(self):
        capacite = ("fixture", sys.executable, ["json"], [], "benchab/conception/capacites_locales.py")
        with mock.patch.dict(C.A_PORTER, {"fixture_incomplete": capacite}):
            resultats = C.evaluer({"fixture_incomplete": capacite})
        for nom, resultat in resultats.items():
            self.assertEqual(resultat["statut"], "ABSENTE", nom)
            self.assertTrue(any("Port incomplet" in raison for raison in resultat["raisons"]))

    def test_cloud_historique_absent_du_diagnostic_courant(self):
        import types

        module = types.SimpleNamespace(etat=mock.Mock(side_effect=AssertionError("aucune lecture de compte")))
        with mock.patch.dict(sys.modules, {"cloud_lecture": module}):
            self.assertNotIn("cloud_anycubic", C.CAPACITES)
            self.assertIn("cloud_anycubic", C.HISTORIQUES)
            C.evaluer()
        module.etat.assert_not_called()

    def test_capacite_presente(self):
        r = C.evaluer({"x": ("stdlib", sys.executable, ["json"], [], "benchab/conception/capacites_locales.py")})
        self.assertEqual(r["x"]["statut"], "DISPONIBLE")
        self.assertEqual(r["x"]["raisons"], [])

    def test_module_absent_isole(self):
        r = C.evaluer(
            {
                "a": (
                    "absente",
                    sys.executable,
                    ["module_qui_nexiste_pas_42"],
                    [],
                    "benchab/conception/capacites_locales.py",
                ),
                "b": ("présente", sys.executable, ["json"], [], "benchab/conception/capacites_locales.py"),
            }
        )
        self.assertEqual(r["a"]["statut"], "ABSENTE")
        self.assertIn("module_qui_nexiste_pas_42", r["a"]["raisons"][0])
        self.assertEqual(r["b"]["statut"], "DISPONIBLE")

    def test_programme_interpreteur_et_outil_absents(self):
        r = C.evaluer(
            {
                "p": ("prog", None, [], ["programme-inexistant-42"], "benchab/conception/capacites_locales.py"),
                "i": ("interp", "/chemin/inexistant/python", ["json"], [], "benchab/conception/capacites_locales.py"),
                "o": ("outil", sys.executable, [], [], "outils/inexistant.py"),
            }
        )
        self.assertTrue(all((v["statut"] == "ABSENTE" for v in r.values())))

    def test_chaque_capacite_declare_un_outil_existant(self):
        for nom, (_, _, _, _, outil) in C.CAPACITES.items():
            self.assertTrue((C.R / outil).exists(), f"{nom} : {outil}")
