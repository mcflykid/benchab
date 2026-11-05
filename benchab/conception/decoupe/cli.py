"""Commandes de découpe intelligente, sans commande à l'imprimante."""

import argparse
import json
from pathlib import Path

from . import atelier
from .connecteurs import CATALOGUE


def main(argv=None, *, prive=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commandes = parser.add_subparsers(dest="commande", required=True)
    commandes.add_parser("catalogue")
    proposition = commandes.add_parser("proposer")
    proposition.add_argument("source", type=Path)
    proposition.add_argument("--unites", choices=("mm", "cm", "m"), required=True)
    proposition.add_argument("--pieces", type=int, default=2)
    proposition.add_argument("--mobile", action="store_true")
    proposition.add_argument("--renfort", action="store_true")
    proposition.add_argument("--volumes-source", action="store_true")
    proposition.add_argument("--priorite", nargs="+")
    proposition.add_argument("--sortie", type=Path, required=True)
    calcul = commandes.add_parser("calculer")
    calcul.add_argument("plan", type=Path)
    calcul.add_argument("--sortie", type=Path, required=True)
    calcul.add_argument("--format", choices=("stl", "3mf"), default="stl")
    voir = commandes.add_parser("voir")
    voir.add_argument("plan", type=Path)
    voir.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.commande == "catalogue":
        print(json.dumps(CATALOGUE, ensure_ascii=False))
        return 0
    if args.commande == "proposer":
        if args.priorite and not args.volumes_source:
            parser.error("--priorite exige --volumes-source")
        plan = atelier.nouveau_plan(
            args.source,
            args.unites,
            args.pieces,
            mobile=args.mobile,
            renfort=args.renfort,
            separer_volumes_source=args.volumes_source,
            priorite_volumes=args.priorite,
        )
        atelier.sauver_plan(plan, args.sortie, prive=prive)
        print(
            json.dumps(
                {
                    "plan": str(args.sortie),
                    "coupes": len(plan["coupes"]),
                    "declencheurs": plan["proposition"]["declencheurs"],
                    "validation_physique": False,
                },
                ensure_ascii=False,
            )
        )
        return 0
    plan = json.loads(args.plan.read_text())
    if args.commande == "voir":
        from benchab.visualisation.decoupe import page

        sortie = page(plan, args.sortie, prive=prive)
    else:
        resultat = atelier.calculer(plan)
        sortie = atelier.exporter(resultat, args.sortie, args.format, prive=prive)
    print(json.dumps({"sortie": str(sortie), "validation_physique": False}, ensure_ascii=False))
    return 0
