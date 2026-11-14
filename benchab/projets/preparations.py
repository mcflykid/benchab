"""Enregistre les fiches de préparation privées dans la base BENCHAB."""

import hashlib
import json
import re
import stat
import uuid
from pathlib import Path

from benchab.donnees.base import Base

_NAMESPACE = uuid.UUID("dd5540e4-b583-47a2-aee0-c95773d1b635")
_NOM_PREPARATION = re.compile(r"P[0-9]+\Z")


def valider_nom(valeur: str) -> str:
    """Contrat commun à la création sur disque et à l'inscription dans la base."""
    if not isinstance(valeur, str) or _NOM_PREPARATION.fullmatch(valeur) is None:
        raise ValueError("Identifiant de préparation invalide : format Pnn requis.")
    return valeur


def identifiant(projet_id: str, nom: str) -> str:
    """Identité partagée entre inscription native et migration des fiches figées."""
    valider_nom(nom)
    return uuid.uuid5(_NAMESPACE, f"{projet_id}\0{nom}").hex


def _lire_fiche(base: Base, chemin: str | Path) -> tuple[Path, dict, str, str]:
    brut = Path(chemin).expanduser()
    if not brut.is_absolute():
        brut = base.prive / brut
    from benchab.chemins import sans_lien

    chemin_fiche = sans_lien(brut, strict=True)
    if not chemin_fiche.is_relative_to(base.prive.resolve()):
        raise ValueError("La fiche de préparation doit rester dans base.prive.")
    if not stat.S_ISREG(chemin_fiche.stat().st_mode):
        raise ValueError("La fiche doit être un fichier ordinaire.")
    avant = chemin_fiche.stat()
    contenu = chemin_fiche.read_bytes()
    apres = chemin_fiche.stat()
    if (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns) != (
        apres.st_dev,
        apres.st_ino,
        apres.st_size,
        apres.st_mtime_ns,
        apres.st_ctime_ns,
    ):
        raise ValueError("La fiche a changé pendant sa lecture.")
    fiche = json.loads(contenu)
    if not isinstance(fiche, dict):
        raise ValueError("La fiche doit contenir un objet JSON.")
    identifiant = fiche.get("id")
    valider_nom(identifiant)
    return chemin_fiche, fiche, hashlib.sha256(contenu).hexdigest(), identifiant


def _id_projet(base: Base, chemin_fiche: Path, fiche: dict, projet_id: str | None) -> str:
    explicite = projet_id if projet_id is not None else fiche.get("projet_id")
    if explicite is not None:
        if not isinstance(explicite, str) or not explicite.strip():
            raise ValueError("projet_id doit être une chaîne non vide.")
        return explicite

    relatif = chemin_fiche.relative_to(base.prive.resolve())
    parents = relatif.parts[:-1]
    if "preparations" in parents:
        parents = parents[: parents.index("preparations")]
    if not parents:
        raise ValueError("Impossible de dériver projet_id de la position relative de la fiche.")
    return Path(*parents).as_posix()


def _json_stable(valeur: dict) -> str:
    return json.dumps(valeur, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def enregistrer(base: Base, fiche: str | Path, envoi=None, *, projet_id: str | None = None) -> dict:
    """Insère une fiche, rend les doublons identiques idempotents et refuse tout remplacement implicite."""
    if not isinstance(base, Base):
        raise TypeError("Une instance Base BENCHAB explicite est requise.")
    chemin_fiche, donnees, empreinte, nom = _lire_fiche(base, fiche)
    ident_projet = _id_projet(base, chemin_fiche, donnees, projet_id)
    if envoi is not None:
        envoi_json = json.dumps(envoi, ensure_ascii=False, allow_nan=False)
        champ_envoi = "envoi" if donnees.get("envoi") is None else "enregistrement_envoi"
        donnees[champ_envoi] = json.loads(envoi_json)
    donnees_json = _json_stable(donnees)
    ident_preparation = identifiant(ident_projet, nom)
    origine = chemin_fiche.relative_to(base.prive.resolve()).as_posix()
    nom_projet = Path(ident_projet).name or ident_projet
    date = donnees.get("date")
    if date is not None and not isinstance(date, str):
        raise ValueError("La date de préparation doit être une chaîne ou absente.")

    with base.transaction() as connexion:
        projet = connexion.execute("SELECT nom, origine FROM projets WHERE id = ?", (ident_projet,)).fetchone()
        if projet is None:
            connexion.execute(
                "INSERT INTO projets (id, nom, origine, etat, donnees_json) VALUES (?, ?, ?, ?, ?)",
                (ident_projet, nom_projet, origine, "préparation", "{}"),
            )
        existante = connexion.execute(
            "SELECT id, date, empreinte, donnees_json FROM preparations WHERE projet_id = ? AND nom = ?",
            (ident_projet, nom),
        ).fetchone()
        if existante is not None:
            if existante[0] != ident_preparation or existante[2] != empreinte or existante[3] != donnees_json:
                raise ValueError("Fiche de préparation déjà enregistrée avec un contenu différent.")
            return {
                "projet_id": ident_projet,
                "preparation_id": ident_preparation,
                "empreinte": empreinte,
                "idempotent": True,
            }
        connexion.execute(
            "INSERT INTO preparations (id, projet_id, nom, date, empreinte, donnees_json) VALUES (?, ?, ?, ?, ?, ?)",
            (ident_preparation, ident_projet, nom, date, empreinte, donnees_json),
        )
    return {
        "projet_id": ident_projet,
        "preparation_id": ident_preparation,
        "empreinte": empreinte,
        "idempotent": False,
    }
