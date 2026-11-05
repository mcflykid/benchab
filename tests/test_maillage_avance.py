"""Essais natifs et refus de publication des transformations de maillages."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from benchab.imports3d import maillage_avance as M


class MaillageAvance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name).resolve()
        self.source = self.d / "source.stl"
        self.sortie = self.d / "copie.stl"
        self.rapport = self.d / "rapport.json"
        self.mesh = trimesh.creation.icosphere(subdivisions=3, radius=10)
        self.mesh.export(self.source)
        self.sha = M.empreinte(self.source)

    def run_mode(self, mode="simplifier", **kw):
        params = dict(tolerance_mm=0.5, echantillons=300)
        params.update(dict(faces_cibles=300) if mode == "simplifier" else dict(detail_mm=2, offset_mm=0.05))
        params.update(kw)
        return M.transformer(self.source, self.sortie, self.rapport, "mm", mode, **params)

    def test_moteurs_natifs_et_versions(self):
        d = M.moteur("diagnostic")
        self.assertTrue(d["alpha_wrap"])
        self.assertTrue(d["simplification"])
        self.assertEqual(d["versions"]["pymeshlab"], "2025.7.post1")
        self.assertEqual(d["versions"]["meshoptimizer"], "0.2.30a0")

    def test_reduction_native_export_relectures_et_source_intacte(self):
        r = self.run_mode()
        self.assertEqual(r["statut"], "CONFORME_ECHANTILLONS", r["raisons_refus"])
        self.assertLess(r["apres"]["faces"], r["avant"]["faces"])
        self.assertTrue(r["apres"]["solide_oriente_exterieur"])
        self.assertEqual(M.empreinte(self.source), self.sha)
        self.assertEqual(M.empreinte(self.sortie), r["sortie_sha256"])
        self.assertEqual(json.loads(self.rapport.read_text()), r)
        self.assertLessEqual(r["comparaison"]["reference_vers_candidat"]["maximum_observe_mm"], 0.5)

    def test_alpha_wrap_repare_une_vraie_surface_ouverte(self):
        self.mesh.update_faces(self.mesh.triangles_center[:, 2] < 9.5)
        self.mesh.export(self.source)
        self.assertFalse(M.charger(self.source, "mm").is_watertight)
        r = self.run_mode("alpha-wrap", tolerance_mm=3, fermer_ouvertures=True)
        self.assertEqual(r["statut"], "RECONSTRUIT_A_REVOIR", r["raisons_refus"])
        self.assertTrue(r["apres"]["solide_oriente_exterieur"])
        self.assertTrue(r["apres"]["ferme_topologiquement"])
        self.assertTrue(r["source_preservee"])
        self.assertEqual(r["bouchage_avant_wrap"]["ouvertures"], 1)
        self.assertGreater(r["apres"]["volume_mm3"], 3500)

    def test_wrap_sans_bouchage_signale_le_risque_de_coque(self):
        self.mesh.update_faces(self.mesh.triangles_center[:, 2] < 9.5)
        self.mesh.export(self.source)
        r = self.run_mode("alpha-wrap", tolerance_mm=3)
        self.assertTrue(r["risque_coque_autour_surface_ouverte"])
        self.assertFalse(r["bouchage_avant_wrap"]["demande"])

    def test_ecart_trop_grand_refuse_le_stl_et_conserve_diagnostic(self):
        r = self.run_mode(tolerance_mm=0.01, faces_cibles=100)
        self.assertEqual(r["statut"], "REFUSE")
        self.assertTrue(self.rapport.exists())
        self.assertFalse(self.sortie.exists())

    def test_alpha_wrap_ecart_hors_tolerance(self):
        r = self.run_mode("alpha-wrap", offset_mm=0.5, tolerance_mm=0.01)
        self.assertEqual(r["statut"], "REFUSE")
        self.assertFalse(self.sortie.exists())

    def test_petits_corps_conserves_par_la_reduction(self):
        petit = trimesh.creation.box(extents=[0.5, 0.5, 0.5])
        petit.apply_translation([15, 0, 0])
        trimesh.util.concatenate([self.mesh, petit]).export(self.source)
        r = self.run_mode(faces_cibles=500)
        self.assertEqual(r["statut"], "CONFORME_ECHANTILLONS", r["raisons_refus"])
        self.assertEqual(r["apres"]["corps_connectes"], 2)

    def test_retrait_de_corps_refuse_meme_si_le_moteur_reussit(self):
        petit = trimesh.creation.box()
        petit.apply_translation([15, 0, 0])
        trimesh.util.concatenate([self.mesh, petit]).export(self.source)
        with patch.object(M, "moteur", return_value=(self.mesh.copy(), {})):
            r = self.run_mode("alpha-wrap", tolerance_mm=20)
        self.assertEqual(r["statut"], "REFUSE")
        self.assertTrue(any("Nombre de corps" in s for s in r["raisons_refus"]))
        self.assertFalse(self.sortie.exists())

    def test_cibles_et_parametres_invalides_avant_moteur(self):
        with patch.object(M, "moteur") as moteur:
            for kw in [
                dict(faces_cibles=0),
                dict(faces_cibles=True),
                dict(tolerance_mm=float("nan")),
                dict(echantillons=0),
                dict(max_faces=10),
                dict(delai=0),
            ]:
                with self.subTest(kw=kw), self.assertRaises((ValueError, TypeError)):
                    self.run_mode(**kw)
            moteur.assert_not_called()
            self.assertFalse(self.sortie.exists())

    def test_sortie_existante_rapport_existant_et_collision(self):
        for cible in [self.sortie, self.rapport]:
            cible.write_text("à garder")
            with patch.object(M, "moteur") as moteur, self.assertRaises(ValueError):
                self.run_mode()
            moteur.assert_not_called()
            self.assertEqual(cible.read_text(), "à garder")
            cible.unlink()
        with self.assertRaises(ValueError):
            M.transformer(
                self.source, self.source, self.rapport, "mm", "simplifier", tolerance_mm=0.5, faces_cibles=300
            )

    def test_liens_symboliques_refuses(self):
        link = self.d / "lien"
        link.symlink_to(self.d, target_is_directory=True)
        with self.assertRaises(ValueError):
            M.transformer(
                self.source, link / "copie.stl", self.rapport, "mm", "simplifier", tolerance_mm=0.5, faces_cibles=300
            )

    def test_source_changee_par_un_traitement_interdit_publication(self):
        def changer(*args, **kw):
            self.source.write_bytes(b"changement")
            return self.mesh.copy(), {}

        with patch.object(M, "moteur", side_effect=changer), self.assertRaises(ValueError):
            self.run_mode("alpha-wrap")
        self.assertFalse(self.sortie.exists())
        self.assertFalse(self.rapport.exists())

    def test_erreur_relecture_n_ecrit_ni_stl_ni_rapport(self):
        with (
            patch.object(M, "verifier_export", side_effect=ValueError("export divergent")),
            self.assertRaises(ValueError),
        ):
            self.run_mode()
        self.assertFalse(self.sortie.exists())
        self.assertFalse(self.rapport.exists())

    def test_runtime_absent_et_delai_interrompu_sans_sortie(self):
        with patch.object(M, "NATIF", self.d / "absent"), self.assertRaises(ValueError):
            self.run_mode()
        with self.assertRaises(ValueError):
            M.moteur("diagnostic", delai=0.00001)
        self.assertFalse(self.sortie.exists())
        self.assertFalse(self.rapport.exists())

    def test_echelle_cm_mesuree_en_mm(self):
        self.mesh.apply_scale(0.1)
        self.mesh.export(self.source)
        r = M.transformer(
            self.source,
            self.sortie,
            self.rapport,
            "cm",
            "simplifier",
            faces_cibles=500,
            tolerance_mm=0.5,
            echantillons=100,
        )
        self.assertEqual(r["statut"], "CONFORME_ECHANTILLONS")
        np.testing.assert_allclose(r["avant"]["dimensions_mm"], [20, 20, 20], atol=1e-5)

    def test_cli_diagnostic_et_rejet_options_ambigues(self):
        self.assertEqual(M.main(["diagnostic"]), 0)
        with self.assertRaises(SystemExit) as e:
            M.main(
                [
                    "simplifier",
                    str(self.source),
                    "--sortie",
                    str(self.sortie),
                    "--rapport",
                    str(self.rapport),
                    "--unites",
                    "mm",
                    "--tolerance-mm",
                    ".5",
                    "--faces-cibles",
                    "300",
                    "--detail-mm",
                    "2",
                ]
            )
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
