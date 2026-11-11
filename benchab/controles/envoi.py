"""Agrège les contrôles d'envoi disponibles sans valider physiquement une pièce."""

from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from benchab.controles.avertissements_slicer import controler as avertissements_slicer
from benchab.controles.commandes_machine import controler as commandes_machine
from benchab.controles.continuite_paroi import controler as continuite_paroi
from benchab.controles.identifiants_filaments import controler as identifiants
from benchab.gcode.lecture import membres_gcode_3mf
from benchab.qualite.statuts import code_statut


def controler(
    chemin, declaration=None, *, reference=None, geometrie=None, revue_locale=None, relation_reference="inconnue"
):
    """Agrège les contrôles du 3MF (mm, s, °C, mm/s) ; retourne PASS/WARN/FAIL/NON VÉRIFIÉ sans commande machine."""
    resultats = [identifiants(chemin)]
    try:
        resultats.append(commandes_machine(chemin))
    except (OSError, ValueError, KeyError, BadZipFile, ParseError) as erreur:
        resultats.append({"controle": "commandes machine au démarrage", "statut": "NON VÉRIFIÉ", "detail": str(erreur)})
    limites = []
    try:
        membres = membres_gcode_3mf(chemin)
    except (OSError, ValueError, BadZipFile):
        membres = []
    if not membres:
        resultats.append(continuite_paroi(chemin))
    for membre in membres:
        numero = int(membre.rsplit("_", 1)[-1].split(".", 1)[0])
        comparaison = None
        if reference:
            comparaison = {"fichier": str(reference), "relation": relation_reference, "plaque": numero}
        continuite = continuite_paroi(chemin, plaque=numero, reference=comparaison)
        resultats.append({**continuite, "plaque": numero})
    # Un avertissement déclaré reste visible sans déduire une compatibilité physique (#109).
    resultats.append({"controle": "avertissements natifs du slicer", **avertissements_slicer(chemin)})
    historique = None
    if declaration is None:
        limites.append("Déclaration absente : les contrôles historiques d’envoi restent NON VÉRIFIÉS.")
    else:
        from benchab.controles.controle_envoi import controler as controle_historique

        try:
            historique = controle_historique(
                chemin,
                declaration,
                reference=reference,
                geometrie=geometrie,
                revue_locale=revue_locale,
            )
        except (OSError, ValueError, KeyError, BadZipFile, ParseError) as erreur:
            historique = {
                "statut": "FAIL",
                "resultats": [
                    {
                        "controle": "contrôle historique d’envoi",
                        "statut": "FAIL",
                        "detail": str(erreur),
                    }
                ],
            }
    if any(r["statut"] == "FAIL" for r in resultats) or historique and historique["statut"] == "FAIL":
        statut = "FAIL"
    elif historique is None or historique["statut"] == "NON VÉRIFIÉ" or any(
        r["statut"] == "NON VÉRIFIÉ" for r in resultats
    ):
        statut = "NON VÉRIFIÉ"
    elif historique["statut"] == "WARN" or any(r["statut"] == "WARN" for r in resultats):
        statut = "WARN"
    else:
        statut = "PASS"
    communs = list(resultats)
    if historique is not None:
        resultats = [*historique["resultats"], *resultats]
    # Garder les preuves historiques permet leur relecture locale sans exécuter
    # une seconde fois la même analyse (docs/reference/controles.md).
    return {
        **(historique or {}),
        "statut": statut,
        "code": code_statut(statut),
        "conforme_numerique": statut == "PASS",
        "resultats": resultats,
        "controles_communs": communs,
        "zones_locales": historique.get("zones_locales") if historique else None,
        "statut_historique": historique["statut"] if historique else "NON VÉRIFIÉ",
        "limites": limites,
        "validation_physique": False,
    }
