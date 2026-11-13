"""Restitue les avertissements slice_info sans conclure sur la compatibilité."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile
from zlib import error as ErreurCompression

from benchab.archives_3mf import Archive3MF
from benchab.qualite.statuts import code_statut

_MEMBRE = "Metadata/slice_info.config"
_MAX_XML = 8 * 1024 * 1024
_TAGS_CONFIG = {"header", "plate"}
_TAGS_PLATE = {"metadata", "filament", "mixed_filament", "warning", "object", "nozzle", "layer_filament_lists"}


def _rapport(statut: str, avertissements: list[dict], raison: str | None = None) -> dict:
    """Construit un rapport numérique ; aucun champ ne décrit une validation physique."""
    resultat = {
        "statut": statut,
        "code": code_statut(statut),
        "avertissements": avertissements,
    }
    if raison:
        resultat["raison"] = raison
    return resultat


def _numero_plaque(plaque):
    """Lit l'index positif déclaré, sans unité ; None si absent ou ambigu."""
    indices = [m.get("value") for m in plaque.findall("metadata") if m.get("key") == "index"]
    if len(indices) != 1 or not indices[0] or not indices[0].isascii() or not indices[0].isdigit():
        return None
    try:
        return int(indices[0]) or None
    except ValueError:
        return None


def _extraire(racine):
    """Conserve les alertes lisibles même si une partie de l'index reste NON VÉRIFIÉE."""
    plaques = racine.findall("plate")
    if racine.tag != "config" or not plaques:
        return _rapport("NON VÉRIFIÉ", [], "structure slice_info.config inconnue")
    raisons = set()
    if any(enfant.tag not in _TAGS_CONFIG for enfant in racine):
        raisons.add("structure slice_info.config inconnue")
    entetes = racine.findall("header")
    clients = [
        (e.get("key"), e.get("value"))
        for h in entetes
        for e in h.findall("header_item")
        if e.get("key") in {"X-BBL-Client-Type", "X-ACNext-Client-Type"}
    ]
    # La valeur qualifie un type déclaré dans le format, pas l'identité du binaire (#109).
    origine = f"NON VÉRIFIÉ ({_MEMBRE})"
    # ASN emploie un autre nom pour le même type déclaré ; plusieurs déclarations restent ambiguës.
    producteur = {}
    if (
        len(entetes) == 1 and len(clients) == 1 and clients[0][1] == "slicer"
        and all(e.tag == "header_item" for e in entetes[0])
    ):
        if clients[0][0] == "X-ACNext-Client-Type":
            versions = [e.get("value") for e in entetes[0] if e.get("key") == "X-ACNext-Client-Version"]
            version = versions[0] if len(versions) == 1 else None
            # Une version inscrite dans un ZIP reste déclarative et bornée.
            producteur["producteur_declare"] = {
                "famille": "Anycubic Slicer Next",
                "version": version if version and re.fullmatch(r"[0-9.]{1,24}(?: [0-9]{1,24})?", version) else None,
                "binaire_verifie": False,
            }
        origine = f"slicer ({_MEMBRE})"
    else:
        raisons.add("origine du slicer absente, ambiguë ou inconnue")
    indices = [_numero_plaque(p) for p in plaques]
    comptes = Counter(indices)
    avertissements = []
    for plaque, numero in zip(plaques, indices, strict=True):
        if any(enfant.tag not in _TAGS_PLATE for enfant in plaque):
            raisons.add("structure de plaque inconnue")
        if numero is None:
            raisons.add("index de plaque absent ou invalide")
        elif comptes[numero] > 1:
            raisons.add("index de plaque dupliqué")
            numero = None
        for warning in plaque.findall("warning"):
            message, code = warning.get("msg"), warning.get("error_code")
            if message is None or not code or warning.get("level") is None:
                raisons.add("avertissement incomplet (msg, level ou error_code absent)")
            if message is not None and code:
                avertissements.append({"plaque": numero, "code": code, "message": message, "origine": origine})
    if raisons:
        return {**_rapport("NON VÉRIFIÉ", avertissements, "; ".join(sorted(raisons))), **producteur}
    return {**_rapport("WARN" if avertissements else "PASS", avertissements), **producteur}


def controler(chemin: str | Path, *, taille_max_xml: int = _MAX_XML) -> dict:
    """Lit les alertes d'un 3MF, budget XML en octets ; aucun réglage lu ni modifié.

    PASS pour une structure qualifiée sans alerte, WARN avec alertes, NON VÉRIFIÉ
    si la couverture est incomplète. Les codes natifs restent du texte ; une
    plaque non rattachable vaut None. Aucune compatibilité physique déduite.
    """
    try:
        with Archive3MF(chemin, taille_max_xml=taille_max_xml) as archive:
            if _MEMBRE not in archive.noms():
                return _rapport("NON VÉRIFIÉ", [], "slice_info.config absent")
            racine = archive.xml(_MEMBRE)
    except (OSError, ValueError, RuntimeError, BadZipFile, ParseError, ErreurCompression) as erreur:
        # Échecs de lecture attendus ; ne pas masquer un défaut de programmation.
        return _rapport("NON VÉRIFIÉ", [], f"lecture slice_info impossible : {erreur}")
    return _extraire(racine)
