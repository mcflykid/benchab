"""Bilan conditionnel de compteurs d'entretien, sans lecture de base ni commande machine.

L'historique est fourni explicitement et reste extérieur aux projets et à SQLite.
Les événements, périodes, unités, seuils et sources doivent y être déclarés.
"""

import hashlib
import json
import math
import os
import stat
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

_PREUVES = {"MESURE", "DECLARATION_HUMAINE", "SOURCE_DOCUMENTEE"}


def _nombre(valeur):
    """Accepte un compteur réel fini et positif ou nul, jamais un booléen."""
    if type(valeur) not in (int, float) or valeur < 0:
        return False
    try:
        return math.isfinite(float(valeur))
    except OverflowError:
        return False


def _date(valeur):
    """Valide un horodatage ISO muni d'un fuseau, requis pour ordonner les événements."""
    if not isinstance(valeur, str):
        return None
    try:
        resultat = datetime.fromisoformat(valeur)
        return resultat.astimezone(timezone.utc) if resultat.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _cle(evenement):
    """Identifie un compteur par organe et mesure, sans assimiler des unités."""
    organe, compteur = evenement.get("organe"), evenement.get("compteur")
    return (organe, compteur) if all(isinstance(x, str) and x.strip() for x in (organe, compteur)) else ("", "")


def _couverture_locale(declarations, date_service, usages):
    """Qualifie une période attestée, bornée en dates ISO avec fuseau."""
    if len(declarations) != 1:
        return {"couverture": "INCONNUE", "motif_couverture": "Plusieurs périodes déclarées pour le compteur."}
    declaration = declarations[0]
    debut, fin = _date(declaration.get("debut")), _date(declaration.get("fin"))
    statut, source = declaration.get("statut"), declaration.get("source")
    if (
        debut is None or fin is None or debut >= fin
        or statut not in ("COMPLETE", "INCOMPLETE", "INCONNUE")
        or not isinstance(source, str) or not source.strip()
    ):
        return {"couverture": "INCONNUE", "motif_couverture": "Période, statut ou source invalide."}
    preuve = {cle: declaration[cle] for cle in ("organe", "compteur", "statut", "debut", "fin", "source")}
    champs = {"couverture": statut, "preuve_couverture": preuve}
    if statut != "COMPLETE":
        return {**champs, "motif_couverture": "Couverture locale incomplète ou inconnue."}
    dates = [_date(usage.get("date")) for usage in usages]
    if date_service is None or not debut <= date_service <= fin or any(
        date is None or not date_service <= date <= fin for date in dates
    ):
        return {
            **champs, "couverture": "INCONNUE",
            "motif_couverture": "Le dernier entretien et les usages retenus doivent être dans la période attestée.",
        }
    return champs


