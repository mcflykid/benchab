"""Témoins synthétiques publics pour le contrôle des parois."""

import json
import zipfile

import pytest

from benchab.controles.continuite_paroi import controler
from benchab.controles.envoi import controler as controler_envoi


def ecrire_gcode(chemin, contenu):
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def test_positions_extrusion_absolue_conservees_par_outil(tmp_path):
    contenu = (
        "G90\nM82\n;Z:20\n;TYPE:Outer wall\nM204 S2000\n"
        "T0\nG92 E100\nG0 X0 Y0 Z20\nG1 X10 E101 F600\n"
        "T1\nG92 E200\nG1 X20 E201\nT0\nG92 E101\nG1 X30 E102\n"
    )
    resultat = controler(ecrire_gcode(tmp_path / "outils.gcode", contenu))
    assert resultat["statut"] == "PASS"
    assert resultat["couches"][0]["segments"] == 3
    assert resultat["couches"][0]["morceaux"] == 3


def test_format_compact_valide_est_mesure_par_le_lecteur_commun(tmp_path):
    """La syntaxe compacte reconnue fournit deux segments ; une syntaxe invalide reste NON VÉRIFIÉE."""
    contenu = (
        "G90\nM83\n;Z:20\n;TYPE:Outer wall\nM204 S2000\n"
        "G0 X0 Y0 Z20\nG1 X10 E1 F600\nG1X20E1\n"
    )
    resultat = controler(ecrire_gcode(tmp_path / "compact.gcode", contenu))
    assert resultat["statut"] == "PASS"
    assert resultat["couches"][0]["segments"] == 2
    assert controler(ecrire_gcode(tmp_path / "invalide.gcode", contenu + "G1X?E1\n"))["statut"] == "NON VÉRIFIÉ"


def test_fragments_et_microsegments_depassent_seuils_absolus(tmp_path):
    lignes = ["M83", "G90", "; outer_wall_acceleration = 5000", "M204 S5000", ";TYPE:Outer wall", "G1 Z20"]
    for index in range(11):
        x = index * 2
        lignes.extend([f"G0 X{x} Y0", f"G1 X{x + 0.2} Y0 E0.1", f"G0 X{x + 1} Y0"])
    resultat = controler(ecrire_gcode(tmp_path / "WARN.gcode", "\n".join(lignes)))
    assert resultat["statut"] == "WARN"
    assert resultat["max_morceaux_par_couche"] == 11
    assert resultat["max_fraction_microsegments"] == 1.0
    assert resultat["acceleration_exterieure_configuree_mm_s2"] == 5000
    assert resultat["acceleration_m204_max_observee_paroi_mm_s2"] == 5000
    assert "non normes fabricant" in resultat["heuristiques"]


def test_cache_3mf_rapport_identique_et_options_distinctes(tmp_path, monkeypatch):
    from benchab.gcode import continuite, lecture

    lecture.vider_cache_lecture()
    fichier = tmp_path / "fixture.3mf"
    with zipfile.ZipFile(fichier, "w") as archive:
        archive.writestr(
            "Metadata/plate_1.gcode",
            "\n".join(
                [
                    "M83",
                    "M204 S2000",
                    ";TYPE:Outer wall",
                    "G1 X0 Y0 Z20 E1",
                    "G1 X1 Y0 E1",
                ]
            ),
        )
    initial = controler(fichier)
    with monkeypatch.context() as changement:

        def refus_relecture(*arguments, **options):
            raise AssertionError("Le lecteur ne doit pas être relancé sur une empreinte déjà analysée")

        changement.setattr(lecture, "iter_lignes_gcode_3mf", refus_relecture)
        assert controler(fichier) == initial
    initial["couches"].clear()
    assert controler(fichier)["couches"]
    assert controler(fichier, intervalle_z_mm=(30, 45))["statut"] == "NON VÉRIFIÉ"
    assert continuite.analyser(fichier)["statut"] == "PASS"


def test_continuite_retraction_g92_et_mode_absolu(tmp_path):
    contenu = "\n".join(
        [
            "G90",
            "M82",
            "G92 E0",
            "; outer_wall_acceleration = 2000",
            "M204 S2000",
            ";TYPE:Outer wall",
            "G1 X0 Y0 Z20 E0.01",
            "G1 F1200",
            "G1 X1 Y0 E0.02",
            "G1 E-0.08",
            "G1 X2.1 Y0 E0.03",
            "G1 X3.1 Y0 E0.04",
        ]
    )
    resultat = controler(ecrire_gcode(tmp_path / "PASS.gcode", contenu))
    assert resultat["statut"] == "PASS"
    assert resultat["max_morceaux_par_couche"] == 2
    assert resultat["max_fraction_microsegments"] == 0.25


def test_plaque_unique_3mf_et_absence_gcode(tmp_path):
    archive = tmp_path / "temoin.3mf"
    with zipfile.ZipFile(archive, "w") as sortie:
        sortie.writestr("Metadata/plate_1.gcode", "M83\n; outer_wall_acceleration = 2000\n")
    assert controler(archive)["statut"] == "NON VÉRIFIÉ"
    sans_gcode = tmp_path / "sans-gcode.3mf"
    with zipfile.ZipFile(sans_gcode, "w"):
        pass
    assert controler(sans_gcode)["statut"] == "NON VÉRIFIÉ"
    assert controler(tmp_path / "absent.gcode")["statut"] == "NON VÉRIFIÉ"


