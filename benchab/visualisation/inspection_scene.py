"""Commande dédiée à l’inspection géométrique en lecture seule d’une scène Blender privée."""

import argparse
import json
from pathlib import Path

from benchab.visualisation.rendu_photo import inspecter_scene


def main(argv=None, *, prive=None):
    """Inspecte un .blend privé (mm), imprime PASS/WARN/NON VÉRIFIÉ en JSON/code et ne sauvegarde pas la scène."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("--sortie", type=Path)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--delai", type=float, default=600)
    parser.add_argument("--mm-par-unite", type=float, help="Échelle explicitement déclarée en mm par unité de scène")
    args = parser.parse_args(argv)
    resultat = inspecter_scene(
        args.scene,
        prive=prive or "prive",
        sortie=args.sortie,
        blender=args.blender,
        delai=args.delai,
        mm_par_unite=args.mm_par_unite,
    )
    print(json.dumps(resultat, ensure_ascii=False, indent=2))
    return 0 if resultat["statut"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
