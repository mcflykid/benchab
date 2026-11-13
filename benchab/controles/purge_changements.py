"""Distingue transitions T et lecture conditionnelle de la matrice de purge (#72)."""

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from zipfile import BadZipFile

from benchab.archives_3mf import Archive3MF
from benchab.controles.depot_tour_purge import ObservationTour
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.debit import positif
from benchab.gcode.lecture import iter_lignes_gcode_3mf, plaques_gcode_archive
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.qualite.statuts import code_statut
from benchab.reglages import lire_matrice_purge


def analyser_lignes(lignes, reglages):
    """Compte les transitions T explicites et calcule leur somme conditionnelle en mm³.

    PASS de matrice signifie seulement que les dimensions et valeurs suivent la convention
    source. Aucun déplacement, firmware, débit ou purge physique n'est validé.
    """
    return _compter(lignes, lire_matrice_purge(reglages))


def _compter(lignes, matrice, *, tour=None):
    """Réutilise une matrice de projet déjà analysée entre les plaques."""
    nombre = matrice["nombre_slots"]
    valeurs = matrice["valeurs"]
    lecteur = Interpreter(error_limit=128)
    paires = Counter()
    outil = None
    for ligne in lignes:
        mouvement = lecteur.feed_line(ligne)
        evenement = lecteur.last_event
        if tour is not None:
            tour.ajouter(mouvement, evenement)
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
        unitaire = valeurs[depart * nombre + arrivee] if connus and valeurs is not None else None
        volume = compte * unitaire if unitaire is not None else None
        resultats.append({
            "depart": depart,
            "arrivee": arrivee,
            "nombre": compte,
            "valeur_unitaire_matrice": unitaire,
            # Une paire hors matrice n’efface pas les contributions calculables des autres (#72).
            "somme_conditionnelle_mm3": volume if volume is not None and math.isfinite(volume) else None,
        })
    total = None
    sommes = [r["somme_conditionnelle_mm3"] for r in resultats]
    if valeurs is not None and all(somme is not None for somme in sommes):
        try:
            somme = math.fsum(sommes)
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
        **({"observation_tour": tour.rapport(lecteur)} if tour is not None else {}),
    }


def _bilan_plafond(total, plafond, provenance):
    if plafond is None:
        return {"statut": "NON VÉRIFIÉ", "raison": "Plafond documenté absent."}
    return {
        "statut": "NON VÉRIFIÉ" if total is None else ("WARN" if total > plafond else "PASS"),
        "plafond_mm3": plafond, "source": provenance,
        **({"raison": "Somme conditionnelle indisponible."} if total is None else {}),
    }


def analyser(source, *, observer_tour=False, plafond_mm3=None, source_plafond=None):
    """Lit les transitions par plaque et leur somme conditionnelle en mm³.

    Renvoie PASS si la somme est calculable ou NON VÉRIFIÉ sinon. Le SHA,
    les réglages et les G-code proviennent du même 3MF ouvert en lecture seule.
    Sur demande, le rôle Prime tower est observé séparément par outil.
    Un plafond optionnel en mm³, avec source explicite, donne WARN en cas
    de dépassement de la somme conditionnelle par plaque ou par archive.
    Aucun résultat ne qualifie une purge physique.
    """
    plafond = positif(plafond_mm3)
    if (plafond_mm3 is not None or source_plafond is not None) and (
        plafond is None or not isinstance(source_plafond, str) or not source_plafond.strip()
    ):
        raise ValueError("Le plafond exige un volume positif fini en mm³ et une source explicite.")
    with Archive3MF(source, taille_max_xml=8 * 1024**2) as archive:
        sha = archive.sha256()
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
            tour = ObservationTour(reglages, matrice["nombre_slots"]) if observer_tour else None
            rapport = _compter(iter_lignes_gcode_3mf(source, membre, archive=archive), matrice, tour=tour)
            rapport.pop("matrice")
            rapport["bilan_plafond"] = _bilan_plafond(
                rapport["somme_conditionnelle_matrice"], plafond, source_plafond,
            )
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
    bilan_plafond = _bilan_plafond(total, plafond, source_plafond)
    if any(p["bilan_plafond"]["statut"] == "WARN" for p in plaques):
        # Un dépassement démontré sur une plaque reste visible si une autre est incomplète.
        bilan_plafond.update(statut="WARN", plaques_en_depassement=[
            p["plaque"] for p in plaques if p["bilan_plafond"]["statut"] == "WARN"
        ])
    statut = "WARN" if bilan_plafond["statut"] == "WARN" else ("PASS" if total is not None else "NON VÉRIFIÉ")
    return {
        "schema": 1,
        "statut": statut,
        "code": code_statut(statut),
        "source_sha256": sha,
        "matrice": matrice,
        "plaques": plaques,
        "somme_conditionnelle_matrice": total,
        "bilan_plafond": bilan_plafond,
        "volume_gcode_attribue_mm3": None,
        "purge_physique_mm3": None,
        "validation_binaire": False,
        "validation_physique": False,
        "portee": "Transitions T explicites et convention documentaire ; pas une mesure de purge.",
    }


