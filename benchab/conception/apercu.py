"""Aperçu interactif privé et décision explicite sur les fichiers examinés."""

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

import numpy as np

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive, sans_lien
from benchab.imports3d.lecture_3mf import lire
from benchab.projets.validation_apercu import fiche_laterale
from benchab.qualite.ecritures import (
    PublicationNeuve,
    destination_neuve,
    ecrire_json_neuf,
    remplacer_texte,
    verrou_publication,
)
from benchab.qualite.empreintes import sha256_fichier
from benchab.visualisation.visionneuse import Visionneuse, charger_maillage

_DECISIONS = ("EN ATTENTE", "À AJUSTER", "VALIDÉ")
_MAX_OCTETS = 128 * 1024**2
_DONNEES = '<script type="application/json" id="donnees">'


def _ajouter(vue, source, indice, sha):
    if source.suffix.lower() == ".stl":
        objets = [(charger_maillage(source), np.eye(4))]
    else:
        # Le lecteur commun applique les unités et placements du 3MF, y compris les composants.
        with Archive3MF(source, taille_max_totale=_MAX_OCTETS) as archive:
            scene = lire(source, max_faces=600_000)
            archive.verifier_source(source)
        objets = [(scene.geometry[nom], matrice) for matrice, nom in
                  (scene.graph[n] for n in scene.graph.nodes_geometry)]
    if not 1 <= len(objets) <= 128:
        raise ValueError("Un à 128 objets placés attendus dans chaque source.")
    for n, (modele, matrice) in enumerate(objets, 1):
        if (modele.is_empty or len(modele.faces) > 600_000
                or not np.isfinite(modele.vertices).all() or not np.isfinite(matrice).all()):
            raise ValueError("L’aperçu exige un maillage non vide et des coordonnées finies.")
        identifiant = f"objet_{indice}_{n}"
        vue.modele(identifiant, modele, couleur=("bleu", "orange", "vert")[indice % 3])
        vue.piece(f"{source.name} · {n}", identifiant, matrice=matrice, info=f"SHA-256 {sha}")


def _produire(source, sortie, pieces, racine):
    fichiers = [sans_lien(f, racine=racine, strict=True) for f in [source, *pieces]]
    if len(set(fichiers)) != len(fichiers) or len(fichiers) > 8:
        raise ValueError("Une candidate et au plus sept pièces distinctes sont admises.")
    if any(f.suffix.lower() not in {".stl", ".3mf"} or not f.is_file() for f in fichiers):
        raise ValueError("Sources STL en mm ou 3MF régulières exigées.")
    if sum(f.stat().st_size for f in fichiers) > _MAX_OCTETS:
        raise ValueError("Budget de 128 Mio dépassé pour les fichiers de l’aperçu.")
    cible = destination_neuve(sortie, prive=racine, temporaire=False)
    if cible.suffix.lower() != ".html":
        raise ValueError("La destination de l’aperçu doit se terminer par .html.")
    with PublicationNeuve(fichiers, [cible]) as publication:
        empreintes = [sha256_fichier(f) for f in fichiers]
        vue = Visionneuse("Aperçu de la candidate", source={"sha256": empreintes[0], "unites": "mm"})
        for indice, (fichier, sha) in enumerate(zip(fichiers, empreintes)):
            _ajouter(vue, fichier, indice, sha)
        with TemporaryDirectory(prefix="benchab-apercu-") as dossier:
            page = Path(dossier) / "apercu.html"
            vue.ecrire(page, prive=racine)
            if [sha256_fichier(f) for f in fichiers] != empreintes:
                raise ValueError("Une géométrie a changé pendant la production de l’aperçu.")
            temporaire = publication.temporaire(cible)
            shutil.copyfile(page, temporaire)
            empreinte_apercu = sha256_fichier(temporaire)
        publication.publier()
    return {"decision": "EN ATTENTE", "fichier": str(cible), "source_sha256": empreintes[0],
            "apercu_sha256": empreinte_apercu}


def _decider(args, racine):
    source = sans_lien(args.source, racine=racine, strict=True)
    apercu = sans_lien(args.apercu, racine=racine, strict=True)
    fiche = sans_lien(fiche_laterale(source), racine=racine)
    if len({source, apercu, fiche}) != 3 or apercu.suffix.lower() != ".html":
        raise ValueError("Source et aperçu HTML distincts requis.")
    if not all(f.is_file() for f in (source, apercu)):
        raise ValueError("Source et aperçu doivent être des fichiers réguliers.")
    donnees = {
        "schema": 1, "decision": args.decision, "source_sha256": args.source_sha256,
        "apercu": {"fichier": os.path.relpath(apercu, fiche.parent),
                   "sha256": args.apercu_sha256},
    }
    if apercu.stat().st_size > _MAX_OCTETS:
        raise ValueError("Aperçu hors budget de lecture.")
    contenu = apercu.read_bytes()
    if hashlib.sha256(contenu).hexdigest() != args.apercu_sha256:
        raise ValueError("L’empreinte de l’aperçu ne correspond plus à celle examinée.")
    _, separateur, reste = contenu.decode("utf-8").partition(_DONNEES)
    metadonnees = json.loads(reste.partition("</script>")[0]) if separateur else {}
    provenance = metadonnees.get("source") if isinstance(metadonnees, dict) else None
    if not isinstance(provenance, dict) or provenance.get("sha256") != args.source_sha256:
        raise ValueError("L’aperçu ne présente pas la géométrie portant cette empreinte.")
    with verrou_publication(fiche.parent, f".{fiche.name}.lock"):
        if (sha256_fichier(source), sha256_fichier(apercu)) != (args.source_sha256, args.apercu_sha256):
            raise ValueError("Une empreinte a changé depuis la décision. Réexaminer l’aperçu.")
        if fiche.exists():
            precedent = json.loads(fiche.read_text(encoding="utf-8")) if fiche.stat().st_size <= 65536 else None
            if not isinstance(precedent, dict) or precedent.keys() != donnees.keys() or precedent["schema"] != 1:
                raise ValueError("Décision existante non reconnue. Conserver la fiche et choisir une candidate neuve.")
            remplacer_texte(fiche, json.dumps(donnees, ensure_ascii=False, indent=2) + "\n")
        else:
            ecrire_json_neuf(fiche, donnees, entrees=[source, apercu])
    return {"decision": args.decision, "fiche": str(fiche)}


def main(argv=None, *, prive=None):
    """Produit un HTML en mm ou consigne une décision privée explicite, sans tranchage (codes 0/2)."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    produire = modes.add_parser("produire")
    produire.add_argument("source", type=Path)
    produire.add_argument("--piece", type=Path, action="append", default=[], help="Pièce en repère commun")
    produire.add_argument("--sortie", required=True, type=Path)
    decider = modes.add_parser("decider")
    decider.add_argument("source", type=Path)
    decider.add_argument("apercu", type=Path)
    decider.add_argument("--decision", choices=_DECISIONS, required=True)
    decider.add_argument("--source-sha256", required=True)
    decider.add_argument("--apercu-sha256", required=True)
    args = parser.parse_args(argv)
    racine = atelier_prive(prive)
    try:
        resultat = (_produire(args.source, args.sortie, args.piece, racine)
                    if args.mode == "produire" else _decider(args, racine))
    except (OSError, ValueError, BadZipFile, KeyError, StopIteration) as erreur:
        parser.exit(2, f"BENCHAB : aperçu refusé : {erreur}\n")
    print(json.dumps(resultat, ensure_ascii=False))
    return 0
