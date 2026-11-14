"""Contrat partagé d’observation humaine privée par zone et média image."""

import math
import re
from pathlib import PurePosixPath

_SHA_HEXA = set("0123456789abcdef")
_FORMATS_SOURCE = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".heic": "HEIC"}
_ORIGINES_MEDIA = {"photo_importee", "image_deja_disponible", "NON VÉRIFIÉ"}


def _sha256(valeur):
    """Vérifie une empreinte SHA-256 hexadécimale, sans unité."""
    return isinstance(valeur, str) and len(valeur) == 64 and set(valeur) <= _SHA_HEXA


def _zone(liaison):
    """Valide la zone embarquée en mm ou conserve explicitement NON VÉRIFIÉ."""
    if not isinstance(liaison, dict):
        raise ValueError("Liaison de zone requise pour le contrat d’observation.")
    if liaison.get("statut") != "PASS":
        motif = liaison.get("motif")
        return {
            "statut": "NON VÉRIFIÉ",
            **({"motif": motif[:512]} if isinstance(motif, str) and motif else {}),
        }
    attendues = {
        "statut", "source_3mf_sha256", "plaque", "region_id_declaree", "plage_z_mm_declaree",
        "gcode_membre", "gcode_sha256", "reglages_membre", "reglages_sha256", "origine_region",
    }
    plage = liaison.get("plage_z_mm_declaree")
    if (
        set(liaison) != attendues
        or not _sha256(liaison.get("source_3mf_sha256"))
        or type(liaison.get("plaque")) is not int
        or liaison["plaque"] < 1
        or not isinstance(liaison.get("region_id_declaree"), str)
        or not liaison["region_id_declaree"].strip()
        or len(liaison["region_id_declaree"]) > 128
        or not isinstance(plage, list)
        or len(plage) != 2
        or any(type(z) not in (int, float) or not math.isfinite(z) or not 0 <= z <= 1_000_000 for z in plage)
        or plage[0] >= plage[1]
        or not isinstance(liaison.get("gcode_membre"), str)
        or (match := re.fullmatch(r"Metadata/plate_(\d+)\.gcode", liaison["gcode_membre"])) is None
        or int(match[1]) != liaison["plaque"]
        or not _sha256(liaison.get("gcode_sha256"))
        or liaison.get("reglages_membre") != "Metadata/project_settings.config"
        or not _sha256(liaison.get("reglages_sha256"))
        or not isinstance(liaison.get("origine_region"), str)
    ):
        raise ValueError("Liaison de zone PASS mal formée ou incomplète.")
    return {cle: liaison[cle] for cle in attendues if cle != "origine_region"}


def construire(candidat, *, sha_suggestion, texte_declare, origine_media):
    """Construit la preuve commune après confirmation humaine explicite.

    Entrées : fiche candidate privée, empreintes SHA-256 et texte déclaré (4 000
    caractères maximum), origine déclarée parmi les deux parcours locaux ou
    NON VÉRIFIÉ. La plage Z est en mm. Sortie : enregistrement schema 1, sans
    interprétation d’image ni attribution physique ; une zone non liée reste NV.
    """
    if (
        not isinstance(candidat, dict)
        or type(candidat.get("schema")) is not int
        or candidat["schema"] != 1
        or candidat.get("suggestion") != "CANDIDAT"
        or not _sha256(sha_suggestion)
        or not isinstance(texte_declare, str)
        or not texte_declare.strip()
        or len(texte_declare) > 4000
        or not isinstance(origine_media, str)
        or origine_media not in _ORIGINES_MEDIA
    ):
        raise ValueError("Candidat, empreintes, origine image ou confirmation humaine invalides.")
    photo = candidat.get("photo")
    if not isinstance(photo, dict):
        raise ValueError("Référence d’image privée absente du candidat.")
    nom = photo.get("source_fichier")
    extension = PurePosixPath(nom).suffix.lower() if isinstance(nom, str) else ""
    if (
        not isinstance(nom, str)
        or PurePosixPath(nom).name != nom
        or extension not in _FORMATS_SOURCE
        or not _sha256(photo.get("source_sha256"))
        or photo.get("format_apercu") != "JPEG"
        or photo.get("apercu_fichier") != "apercu.jpg"
        or not _sha256(photo.get("apercu_sha256"))
    ):
        raise ValueError("Références d’image ou empreintes invalides.")
    liaison = candidat.get("liaison")
    zone = _zone(liaison)
    return {
        "schema": 1,
        "nature": "OBSERVATION_ZONE",
        "statut": "CONFIRMATION_HUMAINE",
        "media": {
            "origine_declaree": origine_media,
            "format_source": _FORMATS_SOURCE[extension],
            "source_sha256": photo["source_sha256"],
            "apercu_sha256": photo["apercu_sha256"],
        },
        "zone": zone,
        "confirmation": {
            "suggestion_sha256": sha_suggestion,
            "observation_declaree": texte_declare.strip(),
        },
        # La confirmation du récit n’évalue ni une hypothèse ni un réglage.
        "portee": {
            "hypothese": "NON ÉVALUÉE",
            "mesure_physique": "NON VÉRIFIÉE",
            "reglage_manuel": "NON APPLIQUÉ",
        },
        "limite": (
            "L’origine du média et l’observation sont déclarées par une personne. "
            "Aucune zone n’est reconnue dans les pixels ; cela ne prouve ni défaut, impression ni cause physique."
        ),
    }
