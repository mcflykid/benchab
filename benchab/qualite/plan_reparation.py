"""Plan en lecture seule de régénération des vues privées déterministes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from benchab.chemins import atelier_prive, sans_lien
from benchab.donnees import vues
from benchab.donnees.base import APPLICATION_ID
from benchab.qualite.ecritures import PublicationNeuve, remplacer_texte, verrou_publication
from benchab.qualite.statuts import code_statut

MANIFESTE = "vues/manifest.json"
README = "README.md"
MAX_CONTENU_LU_OCTETS = 2 * 1024 * 1024


def _empreinte_fichier(racine: Path, relatif: str, *, lecture=False):
    """Empreinte un fichier régulier sans suivre de lien ; lecture bornée si son texte est requis."""
    candidat = racine / relatif
    try:
        chemin = sans_lien(candidat, racine=racine)
    except ValueError:
        # Repérer un lien par lstat sans résoudre ni ouvrir sa cible.
        courant = racine
        for composant in Path(relatif).parts:
            courant = courant / composant
            if courant.is_symlink():
                return {"statut": "LIEN_A_VERIFIER", "chemin": candidat}
        return {"statut": "A_VERIFIER", "chemin": candidat}
    if chemin.is_symlink():
        return {"statut": "LIEN_A_VERIFIER", "chemin": chemin}
    try:
        fd = os.open(chemin, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return {"statut": "ABSENTE", "chemin": chemin}
    except OSError:
        return {"statut": "A_VERIFIER", "chemin": chemin}
    try:
        with os.fdopen(fd, "rb") as flux:
            info = os.fstat(flux.fileno())
            if not stat.S_ISREG(info.st_mode):
                return {"statut": "A_VERIFIER", "chemin": chemin}
            if lecture and info.st_size > MAX_CONTENU_LU_OCTETS:
                return {"statut": "A_VERIFIER", "chemin": chemin}
            identite_avant = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            empreinte = hashlib.file_digest(flux, "sha256").hexdigest()
            contenu = None
            if lecture:
                flux.seek(0)
                contenu = flux.read(MAX_CONTENU_LU_OCTETS + 1)
                if len(contenu) > MAX_CONTENU_LU_OCTETS:
                    return {"statut": "A_VERIFIER", "chemin": chemin}
            apres = os.fstat(flux.fileno())
            identite_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
            courant = chemin.lstat()
            identite_courante = (
                courant.st_dev, courant.st_ino, courant.st_size, courant.st_mtime_ns, courant.st_ctime_ns,
            )
            if not identite_avant == identite_apres == identite_courante:
                return {"statut": "A_VERIFIER", "chemin": chemin}
    except OSError:
        return {"statut": "A_VERIFIER", "chemin": chemin}
    return {
        "statut": "PRESENT",
        "chemin": chemin,
        "sha256": empreinte,
        "contenu": contenu,
        "identite": identite_avant,
    }


def _section_readme(contenu: bytes, attendu: str):
    """Compare le bloc géré sans toucher au texte éditorial autour des marqueurs."""
    try:
        texte = contenu.decode("utf-8")
    except UnicodeDecodeError:
        return "A_VERIFIER", None
    if texte.count(vues.DEBUT) != 1 or texte.count(vues.FIN) != 1:
        return ("ABSENTE", None) if vues.DEBUT not in texte and vues.FIN not in texte else ("A_VERIFIER", None)
    debut = texte.index(vues.DEBUT) + len(vues.DEBUT)
    fin = texte.index(vues.FIN)
    if fin < debut or not texte[debut:fin].startswith("\n") or not texte[debut:fin].endswith("\n"):
        return "A_VERIFIER", None
    corps = texte[debut + 1:fin - 1]
    statut = "PRESENTE_IDENTIQUE" if corps == attendu.rstrip() else "DIVERGENTE"
    return statut, hashlib.sha256(corps.encode("utf-8")).hexdigest()


def _sha_identite(identite):
    """Empreinte l'identité système d'un fichier sans exposer ses numéros de périphérique ou d'inode."""
    if identite is None:
        return None
    return hashlib.sha256(json.dumps(list(identite), separators=(",", ":")).encode("ascii")).hexdigest()


def _action(cible, sha_attendu, source_sha, genre, *, avant, identite):
    """Décrit une future action avec sauvegarde et contrôles requis ; n'exécute rien."""
    return {
        "action": genre,
        "cible": cible,
        "sha256_attendu": sha_attendu,
        "cible_avant": {
            "statut": avant["statut"],
            "sha256": avant.get("sha256_actuel"),
            "identite_sha256": avant.get("identite_sha256"),
        },
        "source": {
            "type": "rendu des vues depuis SQLite en lecture seule",
            "sha256": source_sha,
            **identite,
        },
        "preconditions_requises": {
            "sauvegarde_verifiee_avant": True,
            "rendu_source_recalcule_et_identique": True,
            "cible_inchangee_et_reverifiee_sans_lien": True,
            "controle_apres_application": True,
        },
    }


