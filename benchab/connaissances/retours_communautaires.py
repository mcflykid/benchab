"""Registre public borné de signalements externes et ordre documentaire explicite."""

import copy
import json
from datetime import date
from importlib.resources import files

_NATURES = {
    "mesure locale": 0,
    "observation locale": 1,
    "fabricant": 2,
    "hypothèse externe": 3,
}
_CLES_ENTREE = {
    "id", "date_retour", "date_relecture", "source", "nature", "conditions", "observation", "limites",
}


def _date_valide(texte):
    """Valide une date documentaire ISO canonique, sans interpréter sa précision."""
    if not isinstance(texte, str):
        return False
    try:
        return date.fromisoformat(texte).isoformat() == texte
    except ValueError:
        return False


def _valider(registre):
    """Refuse les entrées publiques incomplètes, non datées ou hors des deux issues autorisées."""
    if (type(registre) is not dict or set(registre) != {"schema", "retours"}
            or type(registre["schema"]) is not int or registre["schema"] != 1):
        raise ValueError("Schéma du registre communautaire absent ou inconnu.")
    retours = registre["retours"]
    if type(retours) is not list or len(retours) != 2:
        raise ValueError("Le registre attend exactement les deux signalements relus.")
    ids = set()
    for retour in retours:
        if type(retour) is not dict or set(retour) != _CLES_ENTREE:
            raise ValueError("Entrée de retour communautaire incomplète ou non prévue.")
        identifiant = retour["id"]
        if (not isinstance(identifiant, str) or identifiant in ids
                or identifiant not in {"asnext-issue-9", "asnext-issue-4"}):
            raise ValueError("Identifiant de retour communautaire absent, dupliqué ou inconnu.")
        ids.add(identifiant)
        date_retour, date_relecture = retour["date_retour"], retour["date_relecture"]
        if not _date_valide(date_retour) or not _date_valide(date_relecture) or date_retour > date_relecture:
            raise ValueError("Dates de retour ou de relecture invalides.")
        suffixe = identifiant.rsplit("-", 1)[1]
        url_attendue = f"https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/issues/{suffixe}"
        if retour["source"] != url_attendue:
            raise ValueError("Source primaire du retour communautaire invalide.")
        if retour["nature"] != "hypothèse externe":
            raise ValueError("Tout retour externe reste une hypothèse.")
        conditions = retour["conditions"]
        if (type(conditions) is not dict or set(conditions) != {"machine", "version_logiciel", "systeme"}
                or not all(isinstance(valeur, str) and valeur.strip() for valeur in conditions.values())):
            raise ValueError("Conditions machine, version et système requises.")
        if not all(isinstance(retour[cle], str) and retour[cle].strip() for cle in ("observation", "limites")):
            raise ValueError("Observation paraphrasée et limites requises.")
    if ids != {"asnext-issue-9", "asnext-issue-4"}:
        raise ValueError("Un des deux retours communautaires est absent.")
    return registre


def lire_registre():
    """Renvoie les deux hypothèses publiques validées, sans écrire ni adopter de réglage."""
    donnees = json.loads(files("benchab").joinpath("reference/retours-communautaires.json").read_text(encoding="utf-8"))
    return tuple(copy.deepcopy(retour) for retour in _valider(donnees)["retours"])


def priorite_documentaire(nature, *, comparabilite_etablie):
    """Renvoie le rang documentaire après comparabilité ; None si les cas comparés ne sont pas comparables.

    Entrées : nature de preuve et comparabilité booléenne. Sortie : rang sans unité ou None.
    Aucun effet sur U25 ni adoption automatique d’une valeur.
    """
    if nature not in _NATURES:
        raise ValueError("nature documentaire inconnue : " + str(nature))
    if type(comparabilite_etablie) is not bool:
        raise ValueError("La comparabilité doit être établie par un booléen explicite.")
    if not comparabilite_etablie:
        return None
    return _NATURES[nature]
