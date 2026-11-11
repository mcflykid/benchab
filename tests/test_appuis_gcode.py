from zipfile import ZipFile

import numpy as np
import pytest

from benchab.controles.appuis_gcode import analyser_fichier, evaluer_parcours, main, mesurer_appui
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
