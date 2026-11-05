"""Répondre aux observations enregistrées par les visionneuses BENCHAB.

Usage : ``python -m benchab.visualisation.commentaires lister`` ou
``python -m benchab.visualisation.commentaires repondre <fichier> <id> <texte>``.
Les écritures sont atomiques et limitées à BENCHAB_PRIVE."""

import argparse
import datetime
from pathlib import Path

from benchab.chemins import atelier_prive, sans_lien
from benchab.visualisation.catalogue_visuel import racine_privee
from benchab.visualisation.stockage_commentaires import atomique, lire, transaction


def charger(f):
    return lire(f)


def ecrire(f, d):
    atomique(f, d)


def trouver(d, ref):
    cs = d.get("commentaires", [])
    if ref.isdigit() and 1 <= int(ref) <= len(cs):
        return cs[int(ref) - 1]
    for c in cs:
        if c.get("id") == ref:
            return c
    raise ValueError(f"commentaire {ref} introuvable")


def main(argv=None, *, prive=None):
    """Liste ou annote les commentaires privés ; code0 ou FAIL, sans traverser la racine ni contacter la machine."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    lister = sub.add_parser("lister")
    lister.add_argument("chemin", nargs="?", default="projets")
    repondre = sub.add_parser("repondre")
    repondre.add_argument("fichier")
    repondre.add_argument("reference")
    repondre.add_argument("texte")
    repondre.add_argument("--auteur", choices=("claude", "codex"), default="claude")
    repondre.add_argument("--resoudre", action="store_true")
    resoudre = sub.add_parser("resoudre")
    resoudre.add_argument("fichier")
    resoudre.add_argument("reference")
    resoudre.add_argument("--auteur", choices=("claude", "codex"), default="claude")
    args = parser.parse_args(argv)
    racine = atelier_prive(prive) if prive else racine_privee()
    if args.action == "lister":
        try:
            base = sans_lien(racine / args.chemin, racine=racine)
        except ValueError:
            parser.error("le chemin doit rester dans BENCHAB_PRIVE")
        for f in sorted(base.rglob("commentaires.json")):
            try:
                f = sans_lien(f, racine=racine)
            except ValueError:
                continue
            d = charger(f)
            cs = d.get("commentaires", [])
            print(f"# {f.relative_to(racine)} — {d.get('titre', '')} ({len(cs)} commentaire(s))")
            for i, c in sorted(enumerate(cs, 1), key=lambda x: x[1].get("statut") == "résolu"):
                print(
                    f"  {i}. [{c.get('statut', 'ouvert')}] {c.get('piece')} @ {c.get('point_mm')} mm, "
                    f"normale {c.get('normale_projet')} : {c.get('texte')}"
                )
                for r in c.get("reponses", []):
                    print(f"       ↳ {r.get('auteur')} ({r.get('date')}) : {r.get('texte')}")
        return 0
    f = Path(args.fichier).expanduser()
    if not f.is_absolute():
        f = racine / f
    # Vérifier aussi les parents avant normalisation : un chemin lexicalement
    # privé peut sortir par '..' ou un lien (docs/decisions/2026-11-04-frontiere-public-prive.md).
    try:
        f = sans_lien(f, racine=racine, strict=True)
    except (OSError, ValueError):
        parser.error("le fichier doit exister dans BENCHAB_PRIVE")
    if not f.is_file():
        parser.error("le fichier doit exister dans BENCHAB_PRIVE")
    with transaction(f):
        d = charger(f)
        c = trouver(d, args.reference)
        maintenant = datetime.datetime.now().isoformat(timespec="microseconds")
        if args.action == "repondre":
            c.setdefault("reponses", []).append({"auteur": args.auteur, "date": maintenant, "texte": args.texte})
        if args.action == "resoudre" or getattr(args, "resoudre", False):
            c["statut"], c["statut_date"] = ("résolu", maintenant)
        ecrire(f, d)
    print("enregistré :", c.get("piece"), "—", c.get("statut", "ouvert"), "—", len(c.get("reponses", [])), "réponse(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
