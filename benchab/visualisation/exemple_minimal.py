"""Exemple de visionneuse : boîte et couvercle articulé, sans fichier externe.

Génération explicite : ``python -m benchab.visualisation.exemple_minimal --sortie prive/exemple.html``."""

import argparse
from pathlib import Path

from benchab.chemins import atelier_prive, est_temporaire, sans_lien


def generer(sortie):
    """Génère une page synthétique neuve; dimensions en mm, aucune modification de modèle source."""
    import trimesh

    from benchab.visualisation.visionneuse import Visionneuse, rotation

    sortie = sans_lien(Path(sortie).expanduser().absolute())
    prive = atelier_prive()
    if not (sortie.is_relative_to(prive) or est_temporaire(sortie)):
        raise ValueError("la sortie doit rester dans BENCHAB_PRIVE ou /private/tmp")
    if sortie.exists() or sortie.is_symlink():
        raise ValueError("la sortie existe déjà ; aucun écrasement")
    boite = trimesh.creation.box((80, 50, 30))
    boite.apply_translation((0, 0, 15))
    creux = trimesh.creation.box((74, 44, 28))
    creux.apply_translation((0, 0, 17))
    boite = boite.difference(creux)
    couvercle = trimesh.creation.box((80, 50, 3))
    couvercle.apply_translation((0, 0, 31.5))
    v = Visionneuse(
        "Boîte d’exemple",
        sous_titre="Test de la visionneuse réutilisable",
        note="Exemple généré sans fichier externe.",
        axe_haut="Z",
    )
    v.modele("Boite", boite, couleur="vert")
    v.modele("Couvercle", couvercle, couleur="os")
    v.piece("Boite", "Boite", groupe="Caisse")
    v.piece("Couvercle", "Couvercle", groupe="Couvercle", eclate=(0, 0, 40))
    v.mecanisme(
        "ouverture",
        "Ouverture du couvercle",
        range(0, 111, 2),
        unite="°",
        poses=lambda a: {"Couvercle": rotation("Y", -a, (-40, 0, 33))},
        etat=lambda a: "fermé" if a == 0 else f"ouvert de {a}°",
    )
    return v.ecrire(sortie)


def main(argv=None):
    """Commande d'exemple ; écrit uniquement une page privée/temporaire neuve."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    print(generer(args.sortie))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
