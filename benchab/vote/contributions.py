"""Collecte contradictoire privée ; aucune voix ni autorisation n'est créée.

Les événements append-only sont conservés dans SQLite, table ``decisions``, avec
un marqueur de type distinct. Aucun journal JSON parallèle n'est canonique.
Voir docs/reference/vote.md pour la séparation entre contributions et votes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from benchab.donnees.base import Base
from benchab.qualite.ecritures import _sans_lien
from benchab.vote import preuves

ETATS = ("DESACTIVE", "OBSERVATION", "ACTIF")
EXECUTIONS = ("modeles_distincts", "sessions_distinctes", "contexte_partage", "roles_simules")
BUDGETS = {"ECONOMIE": (3, 1), "EQUILIBRE": (5, 2), "APPROFONDI": (10, 3)}
CHAMPS = (
    "solution",
    "justification",
    "avantages",
    "limites",
    "preuves",
    "hypotheses",
    "risques",
    "controles",
    "conditions",
)
MAX_OCTETS = 256_000
MAX_PREUVE = 4_000_000
TYPE_EVENEMENT = "contribution_conseil"
PREFIXE_SOURCE = "benchab.vote.contributions:v1:"


class _SansNouvelleInformation(Exception):
    """Annule une transaction idempotente sans réécrire l'export SQLite."""

    def __init__(self, rapport):
        self.rapport = rapport


def _exige(condition, message):
    if not condition:
        raise ValueError(message)


def _texte(valeur):
    return isinstance(valeur, str) and bool(valeur.strip()) and len(valeur) <= 12_000


