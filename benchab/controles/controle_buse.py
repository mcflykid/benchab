"""Contrôle optionnel de diamètre déclaré et d’exigence de dureté embarquée."""

import math

from benchab import reglages


def _nombre_positif(valeur):
    """Lit un nombre fini strictement positif ; aucune unité n’est convertie."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float, str)):
        return None
    try:
        nombre = float(valeur)
    except (OverflowError, ValueError):
        return None
    return nombre if math.isfinite(nombre) and nombre > 0 else None


def _resultat(nom, statut, detail, source):
    """Construit un résultat d’envoi optionnel ; il n’émet aucune commande machine."""
    return {"controle": nom, "statut": statut, "detail": detail, "source": source, "obligatoire": False}


def controler(parametres, declaration, extrudeurs):
    """Compare la buse du 3MF à la déclaration optionnelle, par diamètre mm et HRC par filament.

    Entrées : réglages typés du 3MF, déclaration d’envoi et numéros d’extrudeur actifs (1-indexés).
    Le booléen facultatif buse.trempee décrit la buse montée telle que déclarée.
    Sortie : résultats PASS/WARN/NON VÉRIFIÉ, sans inférer abrasivité ou compatibilité physique.
    Invariant : sans le bloc optionnel `buse`, aucun ancien rapport n’est modifié.
    """
    if not isinstance(declaration, dict) or "buse" not in declaration:
        return []
    contrat = declaration.get("buse")
    contrat_valide = (
        type(contrat) is dict
        and set(contrat) <= {"diametre_mm", "trempee"}
        and ("trempee" not in contrat or type(contrat["trempee"]) is bool)
        and type(contrat.get("diametre_mm")) in (int, float)
        and _nombre_positif(contrat.get("diametre_mm")) is not None
    )
    resultats = []

    diametre = None
    try:
        valeur_nozzle = parametres.get("nozzle_diameter")
        definition = reglages.definition("nozzle_diameter", valeur=valeur_nozzle)
        if (definition.get("type") == "diametre_buse"
                and type(valeur_nozzle).__name__ in definition.get("formes", [])):
            diametre = _nombre_positif(reglages.valeur_unique(parametres, "nozzle_diameter"))
    except (TypeError, ValueError):
        pass
    declare = None
    if contrat_valide:
        valeur_declaree = contrat.get("diametre_mm")
        if not isinstance(valeur_declaree, bool) and isinstance(valeur_declaree, (int, float)):
            declare = _nombre_positif(valeur_declaree)
    if not contrat_valide or diametre is None or declare is None:
        resultats.append(_resultat(
            "diamètre de buse déclaré", "NON VÉRIFIÉ",
            "Contrat buse absent ou malformé, ou diamètre unique typé du 3MF indisponible ou ambigu.",
            "3MF: nozzle_diameter ; déclaration: buse.diametre_mm (mm)",
        ))
    else:
        identique = math.isclose(diametre, declare, rel_tol=0, abs_tol=1e-9)
        resultats.append(_resultat(
            "diamètre de buse déclaré", "PASS" if identique else "WARN",
            f"3MF {diametre:g} mm ; déclaration {declare:g} mm. Écart de déclaration, sans mesure de la buse montée.",
            "3MF typé: nozzle_diameter ; déclaration d’envoi: buse.diametre_mm",
        ))

    trempee = contrat.get("trempee") if contrat_valide else None
    resultats[0]["buse_montee"] = {
        True: "trempée déclarée",
        False: "non trempée déclarée",
        None: "non déclarée",
    }[trempee]
    buse_hrc = None
    try:
        valeur_hrc = parametres.get("nozzle_hrc")
        definition_hrc = reglages.definition("nozzle_hrc", valeur=valeur_hrc)
        if (definition_hrc.get("type") == "chaine"
                and type(valeur_hrc).__name__ in definition_hrc.get("formes", [])):
            buse_hrc = _nombre_positif(reglages.valeur_unique(parametres, "nozzle_hrc"))
    except (TypeError, ValueError):
        pass
    identites = parametres.get("filament_settings_id")
    exigences = parametres.get("required_nozzle_HRC")
    for extrudeur in extrudeurs:
        nom = f"HRC buse du profil 3MF vs exigence (filament {extrudeur})"
        indice = extrudeur - 1 if type(extrudeur) is int and extrudeur > 0 else None
        exigence = None
        if (indice is not None and isinstance(identites, list) and indice < len(identites)
                and all(isinstance(identite, str) and identite.strip() for identite in identites)
                and isinstance(exigences, list) and len(exigences) == len(identites)
                and reglages.definition("required_nozzle_HRC", valeur=exigences).get("type") == "liste_filament"):
            brute = reglages.valeur_brute_indice(
                parametres, "required_nozzle_HRC", indice, lecteur="index_exact"
            )
            exigence = _nombre_positif(brute) if isinstance(brute, str) else None
        if not contrat_valide or exigence is None or buse_hrc is None:
            resultats.append(_resultat(
                nom, "NON VÉRIFIÉ",
                "Contrat invalide, exigence HRC ou dureté de buse embarquée absente, nulle ou ambiguë.",
                "3MF typé: required_nozzle_HRC[filament] et nozzle_hrc (HRC)",
            ))
        else:
            ok = buse_hrc >= exigence
            resultats.append(_resultat(
                nom, "PASS" if ok else "WARN",
                f"Buse embarquée au profil 3MF {buse_hrc:g} HRC ; exigence embarquée {exigence:g} HRC. "
                "La valeur ne mesure ni la buse montée ni sa compatibilité physique.",
                "3MF typé: nozzle_hrc et required_nozzle_HRC (HRC)",
            ))
    return resultats
