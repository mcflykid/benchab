"""Mesures analytiques des arcs XY et continuité après redéfinition G92."""

import math

import pytest

from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode import debit
from benchab.gcode.continuite import controler


@pytest.mark.parametrize(("commande", "attendu"), [("G3", math.pi / 2), ("G2", 3 * math.pi / 2)])
def test_arc_xy_est_mesure_sans_tessellation(commande, attendu):
    """CW/CCW avec signe R conserve la longueur nominale analytique."""
    it = Interpreter()
    for ligne in ("G90", "M83", "G1 X1 Y0 Z2 F600", ";TYPE:Outer wall"):
        it.feed_line(ligne)
    mouvement = it.feed_line(f"{commande} X0 Y1 R{'-1' if commande == 'G2' else '1'} E1")
    assert mouvement["length"] == pytest.approx(attendu)
    assert mouvement["duration_s"] == pytest.approx(attendu / 10)
    assert mouvement["deposited_length"] == pytest.approx(attendu)
    mesure = debit.mesurer(mouvement, 1.75)
    assert mesure["duree_depot_s"] == pytest.approx(attendu / 10)
    assert mesure["debit_mm3_s"] == pytest.approx(math.pi * (1.75 / 2) ** 2 * 10 / attendu)


def test_arc_cercle_complet_et_helice_ij():
    """IJ relatif prend en charge le cercle complet et la longueur hélicoïdale exacte."""
    it = Interpreter()
    for ligne in ("G90", "M83", "G1 X1 Y0 Z0 F600"):
        it.feed_line(ligne)
    cercle = it.feed_line("G3 I-1 J0 E1")
    assert cercle["length"] == pytest.approx(2 * math.pi)
    helice = it.feed_line("G3 X1 Y0 Z2 I-1 J0 E1")
    assert helice["length"] == pytest.approx(math.hypot(2 * math.pi, 2))


def test_arc_diametral_avec_rayon_signe():
    """Le cas de corde égale au diamètre reste un demi-cercle valide."""
    it = Interpreter()
    for ligne in ("G90", "M83", "G1 X1 Y0 F600"):
        it.feed_line(ligne)
    mouvement = it.feed_line("G3 X-1 Y0 R1")
    assert mouvement["length"] == pytest.approx(math.pi)


def test_retraction_arc_est_mesuree_zero_depot():
    """La dette de rétraction reste commune aux arcs comme aux lignes."""
    it = Interpreter()
    for ligne in ("G90", "M83", "G1 X1 Y0 F600", "G1 E-0.5"):
        it.feed_line(ligne)
    mouvement = it.feed_line("G3 X0 Y1 I-1 J0 E0.25")
    assert mouvement["length"] == pytest.approx(math.pi / 2)
    assert mouvement["deposited"] == 0
    assert mouvement["deposited_length"] == 0


def test_arc_non_pris_en_charge_ne_change_aucun_etat():
    """Un autre plan ou syntaxe invalide est refusé atomiquement."""
    it = Interpreter()
    it.feed_line("G90")
    it.feed_line("G1 X1 Y0 Z2 F600")
    avant = (it.xyz, dict(it.e), it.feed)
    assert it.feed_line("G18 G2 X2 Z3 I1 K0") is None
    assert (it.xyz, dict(it.e), it.feed) == avant
    assert it.errors


def test_syntaxe_arc_refusee_ne_retourne_aucune_mesure():
    """Un mode de centre absolu repéré invalide l'arc sans produire de mesure."""
    it = Interpreter()
    it.feed_line("G90.1")
    assert it.feed_line("G3 X1 Y1 I0 J1 F600") is None
    assert any("centres d'arc absolus" in message for message in it.errors)


