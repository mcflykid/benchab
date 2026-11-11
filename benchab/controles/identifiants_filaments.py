"""

Refuse les identifiants de témoin dans une préparation destinée à l'impression.

"""

import json
from zipfile import BadZipFile

from benchab.archives_3mf import Archive3MF

MARQUEUR = b"BENCHAB-temporaire-"


def _contient_marqueur(flux):
    """
    Recherche en flux, y compris quand le marqueur chevauche deux blocs.
    """
    fin = b""
    while bloc := flux.read(1024 * 1024):
        contenu = fin + bloc
        if MARQUEUR in contenu:
            return True
        fin = contenu[-len(MARQUEUR) :]
    return False


def controler(chemin):
    """Exécute controler avec les entrées déclarées et renvoie le résultat du contrôle. Réglages en
    unités natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé."""
    membres = []
    champs = []
    try:
        with Archive3MF(chemin) as archive:
            reglages = archive.reglages()
            if not isinstance(reglages, dict):
                raise ValueError("configuration projet non structurée")
            for cle, valeur in reglages.items():
                if "BENCHAB-temporaire-" in json.dumps(valeur, ensure_ascii=False):
                    champs.append(cle)
            for membre in archive.noms():
                if membre.endswith("/"):
                    continue
                with archive.ouvrir(membre) as flux:
                    if _contient_marqueur(flux):
                        membres.append(membre)
    except (OSError, BadZipFile, KeyError, ValueError, RuntimeError):
        return {
            "controle": "identifiants de filaments définitifs",
            "statut": "FAIL",
            "detail": "Archive ou configuration projet illisible ; contrôle d’envoi impossible.",
            "champs_temporaires": [],
            "membres_temporaires": [],
        }
    return {
        "controle": "identifiants de filaments définitifs",
        "statut": "FAIL" if champs or membres else "PASS",
        "champs_temporaires": sorted(champs),
        "membres_temporaires": sorted(membres),
        "detail": (
            "Identifiants de témoin interdits à l'envoi." if champs or membres else "Aucun identifiant temporaire."
        ),
    }