def _rapport_indisponible(erreur):
    """Retourne un plan vide NON VÉRIFIÉ sans créer ni réparer l'atelier."""
    return {
        "schema": 1,
        "statut": "NON VÉRIFIÉ",
        "code": code_statut("NON VÉRIFIÉ"),
        "application_effectuee": False,
        "detail": erreur,
        "vues": [],
        "actions": [],
        "propositions_liens": [],
    }


def _entete_sqlite(chemin):
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        if not stat.S_ISREG(os.fstat(flux.fileno()).st_mode):
            return b""
        return flux.read(20)


def _source_logique(rendu, fiches):
    """Calcule le SHA logique stable de tous les textes rendus depuis la transaction SQLite."""
    attendus = {**rendu["fichiers"], **fiches}
    empreintes_attendues = {
        chemin: hashlib.sha256(texte.encode("utf-8")).hexdigest()
        for chemin, texte in sorted(attendus.items())
    }
    sources_rendues = {"fichiers": empreintes_attendues, "sections": vues._manifeste(rendu)["sections"]}
    sha_source = hashlib.sha256(
        json.dumps(sources_rendues, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return attendus, empreintes_attendues, sha_source


def _candidats_par_sha(racine: Path, candidats: list[str]) -> dict[str, list[str]]:
    """Indexe jusqu'à 64 candidats privés réguliers, sans lien et bornés, par SHA-256."""
    index: dict[str, list[str]] = {}
    if len(candidats) > 64:
        return index
    for relatif in candidats:
        if not isinstance(relatif, str):
            continue
        chemin = PurePosixPath(relatif)
        if chemin.is_absolute() or not chemin.parts or any(part in {"", ".", ".."} for part in chemin.parts):
            continue
        normalise = chemin.as_posix()
        empreinte = _empreinte_fichier(racine, normalise, lecture=True)
        if empreinte["statut"] != "PRESENT":
            continue
        identiques = index.setdefault(empreinte["sha256"], [])
        if normalise not in identiques:
            identiques.append(normalise)
    return index


def _lien_cible_absente(racine: Path, relatif: str) -> bool:
    """Reconnaît seulement un lien feuille relatif vers une cible absente, sans le suivre."""
    composants = PurePosixPath(relatif).parts
    parent = racine
    for composant in composants[:-1]:
        parent = parent / composant
        if parent.is_symlink():
            return False
    lien = parent / composants[-1]
    try:
        if not stat.S_ISLNK(lien.lstat().st_mode):
            return False
        texte_cible = os.readlink(lien)
        cible = Path(texte_cible)
    except OSError:
        return False
    # Ce lot ne traite qu'un lien déplacé simple dans le privé ; les autres formes restent à vérifier.
    if (
        cible.is_absolute() or not cible.parts
        or any(part in {"", ".", ".."} for part in texte_cible.split("/"))
    ):
        return False
    # readlink renvoie un chemin interprété depuis le parent du lien, pas depuis l'atelier.
    courant = parent
    for composant in cible.parts:
        courant = courant / composant
        if courant.is_symlink():
            return False
    try:
        courant.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def planifier(prive: Path | str | None = None, *, candidats: list[str] | None = None) -> dict[str, Any]:
    """Compare les vues privées à SQLite et prépare un plan JSON sans écriture ni mesure.

    Entrée : répertoire privé local. Sortie : statuts PASS/WARN/NON VÉRIFIÉ,
    chemins relatifs et SHA-256. Une divergence ne prouve aucune falsification.
    `candidats` contient des chemins privés relatifs explicitement fournis ; un lien
    de vue ne reçoit qu'une proposition si un candidat unique a le SHA rendu exact.
    Invariant : les propositions ne modifient aucun lien ni fichier.
    Invariant : SQLite est ouvert en mode ro/query_only ; aucune source n'est réparée.
    """
    try:
        racine = atelier_prive(prive).absolute()
        base = sans_lien(racine / "benchab.sqlite", racine=racine, strict=True)
        entete = _entete_sqlite(base)
        if not entete.startswith(b"SQLite format 3\0") or len(entete) != 20:
            return _rapport_indisponible("Base privée SQLite non reconnue.")
        # Un journal WAL peut demander des fichiers auxiliaires à l'ouverture, même en lecture.
        if entete[18:20] != b"\x01\x01":
            return _rapport_indisponible("Journal SQLite incompatible avec le plan sans écriture.")
        with closing(sqlite3.connect(base.as_uri() + "?mode=ro", uri=True, timeout=1)) as connexion:
            connexion.row_factory = sqlite3.Row
            connexion.execute("PRAGMA query_only = ON")
            # Toutes les vues doivent venir du même instantané et d'une base BENCHAB identifiée (#119).
            connexion.execute("BEGIN")
            application_id = connexion.execute("PRAGMA application_id").fetchone()[0]
            version = connexion.execute("PRAGMA user_version").fetchone()[0]
            if application_id != APPLICATION_ID or version != 1:
                return _rapport_indisponible("Identité ou schéma de la base BENCHAB non établi.")
            rendu = vues._rendre(connexion)
            fiches = vues._fiches_filaments(connexion)
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as erreur:
        return _rapport_indisponible(f"Sources indisponibles en lecture seule ({type(erreur).__name__}).")

    manifeste = vues._manifeste(rendu)
    texte_manifeste = json.dumps(manifeste, ensure_ascii=False, sort_keys=True) + "\n"
    attendus, empreintes_attendues, sha_source = _source_logique(rendu, fiches)
    identite = {"application_id": application_id, "user_version": version}
    lignes = []
    actions = []
    propositions_liens = []
    incertitudes = False
    index_candidats = _candidats_par_sha(racine, candidats or [])

    # SQLite fait foi pour les octets attendus ; le manifeste existant ne les autorise pas (#119).
    for relatif in sorted(attendus):
        attendu = empreintes_attendues[relatif]
        actuel = _empreinte_fichier(racine, relatif)
        statut = actuel["statut"]
        if statut == "PRESENT":
            statut = "PRESENTE_IDENTIQUE" if actuel["sha256"] == attendu else "DIVERGENTE"
        entree = {"cible": relatif, "statut": statut, "sha256_attendu": attendu}
        if "sha256" in actuel:
            entree["sha256_actuel"] = actuel["sha256"]
            entree["identite_sha256"] = _sha_identite(actuel.get("identite"))
        lignes.append(entree)
        if statut in {"ABSENTE", "DIVERGENTE"}:
            actions.append(_action(relatif, attendu, sha_source, "REGENERER_VUE", avant=entree, identite=identite))
        elif statut == "LIEN_A_VERIFIER" and _lien_cible_absente(racine, relatif):
            correspondants = index_candidats.get(attendu, [])
            if len(correspondants) == 1:
                try:
                    _, texte_lien, _, destination_rel, identite_lien = _instantane_lien(racine, relatif)
                except (OSError, ValueError):
                    incertitudes = True
                else:
                    if destination_rel in attendus or destination_rel == MANIFESTE:
                        incertitudes = True
                    else:
                        # Les empreintes lient le choix au lien observé sans publier sa cible privée en clair.
                        propositions_liens.append({
                            "action": "PROPOSER_LIEN_CANDIDAT_SHA_EXACT",
                            "cible": relatif,
                            "candidat": correspondants[0],
                            "sha256": attendu,
                            "source_sha256_rendu": sha_source,
                            "lien_sha256": hashlib.sha256(os.fsencode(texte_lien)).hexdigest(),
                            "lien_identite_sha256": _sha_identite(identite_lien),
                            "destination_sha256": hashlib.sha256(destination_rel.encode("utf-8")).hexdigest(),
                            "application_effectuee": False,
                            "limite": "Identité des octets prouvée ; origine et pertinence historique non établies.",
                        })
            else:
                incertitudes = True
        elif statut not in {"PRESENTE_IDENTIQUE"}:
            incertitudes = True

    attendu_section = rendu["sections"][README]
    sha_section = manifeste["sections"][README]
    lu = _empreinte_fichier(racine, README, lecture=True)
    if lu["statut"] == "ABSENTE":
        statut, sha_actuel = "ABSENTE", None
    elif lu["statut"] == "LIEN_A_VERIFIER" or lu["statut"] == "A_VERIFIER":
        statut, sha_actuel = lu["statut"], None
    else:
        try:
            statut, sha_actuel = _section_readme(lu["contenu"], attendu_section)
        except (KeyError, TypeError):
            statut, sha_actuel = "A_VERIFIER", None
    entree = {"cible": README + "#BENCHAB:BASE", "statut": statut, "sha256_attendu": sha_section}
    if sha_actuel is not None:
        entree["sha256_actuel"] = sha_actuel
    lignes.append(entree)
    if statut in {"ABSENTE", "DIVERGENTE"}:
        actions.append(
            _action(entree["cible"], sha_section, sha_source, "REGENERER_SECTION", avant=entree, identite=identite)
        )
    elif statut != "PRESENTE_IDENTIQUE":
        incertitudes = True

    manifeste_actuel = _empreinte_fichier(racine, MANIFESTE)
    statut = manifeste_actuel["statut"]
    if statut == "PRESENT":
        statut = "PRESENTE_IDENTIQUE" if manifeste_actuel["sha256"] == hashlib.sha256(
            texte_manifeste.encode("utf-8")
        ).hexdigest() else "DIVERGENTE"
    entree = {
        "cible": MANIFESTE,
        "statut": statut,
        "sha256_attendu": hashlib.sha256(texte_manifeste.encode("utf-8")).hexdigest(),
    }
    if "sha256" in manifeste_actuel:
        entree["sha256_actuel"] = manifeste_actuel["sha256"]
    lignes.append(entree)
    if statut in {"ABSENTE", "DIVERGENTE"}:
        actions.append(
            _action(
                MANIFESTE,
                entree["sha256_attendu"],
                sha_source,
                "REGENERER_MANIFESTE",
                avant=entree,
                identite=identite,
            )
        )
    elif statut != "PRESENTE_IDENTIQUE":
        incertitudes = True

    statut_global = "NON VÉRIFIÉ" if incertitudes else ("WARN" if actions or propositions_liens else "PASS")
    return {
        "schema": 1,
        "statut": statut_global,
        "code": code_statut(statut_global),
        "application_effectuee": False,
        "source": {
            "cible": "benchab.sqlite#vues",
            "sha256_rendu_attendu": sha_source,
            **identite,
        },
        "vues": lignes,
        "actions": actions,
        "propositions_liens": propositions_liens,
        "preconditions_futures_requises": {
            "sauvegarde_verifiee_avant": True,
            "source_recalculee_avant_application": True,
            "verification_apres_application": True,
        },
    }


def appliquer_vue(prive: Path | str, rapport: dict[str, Any], cible: str) -> dict[str, Any]:
    """Répare une seule vue privée depuis un plan exact ; renvoie PASS, refuse toute reprise ou source périmée.

    Entrées : racine privée, rapport JSON de `planifier`, cible relative canonique ; SHA-256 sans unité.
    Sortie : cible et sauvegarde relative, statut PASS. La source SQLite reste RO ; projets et tables ne changent pas.
    """
    from contextlib import closing

    cles_rapport = {
        "schema",
        "statut",
        "code",
        "application_effectuee",
        "source",
        "vues",
        "actions",
        "preconditions_futures_requises",
    }
    cles_rapport_avec_propositions = cles_rapport | {"propositions_liens"}
    if type(rapport) is not dict or set(rapport) not in (cles_rapport, cles_rapport_avec_propositions):
        raise ValueError("Plan incomplet ou avec un champ inattendu.")
    if (
        type(rapport["schema"]) is not int
        or rapport["schema"] != 1
        or rapport["statut"] != "WARN"
        or rapport["application_effectuee"] is not False
        or rapport["preconditions_futures_requises"]
        != {
            "sauvegarde_verifiee_avant": True,
            "source_recalculee_avant_application": True,
            "verification_apres_application": True,
        }
    ):
        raise ValueError("Plan de réparation non applicable.")
    if not isinstance(cible, str) or not (
        cible == "vues/INDEX.md" or re.fullmatch(r"vues/etats/[0-9a-f]{24}\.md", cible)
    ):
        raise ValueError("Seules les vues canoniques privées sont applicables.")
    source_plan = rapport["source"]
    cles_source = {"cible", "sha256_rendu_attendu", "application_id", "user_version"}
    if type(source_plan) is not dict or set(source_plan) != cles_source:
        raise ValueError("Source du plan incomplète ou avec un champ inattendu.")
    sha_source = source_plan["sha256_rendu_attendu"]
    identite_plan = {"application_id": source_plan["application_id"], "user_version": source_plan["user_version"]}
    if (
        source_plan["cible"] != "benchab.sqlite#vues"
        or not _sha256_valide(sha_source)
        or type(identite_plan["application_id"]) is not int
        or type(identite_plan["user_version"]) is not int
        or identite_plan != {"application_id": APPLICATION_ID, "user_version": 1}
    ):
        raise ValueError("Identité ou empreinte source du plan invalide.")

    actions = rapport["actions"]
    if not isinstance(actions, list):
        raise ValueError("Liste d'actions du plan invalide.")
    candidates = [action for action in actions if type(action) is dict and action.get("cible") == cible]
    if len(candidates) != 1:
        raise ValueError("Le plan doit contenir une seule action pour cette vue.")
    action = candidates[0]
    if set(action) != {"action", "cible", "sha256_attendu", "cible_avant", "source", "preconditions_requises"}:
        raise ValueError("Action incomplète ou avec un champ inattendu.")
    cible_avant = action["cible_avant"]
    source_action = action["source"]
    preconditions = action["preconditions_requises"]
    if (
        action["action"] != "REGENERER_VUE"
        or not _sha256_valide(action["sha256_attendu"])
        or type(cible_avant) is not dict
        or set(cible_avant) != {"statut", "sha256", "identite_sha256"}
        or type(source_action) is not dict
        or set(source_action) != {"type", "sha256", "application_id", "user_version"}
        or source_action
        != {
            "type": "rendu des vues depuis SQLite en lecture seule",
            "sha256": sha_source,
            **identite_plan,
        }
        or type(preconditions) is not dict
        or set(preconditions)
        != {
            "sauvegarde_verifiee_avant",
            "rendu_source_recalcule_et_identique",
            "cible_inchangee_et_reverifiee_sans_lien",
            "controle_apres_application",
        }
        or not all(value is True for value in preconditions.values())
    ):
        raise ValueError("Action du plan invalide ou avec une précondition inattendue.")
    avant_statut, avant_sha, avant_identite = (
        cible_avant["statut"],
        cible_avant["sha256"],
        cible_avant["identite_sha256"],
    )
    if avant_statut == "ABSENTE":
        if avant_sha is not None or avant_identite is not None:
            raise ValueError("État absent incohérent dans le plan.")
    elif avant_statut == "DIVERGENTE":
        if not _sha256_valide(avant_sha) or not _sha256_valide(avant_identite):
            raise ValueError("Empreinte de la cible antérieure invalide.")
    else:
        raise ValueError("Le plan ne prévoit pas une cible absente ou divergente.")

    lignes = rapport["vues"]
    if not isinstance(lignes, list):
        raise ValueError("Vues du plan invalides.")
    lignes_cible = [ligne for ligne in lignes if type(ligne) is dict and ligne.get("cible") == cible]
    if len(lignes_cible) != 1:
        raise ValueError("Le plan ne contient pas une ligne unique pour cette vue.")
    ligne = lignes_cible[0]
    if (
        set(ligne) - {"cible", "statut", "sha256_attendu", "sha256_actuel", "identite_sha256"}
        or ligne.get("statut") != avant_statut
        or ligne.get("sha256_attendu") != action["sha256_attendu"]
        or ligne.get("sha256_actuel") != avant_sha
        or ligne.get("identite_sha256") != avant_identite
    ):
        raise ValueError("Ligne de vue et action incohérentes dans le plan.")

    racine = atelier_prive(prive).absolute()
    base = sans_lien(racine / "benchab.sqlite", racine=racine, strict=True)
    entete = _entete_sqlite(base)
    if (
        not entete.startswith(b"SQLite format 3\0")
        or len(entete) != 20
        or entete[18:20] != b"\x01\x01"
    ):
        raise ValueError("Format de journal SQLite non pris en charge pour la réparation.")
    cible_path = sans_lien(racine / cible, racine=racine)
    if cible_path.parent.is_symlink() or not cible_path.parent.is_dir():
        raise ValueError("Dossier parent absent ou lié ; aucune réparation tentée.")
    sans_lien(cible_path.parent, racine=racine, strict=True)

    # Le verrou est partagé avec `vues.generer` : la sauvegarde et le remplacement portent sur un même instantané.
    with verrou_publication(racine / "cache", "vues.lock"):
        uri = base.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connexion:
            connexion.row_factory = sqlite3.Row
            connexion.execute("PRAGMA query_only = ON")
            connexion.execute("BEGIN")
            application_id = connexion.execute("PRAGMA application_id").fetchone()[0]
            version = connexion.execute("PRAGMA user_version").fetchone()[0]
            if {"application_id": application_id, "user_version": version} != identite_plan:
                raise ValueError("Identité ou schéma SQLite modifié depuis le plan.")
            rendu = vues._rendre(connexion)
            fiches = vues._fiches_filaments(connexion)
            attendus, empreintes_attendues, source_courante = _source_logique(rendu, fiches)
            if source_courante != sha_source:
                raise ValueError("Rendu SQLite modifié depuis le plan.")
            texte_attendu = attendus.get(cible)
            if texte_attendu is None or empreintes_attendues.get(cible) != action["sha256_attendu"]:
                raise ValueError("Cible absente du rendu canonique ou SHA attendu incohérent.")

            actuel = _empreinte_fichier(racine, cible, lecture=True)
            if avant_statut == "ABSENTE":
                if actuel["statut"] != "ABSENTE":
                    raise ValueError("La cible est apparue depuis le plan.")
                ancien = None
            else:
                if (
                    actuel["statut"] != "PRESENT"
                    or actuel.get("sha256") != avant_sha
                    or _sha_identite(actuel.get("identite")) != avant_identite
                    or not isinstance(actuel.get("contenu"), bytes)
                ):
                    raise ValueError("La cible divergente a changé depuis le plan ou ne peut être lue sûrement.")
                ancien = actuel["contenu"]

            sauvegarde_rel = None
            sauvegarde_sha = None
            if ancien is not None:
                sauvegarde_sha = hashlib.sha256(ancien).hexdigest()
                sauvegarde = racine / "sauvegardes/vues" / f"vue-{uuid.uuid4().hex}-{sauvegarde_sha[:24]}.md"
                with PublicationNeuve([cible_path], [sauvegarde]) as publication:
                    temporaire = publication.temporaire(sauvegarde)
                    temporaire.write_bytes(ancien)
                    publication.publier()
                sauvegarde_rel = sauvegarde.relative_to(racine).as_posix()
                relue = _empreinte_fichier(racine, sauvegarde_rel, lecture=True)
                if (
                    relue["statut"] != "PRESENT"
                    or relue.get("contenu") != ancien
                    or relue.get("sha256") != sauvegarde_sha
                ):
                    raise ValueError("Sauvegarde antérieure non relue à l'identique ; cible conservée.")

            seconde_lecture = _empreinte_fichier(racine, cible, lecture=True)
            if avant_statut == "ABSENTE":
                if seconde_lecture["statut"] != "ABSENTE":
                    raise ValueError("La cible est apparue avant le remplacement.")
            elif (
                seconde_lecture["statut"] != "PRESENT"
                or seconde_lecture.get("sha256") != avant_sha
                or _sha_identite(seconde_lecture.get("identite")) != avant_identite
                or seconde_lecture.get("contenu") != ancien
            ):
                raise ValueError("La cible a changé après sauvegarde ; remplacement refusé.")

            if avant_statut == "ABSENTE":
                # PublicationNeuve refuse une apparition concurrente à la place vide observée (#119).
                with PublicationNeuve([], [cible_path]) as publication:
                    publication.temporaire(cible_path).write_text(texte_attendu, encoding="utf-8")
                    publication.publier()
            else:
                remplacer_texte(cible_path, texte_attendu)
            apres = _empreinte_fichier(racine, cible, lecture=True)
            if apres["statut"] != "PRESENT" or apres.get("sha256") != action["sha256_attendu"]:
                raise RuntimeError("Contrôle après réparation échoué ; statut NON VÉRIFIÉ.")

    return {
        "statut": "PASS",
        "application_effectuee": True,
        "cible": cible,
        "sha256": action["sha256_attendu"],
        "sauvegarde": sauvegarde_rel,
        "sha256_sauvegarde": sauvegarde_sha,
    }


def _instantane_lien(racine: Path, relatif: str, *, cible_absente=True):
    """Relit un lien feuille et sa cible relative, sans suivre le lien ni un parent lié."""
    chemin = PurePosixPath(relatif)
    parent = racine
    for composant in chemin.parts[:-1]:
        parent = parent / composant
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError("Parent de lien absent ou symbolique.")
    lien = parent / chemin.parts[-1]
    info = lien.lstat()
    if not stat.S_ISLNK(info.st_mode):
        raise ValueError("Le lien de vue a changé depuis le plan.")
    texte = os.readlink(lien)
    apres_lecture = lien.lstat()
    identite = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    identite_apres = (
        apres_lecture.st_dev, apres_lecture.st_ino, apres_lecture.st_size,
        apres_lecture.st_mtime_ns, apres_lecture.st_ctime_ns,
    )
    if identite != identite_apres:
        raise ValueError("Le lien a changé pendant sa lecture.")
    cible_brute = PurePosixPath(texte)
    if (
        cible_brute.is_absolute() or not cible_brute.parts
        or any(part in {"", ".", ".."} for part in texte.split("/"))
    ):
        raise ValueError("Cible de lien absolue, sortante ou ambiguë.")
    destination_rel = (chemin.parent / cible_brute).as_posix()
    destination = racine / destination_rel
    if not destination_rel.startswith("vues/") or destination_rel == MANIFESTE:
        raise ValueError("La cible du lien sort des vues ou désigne le manifeste.")
    courant = racine
    for composant in PurePosixPath(destination_rel).parts[:-1]:
        courant = courant / composant
        if courant.is_symlink() or not courant.is_dir():
            raise ValueError("Parent de destination absent ou symbolique.")
    try:
        info_destination = destination.lstat()
    except FileNotFoundError:
        if not cible_absente:
            raise ValueError("La copie publiée est absente.")
    else:
        if cible_absente or not stat.S_ISREG(info_destination.st_mode):
            raise ValueError("La cible du lien n'est pas dans l'état attendu.")
    return lien, texte, destination, destination_rel, identite


def appliquer_candidat_lien(
    prive: Path | str, rapport: dict[str, Any], cible: str, candidat: str
) -> dict[str, Any]:
    """Copie une candidate privée exacte vers la cible absente d'un lien de vue cassé.

    Entrées : atelier privé, plan explicite, chemins relatifs de vue et de candidate.
    Sortie : PASS et preuve privée relue, ou refus sans annoncer de réparation.
    Invariants : conserve le lien et la candidate, refuse cible canonique/manifeste,
    revalide SQLite sous le verrou, prouve l'état antérieur et publie la copie exclusivement.
    """
    if type(rapport) is not dict or type(candidat) is not str:
        raise ValueError("Plan ou candidat invalide.")
    if (
        rapport.get("schema") != 1 or type(rapport.get("schema")) is not int
        or rapport.get("statut") != "WARN" or rapport.get("application_effectuee") is not False
    ):
        raise ValueError("Plan de réparation non applicable.")
    if not isinstance(cible, str) or not (
        cible == "vues/INDEX.md" or re.fullmatch(r"vues/etats/[0-9a-f]{24}\.md", cible)
    ):
        raise ValueError("Seules les vues canoniques privées peuvent porter un lien à réparer.")
    source_plan = rapport.get("source")
    if type(source_plan) is not dict or set(source_plan) != {
        "cible", "sha256_rendu_attendu", "application_id", "user_version"
    } or (
        source_plan["cible"] != "benchab.sqlite#vues"
        or not _sha256_valide(source_plan["sha256_rendu_attendu"])
        or source_plan["application_id"] != APPLICATION_ID or source_plan["user_version"] != 1
    ):
        raise ValueError("Source du plan invalide.")
    propositions = rapport.get("propositions_liens")
    correspondantes = [
        item for item in propositions if type(item) is dict
        and item.get("cible") == cible and item.get("candidat") == candidat
    ] if isinstance(propositions, list) else []
    if len(correspondantes) != 1:
        raise ValueError("Le plan ne contient pas une proposition unique pour ce candidat.")
    proposition = correspondantes[0]
    if (
        set(proposition) != {
            "action", "cible", "candidat", "sha256", "source_sha256_rendu",
            "lien_sha256", "lien_identite_sha256", "destination_sha256",
            "application_effectuee", "limite",
        }
        or proposition["action"] != "PROPOSER_LIEN_CANDIDAT_SHA_EXACT"
        or proposition["application_effectuee"] is not False
        or not _sha256_valide(proposition["sha256"])
        or proposition["source_sha256_rendu"] != source_plan["sha256_rendu_attendu"]
        or proposition["limite"] != "Identité des octets prouvée ; origine et pertinence historique non établies."
    ):
        raise ValueError("Proposition de lien invalide ou avec champ inattendu.")
    chemin_candidat = PurePosixPath(candidat)
    if chemin_candidat.is_absolute() or not chemin_candidat.parts or any(
        part in {"", ".", ".."} for part in chemin_candidat.parts
    ):
        raise ValueError("Le candidat doit être un chemin privé relatif sans remontée.")

    racine = atelier_prive(prive).absolute()
    base = sans_lien(racine / "benchab.sqlite", racine=racine, strict=True)
    entete = _entete_sqlite(base)
    if not entete.startswith(b"SQLite format 3\0") or len(entete) != 20 or entete[18:20] != b"\x01\x01":
        raise ValueError("Format de journal SQLite non pris en charge pour la reprise.")

    # Le verrou commun maintient le rendu, la preuve et la publication cohérents avec le générateur.
    with verrou_publication(racine / "cache", "vues.lock"):
        lien, texte_lien, destination, destination_rel, identite_lien = _instantane_lien(racine, cible)
        if (
            proposition["lien_sha256"] != hashlib.sha256(os.fsencode(texte_lien)).hexdigest()
            or proposition["lien_identite_sha256"] != _sha_identite(identite_lien)
            or proposition["destination_sha256"] != hashlib.sha256(destination_rel.encode("utf-8")).hexdigest()
        ):
            raise ValueError("Lien modifié depuis le plan.")
        actuel = _empreinte_fichier(racine, candidat, lecture=True)
        if actuel["statut"] != "PRESENT" or actuel.get("sha256") != proposition["sha256"]:
            raise ValueError("Candidate absente, liée ou modifiée depuis le plan.")
        with closing(sqlite3.connect(base.as_uri() + "?mode=ro", uri=True, timeout=1)) as connexion:
            connexion.row_factory = sqlite3.Row
            connexion.execute("PRAGMA query_only = ON")
            connexion.execute("BEGIN")
            application_id = connexion.execute("PRAGMA application_id").fetchone()[0]
            version = connexion.execute("PRAGMA user_version").fetchone()[0]
            identite_source = {"application_id": application_id, "user_version": version}
            if identite_source != {
                "application_id": source_plan["application_id"], "user_version": source_plan["user_version"],
            }:
                raise ValueError("Identité SQLite modifiée depuis le plan.")
            rendu = vues._rendre(connexion)
            fiches = vues._fiches_filaments(connexion)
            attendus, empreintes_attendues, sha_source = _source_logique(rendu, fiches)
        if sha_source != source_plan["sha256_rendu_attendu"]:
            raise ValueError("Rendu SQLite modifié depuis le plan.")
        if cible not in attendus or empreintes_attendues[cible] != proposition["sha256"]:
            raise ValueError("La candidate ne correspond pas au rendu canonique actuel.")
        if destination_rel in attendus or destination_rel == MANIFESTE:
            raise ValueError("La cible désigne une vue canonique ou le manifeste.")

        # Le plan complet est relu sous verrou : il confirme lien cassé, candidat unique et source identique.
        replan = planifier(racine, candidats=[candidat])
        nouvelles = [
            item for item in replan["propositions_liens"]
            if item.get("cible") == cible and item.get("candidat") == candidat
        ]
        if replan["statut"] != "WARN" or len(nouvelles) != 1 or nouvelles[0] != proposition:
            raise ValueError("La proposition ne correspond plus au lien, au candidat ou au rendu.")
        _, texte_relu, destination_relue, chemin_relu, identite_relue = _instantane_lien(racine, cible)
        relu = _empreinte_fichier(racine, candidat, lecture=True)
        if (
            texte_relu != texte_lien or destination_relue != destination or chemin_relu != destination_rel
            or identite_relue != identite_lien or relu["statut"] != "PRESENT"
            or relu.get("sha256") != proposition["sha256"] or relu.get("identite") != actuel.get("identite")
        ):
            raise ValueError("Lien ou candidate modifié pendant la relecture ; reprise refusée.")

        donnees_preuve = {
            "schema": 1, "action": "COPIER_CANDIDAT_VERS_CIBLE_DE_LIEN_ABSENTE",
            "cible_vue": cible, "lien_relatif": texte_lien, "destination_relative": destination_rel,
            "cible_avant": "ABSENTE", "identite_lien_sha256": _sha_identite(identite_lien),
            "candidat": candidat, "candidat_sha256": proposition["sha256"],
            "source_sha256_rendu": sha_source,
            "origine_historique_et_pertinence": "NON VÉRIFIÉES",
        }
        texte_preuve = json.dumps(donnees_preuve, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        preuve = racine / "sauvegardes/vues" / (
            f"reprise-lien-{uuid.uuid4().hex}-{proposition['sha256'][:24]}.json"
        )
        with PublicationNeuve([], [preuve]) as publication:
            publication.temporaire(preuve).write_text(texte_preuve, encoding="utf-8")
            publication.publier()
        preuve_rel = preuve.relative_to(racine).as_posix()
        sauvegarde = _empreinte_fichier(racine, preuve_rel, lecture=True)
        if sauvegarde["statut"] != "PRESENT" or sauvegarde.get("contenu") != texte_preuve.encode("utf-8"):
            raise ValueError("La preuve privée antérieure n'a pas été relue à l'identique.")

        # Une cible apparue depuis le plan est refusée par la publication exclusive.
        relu = _empreinte_fichier(racine, candidat, lecture=True)
        _, texte_final, destination_finale, chemin_final, identite_finale = _instantane_lien(racine, cible)
        if (
            texte_final != texte_lien or destination_finale != destination or chemin_final != destination_rel
            or identite_finale != identite_lien or relu["statut"] != "PRESENT"
            or relu.get("sha256") != proposition["sha256"] or relu.get("identite") != actuel.get("identite")
        ):
            raise ValueError("Préconditions changées après preuve ; la copie n'est pas publiée.")
        with PublicationNeuve([actuel["chemin"]], [destination]) as publication:
            publication.temporaire(destination).write_bytes(relu["contenu"])
            publication.publier()
        apres = _empreinte_fichier(racine, destination_rel, lecture=True)
        _, texte_apres, destination_apres, chemin_apres, identite_apres = _instantane_lien(
            racine, cible, cible_absente=False
        )
        if (
            apres["statut"] != "PRESENT" or apres.get("sha256") != proposition["sha256"]
            or apres.get("contenu") != relu["contenu"] or texte_apres != texte_lien
            or destination_apres != destination or chemin_apres != destination_rel
            or identite_apres != identite_lien
        ):
            raise RuntimeError("Contrôle après reprise échoué ; NON VÉRIFIÉ, preuve conservée.")

    return {
        "statut": "PASS", "application_effectuee": True, "cible_vue": cible,
        "destination": destination_rel, "sha256": proposition["sha256"], "lien_conserve": True,
        "preuve": preuve_rel, "sha256_preuve": sauvegarde["sha256"],
        "origine_historique_et_pertinence": "NON VÉRIFIÉES",
    }


def _sha256_valide(valeur):
    """Vérifie une empreinte SHA-256 hexadécimale canonique de 64 caractères."""
    return isinstance(valeur, str) and bool(re.fullmatch(r"[0-9a-f]{64}", valeur))
