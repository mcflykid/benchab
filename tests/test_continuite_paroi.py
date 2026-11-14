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


def test_g92_xyz_preserve_la_continuite_du_repere_spatial(tmp_path):
    gcode = ecrire_gcode(
        tmp_path / "decalage.gcode",
        "M83\n;TYPE:Outer wall\nG92 X0\nG1 X1 Y0 Z2 E1\n",
    )
    resultat = controler(gcode)
    assert resultat["statut"] == "PASS"
    assert resultat["couverture_complete"] is True
    assert resultat["couches"][0]["segments"] == 1


def test_arc_g17_constant_z_est_raccorde_avec_son_point_de_depot_exact(tmp_path):
    """La continuité compte l’arc analytique sans l’approcher par sa corde."""
    gcode = ecrire_gcode(
        tmp_path / "arc.gcode",
        "G90\nM83\nG0 X0 Y0 Z0.2 F600\n;Z:0.2\n;TYPE:Outer wall\n"
        "G1 X1 Y0 E1\nG3 X0 Y1 I-1 J0 E1\nG1 X-1 Y1 E1\n",
    )
    resultat = controler(gcode)
    assert resultat["statut"] == "PASS"
    assert resultat["couverture_complete"] is True
    assert resultat["couches"][0]["segments"] == 3
    assert resultat["couches"][0]["morceaux"] == 1


@pytest.mark.parametrize(
    "commande",
    [
        "G3 X0 Y1 R1 E1",  # quart de tour CCW mineur
        "G2 X0 Y1 R1 E1",  # quart de tour CW mineur
        "G2 X0 Y1 R-1 E1",  # trois quarts CW majeur
        "G3 X0 Y1 R-1 E1",  # trois quarts CCW majeur
        "G3 I-1 J0 E1",  # cercle complet
    ],
)
def test_g2_g3_majeur_mineur_et_cercle_complet_sont_comptes(tmp_path, commande):
    gcode = ecrire_gcode(
        tmp_path / "formes.gcode",
        "G90\nM83\nG0 X1 Y0 Z0.2 F600\n;Z:0.2\n;TYPE:Outer wall\n" + commande + "\n",
    )
    resultat = controler(gcode)
    assert resultat["statut"] == "PASS"
    assert resultat["couverture_complete"] is True
    assert resultat["couches"][0]["segments"] == 1
    assert resultat["couches"][0]["morceaux"] == 1


def test_helix_g17_et_autre_plan_reste_non_verifie(tmp_path):
    for nom, mouvement in (
        ("helix", "G3 X0 Y1 Z0.3 I-1 J0 E1"),
        ("autre-plan", "G18\nG2 X1 Z0.2 I0 K-1 E1"),
    ):
        gcode = ecrire_gcode(
            tmp_path / f"{nom}.gcode",
            "G90\nM83\nG0 X1 Y0 Z0.2 F600\n;Z:0.2\n;TYPE:Outer wall\n" + mouvement + "\n",
        )
        resultat = controler(gcode)
        assert resultat["statut"] == "NON VÉRIFIÉ"
        assert resultat["couverture_complete"] is False


def test_g92_xyz_conserve_arc_raccorde_dans_le_repere_spatial(tmp_path):
    gcode = ecrire_gcode(
        tmp_path / "arc-g92.gcode",
        "G90\nM83\nG0 X1 Y0 Z0.2 F600\nG92 X0 Y0\n;Z:0.2\n;TYPE:Outer wall\n"
        "G3 X-1 Y1 I-1 J0 E1\n",
    )
    resultat = controler(gcode)
    assert resultat["statut"] == "PASS"
    assert resultat["couverture_complete"] is True
    assert resultat["couches"][0]["segments"] == 1


def test_retraction_partielle_depose_exactement_la_fin_de_l_arc(tmp_path):
    gcode = ecrire_gcode(
        tmp_path / "arc-retract.gcode",
        "G90\nM83\nG0 X1 Y0 Z0.2 F600\n;Z:0.2\n;TYPE:Outer wall\n"
        "G1 E-0.5\nG3 X0 Y1 I-1 J0 E1\n",
    )
    resultat = controler(gcode)
    assert resultat["statut"] == "PASS"
    assert resultat["couverture_complete"] is True
    assert resultat["couches"][0]["segments"] == 1
    assert resultat["couches"][0]["morceaux"] == 1


