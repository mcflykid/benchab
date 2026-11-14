"""Témoins de dégagement à la résolution d’une grille."""

import numpy as np

from benchab.controles.retrait_supports import classifier


def test_cavite_fermee_fente_etroite_et_sortie_libre():
    """Le même support est isolé, derrière une fente ou accessible selon l’ouverture."""
    piece = np.zeros((15, 15, 15), dtype=bool)
    piece[4:11, 4:11, 4:11] = True
    piece[5:10, 5:10, 5:10] = False
    support = np.zeros_like(piece)
    support[7, 7, 7] = True
    assert classifier(piece, support, pas_mm=1, rayon_mm=1.5)[0]["acces"] == "ENCLAVE"
    piece[4, 7, 7] = False
    assert classifier(piece, support, pas_mm=1, rayon_mm=1.5)[0]["acces"] == "ETROIT"
    piece[4, 5:10, 5:10] = False
    assert classifier(piece, support, pas_mm=1, rayon_mm=1.5)[0]["acces"] == "OUVERT"


def test_appui_et_retrait_restent_distincts_dans_un_gcode(tmp_path):
    """Un dépôt de support localisé au bord d’une pièce garde un retrait non vérifié."""
    from zipfile import ZipFile

    from benchab.controles.retrait_supports import analyser

    fichier = tmp_path / "depot.3mf"
    with ZipFile(fichier, "w") as archive:
        archive.writestr("Metadata/plate_2.gcode", (
            "G90\nM83\n;HEIGHT:0.2\n;WIDTH:0.6\n"
            ";TYPE:Outer wall\nG1 X0 Y0.2 Z0.2 F600\nG1 X4 E0.2\n"
            ";TYPE:Support\nG1 X0 Y3.0\nG1 X4 E0.2\n"
        ))
    rapport = analyser(fichier, [2])
    assert rapport["statut"] == "NON VÉRIFIÉ"
    plaque = rapport["plaques"][0]
    assert plaque["plaque"] == 2
    assert plaque["regions"][0]["acces"] == "OUVERT"
    assert plaque["regions"][0]["position_mm"][1] == 3


def test_une_branche_ouverte_peut_continuer_dans_un_passage_etroit():
    """Un accès au pied ne masque pas une portion qui traverse une fente."""
    piece = np.zeros((15, 15, 15), dtype=bool)
    piece[4:11, 4:11, 4:11] = True
    piece[5:10, 5:10, 5:10] = False
    piece[4, 7, 7] = False
    support = np.zeros_like(piece)
    support[1:8, 7, 7] = True
    zones = classifier(piece, support, pas_mm=1, rayon_mm=1.5)
    assert {z["acces"] for z in zones} == {"ETROIT", "OUVERT"}
    assert sum(z["cellules"] for z in zones) == 7
