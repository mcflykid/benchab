"""Valide une relecture externe liée aux entrées courantes d'un vote."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from benchab.qualite.ecritures import _sans_lien
from benchab.vote.conseil import _lire

_MODELE_LEGER = "gpt-6-luna"


def _texte(valeur) -> bool:
    return isinstance(valeur, str) and bool(valeur.strip())


def _scenarios(donnees, controles, risques) -> tuple[bool, list[dict], list[str]]:
    manquants = []
    dixieme = donnees.get("dixieme_homme") if isinstance(donnees, dict) else None
    if not isinstance(dixieme, dict) or dixieme.get("mode") != "premortem_echec_impression":
        return False, [], ["Mode dixième homme absent ou invalide."]
    scenarios = dixieme.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 3:
        return False, [], ["Le prémortem doit contenir exactement trois scénarios."]
    normalises = []
    identifiants = set()
    risques_par_id = {risque["id"]: risque for risque in risques if risque.get("id")}
    for index, scenario in enumerate(scenarios, 1):
        if (
            not isinstance(scenario, dict)
            or not _texte(scenario.get("id"))
            or scenario["id"] in identifiants
            or not _texte(scenario.get("cause_plausible"))
        ):
            manquants.append(f"Scénario {index} : identifiant unique ou cause plausible absent.")
            continue
        identifiants.add(scenario["id"])
        couverture = scenario.get("couverture")
        reference = couverture.get("reference") if isinstance(couverture, dict) else None
        if not isinstance(couverture, dict) or not _texte(reference):
            manquants.append(f"Scénario {index} : couverture explicite requise.")
            continue
        if couverture.get("type") == "controle_existant":
            controle = controles.get(reference)
            if not isinstance(controle, dict) or controle.get("statut") != "PASS":
                manquants.append(f"Scénario {index} : contrôle de couverture absent ou non PASS.")
                continue
        elif couverture.get("type") == "risque_nouveau":
            if reference not in risques_par_id:
                manquants.append(f"Scénario {index} : risque nouveau référencé absent ou non sourcé.")
                continue
        else:
            manquants.append(f"Scénario {index} : type de couverture inconnu.")
            continue
        signe = scenario.get("signe_precoce")
        preparation = scenario.get("preparation")
        if not isinstance(signe, dict) or any(
            not _texte(signe.get(champ)) for champ in ("hauteur_ou_couche", "zone", "a_surveiller")
        ):
            manquants.append(f"Scénario {index} : signe précoce, hauteur/couche, zone ou surveillance absent.")
            continue
        if not isinstance(preparation, dict) or any(
            not _texte(preparation.get(champ)) for champ in ("reglages_secours", "plan_reimpression")
        ):
            manquants.append(f"Scénario {index} : réglages de secours ou plan de réimpression absent.")
            continue
        normalises.append(
            {
                "id": scenario["id"].strip(),
                "cause_plausible": scenario["cause_plausible"].strip(),
                "couverture": {"type": couverture["type"], "reference": reference.strip()},
                "signe_precoce": {
                    champ: signe[champ].strip() for champ in ("hauteur_ou_couche", "zone", "a_surveiller")
                },
                "preparation": {
                    champ: preparation[champ].strip() for champ in ("reglages_secours", "plan_reimpression")
                },
            }
        )
    return len(normalises) == 3 and not manquants, normalises, manquants


def verifier(
    base,
    declaration,
    preparation_sha256: str,
    entrees: dict[str, str],
    *,
    controles: list[dict],
    dixieme_requis: bool,
    scenario_test: bool,
) -> dict:
    """Valide un avis GROS externe et, si requis, son prémortem dixième homme."""
    resultat = {
        "valide": False,
        "fichier": None,
        "sha256": None,
        "provenance": None,
        "risques_nouveaux": [],
        "dixieme_homme": None,
        "points_surveillance": [],
        "manquants": [],
    }
    if not isinstance(declaration, dict):
        resultat["manquants"].append("Descripteur de relecture critique absent.")
        return resultat
    fichier, sha_declare = declaration.get("fichier"), declaration.get("sha256")
    if not _texte(fichier):
        resultat["manquants"].append("Chemin de relecture critique absent.")
        return resultat
    try:
        chemin = Path(fichier)
        chemin = _sans_lien(chemin if chemin.is_absolute() else base.prive / chemin)
        if not chemin.is_relative_to(base.prive.resolve()):
            raise ValueError("La relecture critique doit rester dans base.prive.")
        chemin, donnees, sha_actuel = _lire(base, chemin)
        resultat["fichier"] = chemin.relative_to(base.prive.resolve()).as_posix()
        resultat["sha256"] = sha_actuel
    except (OSError, ValueError, TypeError, KeyError) as erreur:
        resultat["manquants"].append("Relecture critique illisible : " + str(erreur))
        return resultat
    if sha_actuel != sha_declare:
        resultat["manquants"].append("Empreinte de la relecture critique absente ou périmée.")
    if not isinstance(donnees, dict) or donnees.get("schema") != 1:
        resultat["manquants"].append("Relecture critique schema 1 requise.")
        return resultat

    if donnees.get("preparation_sha256") != preparation_sha256:
        resultat["manquants"].append("Relecture critique liée à une autre fiche ou à une fiche périmée.")
    if donnees.get("entrees") != entrees:
        resultat["manquants"].append("Relecture critique liée à des entrées absentes, différentes ou périmées.")

    provenance = donnees.get("provenance")
    requis = ("type_sous_agent", "classe", "modele", "taskid", "mode", "sans_historique", "date")
    if not isinstance(provenance, dict) or any(
        not _texte(provenance.get(champ)) for champ in requis if champ != "sans_historique"
    ):
        resultat["manquants"].append("Provenance de la relecture critique incomplète.")
    else:
        type_agent = provenance["type_sous_agent"].strip()
        simulation_valide = scenario_test and type_agent == "simulation explicite"
        agent_valide = type_agent == "sous-agent"
        if provenance.get("classe") != "GROS" or not (simulation_valide or agent_valide):
            resultat["manquants"].append(
                "La relecture doit venir d'un sous-agent GROS, sans simulation présentée comme réelle."
            )
        if provenance["modele"].strip().lower().startswith(_MODELE_LEGER):
            resultat["manquants"].append("Le modèle gpt-6-luna ne satisfait pas la classe GROS requise.")
        if provenance.get("mode") != "sans_historique" or provenance.get("sans_historique") is not True:
            resultat["manquants"].append("La relecture doit être explicitement effectuée sans historique.")
        try:
            date = datetime.fromisoformat(provenance["date"].replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError("Fuseau horaire absent")
        except (ValueError, TypeError, AttributeError):
            resultat["manquants"].append("Date ISO avec fuseau requise pour la relecture critique.")
        resultat["provenance"] = {champ: provenance[champ] for champ in requis if champ in provenance}

    risques = donnees.get("risques_nouveaux", [])
    if not isinstance(risques, list):
        resultat["manquants"].append("Les nouveaux risques doivent être une liste structurée.")
    else:
        identifiants_risque = set()
        for index, risque in enumerate(risques, 1):
            if not isinstance(risque, dict) or not all(
                _texte(risque.get(champ)) for champ in ("description", "constat")
            ):
                resultat["manquants"].append(f"Nouveau risque {index} : description ou constat absent.")
                continue
            identifiant = risque.get("id")
            if identifiant is not None and not _texte(identifiant):
                resultat["manquants"].append(f"Nouveau risque {index} : identifiant invalide.")
                continue
            if isinstance(identifiant, str) and identifiant in identifiants_risque:
                resultat["manquants"].append(f"Nouveau risque {index} : identifiant dupliqué.")
                continue
            source = risque.get("source")
            if not isinstance(source, dict) or not _texte(source.get("fichier")):
                resultat["manquants"].append(f"Nouveau risque {index} : source précise absente.")
                continue
            chemin_source = Path(source["fichier"])
            sha_source = entrees.get(chemin_source.as_posix())
            if sha_source is None or source.get("sha256") != sha_source:
                resultat["manquants"].append(f"Nouveau risque {index} : source non liée aux entrées courantes.")
                continue
            if isinstance(identifiant, str):
                identifiants_risque.add(identifiant)
            resultat["risques_nouveaux"].append(
                {
                    **({"id": identifiant.strip()} if isinstance(identifiant, str) else {}),
                    "description": risque["description"].strip(),
                    "constat": risque["constat"].strip(),
                    "source": {"fichier": chemin_source.as_posix(), "sha256": sha_source},
                    "statut": "NON VÉRIFIÉ",
                }
            )

    scenario_requis = dixieme_requis
    if scenario_requis or "dixieme_homme" in donnees:
        controles_par_id = {controle["id"]: controle for controle in controles}
        valide, scenarios, manquants = _scenarios(donnees, controles_par_id, resultat["risques_nouveaux"])
        resultat["manquants"].extend(manquants)
        if valide:
            resultat["dixieme_homme"] = {"mode": "premortem_echec_impression", "scenarios": scenarios}
            resultat["points_surveillance"] = [scenario["signe_precoce"] for scenario in scenarios]

    resultat["valide"] = not resultat["manquants"]
    return resultat
