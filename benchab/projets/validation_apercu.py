"""Contrôle privé d’une décision visuelle rattachée à la préparation exacte."""

import json
import re
from pathlib import Path

from benchab.chemins import atelier_prive, sans_lien
from benchab.qualite.empreintes import sha256_fichier


def fiche_candidate(source, *, prive=None):
    """Renvoie la fiche obligatoire d’une candidate de projet, sinon None.

    Les chemins sont sans unité. FAIL si la fiche manque. Ne crée aucun fichier.
    """
    projets = atelier_prive(prive) / "projets"
    chemin = Path(source).resolve()
    if not chemin.is_relative_to(projets):
        return None
    segments = chemin.relative_to(projets).parts
    if len(segments) < 3 or segments[1] != "candidats":
        return None
    # Le nom complet distingue les différentes archives dans un même dossier.
    fiche = chemin.with_name(chemin.name + ".apercu.json")
    if not fiche.is_file():
        raise ValueError(f"Aperçu validé obligatoire. Créer {fiche.name} ou fournir --validation-visuelle.")
    return fiche


def verifier(source, fiche, *, prive=None):
    """Retourne PASS si la décision VALIDÉ et les deux SHA-256 sont concordants.

    Les chemins de la source, de la fiche et de son aperçu restent dans
    l’atelier. Lève ValueError si l’intention n’est pas validée. Aucune
    validation physique ou écriture de préparation.
    """
    racine = atelier_prive(prive)
    source = sans_lien(source, racine=racine, strict=True)
    fiche = sans_lien(fiche, racine=racine, strict=True)
    if not fiche.is_file() or fiche.stat().st_size > 65536:
        raise ValueError("Fiche privée de validation visuelle de 64 Kio au plus requise.")
    valeur = json.loads(fiche.read_text(encoding="utf-8"))
    if not isinstance(valeur, dict) or set(valeur) != {"schema", "decision", "source_sha256", "apercu"} or (
        type(valeur["schema"]) is not int or valeur["schema"] != 1
    ):
        raise ValueError("Fiche de validation visuelle invalide.")
    if valeur["decision"] != "VALIDÉ":
        raise ValueError("Aperçu de candidate : décision VALIDÉ requise avant le contrôle ou le tranchage.")
    reference = valeur["apercu"]
    if not isinstance(reference, dict) or set(reference) != {"fichier", "sha256"}:
        raise ValueError("Fichier d’aperçu et empreinte SHA-256 exigés.")
    relatif = reference["fichier"]
    if not isinstance(relatif, str) or not relatif or Path(relatif).is_absolute():
        raise ValueError("Le fichier d’aperçu doit être relatif à la fiche.")
    apercu = sans_lien(fiche.parent / relatif, racine=racine, strict=True)
    if len({source, fiche, apercu}) != 3:
        raise ValueError("Source, décision et aperçu doivent désigner trois fichiers distincts.")
    for fichier, attendu in ((source, valeur["source_sha256"]), (apercu, reference["sha256"])):
        if (not fichier.is_file() or not isinstance(attendu, str) or not re.fullmatch(r"[0-9a-f]{64}", attendu)
                or sha256_fichier(fichier) != attendu):
            raise ValueError("Une empreinte de la source ou de l’aperçu a changé. Refaire et valider l’aperçu.")
    return {"statut": "PASS", "source_sha256": valeur["source_sha256"], "apercu_sha256": reference["sha256"]}
