"""Attribution déclarée des modèles importés, liée aux octets de la source."""

import re
from collections.abc import Mapping

CHAMPS = ("source", "auteur", "licence", "droits", "sha256")


def controler(fiche, sha_source):
    """Renvoie l'attribution et son statut documentaire RENSEIGNÉE ou NON VÉRIFIÉ.

    La fiche est une table de textes facultative. Le SHA-256 de la source est
    fourni par l'audit ; une divergence ou une fiche invalide lève ValueError.
    Aucun texte déclaré ne constitue une vérification des droits de publication.
    """
    if fiche is None:
        fiche = {}
    if not isinstance(fiche, Mapping) or fiche.keys() - set(CHAMPS):
        raise ValueError("Fiche de provenance : champs non reconnus.")
    attribution = {}
    for champ in CHAMPS:
        valeur = fiche.get(champ)
        if valeur is None:
            continue
        if not isinstance(valeur, str):
            raise ValueError(f"Fiche de provenance : {champ} doit être un texte.")
        if valeur := valeur.strip():
            attribution[champ] = valeur
    declare = attribution.get("sha256")
    if declare is not None:
        if not re.fullmatch("[0-9a-fA-F]{64}", declare):
            raise ValueError("Fiche de provenance : empreinte SHA-256 invalide.")
        if declare.lower() != sha_source:
            raise ValueError("Fiche de provenance : empreinte différente de la source auditée.")
        attribution["sha256"] = sha_source
    # Une déclaration partielle reste réutilisable mais ne devient pas une attribution vérifiée (#36).
    manquants = [champ for champ in CHAMPS if champ not in attribution]
    return {
        "fiche": attribution,
        "statut": "NON VÉRIFIÉ" if manquants else "RENSEIGNÉE",
        "manquants": manquants,
        "nature": "déclarée",
    }
