"""Création et import transactionnels de projets dans BENCHAB_PRIVE.

Les dossiers sont privés et le registre SQLite reste la source canonique.
Les sources importées sont copiées, vérifiées par SHA-256 et conservées intactes.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
import uuid
import zipfile
from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive
from benchab.donnees.base import Base
from benchab.gabarits.ressources import fichiers as fichiers_gabarit
from benchab.machine.configuration import charger as configuration_machine
from benchab.qualite.ecritures import renommer_exclusif
from benchab.qualite.publication import charger as charger_publication

NIVEAUX = {"S", "M", "L"}
OBJECTIFS = {"qualite", "equilibre", "rapide", "resistance"}
RESULTATS = {"inconnu", "defaut", "echec", "reussie_avec_reserves", "reussie", "arretee"}
FORMATS = {".3mf", ".gcode.3mf", ".gcode", ".stl", ".step", ".stp", ".py", ".glb"}


def racine_privee(prive=None) -> Path:
    """Résout l'atelier privé sans le créer; sortie Path, liens internes refusés."""
    return atelier_prive(prive)


def _nom(valeur: str) -> str:
    if not isinstance(valeur, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", valeur) is None:
        raise ValueError("nom invalide : minuscules, chiffres et tirets simples")
    return valeur


def _titre(valeur, nom):
    titre = nom if valeur is None else valeur
    if not isinstance(titre, str) or not titre.strip() or any(c in titre for c in "\n\r|"):
        raise ValueError("titre invalide")
    return titre.strip()


def _acquerir_verrou(prive: Path):
    path = prive / "cache/nouveau-projet.lock"
    if path.is_symlink():
        raise ValueError("Verrou de création lié refusé")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("Verrou de création ordinaire requis")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _collision(base: Base, nom: str):
    key = unicodedata.normalize("NFC", nom).casefold()
    for ligne in base.connexion.execute("SELECT nom FROM projets"):
        if unicodedata.normalize("NFC", ligne[0]).casefold() == key:
            raise FileExistsError("nom déjà utilisé dans le registre, casse ignorée")
    for dossier in (base.prive / "projets").iterdir():
        if dossier.name.startswith(".creation-"):
            continue
        if unicodedata.normalize("NFC", dossier.name).casefold() == key:
            raise FileExistsError("nom déjà utilisé sur disque, casse ignorée")


def _remplir_gabarit(stage: Path, nom: str, titre: str, niveau: str):
    """Écrit un gabarit validé syntaxiquement dans le stage privé ; lève FAIL avant publication, aucun import."""
    jour = datetime.date.today().isoformat()
    contenu = fichiers_gabarit(niveau)
    requis = {
        "README.md",
        "BESOIN.md",
        "Makefile",
        "src/__init__.py",
        "src/parametres.py",
        "src/modele.py",
        "src/construire.py",
        "src/preparer.py",
        "src/trancher.py",
        "src/preparation.py",
        "src/references.py",
    }
    if niveau in {"M", "L"}:
        requis.update({"interfaces.json", "src/controler_interfaces.py"})
    if niveau == "L":
        requis.update({"qualite.json", "src/verifier.py", "src/controles/exemple_mouvement.py"})
    manquants = sorted(requis - contenu.keys())
    if manquants:
        raise ValueError("gabarit incomplet : " + ", ".join(manquants))
    for relatif, texte in contenu.items():
        cible = stage / relatif
        cible.parent.mkdir(parents=True, exist_ok=True)
        if cible.exists() or cible.is_symlink():
            raise FileExistsError("fichier de gabarit déjà présent : " + relatif)
        rendu = texte.replace("__TITRE_JSON__", json.dumps(titre, ensure_ascii=False))
        rendu = (
            rendu.replace("__TITRE__", titre)
            .replace("__NIVEAU__", niveau)
            .replace("__DATE__", jour)
            .replace("__NOM__", nom)
        )
        rendu = rendu.replace("__PYTHON__", '"' + sys.executable.replace('"', "") + '"')
        if cible.suffix == ".py":
            # Le créateur ne doit pas publier un projet inutilisable ; compiler
            # n'importe ni n'exécute les scripts (docs/guides/cycle-projet.md).
            compile(rendu, relatif, "exec", dont_inherit=True)
        cible.write_text(rendu, encoding="utf-8")
    (stage / "candidats").mkdir(exist_ok=True)
    (stage / "preuves").mkdir(exist_ok=True)


def _suivi(nom, titre, *, historique=False, resultat="inconnu", preuve=None, fichiers=(), reference=False):
    if resultat not in RESULTATS:
        raise ValueError("résultat physique invalide")
    if resultat != "inconnu" and (not isinstance(preuve, str) or not preuve.strip()):
        raise ValueError("un résultat physique exige une preuve utilisateur explicite")
    if reference and (resultat != "reussie" or not fichiers):
        raise ValueError("une référence importée exige un retour réussi explicite et au moins un fichier")
    suivi = {
        "schema": 1,
        "id": str(uuid.uuid4()),
        "nom": nom,
        "titre": titre,
        "type": "historique" if historique else "conception",
        "statut": "HISTORIQUE" if historique else "EN_CONCEPTION",
        "physique": {
            "imprime": (True if resultat != "inconnu" else None) if historique else False,
            "resultat": resultat,
            "preuve": preuve,
        },
        "references_imprimees": (
            [{"id": "reference-importee", "preuve": preuve, "fichiers": list(fichiers)}] if reference else []
        ),
        "references_historiques": [],
        "travail_en_cours": {"fichiers": []},
        "evenements": [],
        "reproductibilite": {
            "statut": "SOURCES_IMPORTEES_A_QUALIFIER" if historique else "A_VERIFIER",
            "limites": ["Aucun résultat physique déduit du contenu des fichiers"],
        },
    }
    return suivi


def _copier_sources(stage: Path, nom: str, sources):
    if not sources:
        raise ValueError("importer exige au moins un --source")
    rows = []
    for index, source in enumerate(sources):
        path = Path(source).expanduser().absolute()
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError("source absente, non régulière ou symbolique")
        suffix = ".gcode.3mf" if path.name.lower().endswith(".gcode.3mf") else path.suffix.lower()
        if suffix not in FORMATS:
            raise ValueError("format source non pris en charge")
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as flux:
            for bloc in iter(lambda: flux.read(1024 * 1024), b""):
                digest.update(bloc)
        source_sha = digest.hexdigest()
        dest = stage / "sources" / f"original{'-' + str(index + 1) if index else ''}{suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as source_flux, dest.open("xb") as dest_flux:
            shutil.copyfileobj(source_flux, dest_flux)
        after = path.stat()
        copie_digest = hashlib.sha256()
        with dest.open("rb") as copie_flux:
            for bloc in iter(lambda: copie_flux.read(1024 * 1024), b""):
                copie_digest.update(bloc)
        copie_sha = copie_digest.hexdigest()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or source_sha != copie_sha:
            raise ValueError("source modifiée pendant la copie ou copie différente")
        if suffix.endswith(".3mf"):
            # Vérifier la copie entière avant l’inscription, sans transformer l’original (#122).
            with Archive3MF(dest) as archive:
                archive.verifier()
                archive.verifier_source(dest)
        rows.append({"chemin": dest.relative_to(stage).as_posix(), "sha256": source_sha, "nom_original": path.name})
    return rows


def _ecrire_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def creer(
    prive,
    nom,
    titre=None,
    niveau="S",
    objectif=None,
    sources=None,
    resultat="inconnu",
    preuve=None,
    reference=False,
    *,
    base: Base | None = None,
):
    """Crée un projet S/M/L ou importe des sources ; renvoie le dossier privé, sans unité ni résultat physique.

    Fournit les chemins des contrôles communs et les limites de plateau en mm.
    Lève sur FAIL ; ne modifie aucune source importée, préparation figée ou configuration installée.
    """
    nom = _nom(nom)
    titre = _titre(titre, nom)
    if niveau not in NIVEAUX:
        raise ValueError("niveau attendu : S, M ou L")
    if objectif not in (None, *OBJECTIFS):
        raise ValueError("objectif invalide")
    if objectif is not None and sources:
        raise ValueError("objectif réservé à la création d'un projet paramétrique")
    owned_base = base is None
    instance = base or Base(racine_privee(prive))
    root = atelier_prive(instance.prive)
    if prive is not None and atelier_prive(prive) != root:
        if owned_base:
            instance.fermer()
        raise ValueError("La base fournie doit correspondre à prive")
    lock_fd = None
    stage = None
    destination = root / "projets" / nom
    publie = False
    try:
        lock_fd = _acquerir_verrou(root)
        _collision(instance, nom)
        with tempfile.TemporaryDirectory(prefix=".creation-" + nom + "-", dir=root / "projets") as tmp:
            stage = Path(tmp) / nom
            stage.mkdir()
            if sources:
                rows = _copier_sources(stage, nom, sources)
                suivi = _suivi(
                    nom, titre, historique=True, resultat=resultat, preuve=preuve, fichiers=rows, reference=reference
                )
                (stage / "README.md").write_text(
                    f"# {titre}\n\nArchive historique privée. Sources originales copiées et hachées dans `sources/`.\n",
                    encoding="utf-8",
                )
                (stage / "ETAT.md").write_text(
                    "# État — " + titre + "\n\nAucun lancement, retranchage ou verdict physique automatique.\n",
                    encoding="utf-8",
                )
                protected = {
                    str(index): {
                        "fichier": row["chemin"],
                        "sha256": row["sha256"],
                        "raison": "original importé protégé",
                    }
                    for index, row in enumerate(rows)
                }
                qualite = {
                    "_role": "Archive historique privée; aucune génération paramétrique inventée.",
                    "suivi": suivi,
                    "protegees": protected,
                }
                _ecrire_json(stage / "qualite.json", qualite)
                _ecrire_json(stage / "LIVRABLES.json", {"statut": "ARCHIVE_HISTORIQUE", "fichiers": rows})
            else:
                _remplir_gabarit(stage, nom, titre, niveau)
                suivi = _suivi(nom, titre)
                if objectif is not None:
                    fabrication = {
                        "schema": 1,
                        "objectif": objectif,
                        "machine": {"profil": None, "configuration_id": None, "mode_index": None},
                        "filaments": {},
                        "contraintes": {"objets": {}},
                        "optimisation": {"max_candidats": 3, "gain_min_pct": 2},
                    }
                    _ecrire_json(stage / "FABRICATION.json", fabrication)
                if niveau == "L":
                    qualite = json.loads((stage / "qualite.json").read_text())
                    qualite["suivi"] = suivi
                else:
                    qualite = {"suivi": suivi}
                # Un gabarit neuf doit fournir le contrat complet des contrôles
                # communs (docs/guides/cycle-projet.md), sans inventer de résultat.
                qualite.update(
                    manifeste="candidats/assemblage.json",
                    stl_assemblage="candidats",
                    stl_impression="candidats",
                    plaques_3mf="candidats/plaques.3mf",
                    tranche_3mf="candidats/tranche.3mf",
                    placement="preuves/placement.json",
                )
                if qualite.get("volume_impression_mm") is None:
                    qualite["volume_impression_mm"] = configuration_machine()["placement"]["plateau_xyz_mm"]
                _ecrire_json(stage / "qualite.json", qualite)
            row_data = {"suivi": suivi, "fichiers_copies": [], "publication_autorisee": False}
            if sources:
                row_data["fichiers_copies"] = [
                    {"destination": f"projets/{nom}/{row['chemin']}", "sha256": row["sha256"]} for row in rows
                ]
                row_data["originebrute"] = {"qualite": {"suivi": suivi}}
            row_data["niveau"] = "historique" if sources else niveau
            row_data["fichiers_source"] = rows if sources else []
            identifiant = f"projets/{nom}"
            with instance.transaction() as connexion:
                _collision(instance, nom)
                connexion.execute(
                    "INSERT INTO projets(id,nom,origine,etat,donnees_json) VALUES(?,?,?,?,?)",
                    (
                        identifiant,
                        nom,
                        "import" if sources else "gabarit",
                        "historique" if sources else "conception",
                        json.dumps(row_data, ensure_ascii=False, allow_nan=False, sort_keys=True),
                    ),
                )
                renommer_exclusif(stage, destination)
                publie = True
        # Vérification canonique après publication et export JSONL par Base.
        charger_publication(destination)
        return destination
    except BaseException:
        if publie and destination.is_dir():
            try:
                with instance.connexion:
                    instance.connexion.execute("DELETE FROM projets WHERE id=?", (f"projets/{nom}",))
                try:
                    instance.exporter()
                except OSError:
                    pass
                # La publication exclusive garantit que ce dossier appartient à cette transaction.
                rollback = root / "projets" / (".annule-" + uuid.uuid4().hex)
                renommer_exclusif(destination, rollback)
                shutil.rmtree(rollback)
            except OSError:
                pass
        raise
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        if owned_base:
            instance.fermer()


def main(argv=None, *, prive=None, base: Base | None = None):
    """CLI pour le routage BENCHAB; `creer` accepte aussi les arguments historiques sans écriture Source."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    importer = bool(arguments and arguments[0] == "importer")
    if arguments and arguments[0] in {"creer", "importer"}:
        arguments.pop(0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nom")
    parser.add_argument("--titre")
    parser.add_argument("--niveau", choices=sorted(NIVEAUX), default="S")
    parser.add_argument("--objectif", choices=sorted(OBJECTIFS))
    parser.add_argument("--source", type=Path, action="append")
    parser.add_argument("--resultat", choices=sorted(RESULTATS), default="inconnu")
    parser.add_argument("--preuve-retour")
    parser.add_argument("--reference-imprimee", action="store_true")
    args = parser.parse_args(arguments)
    if importer and not args.source:
        parser.error("importer exige --source")
    if not importer and args.source:
        parser.error("--source réservé à importer")
    if args.reference_imprimee and not importer:
        parser.error("--reference-imprimee réservé à importer")
    if args.resultat != "inconnu" and not args.preuve_retour:
        parser.error("un résultat physique exige --preuve-retour")
    try:
        path = creer(
            prive or (base.prive if base else None),
            args.nom,
            args.titre,
            args.niveau,
            args.objectif,
            args.source if importer else None,
            args.resultat,
            args.preuve_retour,
            args.reference_imprimee,
            base=base,
        )
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as erreur:
        print("REFUSÉ : " + str(erreur), file=sys.stderr)
        return 2
    print("créé : " + str(path) + " ; dossier privé publié, SQLite canonique actualisé. Aucun lancement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
