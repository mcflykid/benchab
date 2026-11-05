"""

Gel et vérification des livrables d'un projet (outil commun, 21/10/2026).

  figer    : python3 livrables.py figer <dossier_projet> <motif> [<motif> ...] [--version NOM] [--note TEXTE]
             -> écrit <dossier_projet>/LIVRABLES.json (chemin relatif, taille, SHA-256 de chaque fichier retenu)
  verifier : python3 livrables.py verifier <dossier_projet>
             -> code 0 si tous les fichiers listés existent avec la même empreinte, 1 sinon (liste des écarts)

Les motifs sont des globs relatifs au dossier (ex. 'candidats/*.3mf' 'src/**/*.py'). Ne contacte ni slicer ni
imprimante.
Une vérification réussie prouve l'intégrité des fichiers, pas leur bon fonctionnement physique.

"""

import argparse
import datetime
import json
import sys
from pathlib import Path

from benchab.qualite.ecritures import PublicationNeuve, sortie_durable, valider_chemins
from benchab.qualite.empreintes import sha256_fichier as sha


def interne(d, rel):
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError("Chemin de livrable interne requis")
    p = d / rel
    valider_chemins([p], [])
    return p


def figer(d, motifs, version="", note="", *, prive=None):
    """Fige des références relatives et leurs tailles en octets sous l’atelier, sans écraser de preuve."""
    manifest = sortie_durable(Path(d) / "LIVRABLES.json", prive=prive)
    d = manifest.parent
    valider_chemins([], [manifest])
    for m in motifs:
        interne(d, m)
    fichiers = sorted({p for m in motifs for p in d.glob(m) if p.is_file() and p.name != "LIVRABLES.json"})
    if not fichiers:
        raise ValueError("Gel refusé : aucun livrable sélectionné")
    valider_chemins(fichiers, [])
    data = {
        "version": version,
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "motifs": motifs,
        "fichiers": [{"chemin": str(p.relative_to(d)), "octets": p.stat().st_size, "sha256": sha(p)} for p in fichiers],
    }
    with PublicationNeuve(fichiers, [manifest]) as publication:
        temp = publication.temporaire(manifest)
        temp.write_text(json.dumps(data, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        json.loads(temp.read_text(encoding="utf-8"))
        if any(sha(d / f["chemin"]) != f["sha256"] for f in data["fichiers"]):
            raise ValueError("Livrable modifié avant le gel")
        publication.publier()
    print(f"{len(fichiers)} fichiers figés dans {d / 'LIVRABLES.json'}")


def verifier(d):
    d = Path(d).resolve()
    data = json.loads(interne(d, "LIVRABLES.json").read_text())
    ecarts = []
    if not isinstance(data, dict) or not isinstance(data.get("fichiers"), list) or not data["fichiers"]:
        raise ValueError("Manifeste de livrables absent, vide ou malformé")
    for f in data["fichiers"]:
        if not isinstance(f, dict) or not isinstance(f.get("sha256"), str):
            raise ValueError("Entrée de livrable malformée")
        p = interne(d, f.get("chemin"))
        if not p.exists():
            ecarts.append(("absent", f["chemin"]))
        elif sha(p) != f["sha256"]:
            ecarts.append(("modifié", f["chemin"]))
    print(f"{data.get('version', '')} : {len(data['fichiers'])} fichiers, {len(ecarts)} écart(s)")
    for e in ecarts:
        print("  ", *e)
    return 0 if not ecarts else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("figer")
    p.add_argument("projet", type=Path)
    p.add_argument("motifs", nargs="+")
    p.add_argument("--version", default="")
    p.add_argument("--note", default="")
    p = sub.add_parser("verifier")
    p.add_argument("projet", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "verifier":
            return verifier(args.projet)
        figer(args.projet, args.motifs, args.version, args.note)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Livrables refusés : " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
