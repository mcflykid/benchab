"""Analyse nominale en lecture seule des écarts Z déposés autour des supports."""

from __future__ import annotations

from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.controles.contact_support import PIECE, ContactSupport
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import plaques_gcode_archive

_MAX_ECHANTILLONS = 15_000_000
_MAX_LIGNES = 5_000_000
_MAX_LIGNE_OCTETS = 1024 * 1024
# Les rôles reconnus correspondent aux familles de support du contrôleur de colonnes.
_SUPPORTS = {"support", "support interface", "support transition"}
_AUXILIAIRES = {"wipe tower", "prime tower", "skirt", "brim", "custom"}


def _non_mesurable(sha256: str, raison: str, plaques: list[dict]) -> dict:
    """Construit un résultat sans métrique lorsque la plaque ou la couverture manque."""
    return {
        "schema": 1,
        "controle": "ecart_z_supports",
        "statut": "NON MESURABLE",
        "code": 0,
        "raison": raison,
        "source": {"sha256": sha256},
        "plaques_disponibles": plaques,
        "limites": {
            "surface_contact_physique_mm2": None,
            "portee": "Aucune mesure locale sans plaque unique et G-code lisible.",
        },
    }


def analyser(source: str | Path, plaque: int | None = None) -> dict:
    """Calcule un écart Z nominal depuis les seuls dépôts horizontaux d’une plaque.

    La source est un 3MF exact lu en flux ; plaque est son numéro sans unité.
    Sortie : NON MESURABLE, PARTIEL ou ESTIMATION, distances en mm et surfaces en
    mm². Aucun réglage déclaré ne remplace les positions G-code et aucun fichier
    ni réglage n’est modifié (docs/reference/ecart-z-supports.md).
    """
    with Archive3MF(source) as archive:
        sha256 = archive.sha256()
        try:
            plaques_brutes = plaques_gcode_archive(archive)
        except ValueError:
            return _non_mesurable(sha256, "numero_plaque_duplique", [])
        disponibles = [{"numero": n, "membre": m} for n, m in plaques_brutes]
        if plaque is None:
            if not plaques_brutes:
                return _non_mesurable(sha256, "aucune_plaque_gcode", disponibles)
            if len(plaques_brutes) > 1:
                return _non_mesurable(sha256, "choix_plaque_requis", disponibles)
            numero, membre = plaques_brutes[0]
        else:
            if type(plaque) is not int or plaque < 1:
                return _non_mesurable(sha256, "numero_plaque_invalide", disponibles)
            selection = [(n, m) for n, m in plaques_brutes if n == plaque]
            if len(selection) != 1:
                return _non_mesurable(sha256, "plaque_absente", disponibles)
            numero, membre = selection[0]

        contact = ContactSupport(max_echantillons=_MAX_ECHANTILLONS)
        analyseur = Interpreter(error_limit=64)
        exclusions = {
            "auxiliaires": 0,
            "roles_inconnus": 0,
            "trajectoires_non_lineaires": 0,
            "depots_non_horizontaux": 0,
            "depots_sans_dimensions": 0,
        }
        lignes = 0
        with archive.ouvrir_flux(membre) as flux:
            while donnees := flux.readline(_MAX_LIGNE_OCTETS + 1):
                lignes += 1
                if lignes > _MAX_LIGNES:
                    contact.arret = "budget_lignes"
                    while flux.read(64 * 1024):
                        pass
                    break
                if len(donnees) > _MAX_LIGNE_OCTETS:
                    contact.arret = "budget_ligne_octets"
                    while flux.read(64 * 1024):
                        pass
                    break
                ligne = donnees.decode("utf-8", errors="strict")
                mouvement = analyseur.feed_line(ligne)
                if mouvement is None or mouvement.get("deposited", 0) <= 0 or mouvement.get("deposited_length", 0) <= 0:
                    continue
                role = mouvement.get("type")
                role_normalise = role.strip().lower() if isinstance(role, str) else ""
                if role_normalise in _AUXILIAIRES:
                    exclusions["auxiliaires"] += 1
                    continue
                if role_normalise in _SUPPORTS or role_normalise in PIECE:
                    if mouvement.get("op") in {"G2", "G3"} or mouvement.get("trajectory") != "lineaire":
                        exclusions["trajectoires_non_lineaires"] += 1
                        continue
                    debut, fin = mouvement.get("spatial_start"), mouvement.get("spatial_end")
                    if debut is None or fin is None or debut[2] != fin[2]:
                        exclusions["depots_non_horizontaux"] += 1
                        continue
                    # Compter aussi les dimensions présentes mais inexploitables.
                    absents_avant = contact.absents
                    contact.ajouter(mouvement, analyseur.metadata)
                    exclusions["depots_sans_dimensions"] += contact.absents - absents_avant
                else:
                    exclusions["roles_inconnus"] += 1

        if analyseur.errors:
            resultat = contact.resultat(erreurs=analyseur.errors)
        else:
            resultat = contact.resultat()
        statut = resultat["statut"]
        if exclusions["roles_inconnus"] or any(
            exclusions[k] for k in ("trajectoires_non_lineaires", "depots_non_horizontaux", "depots_sans_dimensions")
        ):
            if statut == "ESTIMATION":
                statut = "PARTIEL"
        if contact.arret or analyseur.errors:
            statut = "PARTIEL"
        if statut == "NON_MESURABLE":
            rapport = _non_mesurable(
                sha256,
                contact.arret or ("erreurs_parseur" if analyseur.errors else "aucun_ecart_observable"),
                disponibles,
            )
            rapport["plaque"] = {"numero": numero, "membre": membre}
            rapport["exclusions"] = exclusions
            rapport["diagnostics_parseur"] = analyseur.errors
            rapport["budgets"] = {
                "echantillons_max": _MAX_ECHANTILLONS,
                "echantillons_utilises": contact.echantillons,
                "arret": contact.arret,
            }
            return rapport
        return {
            "schema": 1,
            "controle": "ecart_z_supports",
            "statut": statut,
            "code": 0,
            "source": {"sha256": sha256},
            "plaque": {"numero": numero, "membre": membre},
            "ecart_z_local_mm": resultat["ecart_z_local_mm"],
            "projection_interface_estimee_mm2": resultat["projection_interface_estimee_mm2"],
            "couverture": resultat["couverture"],
            "exclusions": exclusions,
            "diagnostics_parseur": analyseur.errors,
            "budgets": resultat["budgets"],
            "methode": resultat["methode"],
            "limites": {
                "surface_contact_physique_mm2": None,
                "portee": "Projection nominale XY ; le minimum d’écart Z vient des dépôts linéaires horizontaux.",
                "dimensions": (
                    "HEIGHT et WIDTH doivent être explicites dans le G-code ; "
                    "les réglages déclarés ne les remplacent pas."
                ),
                "roles_auxiliaires": "Support, Support interface et Support transition ; rôles auxiliaires exclus.",
                "roles_inconnus": "Exclus et signalés ; un résultat partiel ne décrit que la portion reconnue.",
                "arcs": "Arcs et trajectoires non linéaires exclus, sans approximation par corde.",
                "physique": "Ne mesure ni contact, retrait, déformation, adhérence ni tenue physique.",
            },
        }
