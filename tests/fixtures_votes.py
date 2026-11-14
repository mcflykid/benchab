"""Fabrique d'atelier et preuves synthétiques pour les tests de vote."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from benchab.donnees.base import Base
from benchab.vote import conseil


def empreinte(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ajouter_critique_synthetique(base: Base, dossier: Path, donnees: dict) -> Path:
    """Ajoute une critique simulée explicitement réservée aux scénarios de test."""
    racine = base.prive.resolve()
    fiche = base.prive / donnees["preparation"]
    contenu_fiche = json.loads(fiche.read_text(encoding="utf-8"))
    entrees = {fiche.resolve().relative_to(racine).as_posix(): empreinte(fiche)}
    for champ in ("tranche_3mf", "plaques_3mf"):
        source = contenu_fiche.get(champ)
        if isinstance(source, dict) and isinstance(source.get("fichier"), str):
            chemin = Path(source["fichier"])
            chemin = chemin if chemin.is_absolute() else base.prive / chemin
            if chemin.is_file() and empreinte(chemin) == source.get("sha256"):
                entrees[chemin.resolve().relative_to(racine).as_posix()] = source["sha256"]
    for controle in donnees.get("controles", []):
        chemin = Path(controle["fichier"])
        chemin = chemin if chemin.is_absolute() else base.prive / chemin
        if chemin.is_file() and empreinte(chemin) == controle.get("sha256"):
            entrees[chemin.resolve().relative_to(racine).as_posix()] = controle["sha256"]
    dossier_preuves = donnees.get("dossier_preuves")
    if isinstance(dossier_preuves, dict):
        declarations = list(dossier_preuves.get("entrees", {}).items())
        declarations.extend((p.get("source"), p.get("sha256")) for p in dossier_preuves.get("preuves", []))
        for relatif, sha in declarations:
            if not isinstance(relatif, str):
                continue
            chemin = base.prive / relatif
            if chemin.is_file() and empreinte(chemin) == sha:
                entrees[chemin.resolve().relative_to(racine).as_posix()] = sha

    critique = {
        "schema": 1,
        "preparation_sha256": empreinte(fiche),
        "entrees": dict(sorted(entrees.items())),
        "provenance": {
            "type_sous_agent": "simulation explicite",
            "classe": "GROS",
            "modele": "fixture synthétique",
            "taskid": "scenario_test-fixture",
            "mode": "sans_historique",
            "sans_historique": True,
            "date": datetime.now(timezone.utc).isoformat(),
        },
        "risques_nouveaux": [],
    }
    sources_courantes = {
        source["sha256"]
        for champ in ("tranche_3mf", "plaques_3mf")
        if isinstance((source := contenu_fiche.get(champ)), dict) and isinstance(source.get("sha256"), str)
    }

    def controle_est_pass(controle):
        chemin = Path(controle["fichier"])
        chemin = chemin if chemin.is_absolute() else base.prive / chemin
        if not chemin.is_file() or empreinte(chemin) != controle.get("sha256"):
            return False
        rapport = json.loads(chemin.read_text(encoding="utf-8"))
        meta = rapport.get("_rapport", {})
        liaison = meta.get("source_sha256") or rapport.get("entree_sha256") or rapport.get("source_sha256")
        return rapport.get("statut") == "PASS" and liaison in sources_courantes

    controles_pass = bool(donnees.get("controles")) and all(controle_est_pass(c) for c in donnees["controles"])
    if controles_pass:
        controle_reference = donnees["controles"][0]["id"]
        critique["dixieme_homme"] = {
            "mode": "premortem_echec_impression",
            "scenarios": [
                {
                    "id": f"scenario-{index}",
                    "cause_plausible": f"Cause fictive de test {index}",
                    "couverture": {"type": "controle_existant", "reference": controle_reference},
                    "signe_precoce": {
                        "hauteur_ou_couche": f"Couche {index}",
                        "zone": f"Zone synthétique {index}",
                        "a_surveiller": f"Signe fictif {index}",
                    },
                    "preparation": {
                        "reglages_secours": f"Réglage synthétique {index}",
                        "plan_reimpression": f"Plan fictif {index}",
                    },
                }
                for index in range(1, 4)
            ],
        }
    cible = base.prive / "critiques" / f"simulation-{dossier.stem}.json"
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(json.dumps(critique, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    donnees["scenario_test"] = True
    donnees["relecture_critique"] = {
        "fichier": cible.relative_to(base.prive).as_posix(),
        "sha256": empreinte(cible),
    }
    return cible


def creer_dossier_preuves(racine: Path, *, votes=None) -> dict:
    """Crée un dossier D2 uniquement avec fichiers synthétiques sous ``racine``."""
    racine = Path(racine)
    entree = racine / "entree-synthetique.txt"
    preuve = racine / "preuve-synthetique.json"
    entree.write_text("Entrée fictive pour test unitaire.\n", encoding="utf-8")
    preuve.write_text(json.dumps({"statut": "PASS", "scenario_test": True}), encoding="utf-8")
    auteurs = {"A4"} | {a for a, v in (votes or {}).items() if v.get("vote") != "pour"}
    return {
        "objectif": "Comparer des solutions fictives sans effet réel",
        "contraintes": ["Dossier de test isolé uniquement"],
        "couts": {"temps": "non mesuré dans ce scénario"},
        "risques": ["Preuve synthétique sans portée réelle"],
        "inconnues": [],
        "entrees": {entree.name: empreinte(entree)},
        "preuves": [
            {
                "id": "preuve-synthetique",
                "niveau": "calculé",
                "origine": "Scénario de test synthétique",
                "date": date.today().isoformat(),
                "source": preuve.name,
                "sha256": empreinte(preuve),
                "statut": "PASS",
                "conclusion": "Le rapport fictif est lisible dans le test.",
            }
        ],
        "verifications_requises": [
            {
                "id": "verification-synthetique",
                "description": "Lire le rapport synthétique",
                "preuve": "preuve-synthetique",
                "niveaux_acceptes": ["calculé"],
            }
        ],
        "criteres": ["risque", "comparaison"],
        "solutions": [
            {
                "id": ident,
                "role": role,
                "description": description,
                "evaluation": {"risque": "Scénario fictif", "comparaison": "Non mesurée"},
                "preuves": ["preuve-synthetique"],
            }
            for ident, role, description in (
                ("nouveau", "proposition", "Option synthétique proposée"),
                ("autre", "alternative", "Option synthétique alternative"),
                ("existant", "statu_quo", "Conserver l'état de référence synthétique"),
            )
        ],
        "choix": "nouveau",
        "objections": [
            {
                "auteur": auteur,
                "probleme": "Preuve fictive : portée limitée",
                "consequence": "Aucune conclusion réelle n'en est tirée",
                "fondement": "Fichiers synthétiques du test",
                "test": "Assertions de la suite unitaire",
                "correction": "Remplacer par des éléments vérifiés avant usage réel",
                "reexamen": "Si une décision réelle est préparée",
            }
            for auteur in sorted(auteurs)
        ],
    }


def creer_atelier_vote(racine: Path, *, objet="Vérifier un dossier synthétique") -> dict:
    """Construit Base, préparation, rapport lié par SHA et dossier de vote privé."""
    base = Base(Path(racine) / "prive")
    dossier = base.prive / "projets/synthetique/preparations/P01"
    dossier.mkdir(parents=True)
    tranche = dossier / "tranche.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G90\nM83\nG1 X10 E1\n")
    fiche = dossier / "PREPARATION.json"
    fiche.write_text(
        json.dumps(
            {
                "id": "P01",
                "tranche_3mf": {
                    "fichier": tranche.relative_to(base.prive).as_posix(),
                    "sha256": empreinte(tranche),
                },
            }
        ),
        encoding="utf-8",
    )
    preuve = base.prive / "rapports/controle-synthetique.json"
    preuve.write_text(
        json.dumps({"statut": "PASS", "source_sha256": empreinte(tranche), "scenario_test": True}),
        encoding="utf-8",
    )
    vote_path = base.prive / "vote-synthetique.json"
    data = {
        "schema": 1,
        "objet": objet,
        "type_action": "verification",
        "preparation": fiche.relative_to(base.prive).as_posix(),
        "risques": [],
        "inconnues": [],
        "controles": [
            {
                "id": "controle-synthetique",
                "fichier": preuve.relative_to(base.prive).as_posix(),
                "sha256": empreinte(preuve),
            }
        ],
        "avis_ia": {
            role: {"vote": "pour", "motif": "Avis synthétique explicite ; aucune indépendance présumée."}
            for role in conseil.AVIS_IA
        },
        "scenario_test": True,
    }
    ajouter_critique_synthetique(base, vote_path, data)
    vote_path.write_text(json.dumps(data), encoding="utf-8")
    return {
        "base": base,
        "dossier": vote_path,
        "donnees": data,
        "preuve": preuve,
        "fiche": fiche,
        "tranche": tranche,
        "fermer": base.fermer,
    }


def ajouter_option_vote(atelier: dict, preparation_id: str) -> str:
    """Ajoute une préparation et un dossier de vote synthétiques au même projet."""
    base = atelier["base"]
    projet = atelier["fiche"].parent.parent.parent
    dossier = projet / "preparations" / preparation_id
    dossier.mkdir(parents=True)
    tranche = dossier / "tranche.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G90\nM83\nG1 X12 E1\n")
    fiche = dossier / "PREPARATION.json"
    fiche.write_text(
        json.dumps(
            {
                "id": preparation_id,
                "tranche_3mf": {
                    "fichier": tranche.relative_to(base.prive).as_posix(),
                    "sha256": empreinte(tranche),
                },
            }
        ),
        encoding="utf-8",
    )
    preuve = base.prive / f"rapports/controle-{preparation_id}.json"
    preuve.write_text(
        json.dumps({"statut": "PASS", "source_sha256": empreinte(tranche), "scenario_test": True}),
        encoding="utf-8",
    )
    option = base.prive / f"vote-{preparation_id}.json"
    donnees = {
        "schema": 1,
        "objet": f"Vérifier l'option synthétique {preparation_id}",
        "type_action": "verification",
        "preparation": fiche.relative_to(base.prive).as_posix(),
        "risques": [],
        "inconnues": [],
        "controles": [
            {
                "id": f"controle-{preparation_id}",
                "fichier": preuve.relative_to(base.prive).as_posix(),
                "sha256": empreinte(preuve),
            }
        ],
        "avis_ia": {
            role: {"vote": "pour", "motif": "Avis synthétique explicite ; aucune indépendance présumée."}
            for role in conseil.AVIS_IA
        },
        "scenario_test": True,
    }
    ajouter_critique_synthetique(base, option, donnees)
    option.write_text(json.dumps(donnees), encoding="utf-8")
    return option.relative_to(base.prive).as_posix()
