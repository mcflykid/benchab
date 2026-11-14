"""Gel et vérification des livrables d'un projet (outil commun, 21/10/2026).

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

from benchab.qualite.ecritures import (
    PublicationNeuve,
    destination_neuve,
    ecrire_json_neuf,
    sortie_durable,
    valider_chemins,
)
from benchab.qualite.empreintes import sha256_fichier as sha


def interne(d, rel):
    """Résout un chemin relatif à l’atelier ; refuse les chemins absolus et les remontées."""
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


def verifier(d, *, sortie=None, resume=False):
    """Compare SHA-256 et présence des livrables, avec rapport complet optionnel.

    Retourne 0 si intacts, 1 si écart, ou lève sur refus. Aucun livrable modifié.
    """
    d = Path(d).resolve()
    manifest = interne(d, "LIVRABLES.json")
    data = json.loads(manifest.read_text())
    ecarts, fichiers, entrees = [], [], [manifest]
    if not isinstance(data, dict) or not isinstance(data.get("fichiers"), list) or not data["fichiers"]:
        raise ValueError("Manifeste de livrables absent, vide ou malformé")
    for f in data["fichiers"]:
        if not isinstance(f, dict) or not isinstance(f.get("sha256"), str):
            raise ValueError("Entrée de livrable malformée")
        p = interne(d, f.get("chemin"))
        entrees.append(p)
        if not p.exists():
            observe = None
        else:
            observe = sha(p)
        etat = "absent" if observe is None else "intact" if observe == f["sha256"] else "modifié"
        fichiers.append({"chemin": f["chemin"], "etat": etat, "sha256_attendu": f["sha256"],
                         "sha256_observe": observe})
        if etat != "intact":
            ecarts.append((etat, f["chemin"]))
    code = int(bool(ecarts))
    court = {"statut": "FAIL" if code else "PASS", "code": code,
             "version": data.get("version", ""), "ecarts": ecarts}
    if sortie is not None:
        # Le rapport complet relie les écarts aux empreintes réellement relues (#35).
        ecrire_json_neuf(sortie, {**court, "fichiers": fichiers}, entrees=entrees)
    if resume:
        print(json.dumps({**court, "fichiers": len(fichiers), "rapport": str(sortie)}, ensure_ascii=False))
    else:
        print(f"{court['version']} : {len(fichiers)} fichiers, {len(ecarts)} écart(s)")
        for e in ecarts:
            print("  ", *e)
    return code


def main(argv=None):
    """Traite figer/verifier ; retourne 0 en succès, 1 en écart et 2 en refus."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("figer")
    p.add_argument("projet", type=Path)
    p.add_argument("motifs", nargs="+")
    p.add_argument("--version", default="")
    p.add_argument("--note", default="")
    p = sub.add_parser("verifier")
    p.add_argument("projet", type=Path)
    p.add_argument("--json", type=Path)
    p.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "verifier":
            if args.resume and args.json is None:
                raise ValueError("--resume exige --json pour conserver le rapport complet.")
            sortie = destination_neuve(args.json) if args.json is not None else None
            return verifier(args.projet, sortie=sortie, resume=args.resume)
        figer(args.projet, args.motifs, args.version, args.note)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Livrables refusés : " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