def main(argv=None, *, prive=None):
    """Affiche un bilan JSON, court si un rapport privé complet est conservé.

    Lit un 3MF tranché et, sur demande, publie un JSON neuf dans l’atelier.
    Code 0 pour le calcul conditionnel disponible (mm³), 1 si dépassement
    du plafond déclaré, 2 si NON VÉRIFIÉ
    ou refus de l’écriture. Aucune purge physique n’est mesurée.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Archive tranchée locale, inchangée")
    parser.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    parser.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    parser.add_argument("--observer-tour", action="store_true", help="Observer les dépôts de rôle Prime tower")
    parser.add_argument("--plafond-mm3", type=float, help="Plafond de la somme conditionnelle")
    parser.add_argument("--source-plafond", help="Provenance du plafond")
    args = parser.parse_args(argv)
    cible = None
    try:
        if args.resume and args.sortie is None:
            raise ValueError("--resume exige --sortie pour conserver le rapport complet.")
        if args.sortie is not None:
            if args.sortie.suffix.lower() != ".json":
                raise ValueError("La sortie de purge doit être un JSON neuf.")
            cible = destination_neuve(args.sortie, prive=prive, temporaire=False)
    except (OSError, ValueError) as erreur:
        print(f"BENCHAB : rapport de purge refusé : {erreur}", file=sys.stderr)
        return 2
    try:
        rapport = analyser(
            args.source, observer_tour=args.observer_tour,
            plafond_mm3=args.plafond_mm3, source_plafond=args.source_plafond,
        )
    except (OSError, ValueError, BadZipFile, KeyError, RuntimeError) as erreur:
        statut = "NON VÉRIFIÉ"
        rapport = {"statut": statut, "code": code_statut(statut), "raison": str(erreur)}
    if cible is not None:
        try:
            ecrire_json_neuf(cible, rapport, entrees=[args.source])
        except (OSError, ValueError) as erreur:
            print(f"BENCHAB : rapport de purge refusé : {erreur}", file=sys.stderr)
            return 2
    affiche = rapport
    if args.resume:
        plaques = rapport.get("plaques", [])
        affiche = {
            "statut": rapport["statut"], "code": rapport["code"],
            "plaques": len(plaques),
            "changements_explicites": sum(p["changements_explicites"] for p in plaques),
            "somme_conditionnelle_matrice": rapport.get("somme_conditionnelle_matrice"),
            "purge_physique_mm3": None, "rapport": str(cible),
        }
        if args.plafond_mm3 is not None and "bilan_plafond" in rapport:
            affiche["bilan_plafond"] = rapport["bilan_plafond"]
        if "raison" in rapport:
            affiche["raison"] = rapport["raison"]
    print(json.dumps(affiche, ensure_ascii=False, indent=2, allow_nan=False))
    return rapport["code"]


if __name__ == "__main__":
    raise SystemExit(main())
