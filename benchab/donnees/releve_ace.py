"""Conserve un relevé RFID d’ACE et compare son estimation au besoin déclaré."""

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.qualite.statuts import code_statut


def _nombre(valeur, cle, minimum=0, maximum=math.inf):
    """Valide un nombre fini inclus dans les bornes, en % ou en g."""
    if type(valeur) not in {int, float} or not math.isfinite(valeur) or not minimum <= valeur <= maximum:
        raise ValueError(f"{cle} hors limites ou non numérique.")
    return float(valeur)


def estimer(releve, *, requis_g=None, marge_g=None):
    """Renvoie un rapport de stock estimé (%, g) depuis une déclaration horodatée.

    WARN indique une marge inférieure ou égale à la marge minimale déclarée.
    Le rapport conserve NON VÉRIFIÉ pour l’incertitude de la conversion et
    ne modifie pas de bobine ni de mesure dans le registre SQLite.
    """
    obligatoires = {"position_ace", "matiere", "restant_pct", "date", "source"}
    if not isinstance(releve, dict) or not obligatoires <= releve.keys() <= obligatoires | {"masse_nominale_g"}:
        raise ValueError("Schéma du relevé ACE invalide.")
    position = releve["position_ace"]
    if type(position) is not int or not 1 <= position <= 16:
        raise ValueError("Position ACE déclarée entre 1 et 16 requise.")
    if not isinstance(releve["matiere"], str) or not releve["matiere"].strip() or len(releve["matiere"]) > 128:
        raise ValueError("Matière déclarée requise.")
    if releve["source"] not in ("lecture_cloud", "observation_utilisateur"):
        raise ValueError("La source doit être lecture_cloud ou observation_utilisateur.")
    try:
        date = datetime.fromisoformat(releve["date"])
    except (ValueError, TypeError) as erreur:
        raise ValueError("Horodatage ISO du relevé invalide.") from erreur
    if date.utcoffset() is None:
        raise ValueError("L’horodatage doit contenir un fuseau horaire.")
    pct = _nombre(releve["restant_pct"], "restant_pct", maximum=100)
    nominale = releve.get("masse_nominale_g")
    masse = _nombre(nominale, "masse_nominale_g", minimum=0.01) * (pct / 100) if nominale is not None else None
    if (requis_g is None) != (marge_g is None):
        raise ValueError("La comparaison exige la masse requise et la marge minimale, en g.")
    marge = None
    statut = "NON VÉRIFIÉ"
    if requis_g is not None:
        requis_g = _nombre(requis_g, "requis_g")
        marge_g = _nombre(marge_g, "marge_g")
        if masse is not None:
            marge = masse - requis_g
            if marge <= marge_g:
                statut = "WARN"
    return dict(
        statut=statut, code=code_statut(statut), releve=releve, nature="estimation RFID déclarée",
        masse_estimee_g=masse, masse_requise_g=requis_g, marge_minimale_g=marge_g,
        marge_estimee_g=marge, incertitude="NON VÉRIFIÉE",
    )


def main(argv=None, *, prive=None):
    """Écrit un relevé ACE privé neuf et retourne son code sans modifier le stock pesé."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("releve", type=Path)
    parser.add_argument("--requis-g", type=float)
    parser.add_argument("--marge-g", type=float)
    parser.add_argument("--sortie", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        sortie = destination_neuve(args.sortie, prive=prive)
        if args.releve.stat().st_size > 8192:
            raise ValueError("Relevé ACE hors budget de 8 Kio.")
        releve = json.loads(args.releve.read_text(encoding="utf-8"))
        rapport = estimer(releve, requis_g=args.requis_g, marge_g=args.marge_g)
        ecrire_json_neuf(sortie, rapport, entrees=(args.releve,))
    except (OSError, ValueError) as erreur:
        parser.error(str(erreur))
    print(json.dumps({"statut": rapport["statut"], "rapport": str(sortie)}, ensure_ascii=False))
    return rapport["code"]