def calculer(donnees):
    """Calcule depuis événements et seuils explicitement sourcés.

    `valeur` compte en g, h ou autre unité explicitement donnée ; les statuts
    `PASS`, `WARN` et `NON VÉRIFIÉ` décrivent uniquement le compteur déclaré,
    jamais une usure ou une validation physique. Une période sourcée peut
    remplacer la couverture globale pour un compteur. Les entrées restent en mémoire.
    """
    if not isinstance(donnees, dict):
        raise ValueError("Bilan synthétique invalide.")
    evenements = donnees.get("evenements")
    seuils = donnees.get("seuils")
    if not isinstance(evenements, list) or not isinstance(seuils, list):
        raise ValueError("Événements et seuils explicites requis.")
    couvertures = donnees.get("couvertures", [])
    if not isinstance(couvertures, list):
        raise ValueError("Les couvertures par compteur doivent être une liste.")

    erreurs, uniques, conflits = [], {}, set()
    for evenement in evenements:
        if (
            not isinstance(evenement, dict)
            or not isinstance(evenement.get("id"), str)
            or not evenement["id"].strip()
        ):
            erreurs.append("Événement sans identifiant stable")
            continue
        identifiant = evenement["id"]
        empreinte = json.dumps(evenement, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        precedent = uniques.get(identifiant)
        if precedent is not None and precedent[0] != empreinte:
            conflits.update((_cle(precedent[1]), _cle(evenement)))
            erreurs.append(f"Événement contradictoire : {identifiant}")
        elif precedent is None:
            uniques[identifiant] = (empreinte, evenement)

    seuils_par_cle = defaultdict(list)
    for seuil in seuils:
        if not isinstance(seuil, dict):
            erreurs.append("Seuil invalide")
            continue
        seuils_par_cle[_cle(seuil)].append(seuil)

    evenements_par_cle = defaultdict(list)
    for _, evenement in uniques.values():
        cle = _cle(evenement)
        evenements_par_cle[cle].append(evenement)
    couvertures_par_cle = defaultdict(list)
    for couverture in couvertures:
        cle = _cle(couverture) if isinstance(couverture, dict) else ("", "")
        if cle == ("", ""):
            erreurs.append("Couverture sans organe ou compteur explicite")
        else:
            couvertures_par_cle[cle].append(couverture)
    cles = set(seuils_par_cle) | set(evenements_par_cle) | set(couvertures_par_cle)
    compteurs = []

    for organe, compteur in sorted(cles, key=lambda cle: tuple(str(x or "") for x in cle)):
        cle = (organe, compteur)
        seuil_liste = seuils_par_cle[cle]
        seuil = seuil_liste[0] if len(seuil_liste) == 1 else None
        evenements_cle = evenements_par_cle[cle]
        resultat = {
            "organe": organe,
            "compteur": compteur,
            "valeur": None,
            "unite": seuil.get("unite") if seuil else None,
            "seuil": seuil.get("maximum") if seuil else None,
            "source_seuil": seuil.get("source") if seuil else None,
            "dernier_entretien": None,
            "sources_evenements": [],
            "couverture": donnees.get("couverture"),
            "statut": "NON VÉRIFIÉ",
        }
        unite = resultat["unite"]
        seuil_valide = (
            isinstance(organe, str)
            and bool(organe.strip())
            and isinstance(compteur, str)
            and bool(compteur.strip())
            and seuil is not None
            and _nombre(seuil.get("maximum"))
            and seuil["maximum"] > 0
            and isinstance(unite, str)
            and bool(unite.strip())
            and isinstance(seuil.get("source"), str)
            and bool(seuil["source"].strip())
        )
        minimum = datetime.min.replace(tzinfo=timezone.utc)
        ordre = sorted(evenements_cle, key=lambda e: (_date(e.get("date")) or minimum, e["id"]))
        debut = next(
            (index for index in range(len(ordre) - 1, -1, -1) if ordre[index].get("type") == "entretien"), None
        )
        usages_apres = ordre[debut + 1 :] if debut is not None else []
        date_service = _date(ordre[debut].get("date")) if debut is not None else None
        if cle in couvertures_par_cle:
            # Une attestation locale invalide ne peut pas reprendre COMPLETE du fichier (#73).
            resultat.update(_couverture_locale(couvertures_par_cle[cle], date_service, usages_apres))
        valide = (
            seuil_valide
            and cle not in conflits
            and resultat["couverture"] == "COMPLETE"
            and debut is not None
            and all(_date(evenement.get("date")) is not None for evenement in evenements_cle)
            and not erreurs
        )
        cumul = Fraction(0)
        entretien = None
        if debut is not None:
            evenement_entretien = ordre[debut]
            source_entretien = (
                isinstance(evenement_entretien.get("source"), str)
                and bool(evenement_entretien["source"].strip())
                and isinstance(evenement_entretien.get("preuve"), str)
                and evenement_entretien["preuve"] in _PREUVES
                and date_service is not None
                and evenement_entretien.get("unite") == unite
            )
            if source_entretien:
                entretien = evenement_entretien["date"]
                resultat["sources_evenements"].append(
                    {cle: evenement_entretien[cle] for cle in ("id", "date", "source", "preuve")}
                )
            valide &= source_entretien
        if date_service is not None and any(
            evenement["id"] != ordre[debut]["id"] and _date(evenement.get("date")) == date_service
            for evenement in ordre
        ):
            valide = False
        for evenement in usages_apres:
            source_valide = (
                isinstance(evenement.get("source"), str)
                and bool(evenement["source"].strip())
                and isinstance(evenement.get("preuve"), str)
                and evenement["preuve"] in _PREUVES
                and _date(evenement.get("date")) is not None
                and evenement.get("unite") == unite
            )
            valide &= source_valide
            if evenement.get("type") == "usage":
                if not _nombre(evenement.get("valeur")):
                    valide = False
                elif source_valide:
                    # Comparer les quantités décimales sans erreur d’addition binaire au seuil (#73).
                    cumul += Fraction(str(evenement["valeur"]))
                resultat["sources_evenements"].append(
                    {cle: evenement[cle] for cle in ("id", "date", "source", "preuve")}
                    if source_valide
                    else {"id": evenement.get("id"), "date": evenement.get("date")}
                )
            else:
                valide = False

        resultat["dernier_entretien"] = entretien
        valide &= entretien is not None and not erreurs
        if valide:
            # Le verdict provient du cumul exact même si JSON ne peut pas l'afficher en flottant.
            resultat["statut"] = "WARN" if cumul >= Fraction(str(seuil["maximum"])) else "PASS"
            try:
                valeur = float(cumul)
            except OverflowError:
                valeur = math.inf
            if math.isfinite(valeur):
                resultat["valeur"] = valeur
            else:
                resultat["detail"] = "Cumul exact établi, mais dépasse la plage représentable en flottant."
        compteurs.append(resultat)

    statuts = {ligne["statut"] for ligne in compteurs}
    # Un WARN connu reste visible même si un autre compteur est incomplet.
    statut_global = (
        "WARN" if "WARN" in statuts else "NON VÉRIFIÉ" if not statuts or "NON VÉRIFIÉ" in statuts else "PASS"
    )
    return {"statut": statut_global, "erreurs": sorted(set(erreurs)), "compteurs": compteurs}


def _sans_doublons(paires):
    """Refuse les clés JSON répétées afin de ne pas masquer une déclaration."""
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError("Clé JSON répétée dans l'historique d'entretien.")
        resultat[cle] = valeur
    return resultat


def _reel_json(texte):
    """Refuse une constante ou un exposant qui ne peut pas rester un réel fini."""
    valeur = float(texte)
    if not math.isfinite(valeur):
        raise ValueError("Nombre JSON non fini dans l’historique d’entretien.")
    return valeur


def lire_historique(chemin, prive):
    """Lit un historique JSON privé borné et stable, puis retourne ses données et SHA-256.

    Entrées : chemin de fichier privé et racine de l'atelier, sans unité.
    Sorties : objet JSON schema 1, SHA-256 des octets lus et chemin privé relatif.
    Échec : ValueError/OSError si fichier lié, non régulier, supérieur à 8 Mio,
    modifié pendant lecture ou hors atelier. Aucune ouverture SQLite ni écriture.
    """
    from benchab.qualite.ecritures import sortie_durable

    source = sortie_durable(chemin, prive=prive)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(source, flags)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > 8 * 1024 * 1024:
            raise ValueError("L'historique doit être un JSON régulier de 8 Mio au plus.")
        brut = flux.read(8 * 1024 * 1024 + 1)
        apres = os.fstat(flux.fileno())
    courant = source.lstat()
    def identite(info):
        """Compare les attributs du fichier qui protègent contre une lecture concurrente."""
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    if len(brut) > 8 * 1024 * 1024 or identite(avant) != identite(apres) or identite(apres) != identite(courant):
        raise ValueError("L'historique est trop grand ou a changé pendant sa lecture.")
    donnees = json.loads(
        brut.decode("utf-8"),
        object_pairs_hook=_sans_doublons,
        parse_float=_reel_json,
        parse_constant=_reel_json,
    )
    if not isinstance(donnees, dict) or type(donnees.get("schema")) is not int or donnees["schema"] != 1:
        raise ValueError("L'historique doit être un objet JSON de schema 1.")
    racine = Path(prive).resolve()
    return donnees, hashlib.sha256(brut).hexdigest(), source.relative_to(racine).as_posix()


def bilan_fichier(chemin, prive):
    """Produit un bilan reproductible depuis le seul historique privé demandé.

    Entrées : historique JSON et racine privée, sans unité.
    Sortie : rapport avec empreinte d'entrée, cumuls explicitement déclarés,
    sources et statuts PASS/WARN/NON VÉRIFIÉ ; ne prédit ni usure ni état physique.
    Invariant : aucune base, donnée de projet, profil ou machine n'est modifié.
    """
    donnees, sha256, relatif = lire_historique(chemin, prive)
    rapport = calculer(donnees)
    rapport["schema"] = 1
    rapport["source"] = {"fichier_prive": relatif, "sha256": sha256}
    rapport["portee"] = (
        "Verdict conditionnel aux événements, couvertures et seuils déclarés ; "
        "aucun compteur n'est déduit du temps estimé du trancheur ; "
        "aucune validation physique ni action machine."
    )
    rapport["validation_physique"] = "NON FOURNIE"
    return rapport


def code(rapport):
    """Traduit le statut du bilan en code CLI commun : PASS 0, WARN 1, FAIL/inconnu 2."""
    from benchab.qualite.statuts import code_statut

    return code_statut(rapport["statut"])


def resumer(rapport, sortie):
    """Résume le bilan conservé en JSON privé sans exposer les événements.

    Entrées : bilan de bilan_fichier (valeurs en g, h ou unité déclarée) et
    chemin du rapport complet. Sortie : PASS/WARN/NON VÉRIFIÉ, code et
    compteurs par statut ; les détails et erreurs restent dans le rapport.
    Ne modifie ni fichier ni machine.
    """
    return {
        "statut": rapport["statut"],
        "code": code(rapport),
        "rapport": str(sortie),
        "compteurs_par_statut": dict(Counter(c["statut"] for c in rapport["compteurs"])),
        "diagnostics_dans_rapport": len(rapport["erreurs"]),
        "validation_physique": rapport["validation_physique"],
    }
