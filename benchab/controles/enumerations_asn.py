"""Compare les valeurs énumérées d'un 3MF au binaire Anycubic configuré."""

import argparse
import json
from pathlib import Path

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.fabrication import slicer_runtime
from benchab.qualite.statuts import code_statut


def controler(source, identite):
    """Renvoie PASS, FAIL ou NON VÉRIFIÉ pour les valeurs sans unité du 3MF.

    L'identité (version, SHA-256) désigne le moteur cible. Le rapport ne déduit
    pas le producteur de l'archive et ne modifie ni source ni réglages.
    """
    with Archive3MF(source, taille_max_xml=8 * 1024 * 1024) as archive:
        donnees = archive.reglages()
    lignes = [ligne for ligne in reglages.verifier(donnees, identite_slicer=identite)
              if "enumeration" in ligne]
    statuts = {ligne["statut"] for ligne in lignes}
    statut = "FAIL" if "FAIL" in statuts else ("PASS" if statuts == {"PASS"} else "NON VÉRIFIÉ")
    return {
        "statut": statut,
        "source": Path(source).name,
        "binaire_cible": {k: identite.get(k) for k in ("version", "sha256")},
        "controles": lignes,
        "portee": "Contrat numérique des valeurs énumérées du moteur cible, sans tranchage.",
    }


def main(argv=None):
    """Lit un 3MF et le binaire déclaré sans les lancer ; codes 0 PASS, 2 sinon."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    a = p.parse_args(argv)
    try:
        rapport = controler(a.source, slicer_runtime.informations(empreinte=True))
    except (OSError, ValueError) as exc:
        p.exit(2, f"ERREUR : {exc}\n")
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return code_statut(rapport["statut"])
