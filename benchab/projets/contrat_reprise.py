"""Contrat de reprise sélective : distinguer fichiers actifs et préparations résumées, sans réparer les originaux."""

import hashlib
import json
import re
from pathlib import Path

from benchab.qualite.empreintes import sha256_fichier

DECISIONS = {"CONSERVÉ_ACTIF", "RÉSUMÉ_HISTORIQUE", "ÉCARTÉ_LOT5", "CONVERTI_CONNAISSANCE"}


def empreinte_origine(qualite):
    """Empreinte SHA-256 du JSON source canonique UTF-8 ; aucune unité, aucun contenu source modifié."""
    contenu = json.dumps(qualite, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(contenu.encode()).hexdigest()


def controler(donnees, projet, verifier_fichier):
    """Vérifie la projection privée et ses fichiers ; renvoie les Pnn résumées vérifiées ou lève FAIL.

    Entrées : fiche et dossier projet, callback de contrôle des fichiers/SHA ; aucune unité.
    Ne retire aucune protection et n'établit jamais la réimprimabilité ni une validation physique.
    """
    contrat = donnees.get("migration", {}).get("contrat_natif")
    if contrat is None:
        return {}
    origine = donnees.get("originebrute", {}).get("qualite")
    if not isinstance(contrat, dict) or contrat.get("schema") != 1 or not isinstance(origine, dict):
        raise ValueError("Contrat de reprise invalide ou origine absente")
    if empreinte_origine(origine) != contrat.get("origine_qualite_sha256"):
        raise ValueError("Contrat de reprise : empreinte de l'origine divergente")
    declarations = contrat.get("declarations")
    if not isinstance(declarations, list) or any(not isinstance(d, dict) for d in declarations):
        raise ValueError("Contrat de reprise : déclarations absentes ou invalides")
    classes = {}
    source_projet = donnees.get("migration", {}).get("source", "")
    for d in declarations:
        chemin, decision = d.get("source_originale"), d.get("decision")
        if not isinstance(chemin, str) or decision not in DECISIONS:
            raise ValueError("Contrat de reprise : sort absent, inconnu ou à arbitrer")
        classes.setdefault(chemin, set()).add(decision)
        # Le contrat conserve l'orthographe Source ; deux chemins sont des alias
        # seulement sous la même racine de projet déclarée (docs/reference/donnees.md).
        prefixe_source = source_projet.rstrip("/") + "/" if isinstance(source_projet, str) else ""
        if prefixe_source and chemin.startswith(prefixe_source):
            classes.setdefault(chemin[len(prefixe_source) :], set()).add(decision)
        if decision != "CONSERVÉ_ACTIF" and not d.get("raison"):
            raise ValueError("Contrat de reprise : décision sans raison")
    ancien = origine.get("suivi", {})
    requis = set(ancien.get("travail_en_cours", {}).get("fichiers", []))
    requis.update(ancien.get("sources_necessaires", []))
    requis.update(ancien.get("preuves_utiles", []))
    references = []
    for cle in ("references_imprimees", "references_historiques"):
        for ref in ancien.get(cle, []):
            references.extend(f["chemin"] for f in ref.get("fichiers", []))
    for chemin in references:
        requis.add(chemin)
        if "CONSERVÉ_ACTIF" not in classes.get(chemin, set()):
            raise ValueError("Contrat de reprise : référence physique écartée : " + chemin)
    if requis - classes.keys():
        raise ValueError(
            "Contrat de reprise : chemins source sans décision : " + ", ".join(sorted(requis - classes.keys()))
        )
    actifs = contrat.get("fichiers_actifs")
    if not isinstance(actifs, list):
        raise ValueError("Contrat de reprise : fichiers actifs absents")
    chemins_actifs = set()
    for item in actifs:
        if not isinstance(item, dict) or type(item.get("gel")) is not bool:
            raise ValueError("Contrat de reprise : fichier actif invalide")
        if item.get("chemin") in chemins_actifs:
            raise ValueError("Contrat de reprise : fichier actif dupliqué")
        chemins_actifs.add(item.get("chemin"))
        verifier_fichier(
            item.get("chemin"),
            item.get("sha256") if item["gel"] else None,
            sha_requis=item["gel"],
            libelle="contrat natif actif",
        )
    prefixe = "projets/" + Path(projet).name + "/"
    for copie in donnees.get("fichiers_copies", []):
        destination = copie.get("destination", "")
        if destination.startswith(prefixe) and destination[len(prefixe) :] not in chemins_actifs:
            raise ValueError("Contrat de reprise : copie importée sans contrôle actif : " + destination)
    resumes = contrat.get("preparations_historiques", {})
    if not isinstance(resumes, dict):
        raise ValueError("Contrat de reprise : préparations résumées invalides")
    for ident, resume in resumes.items():
        if not re.fullmatch(r"P[0-9]{2,}", ident) or not isinstance(resume, dict):
            raise ValueError("Contrat de reprise : identifiant de résumé invalide")
        # Le résumé ne dispense jamais de la conservation du JSON figé exact
        # ni des artefacts explicitement importés (docs/reference/donnees.md).
        from benchab.qualite.publication import relatif

        fiche = relatif(projet, Path("preparations") / ident / "PREPARATION.json")
        if sha256_fichier(fiche) != resume.get("sha256_original"):
            raise ValueError("Contrat de reprise : fiche résumée altérée : " + ident)
        if resume.get("statut") != "historique résumé ; réimpression non établie":
            raise ValueError("Contrat de reprise : statut du résumé invalide : " + ident)
        for artefact in resume.get("artefacts", []):
            decisions = classes.get(artefact.get("source_originale"), set())
            if artefact.get("decision") not in DECISIONS or artefact.get("decision") not in decisions:
                raise ValueError("Contrat de reprise : sort d'artefact incohérent : " + ident)
            if "CONSERVÉ_ACTIF" in decisions and artefact["decision"] != "CONSERVÉ_ACTIF":
                raise ValueError("Contrat de reprise : artefact importé déclassé : " + ident)
    return resumes
