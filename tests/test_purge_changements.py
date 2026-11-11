"""Transitions dirigées et matrice conditionnelle ; aucun réglage machine exécuté."""

import json
from zipfile import ZipFile

import pytest

from benchab.controles.purge_changements import analyser, analyser_lignes
from benchab.reglages import lire_matrice_purge, valeur_brute


def _reglages(matrice=None):
    return {
        "filament_settings_id": ["Profil noir", "Profil blanc"],
        "filament_colour": ["#000000", "#ffffff"],
        "flush_volumes_matrix": matrice if matrice is not None else ["0", "12", "31", "0"],
    }


def test_paires_directionnelles_ne_confondent_pas_la_premiere_selection_ni_une_repetition():
    """Une matrice asymétrique prouve seulement la lecture source vers destination."""
    texte = "T0\nT1\nT1\nT0\nT1\n"
    rapport = analyser_lignes(texte.splitlines(), _reglages())
    assert rapport["changements_explicites"] == 3
    assert rapport["paires"] == [
        {"depart": 0, "arrivee": 1, "nombre": 2, "valeur_unitaire_matrice": 12.0},
        {"depart": 1, "arrivee": 0, "nombre": 1, "valeur_unitaire_matrice": 31.0},
    ]
    assert rapport["somme_conditionnelle_matrice"] == 55.0
    assert rapport["purge_physique_mm3"] is None


def test_matrice_trois_par_trois_ne_qualifie_pas_un_temoin_mono():
    """Ne pas choisir une sous-matrice depuis la seule longueur du tableau."""
    donnees = _reglages(["0"] * 9)
    donnees["filament_settings_id"] = ["Profil seul"]
    donnees["filament_colour"] = ["#000000"]
    rapport = analyser_lignes(["T0", "T0"], donnees)
    assert rapport["changements_explicites"] == 0
    assert rapport["matrice"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["somme_conditionnelle_matrice"] is None


def test_selection_hors_slots_interrompt_la_couverture():
    """Un indice inconnu ne doit pas fabriquer une transition entre deux slots valides."""
    rapport = analyser_lignes(["T0", "T255", "T1"], _reglages())
    assert rapport["changements_explicites"] == 2
    assert rapport["changements_hors_contexte"] == 2
    assert rapport["somme_conditionnelle_matrice"] is None


def test_lecture_conditionnelle_ne_change_pas_le_type_historique():
    """Le dictionnaire n'autorise toujours pas la lecture générique de la matrice historique."""
    donnees = _reglages()
    assert lire_matrice_purge(donnees)["statut"] == "PASS"
    with pytest.raises(ValueError, match="NON VÉRIFIÉ"):
        valeur_brute(donnees, "flush_volumes_matrix")
    assert lire_matrice_purge(_reglages(["0", "12", "31", "2"]))["statut"] == "NON VÉRIFIÉ"
    assert lire_matrice_purge(_reglages(["0", "NaN", "31", "0"]))["statut"] == "NON VÉRIFIÉ"


def test_archive_multiplaque_garde_les_sequences_separees(tmp_path):
    """La première sélection de la seconde plaque n’est pas un changement de la première."""
    source = tmp_path / "bicolore.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(_reglages()))
        archive.writestr("Metadata/plate_1.gcode", "T0\nT1\n")
        archive.writestr("Metadata/plate_2.gcode", "T0\n")
    avant = source.read_bytes()

    rapport = analyser(source)

    assert rapport["statut"] == "PASS"
    assert [p["changements_explicites"] for p in rapport["plaques"]] == [1, 0]
    assert [p["somme_conditionnelle_matrice"] for p in rapport["plaques"]] == [12.0, 0.0]
    assert rapport["volume_gcode_attribue_mm3"] is None
    assert source.read_bytes() == avant
