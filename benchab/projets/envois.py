"""Associations d'archives aux préparations, distinctes d'une impression physique.

La fiche et son inscription initiale restent figées. Les associations ultérieures
sont des événements techniques dans la base ; aucune réussite physique n'est déduite.
"""

import hashlib
import json
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from benchab.donnees.base import Base
from benchab.fabrication.preparation import commandes, comparer_envoi, gcodes, identifiant, verifier_gcodes
from benchab.projets.preparations import enregistrer as inscrire_fiche
from benchab.qualite.ecritures import PublicationNeuve, _sans_lien
from benchab.qualite.empreintes import sha256_fichier

TYPE = "association_fichier_preparation"
NAMESPACE = uuid.UUID("51538364-e2d6-4287-b760-56f1638fca3d")


def _fiche(base, projet, ident):
    identifiant(ident)
    if not Path(projet).is_absolute():
        projet = base.prive / projet
    projet = _sans_lien(projet)
    if not projet.is_relative_to(base.prive.resolve()):
        raise ValueError("Le projet doit rester dans base.prive.")
    chemin = projet / "preparations" / ident / "PREPARATION.json"
    reference = inscrire_fiche(base, chemin)
    fiche = json.loads(chemin.read_text())
    return chemin, reference, fiche


def _evenements(connexion, reference):
    lignes = connexion.execute(
        "SELECT donnees_json FROM decisions WHERE projet_id=? "
        "AND json_extract(donnees_json, '$.type')=? "
        "AND json_extract(donnees_json, '$.preparation_id')=?",
        (reference["projet_id"], TYPE, reference["preparation_id"]),
    )
    return sorted((json.loads(r[0]) for r in lignes), key=lambda e: e["ordre"])


def _fusionner(fiche, evenements):
    fiche = json.loads(json.dumps(fiche))
    for evenement in evenements:
        envoi = evenement["envoi"]
        if fiche.get("envoi"):
            fiche.setdefault("envois_supplementaires", []).append(envoi)
        else:
            fiche["envoi"] = envoi
    if evenements:
        fiche["statut"] = "envoi déclaré — démarrage et résultat physique non vérifiés"
    return fiche


def lire_fiche(base: Base, projet, ident):
    _, reference, fiche = _fiche(base, projet, ident)
    return _fusionner(fiche, _evenements(base.connexion, reference))


def _archiver(base, fichier):
    if not Path(fichier).is_absolute():
        fichier = base.prive / fichier
    fichier = _sans_lien(fichier)
    if not fichier.is_file() or fichier.suffix.lower() not in {".3mf", ".gcode"}:
        raise ValueError("Fichier G-code ou 3MF régulier requis.")
    empreinte = sha256_fichier(fichier)
    if fichier.suffix.lower() == ".3mf" and not zipfile.is_zipfile(fichier):
        raise ValueError("Archive 3MF illisible.")
    if zipfile.is_zipfile(fichier):
        mouvements = gcodes(fichier)
    else:
        contenu = fichier.read_bytes()
        mouvements = {"1": {"sha256": hashlib.sha256(contenu).hexdigest(), "commandes_sha256": commandes(contenu)}}
    verifier_gcodes(mouvements)
    dossier = _sans_lien(base.prive / "archives/envois")
    if fichier.is_relative_to((base.prive / "archives").resolve()):
        if sha256_fichier(fichier) != empreinte:
            raise ValueError("Source d'envoi modifiée pendant analyse.")
        return fichier, empreinte, mouvements
    dossier.mkdir(parents=True, exist_ok=True)
    for indice in range(1000):
        suffixe = "" if indice == 0 else f"-{indice:03d}"
        copie = _sans_lien(dossier / (empreinte + suffixe + fichier.suffix.lower()))
        if copie.exists():
            if sha256_fichier(copie) == empreinte:
                break
            continue
        with PublicationNeuve([fichier], [copie]) as publication:
            shutil.copyfile(fichier, publication.temporaire(copie))
            if sha256_fichier(publication.temporaire(copie)) != empreinte:
                raise ValueError("Copie de l'archive incohérente.")
            publication.publier()
        break
    else:
        raise ValueError("Trop de collisions d'archives ; aucune copie remplacée.")
    if sha256_fichier(fichier) != empreinte:
        raise ValueError("Source d'envoi modifiée pendant analyse.")
    return copie, empreinte, mouvements


def enregistrer(base: Base, projet, ident, fichier, *, ajouter=False, note=""):
    chemin, reference, fiche = _fiche(base, projet, ident)
    copie, empreinte, mouvements = _archiver(base, fichier)
    comparaison = comparer_envoi(mouvements, fiche["gcode"])
    date = datetime.now().astimezone().isoformat()
    envoi = {
        "date": date,
        "fichier": copie.relative_to(base.prive.resolve()).as_posix(),
        "nom_original": Path(fichier).name,
        "sha256": empreinte,
        "gcode": mouvements,
        "comparaison": comparaison,
        "note": note,
        "demarrage_physique_verifie": False,
        "resultat_physique_verifie": False,
    }
    with base.transaction() as connexion:
        connexion.execute("BEGIN IMMEDIATE")
        evenements = _evenements(connexion, reference)
        actuelle = _fusionner(fiche, evenements)
        if actuelle.get("envoi") and not ajouter:
            raise ValueError("Un envoi est déjà enregistré ; ajout explicite requis.")
        if ajouter and not actuelle.get("envoi"):
            raise ValueError("Ajout sans premier envoi enregistré.")
        connus = [actuelle.get("envoi")] + actuelle.get("envois_supplementaires", [])
        if any(e and e["sha256"] == empreinte for e in connus):
            raise ValueError("Ce fichier est déjà enregistré.")
        if sha256_fichier(chemin) != reference["empreinte"]:
            raise ValueError("Fiche de préparation modifiée pendant l'association.")
        evenement = {
            "type": TYPE,
            "preparation_id": reference["preparation_id"],
            "fiche_sha256": reference["empreinte"],
            "ordre": len(evenements) + 1,
            "envoi": envoi,
            "origine": "association technique de fichier ; aucune observation physique",
        }
        ident_evenement = uuid.uuid5(NAMESPACE, reference["preparation_id"] + ":" + empreinte).hex
        connexion.execute(
            "INSERT INTO decisions(id,projet_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
            (
                ident_evenement,
                reference["projet_id"],
                date,
                "Fichier associé à une préparation",
                copie.relative_to(base.prive.resolve()).as_posix(),
                json.dumps(evenement, ensure_ascii=False, allow_nan=False),
            ),
        )
    return envoi
