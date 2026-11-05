"""Contrôles mécaniques associés aux règles de préparation."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from benchab.controles import debit_gcode, plaques_interface
from benchab.controles.rapports import RapportCourant

_LIBELLE_AUTO = re.compile(r"^Plaque\s+\d+\s*\(.*\)$", re.IGNORECASE)
_CARACTERES_SIMPLES = re.compile(r"^[\w .-]+$", re.UNICODE)


def verifier_nommage(fichier: str | Path) -> dict:
    """Vérifie les noms persistés des plaques et objets d'un 3MF enregistré."""
    source = Path(fichier)
    erreurs: list[str] = []
    with zipfile.ZipFile(source) as archive:
        membres = archive.namelist()
        membre = "Metadata/model_settings.config"
        if membres.count(membre) != 1 or len(membres) != len(set(membres)):
            raise ValueError("Configuration de modèle absente, dupliquée ou ambiguë.")
        racine = ElementTree.fromstring(archive.read(membre))

    plaques = racine.findall("plate")
    objets = racine.findall("object")
    if not plaques:
        erreurs.append("Aucune plaque enregistrée.")
    if not objets:
        erreurs.append("Aucun objet nommé dans la configuration enregistrée.")

    noms_plaques = _noms_uniques(plaques, "plater_name", "plaque", erreurs)
    noms_objets = _noms_uniques(objets, "name", "objet", erreurs)
    for nom in noms_plaques:
        if _LIBELLE_AUTO.fullmatch(nom):
            erreurs.append(f"Libellé automatique de plaque interdit : {nom!r}.")

    return {
        "controle": "U22",
        "statut": "PASS" if not erreurs else "FAIL",
        "noms_plaques": noms_plaques,
        "noms_objets": noms_objets,
        "erreurs": erreurs,
        "limite": "Vérifie le contenu enregistré du 3MF ; ne prouve pas une réouverture dans l’interface slicer.",
    }


def _noms_uniques(elements, cle: str, type_nom: str, erreurs: list[str]) -> list[str]:
    noms: list[str] = []
    for index, element in enumerate(elements, 1):
        valeurs = [meta.get("value") for meta in element.findall("metadata") if meta.get("key") == cle]
        if len(valeurs) != 1 or not isinstance(valeurs[0], str) or not valeurs[0].strip():
            erreurs.append(f"Nom {type_nom} absent ou répété à l’index {index}.")
            continue
        nom = valeurs[0]
        if nom != nom.strip() or not _CARACTERES_SIMPLES.fullmatch(nom):
            erreurs.append(f"Nom {type_nom} non simple : {nom!r}.")
        if nom in noms:
            erreurs.append(f"Nom {type_nom} dupliqué : {nom!r}.")
        noms.append(nom)
    return noms


def controler(fichier: str | Path) -> dict:
    """Exécute les contrôles de règle directement vérifiables sur un 3MF."""
    source = Path(fichier)
    resultats = []
    try:
        placement = plaques_interface.analyser(source)
        resultats.append(
            {
                "controle": "U20",
                "statut": "PASS" if placement["conforme"] else "FAIL",
                "rapport": placement,
            }
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as erreur:
        resultats.append({"controle": "U20", "statut": "FAIL", "erreur": str(erreur)})

    try:
        resultats.append(verifier_nommage(source))
    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as erreur:
        resultats.append({"controle": "U22", "statut": "FAIL", "erreur": str(erreur)})

    resultats.append(
        {
            "controle": "U24",
            "statut": "NON VÉRIFIÉ",
            "motif": (
                "Aucune preuve mécanique ne démontre la causalité du mouvement par la pièce motrice et son contact."
            ),
        }
    )

    try:
        debit = debit_gcode.analyser(source)
        resultats.append(
            {
                "controle": "U25",
                "statut": "PASS" if debit.get("conforme") is True else "FAIL",
                "rapport": debit,
            }
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as erreur:
        resultats.append({"controle": "U25", "statut": "NON VÉRIFIÉ", "erreur": str(erreur)})

    statuts = {resultat["statut"] for resultat in resultats}
    global_statut = "FAIL" if "FAIL" in statuts else "NON VÉRIFIÉ" if "NON VÉRIFIÉ" in statuts else "PASS"
    return {
        "statut": global_statut,
        "resultats": resultats,
        "limites": [
            "U21 n’impose aucun plafond universel de vitesse ; le débit est comparé aux plafonds déclarés dans le 3MF.",
            "U36 est appliqué par les contrôles de protections et de préparation, pas déduit du seul fichier fourni.",
            "Un contrôle numérique ne valide pas le résultat physique.",
        ],
        "validation_physique": False,
    }


def main(argv=None, *, base=None, prive=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sortie", type=Path, help="Rapport JSON, dans l’espace privé de BENCHAB.")
    parser.add_argument("--prive", type=Path, help="Racine privée, requise en exécution autonome.")
    args = parser.parse_args(argv)
    valeur_privee = base.prive if base is not None else prive or args.prive
    if valeur_privee is None:
        parser.error("Fournir base.prive ou --prive.")
    racine_privee = Path(valeur_privee).expanduser().resolve()
    sortie = args.sortie or racine_privee / "rapports" / "regles-lot4.json"
    try:
        sortie_abs = sortie.expanduser().resolve()
        if not sortie_abs.is_relative_to(racine_privee.resolve()):
            raise ValueError("Le rapport doit rester dans l’espace privé.")
        rapport = RapportCourant(sortie_abs, [args.source], "regles_lot4")
        resultat = controler(args.source)
        rapport.publier(resultat)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as erreur:
        print(f"Contrôle refusé : {erreur}", file=sys.stderr)
        return 2
    print(json.dumps(resultat, ensure_ascii=False, indent=1))
    return 0 if resultat["statut"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
