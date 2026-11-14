"""Contrat #103 : contexte explicite, ordre conservé et aucune diffusion de valeurs."""

import copy

import pytest

from benchab import reglages


@pytest.mark.parametrize("identites", [["PLA A"], ["PLA A", "PLA B"], ["PLA A", "PLA A"]])
def test_correspondance_conserve_ordre_et_identites_sans_modifier_source(identites):
    """Les slots déclarés peuvent partager un profil ; leurs positions restent distinctes."""
    source = {"filament_settings_id": identites, "filament_colour": ["#000000"] * len(identites),
              "nozzle_temperature": ["210"] * len(identites)}
    avant = copy.deepcopy(source)
    contexte = reglages.contexte_filaments(source)
    assert contexte["nombre_filaments_declares"] == len(identites)
    assert contexte["correspondances_filaments"]["nozzle_temperature"] == identites
    contexte["correspondances_filaments"]["nozzle_temperature"][0] = "modifié"
    assert source == avant


def test_residus_palette_et_valeurs_non_appariees_restent_non_verifies():
    """Deux valeurs sur un slot ne deviennent ni une diffusion ni une réindexation."""
    source = {"filament_settings_id": ["PLA"], "filament_colour": ["noir", "ancien"],
              "nozzle_temperature": ["210", "220"]}
    contexte = reglages.contexte_filaments(source)
    assert "nozzle_temperature" not in contexte["correspondances_filaments"]
    assert "filament_colour" not in contexte["correspondances_filaments"]
    assert contexte["statut_contexte_filaments"] == "NON VÉRIFIÉ"
    assert source["nozzle_temperature"] == ["210", "220"]


@pytest.mark.parametrize("identites", [None, [], [""], ["PLA", None]])
def test_identites_absentes_ou_invalides_ne_creent_pas_de_correspondance(identites):
    """Une cardinalité égale ne qualifie pas des identités absentes ou ambiguës."""
    source = {"nozzle_temperature": ["210"]}
    if identites is not None:
        source["filament_settings_id"] = identites
    contexte = reglages.contexte_filaments(source)
    assert contexte["correspondances_filaments"] == {}
    assert contexte["statut_contexte_filaments"] == "NON VÉRIFIÉ"


def test_consommateur_declare_resout_compte_sans_reduire_palette():
    """Le lecteur CLI compte les profils et garde la palette résiduelle intacte."""
    source = {"filament_settings_id": ["PLA"], "filament_colour": ["noir", "ancien"],
              "filament_ids": ["ID-PLA"], "nozzle_temperature": ["210"]}
    contexte = reglages.contexte_filaments(source, consommateur="cli")
    assert contexte["consommateur_reglages"] == "cli"
    assert contexte["correspondances_filaments"]["nozzle_temperature"] == ["PLA"]
    assert "filament_colour" not in contexte["correspondances_filaments"]
    assert source["filament_colour"] == ["noir", "ancien"]


def test_suffixe_type_ne_qualifie_pas_sa_cle_de_base():
    """Une option suffixée déclare ses propres slots et ne prouve pas ceux d'une autre clé."""
    source = {"filament_settings_id": ["PLA"], "nozzle_temperature_BRASS": ["210"]}
    contexte = reglages.contexte_filaments(source)
    assert "nozzle_temperature" not in contexte["correspondances_filaments"]
    assert contexte["correspondances_filaments"]["nozzle_temperature_BRASS"] == ["PLA"]


def test_contexte_filament_ne_masque_pas_le_profil_machine_source():
    """Le contexte des filaments choisis laisse les vecteurs machine dans leur repère d'origine."""
    machine = {"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": ["600", "300", "600"]}
    contexte = reglages.contexte_filaments({"filament_settings_id": ["PLA"]}, consommateur="cli")
    source = reglages.Reglages(machine, contexte=contexte)
    assert reglages.longueur_attendue("machine_max_speed_x", source) == 3