def test_3mf_multi_plaques_sans_selection_est_explicitement_non_verifie(tmp_path):
    archive = tmp_path / "multi.3mf"
    with zipfile.ZipFile(archive, "w") as sortie:
        sortie.writestr("Metadata/plate_1.gcode", "M83\n")
        sortie.writestr("Metadata/plate_2.gcode", "M83\n")
    resultat = controler(archive)
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert "plusieurs plaques" in resultat["avertissements"][0]


def test_analyse_par_defaut_toutes_hauteurs_et_intervalle_configurable(tmp_path):
    gcode = ecrire_gcode(
        tmp_path / "hauteurs.gcode",
        "\n".join(
            [
                "M83",
                ";TYPE:Outer wall",
                "G1 X0 Y0 Z2 E1",
                "G1 X1 Y0 E2",
                "G1 X2 Y0 Z60 E3",
                "G1 X3 Y0 E4",
            ]
        ),
    )
    toutes = controler(gcode)
    assert toutes["statut"] == "PASS"
    assert toutes["intervalle_z_mm"] is None
    assert [couche["z_mm"] for couche in toutes["couches"]] == [2.0, 60.0]
    fenetre = controler(gcode, intervalle_z_mm=(20, 45))
    assert fenetre["statut"] == "NON VÉRIFIÉ"
    assert fenetre["intervalle_z_mm"] == [20.0, 45.0]


def test_reference_exige_relation_explicite_et_ne_deduit_pas_par_nom(tmp_path):
    gcode = ecrire_gcode(
        tmp_path / "terre.gcode",
        "M83\n;TYPE:Outer wall\nG1 X0 Y0 Z2 E1\nG1 X1 Y0 E2\n",
    )
    resultat = controler(
        gcode,
        reference={"identifiant": "meme-modele-que-terre", "relation": "seulement_meme_machine_filament"},
    )
    assert resultat["reference"]["relation"] == "seulement_meme_machine_filament"
    assert resultat["reference"]["comparaison_geometrique_autorisee"] is False
    inconnue = controler(gcode, reference={"relation": "inconnue"})
    assert inconnue["reference"]["comparaison_geometrique_autorisee"] is False
    with pytest.raises(ValueError, match="relation reconnue"):
        controler(gcode, reference={"identifiant": "terre"})


def test_reference_reellement_analysee_et_ecarts_geometriques_seulement_meme_source(tmp_path):
    gcode = ecrire_gcode(
        tmp_path / "piece.gcode",
        "M83\n; outer_wall_acceleration = 2000\n;TYPE:Outer wall\nG1 Z20\nG1 X1 E1\n",
    )
    reference = {"fichier": gcode, "identifiant": "fixture", "relation": "meme_source_modele"}
    meme = controler(gcode, reference=reference)
    assert meme["reference"]["statut_analyse"] == "PASS"
    assert meme["reference"]["ecarts"]["max_morceaux_par_couche"] == 0
    reference["relation"] = "seulement_meme_machine_filament"
    differente = controler(gcode, reference=reference)
    assert "ecarts" not in differente["reference"]
    assert differente["reference"]["mesures"]["max_morceaux_par_couche"] == 1
    assert "sans écart géométrique" in differente["reference"]["limite"]


@pytest.mark.parametrize(
    "options",
    [
        {"intervalle_z_mm": (45, 20)},
        {"intervalle_z_mm": (20, float("nan"))},
        {"seuils_absolus": {"morceaux_par_couche": 0}},
        {"seuils_absolus": {"fraction_microsegments": 1.1}},
        {"longueur_micro_mm": 0},
    ],
)
def test_options_invalides_refusees(tmp_path, options):
    gcode = ecrire_gcode(tmp_path / "options.gcode", "M83\n")
    with pytest.raises(ValueError):
        controler(gcode, **options)


def test_g92_xyz_marque_la_couverture_incomplete(tmp_path):
    gcode = ecrire_gcode(
        tmp_path / "decalage.gcode",
        "M83\n;TYPE:Outer wall\nG92 X0\nG1 X1 Y0 Z2 E1\n",
    )
    resultat = controler(gcode)
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert resultat["couverture_complete"] is False


def test_envoi_preserve_statut_global_non_verifie_et_fail_dominant(tmp_path):
    """Sans trajectoires ni limites initiales, attend NON VÉRIFIÉ ; aucun contrôle manquant assimilé à PASS."""
    archive = tmp_path / "preparation.3mf"
    with zipfile.ZipFile(archive, "w") as sortie:
        sortie.writestr("Metadata/project_settings.config", json.dumps({"filament_id": ["stable"]}))
        sortie.writestr("Metadata/plate_1.gcode", "M83\n")
    resultat = controler_envoi(archive)
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert {controle["controle"] for controle in resultat["resultats"]} == {
        "identifiants de filaments définitifs",
        "continuité paroi extérieure",
        "commandes machine au démarrage",
    }
    assert resultat["resultats"][1]["statut"] == "NON VÉRIFIÉ"
    assert resultat["resultats"][2]["statut"] == "NON VÉRIFIÉ"
    assert resultat["validation_physique"] is False


def test_fail_legacy_dominant_dans_agregation(tmp_path, monkeypatch):
    archive = tmp_path / "preparation.3mf"
    with zipfile.ZipFile(archive, "w") as sortie:
        sortie.writestr("Metadata/project_settings.config", json.dumps({}))
        sortie.writestr("Metadata/plate_1.gcode", "M83\n")
    monkeypatch.setattr(
        "benchab.controles.envoi.identifiants",
        lambda _: {"controle": "témoin", "statut": "FAIL"},
    )
    assert controler_envoi(archive)["statut"] == "FAIL"
