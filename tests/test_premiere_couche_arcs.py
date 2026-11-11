"""Contrats opt-in des arcs planaires de première couche."""

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.controles import premiere_couche as PC
from benchab.controles.gcode_interpreter import Interpreter


def mouvement_arc(gcode):
    """Extrait un arc déposé d'une fixture G-code synthétique."""
    lecteur = Interpreter()
    mouvement = None
    for ligne in gcode.splitlines():
        courant = lecteur.feed_line(ligne)
        if courant and courant["op"] in {"G2", "G3"}:
            mouvement = courant
    if lecteur.errors:
        raise AssertionError(lecteur.errors)
    if mouvement is None:
        raise AssertionError("arc absent")
    return mouvement


class ArcsPremiereCouche(unittest.TestCase):
    """Vérifie la qualification numérique explicite des arcs G17."""

    def test_cercle_complet_conserve_le_vide_central(self):
        """Un cercle analytique laisse libre son intérieur."""
        mouvement = mouvement_arc(
            "M83\nG1 X10 Y0 F600\n;TYPE:Outer wall\nG3 X10 Y0 I-10 J0 E10"
        )
        origine = (-12.0, -12.0)
        masque = PC.masquer_arcs([(mouvement, 0.42)], origine, (241, 241), 0.1)
        centre = (120, 120)
        self.assertTrue(masque[120, 220])
        self.assertFalse(masque[centre])

    def test_arc_ne_couvre_pas_la_corde(self):
        """L'empreinte de l'arc ne remplit pas la corde."""
        mouvement = mouvement_arc(
            "M83\nG1 X10 Y0 F600\n;TYPE:Outer wall\nG3 X0 Y10 I-10 J0 E10"
        )
        origine = (-12.0, -12.0)
        masque = PC.masquer_arcs([(mouvement, 0.42)], origine, (241, 241), 0.1)
        # Le milieu de la corde est loin de l'arc de quart de cercle.
        self.assertFalse(masque[170, 170])

    def test_arcs_non_planaires_et_budget_sont_refuses(self):
        """Les hélices, arcs arrondis et dépassements de budget restent refusés."""
        gcode_helice = "M83\n;Z:.2\nG0 Z.2 F600\n;TYPE:Outer wall\nG1 X10 Y0 E1\nG3 X0 Y10 Z1 I-10 J0 E10"
        self.assertEqual(PC.controler(gcode_helice, arcs_planaires=True)["statut"], "NON VÉRIFIÉ")
        mouvement = mouvement_arc(
            "M83\nG1 X10 Y0 F600\n;TYPE:Outer wall\nG3 X0 Y10 Z1 I-10 J0 E10"
        )
        with self.assertRaisesRegex(ValueError, "Z constant"):
            PC.masquer_arcs([(mouvement, 0.42)], (-12.0, -12.0), (241, 241), 0.1)
        cercle = mouvement_arc(
            "M83\nG1 X10 Y0 F600\n;TYPE:Outer wall\nG3 X10 Y0 I-10 J0 E10"
        )
        with self.assertRaisesRegex(ValueError, "budget"):
            PC.masquer_arcs([(cercle, 0.42)], (-12.0, -12.0), (241, 241), 0.1, budget=1)

        cercle["end"] = (cercle["end"][0] + 1e-7, cercle["end"][1], cercle["end"][2])
        with self.assertRaisesRegex(ValueError, "arrondie"):
            PC.masquer_arcs([(cercle, 0.42)], (-12.0, -12.0), (241, 241), 0.1)

    def test_budget_cumule_avant_toute_allocation_de_masque(self):
        """Les cellules candidates des arcs sont comptées avant les masques."""
        gcode = "M83\n;Z:.2\nG0 Z.2 F600\n;TYPE:Outer wall\nG1 X10 Y0 E1\nG3 X10 Y0 I-10 J0 E10"
        with patch.object(PC, "MAX_ARC_CELL_TESTS", 1), patch.object(
            PC.np, "zeros", side_effect=AssertionError("allocation interdite")
        ):
            resultat = PC.controler(gcode, arcs_planaires=True)
        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIn("budget", resultat["detail"])

    def test_z_physique_arc_doit_correspondre_au_marqueur(self):
        """Un commentaire de couche ne masque pas un arc physiquement à un autre Z."""
        gcode = (
            "M83\n;Z:.2\nG0 X10 Y0 Z4 F600\n;TYPE:Outer wall\n"
            "G3 X0 Y10 I-10 J0 E10"
        )
        resultat = PC.controler(gcode, arcs_planaires=True)
        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIn("Z déclaré", resultat["detail"])

    def test_retour_a_un_z_deja_vu_reutilise_son_index(self):
        """Un marqueur revenant à la première couche ne classe pas son arc en deuxième."""
        gcode = (
            "M83\n;Z:.2\nG0 X10 Y0 Z.2 F600\n;TYPE:Outer wall\nG3 X10 Y0 I-10 J0 E1\n"
            ";Z:.4\nG0 Z.4\nG3 X10 Y0 I-10 J0 E1\n;Z:.2\nG0 Z.2\nG3 X10 Y0 I-10 J0 E1"
        )
        _, _, arcs = PC._extraire_couches(gcode, True)
        self.assertEqual([len(couche) for couche in arcs], [2, 1])

    def test_arc_depose_invisible_sur_la_grille_est_non_verifie(self):
        """Un arc sans aucun échantillon couvert ne peut produire un PASS vide."""
        mouvement = mouvement_arc(
            "M83\nG1 X0.02 Y0 F600\n;TYPE:Outer wall\nG3 X0.02 Y0 I-0.02 J0 E0.1"
        )
        with self.assertRaisesRegex(ValueError, "invisible"):
            PC.masquer_arcs([(mouvement, 0.01)], (-1.0, -1.0), (21, 21), 0.1)
        gcode = (
            "M83\n;Z:.2\nG0 X-1 Y-1 Z.2 F600\n;TYPE:Outer wall\n"
            "G1 X-0.5 E1\nG0 X0.037 Y0.017\n;WIDTH:0.01\n"
            "G3 X0.037 Y0.017 I-0.014 J0 E0.1"
        )
        resultat = PC.controler(gcode, arcs_planaires=True)
        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIn("invisible", resultat["detail"])

    def test_limites_opt_in_interpreteur_et_nombre_arcs(self):
        """L'opt-in borne diagnostics et le nombre de dictionnaires d'arc retenus."""
        gcode = (
            "M83\n;Z:.2\nG0 X10 Y0 Z.2 F600\n;TYPE:Outer wall\n"
            "G3 X10 Y0 I-10 J0 E1\nG3 X10 Y0 I-10 J0 E1"
        )
        with patch.object(PC, "Interpreter", wraps=Interpreter) as constructeur:
            PC.controler(gcode, arcs_planaires=True)
        constructeur.assert_called_with(error_limit=128)
        with patch.object(PC, "MAX_ARCS_PLANAIRES", 1, create=True):
            resultat = PC.controler(gcode, arcs_planaires=True)
        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIn("budget d'arcs", resultat["detail"])

    def test_majoritaire_horaire_apres_g92_et_retraction_partielle(self):
        """Le grand balayage horaire suit le repère G92 et la fraction déposée."""
        majeur = mouvement_arc(
            "M83\nG1 X10 Y0 F600\n;TYPE:Outer wall\nG2 X0 Y10 R-10 E10"
        )
        self.assertLess(majeur["arc_g17"]["balayage_signe_rad"], -math.pi)
        origine_large = (-25.0, -25.0)
        masque = PC.masquer_arcs([(majeur, 0.42)], origine_large, (501, 501), 0.1)
        self.assertTrue(self._point_milieu_cercle(majeur, masque, origine_large))
        majeur_ccw = mouvement_arc(
            "M83\nG1 X10 Y0 F600\n;TYPE:Outer wall\nG3 X0 Y10 R-10 E10"
        )
        self.assertGreater(majeur_ccw["arc_g17"]["balayage_signe_rad"], math.pi)
        masque_ccw = PC.masquer_arcs([(majeur_ccw, 0.42)], origine_large, (501, 501), 0.1)
        self.assertTrue(self._point_milieu_cercle(majeur_ccw, masque_ccw, origine_large))

        g92 = mouvement_arc(
            "M83\nG1 X20 Y0 F600\nG92 X10 Y0\n;TYPE:Outer wall\nG3 X0 Y10 I-10 J0 E10"
        )
        self.assertEqual(g92["arc_g17"]["centre_xy_mm"], (10.0, 0.0))
        g92_z = (
            "M83\n;Z:.2\nG0 X10 Y0 Z.2 F600\nG92 Z0\n;TYPE:Outer wall\n"
            "G3 X0 Y10 I-10 J0 E10"
        )
        self.assertIn(PC.controler(g92_z, arcs_planaires=True)["statut"], {"PASS", "WARN"})

        retracte = mouvement_arc(
            "M83\nG1 X10 Y0 F600\nG1 E-0.5\n;TYPE:Outer wall\nG3 X0 Y10 I-10 J0 E1"
        )
        self.assertAlmostEqual(retracte["arc_g17"]["debut_depot_fraction"], 0.5)
        masque_retracte = PC.masquer_arcs([(retracte, 0.42)], (-12.0, -12.0), (241, 241), 0.1)
        self.assertFalse(masque_retracte[214, 154])  # angle ~20°, avant le début du dépôt
        self.assertTrue(masque_retracte[191, 191])  # angle ~45°, après rétraction

    def _point_milieu_cercle(self, mouvement, masque, origine):
        """Vérifie la cellule la plus proche du milieu angulaire exact."""
        arc = mouvement["arc_g17"]
        cx, cy = arc["centre_xy_mm"]
        angle = arc["angle_debut_rad"] + arc["balayage_signe_rad"] / 2
        rayon = arc["rayon_mm"]
        point = (cx + rayon * math.cos(angle), cy + rayon * math.sin(angle))
        indice = tuple(round((coord - base) / 0.1) for coord, base in zip(point, origine))
        return bool(masque[indice])

    def test_option_absente_garde_le_refus_historique_et_opt_in_est_note(self):
        """Le refus historique subsiste sans option et le rapport opt-in l'identifie."""
        gcode = "M83\n;Z:.2\nG0 Z.2 F600\n;TYPE:Outer wall\nG1 X10 Y0 E1\nG3 X0 Y10 I-10 J0 E10"
        self.assertEqual(PC.controler(gcode)["statut"], "NON VÉRIFIÉ")
        resultat = PC.controler(gcode, arcs_planaires=True)
        self.assertIn(resultat["statut"], {"PASS", "WARN"})
        self.assertEqual(resultat["arcs_planaires"]["nombre"], 1)
        self.assertEqual(resultat["arcs_planaires"]["grille"]["pas_mm"], 0.1)
        self.assertIn("aucune validation physique", resultat["arcs_planaires"]["portee"])
        self.assertEqual(resultat["arcs_planaires"]["tolerance_z_marqueur_mm"], 0.0005)
        self.assertEqual(resultat["arcs_planaires"]["tolerance_rayon_absolue_mm"], 1e-9)

    def test_cli_n_ecrit_le_bloc_arc_que_sur_demande(self):
        """Le rapport JSON n'ajoute son annotation arc que sur demande explicite."""
        gcode = "M83\n;Z:.2\nG0 Z.2 F600\n;TYPE:Outer wall\nG1 X10 Y0 E1\nG3 X0 Y10 I-10 J0 E10"
        with tempfile.TemporaryDirectory() as temporaire:
            source = Path(temporaire) / "source.gcode"
            source.write_text(gcode)
            historique = Path(temporaire) / "historique.json"
            PC.main([str(source), "--json", str(historique)])
            self.assertNotIn("arcs_planaires", json.loads(historique.read_text())["plaques"]["plaque 1"])
            qualifie = Path(temporaire) / "arcs.json"
            PC.main([str(source), "--json", str(qualifie), "--arcs-planaires"])
            self.assertIn("arcs_planaires", json.loads(qualifie.read_text())["plaques"]["plaque 1"])


if __name__ == "__main__":
    unittest.main()