@pytest.mark.parametrize("plan", ["G18", "G19"])
def test_plan_modal_non_xy_refuse_arc_sans_mutation_puis_retour_g17(plan):
    """Un plan persiste entre lignes ; le retour G17 réactive seulement les arcs XY."""
    it = Interpreter()
    for ligne in ("G90", "M83", "G1 X1 Y0 Z2 F600", "G1 E-0.5", plan):
        it.feed_line(ligne)
    avant = (it.xyz, it.spatial_xyz, dict(it.e), dict(it.retracted), it.feed)
    assert it.feed_line("G3 X0 Y1 R1 E1 F1200") is None
    assert (it.xyz, it.spatial_xyz, dict(it.e), dict(it.retracted), it.feed) == avant
    assert any("plan" in erreur for erreur in it.errors)
    erreurs = list(it.errors)
    it.feed_line("G17")
    m = it.feed_line("G3 X0 Y1 R1 E1")
    assert m["length"] == pytest.approx(math.pi / 2)
    assert it.errors == erreurs


def test_depart_depot_arc_non_qualifie_n_est_pas_une_corde():
    """La position de dépôt courbe n'est pas fabriquée par interpolation linéaire."""
    it = Interpreter()
    for ligne in ("G90", "M83", "G1 X1 Y0 F600", "G1 E-0.5"):
        it.feed_line(ligne)
    m = it.feed_line("G3 X0 Y1 I-1 E1")
    assert m["deposited_length"] == pytest.approx(math.pi / 4)
    assert m["deposition_start"] is None


def test_arc_rayon_fini_ne_masque_pas_debordement_des_carres():
    """L'homothétie numérique conserve l'angle ; aucune taille physique n'est validée."""
    it = Interpreter()
    rayon = format(1e200, ".0f")
    for ligne in ("G90", "M83", f"G1 X{rayon} Y0 F600"):
        it.feed_line(ligne)
    m = it.feed_line(f"G3 X0 Y{rayon} R{rayon} E1")
    assert m["length"] == pytest.approx(math.pi / 2 * 1e200)


def test_repere_spatial_non_fini_refuse_avant_mutation():
    """Une translation G92 hors capacité flottante invalide le mouvement, pas l'état courant."""
    it = Interpreter()
    valeur = format(1e308, ".0f")
    for ligne in ("G90", "M83", f"G1 X{valeur} F600", f"G92 X-{valeur}"):
        it.feed_line(ligne)
    avant = (it.xyz, it.spatial_xyz, dict(it.e), it.feed)
    assert it.feed_line("G1 X0 E1 F1200") is None
    assert (it.xyz, it.spatial_xyz, dict(it.e), it.feed) == avant


def test_coordonnees_sans_decalage_restent_exactes():
    """Sans G92 XYZ, aucune soustraction/réaddition n'altère la coordonnée absolue lue."""
    it = Interpreter()
    it.feed_line("G1 X72.4 F600")
    m = it.feed_line("G1 X11.264")
    assert m["end"][0] == 11.264


def test_g92_xyz_preserve_trajectoire_et_couches():
    """La commande redéfinit les coordonnées visibles sans créer un saut spatial."""
    lignes = [
        "G90", "M83", ";TYPE:Outer wall", "G1 X10 Y0 Z2 F600 E1",
        "G92 X0 Y0 Z0", "G1 X1 Y0 Z0 E1",
    ]
    it = Interpreter()
    mouvements = [m for ligne in lignes if (m := it.feed_line(ligne))]
    assert mouvements[-1]["spatial_start"] == pytest.approx((10, 0, 2))
    assert mouvements[-1]["spatial_end"] == pytest.approx((11, 0, 2))
    assert mouvements[-1]["length"] == pytest.approx(1)
    resultat = controler_lignes(lignes)
    assert resultat["statut"] == "PASS"
    assert resultat["couches"][0]["segments"] == 2


def controler_lignes(lignes):
    """Envoie un G-code temporaire au contrôle de continuité."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as dossier:
        fichier = Path(dossier) / "mesure.gcode"
        fichier.write_text("\n".join(lignes), encoding="utf-8")
        return controler(fichier)
