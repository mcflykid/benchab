"""Distingue transitions T et lecture conditionnelle de la matrice de purge (#72)."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from zipfile import BadZipFile

from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import empreinte_3mf, iter_lignes_gcode_3mf, plaques_gcode_archive
from benchab.qualite.statuts import code_statut
from benchab.reglages import lire_matrice_purge


def analyser_lignes(lignes, reglages):
    """Compte les transitions T explicites et calcule leur somme conditionnelle en mm³.

    PASS de matrice signifie seulement que les dimensions et valeurs suivent la convention
    source. Aucun déplacement, firmware, débit ou purge physique n'est validé.
    """
    return _compter(lignes, lire_matrice_purge(reglages))


def _compter(lignes, matrice):
    """Réutilise une matrice de projet déjà analysée entre les plaques."""
    nombre = matrice["nombre_slots"]
    valeurs = matrice["valeurs"]
    lecteur = Interpreter(error_limit=128)
    paires = Counter()
    outil = None
    for ligne in lignes:
        lecteur.feed_line(ligne)
        evenement = lecteur.last_event
        if evenement is None or not evenement["command"].startswith("T") or lecteur.tool is None:
            continue
        suivant = lecteur.tool
        if outil is not None and outil != suivant:
            paire = outil, suivant
            if paire not in paires and len(paires) >= 4096:
                raise ValueError("Budget de 4 096 paires explicites dépassé.")
            paires[paire] += 1
        outil = suivant
    hors_contexte = 0
    resultats = []
    for (depart, arrivee), compte in sorted(paires.items()):
        connus = nombre is not None and 0 <= depart < nombre and 0 <= arrivee < nombre
        if not connus:
            hors_contexte += compte
        resultats.append({
            "depart": depart,
            "arrivee": arrivee,
            "nombre": compte,
            "valeur_unitaire_matrice": valeurs[depart * nombre + arrivee] if connus and valeurs is not None else None,
        })
    total = None
    if valeurs is not None and not hors_contexte:
        try:
            somme = math.fsum(r["nombre"] * r["valeur_unitaire_matrice"] for r in resultats)
            if math.isfinite(somme):
                total = somme
        except OverflowError:
            pass
    return {
        "changements_explicites": sum(paires.values()),
        "changements_hors_contexte": hors_contexte,
        "paires": resultats,
        "matrice": matrice,
        "somme_conditionnelle_matrice": total,
        "purge_physique_mm3": None,
        "validation_binaire": False,
        "validation_physique": False,
    }


def analyser(source):
    """Analyse les G-code embarqués par plaque ; lecture conditionnelle en mm³, sans modification."""
    with Archive3MF(source, taille_max_xml=8 * 1024**2) as archive:
        sha = empreinte_3mf(source)
        try:
            reglages = archive.reglages()
        except KeyError:
            reglages = None
        matrice = lire_matrice_purge(reglages)
        membres = plaques_gcode_archive(archive)
        if not membres:
            raise ValueError("Aucun G-code de plaque.")
        plaques = []
        for numero, membre in membres:
            rapport = _compter(iter_lignes_gcode_3mf(source, membre, archive=archive), matrice)
            rapport.pop("matrice")
            plaques.append({"plaque": numero, "membre": membre, **rapport})
        archive.verifier_source(source)
    valeurs = [p["somme_conditionnelle_matrice"] for p in plaques]
    total = None
    if all(v is not None for v in valeurs):
        try:
            candidat = math.fsum(valeurs)
            if math.isfinite(candidat):
                total = candidat
        except OverflowError:
            pass
    statut = "PASS" if total is not None else "NON VÉRIFIÉ"
    return {
        "schema": 1,
        "statut": statut,
        "code": code_statut(statut),
        "source_sha256": sha,
        "matrice": matrice,
        "plaques": plaques,
        "somme_conditionnelle_matrice": total,
        "volume_gcode_attribue_mm3": None,
        "purge_physique_mm3": None,
        "validation_binaire": False,
        "validation_physique": False,
        "portee": "Transitions T explicites et convention documentaire ; pas une mesure de purge.",
    }


def main(argv=None):
    """Affiche un JSON local ; code 0 calcul conditionnel disponible, 2 NON VÉRIFIÉ."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Archive tranchée locale, inchangée")
    args = parser.parse_args(argv)
    try:
        rapport = analyser(args.source)
    except (OSError, ValueError, BadZipFile, KeyError, RuntimeError) as erreur:
        statut = "NON VÉRIFIÉ"
        rapport = {"statut": statut, "code": code_statut(statut), "raison": str(erreur)}
    print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
    return rapport["code"]


if __name__ == "__main__":
    raise SystemExit(main())
