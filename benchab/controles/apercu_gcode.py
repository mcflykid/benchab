"""Identifie et exporte le G-code exact à ouvrir dans l’aperçu du trancheur."""

import argparse
import json
from zipfile import BadZipFile

from benchab.gcode.lecture import empreintes_plaques, exporter_apercu


def main(argv=None, *, prive=None):
    """Affiche les SHA par plaque ou publie un G-code privé épinglé. Codes 0 ou 2."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--plaque", type=int)
    parser.add_argument("--sha256")
    parser.add_argument("--sortie")
    args = parser.parse_args(argv)
    if any(v is not None for v in (args.plaque, args.sha256, args.sortie)) and not (
        args.plaque and args.sha256 and args.sortie
    ):
        parser.error("--plaque, --sha256 et --sortie sont requis ensemble.")
    try:
        if args.sortie:
            chemin = exporter_apercu(args.source, args.plaque, args.sha256, args.sortie, prive=prive)
            resultat = {"plaque": args.plaque, "sha256": args.sha256, "fichier": str(chemin)}
        else:
            resultat = {"plaques": empreintes_plaques(args.source)}
    except (ValueError, OSError, BadZipFile) as erreur:
        print(json.dumps({"statut": "NON VÉRIFIÉ", "raison": str(erreur)}, ensure_ascii=False))
        return 2
    print(json.dumps({"statut": "PASS", **resultat}, ensure_ascii=False))
    return 0