def _identifiant(valeur):
    return isinstance(valeur, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", valeur) is not None


def _encoder(valeur):
    return json.dumps(valeur, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _sha_json(valeur):
    return hashlib.sha256(_encoder(valeur).encode("utf-8")).hexdigest()


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _constante_interdite(valeur):
    raise ValueError("Constante JSON non finie : " + valeur)


def _flottant(texte):
    valeur = float(texte)
    if not math.isfinite(valeur):
        raise ValueError("Nombre JSON non fini")
    return valeur


def _objet_unique(paires):
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError("Clé JSON répétée : " + cle)
        resultat[cle] = valeur
    return resultat


def _decoder(contenu):
    try:
        return json.loads(
            contenu.decode("utf-8"),
            object_pairs_hook=_objet_unique,
            parse_constant=_constante_interdite,
            parse_float=_flottant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as erreur:
        raise ValueError("JSON illisible ou trop imbriqué") from erreur


def _racine(base):
    if not isinstance(base, Base):
        raise TypeError("Une Base BENCHAB explicite est requise.")
    racine = _sans_lien(base.prive.resolve(strict=True))
    if not racine.is_dir():
        raise ValueError("Racine privée BENCHAB absente.")
    return racine


def _chemin_prive(base, valeur):
    racine = _racine(base)
    brut = Path(valeur).expanduser()
    if brut.is_absolute():
        candidat = Path(os.path.abspath(brut))
    else:
        if ".." in brut.parts:
            raise ValueError("Fichier régulier requis dans base.prive.")
        candidat = racine / brut
    if not candidat.is_relative_to(racine):
        raise ValueError("Fichier régulier requis dans base.prive.")
    brut = candidat
    brut = _sans_lien(brut)
    chemin = brut.resolve(strict=True)
    if not chemin.is_relative_to(racine) or not chemin.is_file():
        raise ValueError("Fichier régulier requis dans base.prive.")
    return chemin


def _lire_stable(chemin, limite):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(chemin, flags)
    except OSError as erreur:
        raise ValueError("Fichier privé illisible ou lié.") from erreur
    try:
        avant = os.fstat(fd)
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > limite:
            raise ValueError("Fichier régulier au-delà du budget requis.")
        blocs = []
        taille = 0
        while bloc := os.read(fd, min(64 * 1024, limite + 1 - taille)):
            blocs.append(bloc)
            taille += len(bloc)
            if taille > limite:
                raise ValueError("Budget de lecture dépassé.")
        apres = os.fstat(fd)
        if _signature(avant) != _signature(apres):
            raise ValueError("Fichier modifié pendant sa lecture.")
        if _signature(apres) != _signature(chemin.lstat()):
            raise ValueError("Chemin remplacé pendant sa lecture.")
        return b"".join(blocs), _signature(apres)
    finally:
        os.close(fd)


def _lire_contribution(base, chemin, mode):
    fichier = _chemin_prive(base, chemin)
    contenu, signature = _lire_stable(fichier, MAX_OCTETS)
    data = _decoder(contenu)
    verifier(data, mode)
    racine = _racine(base)
    return data, fichier, fichier.relative_to(racine).as_posix(), hashlib.sha256(contenu).hexdigest(), signature


def _verifier(data, mode="EQUILIBRE"):
    """Valide une contribution et ses budgets; renvoie la donnée inchangée ou lève `ValueError`.

    Entrées : objet JSON et mode de budget; sorties : objet validé, statuts de contribution
    ensuite OBSERVATION/À SOUMETTRE; invariants : statu quo, append-only et aucune voix automatique.
    Voir docs/reference/vote.md.
    """
    _exige(isinstance(data, dict), "Objet JSON requis.")
    _exige(len(_encoder(data).encode("utf-8")) <= MAX_OCTETS, "Contribution trop volumineuse.")
    _exige(isinstance(mode, str) and mode in BUDGETS, "Mode de budget inconnu.")
    champs_autorises = {
        "schema",
        "id",
        "objectif",
        "categorie",
        "execution",
        "propositions",
        "critiques",
        "reponses",
        "recommandation",
        "criteres",
        "inconnues",
        "scenario_test",
    }
    _exige(not set(data) - champs_autorises, "Champ inconnu dans la contribution.")
    _exige(data.get("schema") == 1 and _identifiant(data.get("id")), "Schéma ou identifiant invalide.")
    _exige(_texte(data.get("objectif")) and data.get("categorie") in ("A", "B", "C"), "Objectif/catégorie invalides.")

    execution = data.get("execution")
    _exige(isinstance(execution, dict) and set(execution) == {"type", "participants"}, "Exécution invalide.")
    _exige(execution["type"] in EXECUTIONS, "Mode d'exécution inconnu.")
    participants = execution["participants"]
    _exige(
        isinstance(participants, list) and 1 <= len(participants) <= BUDGETS[mode][0],
        "Budget de participants dépassé.",
    )
    ids, sessions, modeles = set(), set(), set()
    for participant in participants:
        _exige(
            isinstance(participant, dict) and set(participant) == {"id", "modele", "session", "source"},
            "Participant invalide.",
        )
        _exige(
            _identifiant(participant["id"])
            and participant["id"] not in ids
            and all(_texte(participant[cle]) for cle in ("modele", "session", "source")),
            "Participant dupliqué ou incomplet.",
        )
        ids.add(participant["id"])
        sessions.add(participant["session"])
        modeles.add(participant["modele"])
    if execution["type"] in ("modeles_distincts", "sessions_distinctes"):
        _exige(len(sessions) == len(participants), "Sessions distinctes non déclarées.")
    if execution["type"] == "modeles_distincts":
        _exige(len(modeles) == len(participants), "Modèles distincts non déclarés.")

    propositions = data.get("propositions")
    _exige(
        isinstance(propositions, list) and 1 <= len(propositions) <= BUDGETS[mode][0] + 1,
        "Budget de propositions dépassé.",
    )
    identifiants_propositions, nombre_statu_quo = set(), 0
    for proposition in propositions:
        _exige(
            isinstance(proposition, dict) and set(proposition) == {"id", "role", "versions"},
            "Proposition invalide.",
        )
        _exige(
            _identifiant(proposition["id"])
            and proposition["id"] not in identifiants_propositions
            and proposition["role"] in ("solution", "statu_quo"),
            "Identifiant ou rôle de proposition invalide.",
        )
        identifiants_propositions.add(proposition["id"])
        nombre_statu_quo += proposition["role"] == "statu_quo"
        versions = proposition["versions"]
        _exige(
            isinstance(versions, list) and 1 <= len(versions) <= BUDGETS[mode][1] + 1,
            "Nombre de passes dépassé.",
        )
        for index, version in enumerate(versions):
            _exige(
                isinstance(version, dict) and set(version) == set(CHAMPS) | {"auteur", "autres_propositions_vues"},
                "Version de proposition incomplète ou inconnue.",
            )
            _exige(
                version["auteur"] in ids and type(version["autres_propositions_vues"]) is bool,
                "Auteur/exposition de version invalide.",
            )
            _exige(
                all(_texte(version[cle]) for cle in CHAMPS if cle != "preuves"),
                "Description de proposition incomplète.",
            )
            references = version["preuves"]
            _exige(
                isinstance(references, list)
                and all(
                    isinstance(reference, dict)
                    and set(reference) == {"source", "sha256"}
                    and _texte(reference["source"])
                    and re.fullmatch(r"[0-9a-f]{64}", str(reference["sha256"])) is not None
                    for reference in references
                ),
                "Références de preuves invalides.",
            )
            if index == 0 and execution["type"] in ("modeles_distincts", "sessions_distinctes"):
                _exige(
                    not version["autres_propositions_vues"],
                    "Proposition initiale exposée : indépendance déclarée impossible.",
                )
            if index:
                _exige(
                    any(version[cle] != versions[index - 1][cle] for cle in CHAMPS),
                    "Révision sans information nouvelle.",
                )
    _exige(nombre_statu_quo == 1, "Une proposition de statu quo est requise.")

    critiques = data.get("critiques", [])
    _exige(isinstance(critiques, list) and len(critiques) <= 64, "Liste de critiques invalide.")
    identifiants_critiques = set()
    index_propositions = {p["id"]: p for p in propositions}
    for critique in critiques:
        _exige(
            isinstance(critique, dict)
            and set(critique) == {"id", "auteur", "proposition", "version", "probleme", "fondement", "verification"},
            "Critique invalide.",
        )
        _exige(
            _identifiant(critique["id"])
            and critique["id"] not in identifiants_critiques
            and critique["auteur"] in ids
            and critique["proposition"] in index_propositions,
            "Référence de critique invalide.",
        )
        versions = index_propositions[critique["proposition"]]["versions"]
        _exige(
            type(critique["version"]) is int and 1 <= critique["version"] <= len(versions),
            "Version critiquée absente.",
        )
        _exige(
            critique["auteur"] != versions[critique["version"] - 1]["auteur"],
            "Une auto-critique ne constitue pas une relecture croisée.",
        )
        _exige(
            all(_texte(critique[cle]) for cle in ("probleme", "fondement", "verification")),
            "Critique non argumentée.",
        )
        identifiants_critiques.add(critique["id"])

    reponses = data.get("reponses", [])
    _exige(isinstance(reponses, list) and len(reponses) <= 64, "Liste de réponses invalide.")
    reponses_critiques = set()
    for reponse in reponses:
        _exige(
            isinstance(reponse, dict) and set(reponse) == {"critique", "auteur", "decision", "justification"},
            "Réponse invalide.",
        )
        _exige(
            reponse["critique"] in identifiants_critiques
            and reponse["critique"] not in reponses_critiques
            and reponse["auteur"] in ids
            and reponse["decision"] in ("corriger", "rejeter", "preuve_manquante"),
            "Réponse invalide ou critique sans lien.",
        )
        _exige(_texte(reponse["justification"]), "Réponse non motivée.")
        reponses_critiques.add(reponse["critique"])

    _exige(data.get("recommandation") in identifiants_propositions, "Recommandation absente des propositions.")
    for champ in ("criteres", "inconnues"):
        valeurs = data.get(champ)
        _exige(isinstance(valeurs, list) and all(_texte(valeur) for valeur in valeurs), champ + " invalide.")
    _exige(bool(data["criteres"]), "Au moins un critère est requis.")
    _exige(type(data.get("scenario_test", False)) is bool, "scenario_test invalide.")
    return data


def verifier(data, mode="EQUILIBRE"):
    """Valide les contributions et budgets; renvoie l'objet ou lève `ValueError`.

    Entrées : objet JSON et mode; sortie : contribution inchangée. Les erreurs de forme sont
    uniformisées; aucune voix automatique n'est créée. Voir docs/reference/vote.md.
    """
    try:
        return _verifier(data, mode)
    except (TypeError, KeyError, RecursionError) as erreur:
        raise ValueError("Contribution mal formée.") from erreur


def charger(base: Base, chemin: str | Path, mode: str = "EQUILIBRE") -> dict:
    """Lit et valide un JSON privé borné; renvoie contribution, chemin relatif et SHA-256.

    Entrées : chemin privé et mode de budget; sortie : objet validé avec empreinte; aucun vote,
    fichier externe ou lien symbolique n'est accepté. Voir docs/reference/vote.md.
    """
    data, _, relatif, empreinte, _ = _lire_contribution(base, chemin, mode)
    return {"contribution": data, "source": relatif, "sha256": empreinte}


def anonymiser(data: dict, mode: str = "EQUILIBRE") -> dict:
    """Masque les identifiants d'auteur/proposition; texte libre et chemins restent potentiellement identifiants.

    Entrées : contribution validée; sortie : vue anonymisée, sans session ni vote. L'indépendance
    reste déclarative et les textes ne sont pas réécrits; voir docs/reference/vote.md.
    """
    verifier(data, mode)
    alias = {proposition["id"]: f"P{index + 1}" for index, proposition in enumerate(data["propositions"])}
    return {
        "objectif": data["objectif"],
        "criteres": copy.deepcopy(data["criteres"]),
        "propositions": [
            {
                "id": alias[proposition["id"]],
                "role": proposition["role"],
                **{champ: copy.deepcopy(proposition["versions"][-1][champ]) for champ in CHAMPS},
            }
            for proposition in data["propositions"]
        ],
        "limite": "Anonymisation structurelle seulement ; vérifier les identifiants dans les textes libres et chemins.",
    }


def _preuve(base: Base, proposition: str, reference: dict) -> dict:
    resultat = {
        "proposition": proposition,
        **reference,
        "verifie": False,
        "motif": "source absente ou extérieure au privé",
    }
    source = reference["source"]
    brut = Path(source).expanduser()
    if brut.is_absolute() or ".." in brut.parts:
        resultat["motif"] = "chemin de preuve extérieur ou traversant refusé"
        return resultat
    try:
        fichier = _chemin_prive(base, brut)
        if fichier.stat().st_size > MAX_PREUVE:
            resultat["motif"] = "rapport trop volumineux pour ce collecteur"
            return resultat
        contenu, _ = _lire_stable(fichier, MAX_PREUVE)
        if hashlib.sha256(contenu).hexdigest() != reference["sha256"]:
            resultat["motif"] = "empreinte périmée"
            return resultat
        if fichier.suffix.lower() != ".json":
            resultat["motif"] = "contenu non JSON : examen nécessaire"
            return resultat
        try:
            rapport = _decoder(contenu)
        except ValueError:
            resultat["motif"] = "rapport JSON illisible, dupliqué ou non fini"
            return resultat
        valide = preuves.statut_rapport(rapport) == "lisible"
        resultat["verifie"] = valide
        resultat["motif"] = "statut JSON reconnu" if valide else "rapport FAIL/WARN/illisible ou sans statut reconnu"
    except (OSError, ValueError, TypeError, RecursionError):
        resultat["motif"] = "preuve inaccessible, liée ou invalide"
    return resultat


def preuves_disponibles(base: Base, data: dict, mode: str = "EQUILIBRE") -> list[dict]:
    """Relit les SHA des preuves de la dernière version; renvoie `verifie` sans authentifier l'auteur.

    Entrées : Base privée, contribution validée; sortie : une ligne par référence, statuts booléens.
    Voir docs/reference/vote.md : une déclaration d'indépendance n'est pas authentifiée.
    """
    verifier(data, mode)
    resultats = []
    for proposition in data["propositions"]:
        for reference in proposition["versions"][-1]["preuves"]:
            resultats.append(_preuve(base, proposition["id"], reference))
    return resultats


def _source_event(identifiant):
    return PREFIXE_SOURCE + identifiant


def _evenement(row, identifiant=None):
    data = _decoder(row["donnees_json"].encode("utf-8"))
    requis = {
        "schema",
        "type",
        "id",
        "revision",
        "ordre",
        "source_fichier",
        "source_sha256",
        "mode",
        "contribution_sha256",
        "synthese_sha256",
        "contribution",
        "synthese",
    }
    _exige(isinstance(data, dict) and set(data) == requis, "Événement de contribution mal formé.")
    _exige(data["schema"] == 1 and data["type"] == TYPE_EVENEMENT, "Type d'événement de contribution inconnu.")
    _exige(_identifiant(data["id"]), "Identifiant d'événement invalide.")
    _exige(identifiant is None or data["id"] == identifiant, "Identifiant de contribution incohérent.")
    _exige(row["source"] == _source_event(data["id"]), "Source SQLite d'événement incohérente.")
    _exige(type(data["revision"]) is int and data["revision"] > 0, "Numéro de version d'historique invalide.")
    _exige(type(data["ordre"]) is int and data["ordre"] > 0, "Ordre d'historique invalide.")
    _exige(_texte(data["source_fichier"]), "Chemin d'origine absent.")
    brut = Path(data["source_fichier"])
    _exige(not brut.is_absolute() and ".." not in brut.parts, "Chemin source d'historique hors privé.")
    _exige(re.fullmatch(r"[0-9a-f]{64}", str(data["source_sha256"])) is not None, "SHA source invalide.")
    _exige(data["mode"] in BUDGETS, "Mode historique invalide.")
    contribution = verifier(data["contribution"], data["mode"])
    _exige(contribution["id"] == data["id"], "Contribution enregistrée sous un autre identifiant.")
    _exige(_sha_json(contribution) == data["contribution_sha256"], "Contribution SQLite altérée.")
    synthese = data["synthese"]
    _exige(isinstance(synthese, dict) and _sha_json(synthese) == data["synthese_sha256"], "Synthèse SQLite altérée.")
    _exige(
        synthese.get("id") == data["id"]
        and synthese.get("objectif") == contribution["objectif"]
        and synthese.get("autorisation") is False
        and synthese.get("vote") is None,
        "Synthèse de contribution incohérente ou autorisante.",
    )
    _exige(
        row["texte"] == contribution["objectif"] and row["date"] and row["projet_id"] is None,
        "Colonnes SQLite de contribution incohérentes.",
    )
    return data


def _lister_evenements(connexion):
    rows = connexion.execute(
        "SELECT id,projet_id,date,texte,source,donnees_json FROM decisions WHERE source GLOB ?",
        (PREFIXE_SOURCE + "*",),
    ).fetchall()
    evenements = [_evenement(row) for row in rows]
    ordres = [event["ordre"] for event in evenements]
    _exige(len(set(ordres)) == len(ordres), "Ordre global de journal dupliqué.")
    _exige(sorted(ordres) == list(range(1, len(ordres) + 1)), "Ordre global de journal incomplet.")
    groupes = {}
    for event in evenements:
        groupes.setdefault(event["id"], []).append(event)
    for identifiant, groupe in groupes.items():
        groupe.sort(key=lambda event: event["revision"])
        _exige(
            [event["revision"] for event in groupe] == list(range(1, len(groupe) + 1)),
            "Historique de versions incomplet ou dupliqué.",
        )
        for precedent, courant in zip(groupe, groupe[1:]):
            _verifier_prefixe(precedent["contribution"], courant["contribution"])
    return evenements


def _verifier_historique(connexion, data):
    rows = connexion.execute(
        "SELECT id,projet_id,date,texte,source,donnees_json FROM decisions WHERE source=?",
        (_source_event(data["id"]),),
    ).fetchall()
    evenements = sorted((_evenement(row, data["id"]) for row in rows), key=lambda event: event["revision"])
    _exige(
        [event["revision"] for event in evenements] == list(range(1, len(evenements) + 1)),
        "Historique de versions incomplet ou dupliqué.",
    )
    for precedent, courant in zip(evenements, evenements[1:]):
        _verifier_prefixe(precedent["contribution"], courant["contribution"])
    if evenements:
        _verifier_prefixe(evenements[-1]["contribution"], data)
    return evenements


def _verifier_prefixe(precedent, courant):
    for cle in ("objectif", "categorie", "execution", "scenario_test"):
        _exige(precedent.get(cle) == courant.get(cle), "Historique modifié : " + cle)
    propositions = {proposition["id"]: proposition for proposition in courant["propositions"]}
    for ancienne in precedent["propositions"]:
        actuelle = propositions.get(ancienne["id"])
        _exige(
            actuelle is not None
            and actuelle["role"] == ancienne["role"]
            and actuelle["versions"][: len(ancienne["versions"])] == ancienne["versions"],
            "Historique de proposition modifié.",
        )
    for cle in ("critiques", "reponses"):
        _exige(
            courant.get(cle, [])[: len(precedent.get(cle, []))] == precedent.get(cle, []),
            "Historique modifié : " + cle,
        )


def _synthese(base, data, etat, mode, categorie):
    references = preuves_disponibles(base, data, mode)
    index_reponses = {reponse["critique"]: reponse for reponse in data.get("reponses", [])}
    critiques = [
        {
            **critique,
            "reponse": index_reponses.get(critique["id"]),
            "statut": "À VÉRIFIER" if critique["id"] in index_reponses else "OUVERTE",
        }
        for critique in data.get("critiques", [])
    ]
    return {
        "etat": etat,
        "statut": "OBSERVATION" if etat == "OBSERVATION" else "À SOUMETTRE AU CONSEIL",
        "id": data["id"],
        "objectif": data["objectif"],
        "categorie": categorie,
        "mode": mode,
        "mode_execution": data["execution"]["type"],
        "independance": "déclarée, non authentifiée par ce collecteur",
        "scenario_test": data.get("scenario_test", False),
        "criteres": copy.deepcopy(data["criteres"]),
        "solutions": [
            {**copy.deepcopy(proposition), "alias": f"P{index + 1}"}
            for index, proposition in enumerate(data["propositions"])
        ],
        "critiques": critiques,
        "preuves": references,
        "verifications_manquantes": [reference for reference in references if not reference["verifie"]],
        "inconnues": copy.deepcopy(data["inconnues"]),
        "recommandation": data["recommandation"],
        "limites": [
            "Aucune contribution n'est générée par le collecteur.",
            "Une déclaration ne prouve ni indépendance ni authenticité.",
            "Les objections et preuves restent à examiner par le Conseil.",
            "Aucune voix, décision ou autorisation n'est créée.",
        ],
        "conditions_reexamen": "nouvelle preuve, objection ou révision utile ; limite de passes selon le mode",
        "autorisation": False,
        "vote": None,
    }


def preparer(
    base: Base,
    chemin: str | Path,
    etat: str = "OBSERVATION",
    mode: str = "EQUILIBRE",
    classement: dict | None = None,
) -> dict:
    """Importe une version dans l'historique SQLite privé sans créer de vote.

    Entrées : Base, JSON privé, état/mode et classement; sortie : synthèse ou statut de refus.
    Versions antérieures append-only; aucun vote ni droit d'exécution n'est produit.
    Voir docs/reference/vote.md.
    """
    _exige(etat in ETATS, "État de contribution inconnu.")
    if etat == "DESACTIVE":
        return {"etat": etat, "statut": "INACTIF", "autorisation": False, "vote": None}
    data, fichier, relatif, sha_source, signature = _lire_contribution(base, chemin, mode)
    categorie = data["categorie"]
    if classement is not None:
        _exige(
            isinstance(classement, dict) and classement.get("categorie", "A") in ("A", "B", "C"), "Classement invalide."
        )
        categorie = max(categorie, classement.get("categorie", "A"))
    if categorie == "A":
        return {"etat": etat, "statut": "NON NÉCESSAIRE (catégorie A)", "autorisation": False, "vote": None}

    synthese = _synthese(base, data, etat, mode, categorie)
    try:
        with base.transaction() as connexion:
            # BEGIN IMMEDIATE sérialise la révision et l'ordre du journal avant l'INSERT.
            connexion.execute("BEGIN IMMEDIATE")
            evenements = _verifier_historique(connexion, data)
            if evenements and _encoder(data) == _encoder(evenements[-1]["contribution"]):
                raise _SansNouvelleInformation(
                    {
                        "etat": etat,
                        "statut": "SANS NOUVELLE INFORMATION",
                        "autorisation": False,
                        "vote": None,
                        "id": data["id"],
                    }
                )
            if (
                _signature(fichier.lstat()) != signature
                or hashlib.sha256(_lire_stable(fichier, MAX_OCTETS)[0]).hexdigest() != sha_source
            ):
                raise ValueError("Contribution modifiée pendant son import.")
            evenements_globaux = _lister_evenements(connexion)
            ordre = max((event["ordre"] for event in evenements_globaux), default=0) + 1
            revision = len(evenements) + 1
            evenement = {
                "schema": 1,
                "type": TYPE_EVENEMENT,
                "id": data["id"],
                "revision": revision,
                "ordre": ordre,
                "source_fichier": relatif,
                "source_sha256": sha_source,
                "mode": mode,
                "contribution_sha256": _sha_json(data),
                "synthese_sha256": _sha_json(synthese),
                "contribution": data,
                "synthese": synthese,
            }
            connexion.execute(
                "INSERT INTO decisions(id,projet_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    None,
                    datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    data["objectif"],
                    _source_event(data["id"]),
                    _encoder(evenement),
                ),
            )
    except _SansNouvelleInformation as sans_changement:
        return sans_changement.rapport
    return synthese


def derniere(base: Base, objectif: str, *, identifiant=None) -> dict | None:
    """Retourne la dernière contribution non simulée de l'objectif, après relecture des preuves SHA.

    Entrées : Base privée, objectif et identifiant optionnel ; synthèse ou `None`. Une preuve modifiée
    devient manquante, jamais PASS implicite. Aucun vote ni état physique n'est déduit.
    Voir docs/reference/vote.md.
    """
    if not _texte(objectif):
        raise ValueError("Objectif non vide requis.")
    if identifiant is not None and not _identifiant(identifiant):
        raise ValueError("Identifiant de contribution invalide.")
    evenements = _lister_evenements(base.connexion)
    candidats = sorted(evenements, key=lambda event: event["ordre"], reverse=True)
    for event in candidats:
        data = event["contribution"]
        synthese = event["synthese"]
        if (data["objectif"] != objectif or data.get("scenario_test")
                or (identifiant is not None and data["id"] != identifiant)):
            continue
        if synthese.get("etat") not in ("OBSERVATION", "ACTIF"):
            continue
        courant = copy.deepcopy(synthese)
        references = preuves_disponibles(base, data, event["mode"])
        courant["preuves"] = references
        courant["verifications_manquantes"] = [reference for reference in references if not reference["verifie"]]
        return courant
    return None