@pytest.mark.parametrize(("ecart_mm", "morceaux_attendus"), [(0.019, 1), (0.021, 2)])
def test_arc_garde_le_seuil_de_raccord_de_002_mm(ecart_mm, morceaux_attendus, monkeypatch):
    """Le départ exact de l'arc utilise le seuil historique de raccord en mm."""
    from benchab.gcode.continuite import _Etat

    etat = _Etat(None, 0.3)
    etat.role = "outer wall"
    etat.z_meta = 0.2
    etat.derniere_fin = (0.0, 0.0, 0.2)
    etat.stats[0.2] = {
        "morceaux": 1,
        "segments": 1,
        "microsegments": 0,
        "accelerations": {},
    }
    mouvement = {
        "op": "G3",
        "start": (ecart_mm, 0.0, 0.2),
        "end": (ecart_mm - 1.0, 1.0, 0.2),
        "spatial_end": (ecart_mm - 1.0, 1.0, 0.2),
        "deposition_start": None,
        "length": 1.5707963267948966,
        "deposited_length": 1.5707963267948966,
        "arc_g17": {
            "centre_xy_mm": (ecart_mm - 1.0, 0.0),
            "rayon_mm": 1.0,
            "angle_debut_rad": 0.0,
            "balayage_signe_rad": 1.5707963267948966,
            "debut_depot_fraction": 0.0,
        },
    }
    monkeypatch.setattr(etat.lecteur, "feed_line", lambda ligne: mouvement)
    monkeypatch.setattr(etat.lecteur, "last_event", {"parameters": {"X": 1.0, "Y": 1.0, "E": 1.0}})

    etat.ligne("G3")

    assert etat.stats[0.2]["morceaux"] == morceaux_attendus


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
        "avertissements natifs du slicer",
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


def test_3mf_continuite_selectionne_nom_exact_et_refuse_les_alias(tmp_path):
    """Le numéro choisi désigne un seul membre, quel que soit le zéro initial."""
    gcode = "G90\nM83\n;TYPE:Outer wall\nG0 Z0.2\nG1 X10 E0.5 F600\n"
    canonique, rembourre, ambigu = (tmp_path / f"{nom}.3mf" for nom in ("canonique", "rembourre", "ambigu"))
    for fichier, membres in (
        (canonique, [("plate_1", gcode)]),
        (rembourre, [("plate_01", gcode)]),
        (ambigu, [("plate_1", gcode), ("plate_01", "G90\n")]),
    ):
        with zipfile.ZipFile(fichier, "w") as sortie:
            for nom, contenu in membres:
                sortie.writestr(f"Metadata/{nom}.gcode", contenu)
    attendu = controler(canonique, plaque=1)
    assert attendu["statut"] == "PASS"
    assert controler(rembourre, plaque=1) == attendu
    for plaque in (None, 1):
        refus = controler(ambigu, plaque=plaque)
        assert refus["statut"] == "NON VÉRIFIÉ"
        assert "ambigu" in refus["avertissements"][0].lower()
        assert "couches_analysees" not in refus


def test_continuite_vase_groupe_les_increments_z_par_tour_declare(tmp_path):
    """Les mouvements Z d’une même couche vase partagent le même dénominateur."""
    source = tmp_path / "vase.3mf"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", '{"spiral_mode":"1"}')
        archive.writestr(
            "Metadata/plate_1.gcode",
            "G90\nM83\n;LAYER_CHANGE\n;Z:0.4\n;TYPE:Outer wall\nG1 Z0.2 F600\n"
            "G1 X1 Z0.25 E0.1\nG1 X1.1 Z0.3 E0.01\nG1 X2 Z0.4 E0.1\n",
        )
    rapport = controler(source)
    (tour,) = rapport["couches"]
    assert tour["segments"] == 3
    assert tour["fraction_microsegments"] == pytest.approx(1 / 3, abs=1e-6)
    assert tour["morceaux"] == 1


def test_erreur_isolee_ne_fragmente_pas_les_segments_suivants(tmp_path):
    """Une ligne refusée rompt la continuité une fois, pas pour toute la suite du fichier."""
    contenu = (
        "G90\nM83\n;Z:0.2\n;TYPE:Outer wall\nG0 X0 Y0 Z0.2\nG1 X10 E0.5 F600\n"
        "G1X?E1\nG1 X20 E0.5\nG1 X30 E0.5\nG1 X40 E0.5\n"
    )
    rapport = controler(ecrire_gcode(tmp_path / "erreur.gcode", contenu))
    assert rapport["statut"] == "NON VÉRIFIÉ"
    (couche,) = rapport["couches"]
    assert couche["segments"] == 4
    # Le premier segment, puis la reprise après la ligne refusée : deux morceaux.
    assert couche["morceaux"] == 2


def test_continuite_vase_gcode_brut_lit_le_bloc_de_configuration(tmp_path):
    """Un G-code brut en spirale est regroupé comme le même tranchage dans un 3MF."""
    corps = (
        "G90\nM83\n;LAYER_CHANGE\n;Z:0.4\n;TYPE:Outer wall\nG1 Z0.2 F600\n"
        "G1 X1 Z0.25 E0.1\nG1 X1.1 Z0.3 E0.01\nG1 X2 Z0.4 E0.1\n"
    )
    vase = controler(ecrire_gcode(tmp_path / "vase.gcode", corps + "; CONFIG_BLOCK_START\n; spiral_mode = 1\n"))
    assert vase["mode_vase"] is True
    (tour,) = vase["couches"]
    assert tour["segments"] == 3
    assert tour["morceaux"] == 1
    normal = controler(ecrire_gcode(tmp_path / "normal.gcode", corps + "; spiral_mode = 0\n"))
    assert normal["mode_vase"] is False
    assert normal["couches_analysees"] == 3
    contradictoire = controler(
        ecrire_gcode(tmp_path / "contradictoire.gcode", corps + "; spiral_mode = 0\n; spiral_mode = 1\n")
    )
    assert contradictoire["statut"] == "NON VÉRIFIÉ"
