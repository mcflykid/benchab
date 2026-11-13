"""Vérifie les explorations de forme et les promotions déclarées en candidates."""

import argparse
import json
import re
from pathlib import Path

from benchab.chemins import atelier_prive, sans_lien
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.qualite.empreintes import sha256_fichier

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")


def _preuve(racine, fiche):
    """Contrôle une référence relative et son empreinte SHA-256, sans modifier la preuve."""
    if not isinstance(fiche, dict) or set(fiche) != {"fichier", "sha256"}:
        raise ValueError("Chaque source paramétrée et aperçu doit déclarer fichier et sha256.")
    relatif = fiche["fichier"]
    sha = fiche["sha256"]
    if (not isinstance(relatif, str) or not relatif or Path(relatif).is_absolute()
            or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)):
        raise ValueError("Chemin relatif et empreinte SHA-256 complets requis.")
    p = sans_lien(racine / relatif, racine=racine, strict=True)
    if not p.is_file() or sha256_fichier(p) != sha:
        raise ValueError(f"Preuve absente ou empreinte périmée : {relatif}")
    return p


def verifier(racine, variantes):
    """Renvoie les explorations et promotions valides d’un registre privé borné.

    Les identifiants sont sans unité. Les parents précèdent leurs enfants.
    Une candidate exige une décision VALIDÉE et des preuves aux empreintes
    actuelles. Lève ValueError sur incohérence et ne modifie aucun fichier.
    """
    racine = sans_lien(racine, strict=True)
    if not isinstance(variantes, list) or len(variantes) > 1000:
        raise ValueError("Le registre doit être une liste d’au plus 1 000 variantes.")
    identifiants = set()
    explorations, candidates, nettoyages = [], [], []
    entrees = set()
    attendus = {"id", "parent", "changement", "source", "apercu", "decision", "raison"}
    for variante in variantes:
        if not isinstance(variante, dict) or not attendus <= variante.keys() <= attendus | {"candidate"}:
            raise ValueError("Format de variante invalide.")
        ident, parent = variante["id"], variante["parent"]
        if not isinstance(ident, str) or not _ID.fullmatch(ident) or ident in identifiants:
            raise ValueError("Identifiant de variante unique requis.")
        if parent is not None and (not isinstance(parent, str) or parent not in identifiants):
            raise ValueError("Le parent doit être déclaré avant la variante.")
        identifiants.add(ident)
        decision, raison = variante["decision"], variante["raison"]
        if (not isinstance(variante["changement"], str) or not variante["changement"].strip()
                or decision not in ("EN ATTENTE", "VALIDÉE", "À AJUSTER", "ABANDONNÉE")
                or not isinstance(raison, str) or (decision != "EN ATTENTE" and not raison.strip())):
            raise ValueError("Changement demandé, décision et raison cohérentes requis.")
        entrees.add(_preuve(racine, variante["source"]))
        apercu = variante["apercu"]
        if apercu is not None:
            entrees.add(_preuve(racine, apercu))
        candidate = variante.get("candidate")
        if candidate is not None:
            if decision != "VALIDÉE" or apercu is None:
                raise ValueError("Une candidate exige une variante VALIDÉE et un aperçu identifié.")
            if not isinstance(candidate, str) or not _ID.fullmatch(candidate) or candidate in candidates:
                raise ValueError("Identifiant de candidate unique requis.")
            if len(candidates) >= 3:
                raise ValueError("Le registre accepte au plus trois candidates.")
            candidates.append(candidate)
        else:
            explorations.append(ident)
            if decision == "ABANDONNÉE":
                nettoyages.append({"variante": ident, "action": "Examiner les exports dérivés, conserver les preuves"})
    return {
        "statut": "PASS", "explorations": explorations, "candidates": candidates,
        "propositions_nettoyage": nettoyages, "variantes": variantes, "preuves": sorted(map(str, entrees)),
    }


def main(argv=None, *, prive=None):
    """Publie un rapport neuf d’exploration dans l’atelier privé et renvoie son code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registre", type=Path)
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        atelier = atelier_prive(prive)
        registre = sans_lien(args.registre, strict=True)
        if not registre.is_relative_to(atelier) or not registre.is_file() or registre.stat().st_size > 1024**2:
            raise ValueError("Registre régulier de 1 Mio au plus dans l’atelier choisi requis.")
        sortie = destination_neuve(args.sortie, prive=atelier, temporaire=False)
        rapport = verifier(registre.parent, json.loads(registre.read_text(encoding="utf-8")))
        ecrire_json_neuf(sortie, rapport, entrees=(registre, *rapport["preuves"]))
    except (OSError, ValueError) as erreur:
        parser.error(str(erreur))
    print(json.dumps({"statut": rapport["statut"], "rapport": str(sortie)}, ensure_ascii=False))
    return 0
