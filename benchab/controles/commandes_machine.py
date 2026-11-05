"""Limites et chauffe du démarrage : le texte réellement exécuté doit correspondre au 3MF."""

import math
import re

from benchab import reglages
from benchab.gcode.lecture import lire_texte_gcode_3mf, membres_gcode_3mf
from benchab.machine.configuration import charger

AXES = ("X", "Y", "Z", "E")
FAMILLES = {
    "M201": ("machine_max_acceleration_", "acceleration_max_mm_s2", "mm/s²"),
    "M203": ("machine_max_speed_", "vitesse_max_mm_s", "mm/s"),
    "M205": ("machine_max_jerk_", "jerk_max_mm_s", "mm/s"),
}
ACCELERATIONS = {
    "P": ("machine_max_acceleration_extruding", "acceleration_extrusion_max_mm_s2"),
    "R": ("machine_max_acceleration_retracting", "acceleration_retraction_max_mm_s2"),
    "T": ("machine_max_acceleration_travel", "acceleration_deplacement_max_mm_s2"),
}


def analyser(texte, settings, limites, *, mode="standard"):
    """Compare le démarrage : mm/s, mm/s², jerk mm/s, températures °C ;
    retourne PASS/FAIL/NON VÉRIFIÉ, sans correction. Les températures par outil exigent
    l'indice explicitement présent ; une association absente reste NON VÉRIFIÉE."""
    settings = reglages.Reglages(settings)
    lignes = []
    vus = set()
    demarrage = texte.split(";LAYER_CHANGE", 1)[0]
    selection = re.search(r"(?m)^T(\d+)\s*(?:;.*)?$", demarrage)
    outil = int(selection.group(1)) if selection else 0 if len(settings.get("filament_settings_id", [])) == 1 else None
    limites_mode = limites.get(mode, {})

    def noter(numero, commande, parametre, valeur, attendu, maximum, unite):
        connu = attendu is not None and (maximum is not None or unite == "°C")
        statut = "NON VÉRIFIÉ"
        if connu:
            statut = "PASS" if math.isclose(valeur, attendu, rel_tol=0, abs_tol=1e-6) else "FAIL"
            if maximum is not None and valeur > maximum + 1e-6:
                statut = "FAIL"
        lignes.append(
            {
                "ligne": numero,
                "commande": commande,
                "parametre": parametre,
                "observe": valeur,
                "attendu_3mf": attendu,
                "limite_config": maximum,
                "unite": unite,
                "statut": statut,
            }
        )

    def thermique(cle):
        definition = reglages.definition(cle)
        if definition["type"] == "inconnu":
            return None
        v = settings.get(cle)
        if isinstance(v, list):
            # Un outil doit correspondre à son entrée explicite ; get_at n'est pas
            # prouvé pour l'extraction G-code Anycubic de température (docs/reference/commandes-machine.md).
            v = reglages.valeur_indice(settings, cle, outil, lecteur="index_exact") if outil is not None else None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for numero, brut in enumerate(texte.splitlines(), 1):
        # Les M204 de trajectoire ne sont pas les limites initiales. La première
        # couche délimite le démarrage (docs/reference/commandes-machine.md).
        if brut.strip().startswith(";LAYER_CHANGE"):
            break
        code = brut.split(";", 1)[0].strip()
        if not code:
            continue
        commande = code.split()[0]
        if commande not in {*FAMILLES, "M204", "G9111", "M104", "M140"}:
            continue
        vus.add(commande)
        if commande in FAMILLES:
            prefixe, categorie, unite = FAMILLES[commande]
            parametres = dict(re.findall(r"([XYZE])\s*([-+]?\d+(?:\.\d+)?)", code))
            for axe, v in parametres.items():
                attendu = reglages.valeur_mode(settings, prefixe + axe.lower(), mode)
                maximum = limites_mode.get(categorie, {}).get(axe.lower())
                noter(numero, commande, axe, float(v), attendu, maximum, unite)
            for axe in set(AXES) - parametres.keys():
                lignes.append(
                    {
                        "ligne": numero,
                        "commande": commande,
                        "parametre": axe,
                        "statut": "NON VÉRIFIÉ",
                        "detail": "Axe absent de la commande initiale",
                    }
                )
            if not parametres:
                lignes.append(
                    {
                        "ligne": numero,
                        "commande": commande,
                        "statut": "NON VÉRIFIÉ",
                        "detail": "Paramètres machine absents ou non interprétables",
                    }
                )
        elif commande == "M204":
            parametres = re.findall(r"([PRTS])\s*([-+]?\d+(?:\.\d+)?)", code)
            for axe, valeur in parametres:
                cle, limite = ACCELERATIONS.get(axe, (None, None))
                attendu = reglages.valeur_mode(settings, cle, mode) if cle else None
                noter(numero, commande, axe, float(valeur), attendu, limites_mode.get(limite), "mm/s²")
            if not parametres:
                lignes.append(
                    {
                        "ligne": numero,
                        "commande": commande,
                        "statut": "NON VÉRIFIÉ",
                        "detail": "Accélération initiale absente ou non interprétable",
                    }
                )
        else:
            if commande == "G9111":
                valeurs = [
                    (nom, float(v)) for nom, v in re.findall(r"(bedTemp|extruderTemp)=([-+]?\d+(?:\.\d+)?)", code)
                ]
            else:
                valeurs = [
                    ("extruderTemp" if commande == "M104" else "bedTemp", float(v))
                    for v in re.findall(r"\bS([-+]?\d+(?:\.\d+)?)", code)
                ]
            for nom, valeur in valeurs:
                # Chaque plateau possède sa température : ne pas substituer le
                # texturé au lisse (docs/reference/commandes-machine.md).
                plateaux = {
                    "Textured PEI Plate": "textured_plate_temp_initial_layer",
                    "Smooth PEI Plate": "hot_plate_temp_initial_layer",
                    "Cool Plate": "cool_plate_temp_initial_layer",
                    "Engineering Plate": "eng_plate_temp_initial_layer",
                }
                cle = (
                    "nozzle_temperature_initial_layer"
                    if nom == "extruderTemp"
                    else plateaux.get(settings.get("curr_bed_type"))
                )
                noter(numero, commande, nom, valeur, thermique(cle) if cle else None, None, "°C")
            if not valeurs:
                lignes.append(
                    {
                        "ligne": numero,
                        "commande": commande,
                        "statut": "NON VÉRIFIÉ",
                        "detail": "Température attendue ou commandée non interprétable",
                    }
                )
    if not vus & {"G9111", "M104", "M140"}:
        lignes.append({"commande": "chauffe", "statut": "NON VÉRIFIÉ", "detail": "Aucune chauffe initiale reconnue"})
    for commande in ("M201", "M203", "M204", "M205"):
        if commande not in vus:
            lignes.append({"commande": commande, "statut": "NON VÉRIFIÉ", "detail": "Commande initiale absente"})
    statut = (
        "FAIL"
        if any(r["statut"] == "FAIL" for r in lignes)
        else "NON VÉRIFIÉ"
        if any(r["statut"] == "NON VÉRIFIÉ" for r in lignes)
        else "PASS"
    )
    return {
        "controle": "commandes machine au démarrage",
        "statut": statut,
        "mode": mode,
        "resultats": lignes,
        "portee": "Démarrage enregistré, pas les changements manuels de mode sur la machine",
        "validation_physique": False,
    }


def controler(chemin, *, mode=None):
    """Lit réglages et G-code de toutes plaques, unités mm/s, mm/s² et °C ;
    agrège les statuts, sans slicer ni machine."""
    limites = charger().get("mouvement", {})
    mode = mode or limites.get("mode_par_defaut")
    settings = reglages.lire_3mf(chemin)
    resultats = []
    for membre in membres_gcode_3mf(chemin):
        texte = lire_texte_gcode_3mf(chemin, membre)
        resultats.append({"plaque": membre, **analyser(texte, settings, limites, mode=mode)})
    statut = (
        "FAIL"
        if any(r["statut"] == "FAIL" for r in resultats)
        else "NON VÉRIFIÉ"
        if not resultats or any(r["statut"] == "NON VÉRIFIÉ" for r in resultats)
        else "PASS"
    )
    return {
        "controle": "commandes machine au démarrage",
        "statut": statut,
        "resultats": resultats,
        "validation_physique": False,
    }
