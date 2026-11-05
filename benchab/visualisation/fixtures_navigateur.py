"""Génère une scène isolée pour les parcours navigateur, sans fabrication."""

import argparse
from pathlib import Path

import trimesh

from benchab.visualisation.fixtures_inspection import valider_sortie
from benchab.visualisation.visionneuse import Visionneuse, translation


def generer(sortie):
    sortie = valider_sortie(sortie)
    v = Visionneuse("Assemblage synthétique", note="Fixture numérique, aucune validation physique.")
    v.modele("Bloc", trimesh.creation.box((30, 20, 20)), couleur="vert")
    v.piece("Gauche", "Bloc", matrice=translation(-22, 0, 10), groupe="Blocs")
    v.piece("Droite", "Bloc", matrice=translation(22, 0, 10), groupe="Blocs")
    return v.ecrire(Path(sortie) / "assemblage.html")


def generer_anomalie(sortie):
    from benchab.visualisation.anomalies_geometriques import charger_diagnostic
    from benchab.visualisation.visionneuse import rotation

    sortie = valider_sortie(sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.creation.box((4, 4, 2))
    mesh.apply_translation((0, 0, 3))
    source = sortie / "source-synthetique.stl"
    mesh.export(source)
    mesh, diagnostic = charger_diagnostic(source, "Piece")
    v = Visionneuse("Anomalie synthétique connue", note="Fixture numérique ; aucune validation physique.")
    v.modele("Piece", mesh)
    v.piece("Piece", "Piece", matrice=translation(40, 10, 7) @ rotation("Z", 90))
    v.diagnostics = [diagnostic]
    return v.ecrire(sortie / "anomalie.html")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sortie", type=Path, required=True)
    p.add_argument("--anomalie", action="store_true")
    a = p.parse_args()
    print(generer(a.sortie))
    if a.anomalie:
        print(generer_anomalie(a.sortie))
