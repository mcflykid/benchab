"""Compare les valeurs énumérées d'un 3MF au binaire Anycubic configuré."""

import argparse
import json
from pathlib import Path

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.fabrication import slicer_runtime
from benchab.qualite.ecritures import destination_rapport_optionnel, ecrire_json_neuf
from benchab.qualite.statuts import code_statut


def controler(source, identite, *, inclure_contextuelles=False):
    """Renvoie PASS, FAIL ou NON VÉRIFIÉ pour les valeurs sans unité du 3MF.

    L'identité (version, SHA-256) désigne le moteur cible. Le rapport ne déduit
    pas le producteur de l'archive et ne modifie ni source ni réglages.
    L’option contextuelle exige les réglages compagnons dans le même 3MF.
    """
    with Archive3MF(source, taille_max_xml=8 * 1024 * 1024) as archive:
        donnees = archive.reglages()
    lignes = [
        ligne for ligne in reglages.verifier(
            donnees, identite_slicer=identite, inclure_contextuelles=inclure_contextuelles,
        ) if "enumeration" in ligne
    ]
    statuts = {ligne["statut"] for ligne in lignes}
    statut = "FAIL" if "FAIL" in statuts else ("PASS" if statuts == {"PASS"} else "NON VÉRIFIÉ")
    return {
        "statut": statut,
        "source": Path(source).name,
        "binaire_cible": {k: identite.get(k) for k in ("version", "sha256")},
        "controles": lignes,
        "portee": "Contrat numérique des valeurs énumérées du moteur cible, sans tranchage.",
    }


def main(argv=None, *, prive=None):
    """Lit le 3MF et l’identité du binaire sans lancer le moteur.

    Codes 0 PASS, 2 FAIL ou NON VÉRIFIÉ. Le résumé exige un rapport privé
    neuf et donne le premier constat pour un verdict incomplet.
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("--contextuelles", action="store_true", help="Vérifie aussi les conditions des observations")
    p.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    p.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    a = p.parse_args(argv)
    try:
        sortie = destination_rapport_optionnel(a.sortie, resume=a.resume, nom="des énumérations", prive=prive)
        rapport = controler(
            a.source, slicer_runtime.informations(empreinte=True), inclure_contextuelles=a.contextuelles,
        )
        if sortie is not None:
            ecrire_json_neuf(sortie, rapport, entrees=[a.source])
    except (OSError, ValueError) as exc:
        p.exit(2, f"ERREUR : {exc}\n")
    affiche = rapport
    if a.resume:
        statut = rapport["statut"]
        constat = next((ligne for ligne in rapport["controles"] if ligne["statut"] == statut), None)
        affiche = {"statut": statut, "rapport": str(sortie), "nombre_controles": len(rapport["controles"])}
        if statut != "PASS":
            affiche["constat"] = constat if constat is not None else "Aucune valeur énumérée qualifiée."
    print(json.dumps(affiche, ensure_ascii=False, indent=2, allow_nan=False))
    return code_statut(rapport["statut"])
