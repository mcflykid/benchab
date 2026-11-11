import json
import math
from zipfile import ZipFile

import numpy as np
import pytest

from benchab.controles.appuis_gcode import (
    _echantillonner_arc,
    analyser_fichier,
    analyser_lignes,
    evaluer_parcours,
    main,
    mesurer_appui,
)
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import membres_gcode_3mf, vider_cache_lecture


def test_mesure_appui_reunit_plusieurs_couches_et_conserve_seuils():
    cibles = np.array([[0.0, 0.0, 0.21], [0.15, 0.0, 0.21], [0.30, 0.0, 0.21]])
    couche_precedente = np.array([[0.0, 0.0, 0.21], [0.30, 0.0, 0.21]])
    couche_anterieure = np.array([[0.15, 0.0, 0.21]])
    resultat = mesurer_appui(
        cibles,
        [couche_precedente, couche_anterieure],
        recouvrement_min_mm=0.05,
        pas_mm=0.15,
    )
    assert resultat["points"] == 3
    assert resultat["points_avec_appui_pct"] == 100.0
    assert resultat["recouvrement_min_echantillonne_mm"] == pytest.approx(0.42)
    assert resultat["longueur_max_parcours_sans_appui_mm"] == 0.0
    assert "ne prédit" in resultat["limite"]


def test_longueur_non_appuyee_coupe_les_discontinuites_de_parcours():
    supports = np.array([[0.0, 0.0, 0.05], [0.15, 0.0, 0.05]])
    cibles = np.array([[0.0, 0.0, 0.05], [0.15, 0.0, 0.05], [0.30, 0.0, 0.05], [1.0, 0.0, 0.05]])
    resultat = mesurer_appui(cibles, supports, recouvrement_min_mm=0.05, pas_mm=0.15)
    assert resultat["points_avec_appui_pct"] == 50.0
    assert resultat["longueur_max_parcours_sans_appui_mm"] == pytest.approx(0.15)


def test_evaluation_parcours_parametre_portee_et_ancrage_des_extremites():
    points = np.array([[0.0, 0.0, 0.05], [0.15, 0.0, 0.05], [0.30, 0.0, 0.05]])
    supports = np.array([[0.0, 0.0, 0.05], [0.30, 0.0, 0.05]])
    resultat = evaluer_parcours(
        points,
        supports,
        longueur_libre_max_mm=0.2,
        recouvrement_min_mm=0.0,
        pas_mm=0.15,
    )
    assert resultat["deux_extremites_ancrees"]
    assert resultat["ancre_nominale"]
    assert resultat["portee_libre_max_mm"] == pytest.approx(0.15)


def test_evaluation_parcours_ne_relie_pas_une_couche_si_une_autre_est_eligible():
    points = np.array([[0.0, 0.0, 0.1], [0.15, 0.0, 0.1], [0.30, 0.0, 0.1]])
    immediats = np.array([[0.0, 0.0, 0.1]])
    precedents = np.array([[0.30, 0.0, 0.1]])
    resultat = evaluer_parcours(points, [immediats, precedents], recouvrement_min_mm=0.0, pas_mm=0.15)
    assert resultat["ancre_nominale"]
    assert resultat["points_avec_appui_pct"] == 100.0


@pytest.mark.parametrize(
    "cibles,supports,options",
    [
        ([], [[0, 0, 1]], {}),
        ([[0, 0, 0]], [[0, 0, 1]], {}),
        ([[0, 0, 1]], [], {}),
        ([[0, 0, 1]], [[0, 0, 1]], {"pas_mm": 0}),
        ([[0, 0, 1]], [[0, 0, 1]], {"voisins": 0}),
    ],
)
def test_mesure_appui_refuse_entrees_ou_parametres_invalides(cibles, supports, options):
    with pytest.raises(ValueError):
        mesurer_appui(cibles, supports, **options)


