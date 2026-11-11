"""Trous verticaux et profils en goutte ; aucune compensation mesurée implicite.

Les hypothèses non mesurées sont inactives par défaut. Une table privée MESURÉE
conserve les observations utilisateur ; une estimation ne devient pas une mesure.
"""

import copy
import json
import math
from pathlib import Path

import numpy as np

from benchab.chemins import atelier_prive

TABLE = None
THEORIE = {
    "statut": "NON MESURÉE ; heuristique non appliquée par défaut",
    "source": "Hypothèses d'étude, pas une calibration ni une mesure physique.",
    "vertical_mm": [[2.0, 0.3], [3.0, 0.25], [5.0, 0.2], [8.0, 0.15], [12.0, 0.15], [20.0, 0.1]],
    "horizontal": {"forme": "goutte", "angle_pointe_deg": 45, "ajout_diametre_mm": 0.0},
    "mesures": [],
}


def chemin_table(chemin=None):
    """Résout la table de compensation choisie; sortie Path, aucune calibration implicite."""
    return Path(chemin or TABLE or atelier_prive() / "config/compensation-trous.json")


def table(chemin=None):
    """Lit la table de compensation du chemin choisi ou copie la théorie.

    Sortie : dictionnaire, cotes et ajouts en mm, angles en degrés.
    Si le fichier est absent, les valeurs restent théoriques. Aucun
    étalonnage ni écriture n’est effectué.
    """
    path = chemin_table(chemin)
    return json.loads(path.read_text()) if path.is_file() else copy.deepcopy(THEORIE)


def diametre_vertical(d, theorique=False, *, table_path=None):
    """Sans calibration mesurée, rend d inchangé ; hypothèse seulement si theorique=True."""
    tb = table(table_path)
    if not tb["statut"].startswith("MESURÉ") and not theorique:
        return float(d)
    t = np.array(tb["vertical_mm"])
    return round(float(d + np.interp(d, t[:, 0], t[:, 1])), 3)


def goutte_2d(d, angle=None, ajout=None, *, table_path=None):
    """Cercle prolongé par deux tangentes à ±angle de la verticale jusqu'à une pointe."""
    from shapely.geometry import MultiPoint, Point

    h = table(table_path)["horizontal"]
    a = math.radians(angle or h["angle_pointe_deg"])
    r = (d + (h["ajout_diametre_mm"] if ajout is None else ajout)) / 2
    pointe = r / math.sin(a)  # hauteur de la pointe au-dessus du centre
    return MultiPoint(
        list(Point(0, 0).buffer(r, 32).exterior.coords) + [(0, pointe)]
    ).convex_hull  # tangentes = enveloppe convexe


def goutte_maillage(d, longueur, axe="X", centre=(0, 0, 0), **k):
    """Extrude le profil goutte en maillage centré sur l’axe X ou Y.

    Entrées : diamètre d, longueur et centre en mm ; options du profil
    dont l’angle en degrés. Sortie : maillage Trimesh en mm, sans écriture
    ni preuve d’ajustement imprimé.
    """
    import trimesh

    m = trimesh.creation.extrude_polygon(goutte_2d(d, **k), longueur)
    m.apply_translation([0, 0, -longueur / 2])
    # profil dans (x, y) local, extrusion selon z local -> y local devient Z, z local devient l'axe du trou
    R = {"X": [[0, 0, 1], [1, 0, 0], [0, 1, 0]], "Y": [[1, 0, 0], [0, 0, 1], [0, 1, 0]]}[axe]
    T = np.eye(4)
    T[:3, :3] = np.array(R, float)
    T[:3, 3] = centre
    m.apply_transform(T)
    return m


def goutte_build123d(d, longueur, **k):
    """Extrude le profil goutte dans le plan XZ, de part et d’autre.

    Entrées : diamètre et longueur en mm et options du profil. Sortie :
    solide build123d en mm, sans export ni validation physique.
    """
    from build123d import Plane, Polyline, extrude, make_face

    pts = list(goutte_2d(d, **k).exterior.coords)
    return extrude(make_face(Plane.XZ * Polyline(*pts, close=True)), amount=longueur / 2, both=True)


def main(argv=None, *, prive=None):
    """Affiche les cotes calculées depuis la table privée; diamètres en mm, sans validation physique."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path)
    arguments = parser.parse_args(argv)
    table_path = arguments.table or atelier_prive(prive) / "config/compensation-trous.json"
    t = table(table_path)
    print("table :", t["statut"])
    for d in (3, 4, 5, 6, 8, 10, 12):
        print(f"  trou vertical Ø {d} -> dessiner Ø {diametre_vertical(d, table_path=table_path)}")
    g = goutte_2d(5, table_path=table_path)
    print(
        "  goutte Ø 5 : hauteur",
        round(g.bounds[3] - g.bounds[1], 2),
        "mm, largeur",
        round(g.bounds[2] - g.bounds[0], 2),
        "mm",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
