"""Contrats des études dans le registre existant ; veille sur instantané fourni.

Lecture seule : aucune installation, réseau, modification du registre ou décision
automatique. Une nouveauté pertinente produit une proposition à examiner.
"""

import argparse
import json
import re
from pathlib import Path

from benchab.chemins import atelier_prive

COUVERTURES = {"NON_ETUDIE", "DOCUMENTATION_LUE", "CODE_CIBLE_LU", "TEST_SYNTHETIQUE"}
INTEGRATIONS = {"DECLARE", "DISPONIBLE", "ESSAYE", "INTEGRE", "REFUSE", "REPORTE", "HISTORIQUE"}


def etudes(donnees):
    """Retourne les entrées du registre déclarées comme études techniques."""
    return [c for c in donnees.get("connaissances", []) if c.get("type") == "ETUDE_TECHNIQUE"]


def verifier(donnees):
    """Vérifie la cohérence des déclarations d’étude et renvoie leurs erreurs."""
    erreurs = []
    identites = set()
    for item in etudes(donnees):
        e = item.get("etude", {})
        nom = item.get("id", "?")

        def faute(message):
            erreurs.append(nom + " : " + message)

        if not isinstance(e, dict):
            faute("étude malformée")
            continue
        couverture = e.get("couverture")
        integration = e.get("integration")
        if couverture not in COUVERTURES:
            faute("couverture inconnue ; aucun audit exhaustif implicite")
        if integration not in INTEGRATIONS:
            faute("intégration inconnue")
        for cle in ("depot", "perimetre", "decision", "raison", "reexaminer_si", "capacites", "non_lu"):
            if not e.get(cle):
                faute("champ absent " + cle)
        if any(
            not isinstance(e.get(cle), str) for cle in ("depot", "perimetre", "decision", "raison", "reexaminer_si")
        ):
            faute("identité et décision doivent être textuelles")
            continue
        if not isinstance(e.get("fichiers_lus", []), list):
            faute("liste des sources requise")
            continue
        if couverture != "NON_ETUDIE":
            if not re.fullmatch("[a-f0-9]{40}", str(e.get("commit", ""))):
                faute("commit exact requis")
            if not e.get("fichiers_lus"):
                faute("aucun fichier consulté")
            if not e.get("licence"):
                faute("licence non renseignée")
            for source in e.get("fichiers_lus", []):
                if (
                    not isinstance(source, dict)
                    or not source.get("url")
                    or not source.get("portee")
                    or not re.fullmatch("[a-f0-9]{64}", str(source.get("sha256", "")))
                ):
                    faute("source étudiée sans URL, portée et empreinte exacte")
        if couverture == "NON_ETUDIE" and (e.get("fichiers_lus") or integration in {"ESSAYE", "INTEGRE"}):
            faute("source non étudiée présentée comme acquise")
        if couverture == "DOCUMENTATION_LUE" and e.get("code_audite"):
            faute("documentation ne vaut pas audit de code")
        if couverture == "TEST_SYNTHETIQUE" and item.get("niveau_preuve") != "NUMERIQUE":
            faute("essai synthétique ne vaut pas preuve physique")
        capacites = e.get("capacites", {})
        if not isinstance(capacites, dict):
            faute("capacités malformées")
            continue
        if any(
            not isinstance(capacites.get(cle, []), list) or not all(isinstance(x, str) for x in capacites.get(cle, []))
            for cle in ("declarees", "testees", "integrees")
        ):
            faute("listes de capacités requises")
            continue
        declarees = set(capacites.get("declarees", []))
        testees = set(capacites.get("testees", []))
        integrees = set(capacites.get("integrees", []))
        if not testees <= declarees or not integrees <= testees:
            faute("capacités intégrées/testées non justifiées")
        if testees and couverture != "TEST_SYNTHETIQUE":
            faute("documentation ou code lu ne prouvent pas un essai")
        if integration == "INTEGRE" and not integrees:
            faute("aucune capacité intégrée testée")
        if testees and not e.get("preuves_essais"):
            faute("essai sans preuve")
        identite = (e.get("depot"), e.get("commit"), e.get("perimetre"))
        if identite in identites:
            faute("doublon de commit et périmètre")
        identites.add(identite)
    return erreurs


def proposer(donnees, instantane):
    """Comparer les seuls déclencheurs déclarés ; une release ne prouve rien."""
    propositions = []
    for item in etudes(donnees):
        if item.get("validite") == "remplacee":
            continue
        e = item["etude"]
        nouveau = instantane.get(e["depot"])
        if not nouveau:
            continue
        pertinentes = sorted(set(nouveau.get("capacites_demontrees", [])) & set(e.get("declencheurs_capacites", [])))
        nouvelle_revision = bool(nouveau.get("commit")) and nouveau.get("commit") != e.get("commit")
        propositions.append(
            {
                "id": item["id"],
                "depot": e["depot"],
                "revision_etudiee": e.get("commit"),
                "revision_observee": nouveau.get("commit"),
                "url": nouveau.get("url"),
                "capacites_pertinentes": pertinentes,
                "action": "PROPOSER_ETUDE" if nouvelle_revision and pertinentes else "INFORMATION_SEULE",
                "installation": False,
                "limite": (
                    "Capacités déclarées par l’instantané fourni, à vérifier sur les sources ; "
                    "aucune intégration acquise."
                ),
            }
        )
    return propositions


def main(argv=None, *, racine=None):
    """Affiche les études de l’atelier choisi ; unités/statuts des sources conservés, aucune validation
    physique inférée.
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--racine", type=Path, default=atelier_prive(racine))
    p.add_argument("--instantane", type=Path, help="JSON explicite, aucune consultation réseau implicite")
    a = p.parse_args(argv)
    from benchab.connaissances.memoire import charger

    donnees = charger(a.racine)
    erreurs = verifier(donnees)
    resultat = {
        "execution": "PASS",
        "resultat": "FAIL" if erreurs else "PASS",
        "erreurs": erreurs,
        "etudes": len(etudes(donnees)),
    }
    if a.instantane:
        resultat["propositions"] = proposer(donnees, json.loads(a.instantane.read_text()))
    print(json.dumps(resultat, ensure_ascii=False, indent=2))
    return int(bool(erreurs))


if __name__ == "__main__":
    raise SystemExit(main())