def _ecrire_fixture_gcode_3mf(path):
    gcode = """M83
;LAYER_CHANGE
;Z:0.2
;TYPE:Support
;WIDTH:0.4
G1 X0 Y0 Z0.2 F1200
G1 X1 Y0 E0.2
;LAYER_CHANGE
;Z:0.4
;TYPE:Inner wall
;WIDTH:0.4
G1 X0 Y0 Z0.4 F1200
G1 X1 Y0 E0.2
;LAYER_CHANGE
;Z:0.6
;TYPE:Inner wall
;WIDTH:0.4
G1 X0 Y1 Z0.6 F1200
G1 X1 Y1 E0.2
;TYPE:Overhang wall
G1 X0 Y1 Z0.6 F1200
G1 X1 Y1 E0.2
"""
    with ZipFile(path, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", gcode)


def test_analyseur_flux_3mf_agrege_couches_supportantes_et_porte_a_faux(tmp_path):
    source = tmp_path / "fixture.3mf"
    _ecrire_fixture_gcode_3mf(source)
    vider_cache_lecture()
    local = analyser_fichier(source, mode="local", pas_mm=0.15)
    assert local["statut"] == "PASS"
    assert local["plaques"][0]["couches"] == 3
    assert local["plaques"][0]["par_couche"][-1]["points_avec_appui_pct"] == 0.0
    assert local["plaques"][0]["sans_appui_nominal"] > 0

    anterieur = analyser_fichier(source, mode="anterieur", pas_mm=0.15)
    assert anterieur["plaques"][0]["par_couche"][-1]["points_avec_appui_pct"] > 0
    assert anterieur["sha256"] == local["sha256"]
    assert anterieur["plaques"] == local["plaques"] or anterieur["plaques"][0]["mode"] != local["plaques"][0]["mode"]


def test_cli_flux_refuse_rapport_hors_prive_et_ecrit_resume_prive(tmp_path, monkeypatch, capsys):
    source = tmp_path / "fixture.3mf"
    _ecrire_fixture_gcode_3mf(source)
    private = tmp_path / "prive"
    private.mkdir()
    monkeypatch.setenv("BENCHAB_PRIVE", str(private))
    output = private / "appuis.json"
    assert main([str(source), "--mode", "causal", "--pas-mm", "0.15", "--json", str(output)]) == 0
    assert output.is_file()
    assert '"mode": "causal"' in capsys.readouterr().out
    with pytest.raises(ValueError, match="doit rester"):
        main([str(source), "--json", str(tmp_path / "public.json")])


def test_appuis_refuse_deux_graphies_avant_selection(tmp_path, capsys):
    """Une sélection de plaque ne peut pas masquer l’index homonyme du 3MF."""
    import pytest

    source = tmp_path / "alias.3mf"
    with ZipFile(source, "w") as z:
        for numero in ("1", "01"):
            z.writestr(f"Metadata/plate_{numero}.gcode", "G90\nG1 X1 Y2 Z0.2\n")
    avant = source.read_bytes()
    with pytest.raises(ValueError, match="numéro de plaque dupliqué"):
        membres_gcode_3mf(source)
    with pytest.raises(ValueError, match="numéro de plaque dupliqué"):
        analyser_fichier(source, plaques={1})
    assert main([str(source), "--plaque", "1"]) == 2
    sortie = capsys.readouterr()
    assert "numéro de plaque dupliqué" in sortie.err
    assert not sortie.out and source.read_bytes() == avant


def test_sha_rapport_appuis_vient_de_l_archive_epinglee(tmp_path, monkeypatch):
    """Le rapport ne mélange jamais le membre épinglé et un SHA d’une autre lecture."""
    import hashlib

    from benchab.controles import appuis_gcode

    source = tmp_path / "preuve.3mf"
    _ecrire_fixture_gcode_3mf(source)
    avant = source.read_bytes()
    monkeypatch.setattr(appuis_gcode, "empreinte_3mf", lambda chemin: "a" * 64, raising=False)
    rapport = analyser_fichier(source, plaques={1})
    assert rapport["sha256"] == hashlib.sha256(avant).hexdigest()
    assert source.read_bytes() == avant


@pytest.mark.parametrize(
    ("commande", "depart", "attendu_sweep"),
    [
        ("G3 X0 Y10 I-10 J0 E1", (10.0, 0.0), math.pi / 2),
        ("G2 X0 Y10 I-10 J0 E1", (10.0, 0.0), -3 * math.pi / 2),
        ("G2 X0 Y10 R-10 E1", (10.0, 0.0), -3 * math.pi / 2),
        ("G3 X10 Y0 I-10 J0 E1", (10.0, 0.0), math.tau),
    ],
)
def test_echantillons_arcs_exactement_sur_la_courbe(commande, depart, attendu_sweep):
    """Les points sont évalués sur le G17 nominal, y compris les majeurs et cercles."""
    interpr = Interpreter()
    interpr.feed_line("M83")
    interpr.feed_line(";WIDTH:0.4")
    interpr.feed_line("G1 X10 Y0 Z0.2 F1200")
    mouvement = interpr.feed_line(commande)

    points, longueurs = _echantillonner_arc(mouvement, 0.25)

    assert len(points) > 2
    assert np.hypot(points[:, 0], points[:, 1]) == pytest.approx(np.full(len(points), 10.0), abs=1e-10)
    assert points[0, :2] == pytest.approx(depart, abs=1e-10)
    assert points[-1, :2] == pytest.approx(mouvement["end"][:2], abs=1e-10)
    assert mouvement["arc_g17"]["balayage_signe_rad"] == pytest.approx(attendu_sweep)
    assert sum(longueurs[1:]) == pytest.approx(mouvement["deposited_length"])


def test_option_arcs_planaires_preservee_par_defaut_et_refuse_une_helice():
    """Le mode historique refuse les arcs ; le mode opt-in qualifie le cercle plan."""
    gcode = [
        "M83", ";LAYER_CHANGE", ";Z:0.2", ";TYPE:Support", ";WIDTH:0.4",
        "G1 X10 Y0 Z0.2 F1200", "G3 X0 Y10 I-10 J0 E1",
        ";LAYER_CHANGE", ";Z:0.4", ";TYPE:Inner wall", ";WIDTH:0.4",
        "G1 X10 Y0 Z0.4", "G3 X0 Y10 I-10 J0 E1",
    ]

    historique = analyser_lignes(gcode)
    opt_in = analyser_lignes(gcode, arcs_planaires=True)
    helice = analyser_lignes(gcode[:-1] + ["G3 X0 Y10 Z0.8 I-10 J0 E1"], arcs_planaires=True)

    assert historique["statut"] == "NON VÉRIFIÉ"
    assert opt_in["statut"] == "PASS"
    assert opt_in["parametres"]["arcs_planaires"] is True
    assert helice["statut"] == "NON VÉRIFIÉ"


def test_arc_apres_g92_et_retraction_est_echantillonne_depuis_le_depot_reel():
    """Le début des échantillons suit la fin de la dette de rétraction après G92 E0."""
    interpr = Interpreter()
    for ligne in (
        "M83", ";WIDTH:0.4", "G1 X10 Y0 Z0.2 F1200", "G1 E-0.5", "G92 E0",
    ):
        interpr.feed_line(ligne)
    mouvement = interpr.feed_line("G3 X0 Y10 I-10 J0 E1")

    points, longueurs = _echantillonner_arc(mouvement, 0.25)

    assert mouvement["arc_g17"]["debut_depot_fraction"] == pytest.approx(0.5)
    assert points[0, :2] == pytest.approx((10 / math.sqrt(2), 10 / math.sqrt(2)))
    assert sum(longueurs) == pytest.approx(mouvement["deposited_length"])


def test_arc_arrondi_sans_largeur_ou_hors_budget_reste_non_verifie():
    """Un arc sans largeur ou trop dense ne produit jamais un parcours qualifié."""
    sans_largeur = Interpreter()
    sans_largeur.feed_line("M83")
    sans_largeur.feed_line("G1 X10 Y0 Z0.2 F1200")
    arc_sans_largeur = sans_largeur.feed_line("G3 X0 Y10 I-10 J0 E1")
    assert analyser_lignes([
        "M83", ";LAYER_CHANGE", ";Z:0.2", ";TYPE:Inner wall",
        "G1 X10 Y0 Z0.2 F1200", "G3 X0 Y10 I-10 J0 E1",
    ], arcs_planaires=True)["statut"] == "NON VÉRIFIÉ"
    with pytest.raises(ValueError, match="largeur"):
        _echantillonner_arc(arc_sans_largeur, 0.25)

    complet = Interpreter()
    complet.feed_line("M83")
    complet.feed_line(";WIDTH:0.4")
    complet.feed_line("G1 X10 Y0 Z0.2 F1200")
    mouvement = complet.feed_line("G3 X0 Y10 I-10 J0 E1")
    with pytest.raises(ValueError, match="budget"):
        _echantillonner_arc(mouvement, 1e-6)

    sans_largeur_lineaire = analyser_lignes(
        ["M83", ";LAYER_CHANGE", ";Z:0.2", ";TYPE:Inner wall", "G1 X0 Y0 Z0.2 F1200", "G1 X1 Y0 E1"],
        arcs_planaires=True,
    )
    assert sans_largeur_lineaire["statut"] == "NON VÉRIFIÉ"


def test_extremite_arrondie_refusee_meme_si_interpreteur_tolere_le_cercle():
    """Une discordance endpoint supérieure aux seuls arrondis binaires reste NON VÉRIFIÉE."""
    interpreteur = Interpreter()
    for ligne in ("M83", ";WIDTH:0.4", "G1 X10 Y0 Z0.2 F1200"):
        interpreteur.feed_line(ligne)
    arrondi = interpreteur.feed_line("G3 X0 Y10.0000001 I-10 J0 E1")

    with pytest.raises(ValueError, match="arrondies"):
        _echantillonner_arc(arrondi, 0.25)


def test_longueur_arc_compte_un_intervalle_entier_sans_marge_suffisante():
    """Des points tous touchés ne donnent pas un faux ancrage si l'intervalle reste indécis."""
    points = np.array([[0.0, 0.0, 0.05], [0.15, 0.0, 0.05], [0.30, 0.0, 0.05]])
    supports = np.array([[0.0, 0.0, 0.05], [0.15, 0.0, 0.05], [0.30, 0.0, 0.05]])
    distances = np.array([0.0, 0.15, 0.15])

    resultat = evaluer_parcours(
        points,
        supports,
        recouvrement_min_mm=0.05,
        longueur_libre_max_mm=0.2,
        pas_mm=0.15,
        distances_mm=distances,
    )

    assert resultat["points_avec_appui_pct"] == 100.0
    assert resultat["longueur_max_parcours_sans_appui_mm"] == pytest.approx(0.30)
    assert not resultat["ancre_nominale"]


def test_cli_expose_uniquement_le_mode_opt_in_arcs_planaires(tmp_path, capsys):
    """Le drapeau opt-in est accepté en local et expose ses limites nominales."""
    source = tmp_path / "arcs.gcode"
    source.write_text(
        "M83\n;LAYER_CHANGE\n;Z:0.2\n;TYPE:Support\n;WIDTH:0.4\n"
        "G1 X10 Y0 Z0.2 F1200\nG3 X0 Y10 I-10 J0 E1\n"
        ";LAYER_CHANGE\n;Z:0.4\n;TYPE:Inner wall\n;WIDTH:0.4\n"
        "G1 X10 Y0 Z0.4\nG3 X0 Y10 I-10 J0 E1\n",
        encoding="utf-8",
    )

    assert main([str(source), "--arcs-planaires"]) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["statut"] == "PASS"
    assert rapport["parametres"]["arcs_planaires"] is True
    assert rapport["parametres"]["budget_echantillons_arcs_max"] == 1_000_000
    assert "moitié de sa longueur" in rapport["limite"]


def test_resume_appuis_laisse_le_detail_complet_dans_un_rapport_neuf(tmp_path, capsys):
    """Le résumé garde les compteurs et le rapport contient toutes les lignes."""
    import json

    source = tmp_path / "fixture.3mf"
    _ecrire_fixture_gcode_3mf(source)
    prive = tmp_path / "atelier"
    prive.mkdir()
    cible = prive / "detail.json"
    assert main([str(source)], prive=prive) == 0
    historique = json.loads(capsys.readouterr().out)

    assert main([str(source), "--resume", "--json", str(cible)], prive=prive) == 0
    resume = json.loads(capsys.readouterr().out)
    assert resume["rapport"] == str(cible)
    assert resume["statut"] == historique["statut"] and resume["sha256"] == historique["sha256"]
    assert resume["parcours"] == sum(p["parcours"] for p in historique["plaques"])
    assert resume["plaques"] == len(historique["plaques"])
    assert "lignes" not in resume and "par_couche" not in resume
    assert json.loads(cible.read_bytes()) == historique

    assert main([str(source), "--resume"], prive=prive) == 2
    affiche = capsys.readouterr()
    assert not affiche.out and "--resume exige --json" in affiche.err
