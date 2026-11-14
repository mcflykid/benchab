"""Pièces paramétriques nominales proposées dans l’atelier local."""

import math

import trimesh

from benchab.conception.decoupe import _vers_trimesh
from benchab.conception.decoupe.connecteurs import _clip_nominal
from benchab.conception.eprouvette_jeux import plaque


def generer(modele, cote_mm, jeu_mm):
    """Renvoie des solides posés en mm et leurs cotes nominales, sans ajustement physique déduit."""
    if not math.isfinite(cote_mm) or not 8 <= cote_mm <= 80 or not math.isfinite(jeu_mm) or not 0.1 <= jeu_mm <= 0.6:
        raise ValueError("Cote de 8 à 80 mm et jeu de 0,1 à 0,6 mm requis.")
    if modele == "boite":
        paroi = 1.6
        hauteur = cote_mm / 2
        ouverture = cote_mm - 2 * paroi
        corps = trimesh.creation.box([cote_mm, cote_mm, hauteur])
        vide = trimesh.creation.box([ouverture, ouverture, hauteur])
        vide.apply_translation([0, 0, paroi])
        boite = trimesh.boolean.difference([corps, vide], engine="manifold")
        couvercle = trimesh.creation.box([cote_mm, cote_mm, paroi])
        emboitement = trimesh.creation.box([ouverture - 2 * jeu_mm, ouverture - 2 * jeu_mm, 2])
        emboitement.apply_translation([0, 0, 1.7])
        dessus = trimesh.boolean.union([couvercle, emboitement], engine="manifold")
        pieces = {"Boîte": boite, "Couvercle": dessus}
    elif modele == "clip":
        male, _, _, _, _ = _clip_nominal("clip-languette", cote_mm / 2, cote_mm, jeu_mm, 1.6)
        pieces = {"Clip à languette": _vers_trimesh(male)}
    elif modele == "temoin":
        eprouvette, _ = plaque(cote_mm)
        pieces = {"Témoin de jeux": eprouvette}
    elif modele == "fixation":
        # Deux passages ouverts, orientés selon Z, évitent un plafond de perçage (#324).
        largeur = max(8.0, cote_mm / 3)
        semelle = trimesh.creation.box([cote_mm, largeur, 3.0])
        outils = []
        for x in (-cote_mm / 4, cote_mm / 4):
            trou = trimesh.creation.cylinder(radius=min(1.6, cote_mm / 8), height=5, sections=48)
            trou.apply_translation([x, 0, 0])
            outils.append(trou)
        pieces = {"Plat de fixation": trimesh.boolean.difference([semelle, *outils], engine="manifold")}
    else:
        raise ValueError("Pièce paramétrique inconnue.")
    for index, mesh in enumerate(pieces.values()):
        if not mesh.is_volume:
            raise ValueError("Le résultat de la construction n’est pas un solide fermé.")
        mesh.apply_translation([index * (cote_mm + 5), 0, -mesh.bounds[0, 2]])
    return pieces
