"""Plan numérique des estimations de filament d'un retour ; aucune imputation."""

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path
from zipfile import BadZipFile

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive, sans_lien
from benchab.donnees.base import APPLICATION_ID, Base
from benchab.fabrication import envois_slicer
from benchab.qualite.ecritures import _sans_lien

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PLAQUE = re.compile(r"Metadata/plate_(\d+)\.gcode\Z")
_MAX_FICHE = 8 * 1024 * 1024
_MAX_PREUVE_PESEE = 64 * 1024
_MAX_LIGNE_GCODE = 1024 * 1024
_T_EXPLICITE = re.compile(rb"[ \t]*T([0-9]{1,3})[ \t]*(?:;[^\r\n]*)?(?:\r?\n)?")
_CHAMPS_PESEE = {
    "schema", "retour_id", "envoi_sha256", "plaque", "membre_gcode", "gcode_sha256",
    "index_gcode", "emplacement_declare", "bobine_id", "masse_avant_g", "masse_apres_g",
}


def _json_objet(texte: str) -> dict:
    """Décode un objet JSON privé sans clé répétée ni constante numérique non finie."""
    def constante(_: str):
        raise ValueError("constante JSON non finie")

    def paires_uniques(paires):
        """Refuse les clés répétées, y compris dans les objets JSON imbriqués."""
        resultat = {}
        for cle, valeur in paires:
            if cle in resultat:
                raise ValueError("clé JSON dupliquée")
            resultat[cle] = valeur
        return resultat

    resultat = json.loads(texte, parse_constant=constante, object_pairs_hook=paires_uniques)
    if not isinstance(resultat, dict):
        raise ValueError("objet JSON attendu")
    return resultat


def _nv(retour_id: str, motif: str) -> dict:
    """Construit un refus conservateur sans estimation ni écriture."""
    return {
        "schema": 1,
        "retour_id": retour_id,
        "statut": "NON VÉRIFIÉ",
        "estimation": None,
        "affectation_bobine": None,
        "motifs": [motif],
        "limites": [
            "Le fichier envoyé ne prouve pas le fichier exécuté.",
            "Aucun outil n’est relié à un emplacement ACE ou à une bobine réelle.",
            "Les grammes du G-code sont une estimation du slicer, pas une mesure physique.",
        ],
    }


def _nv_cli(retour_id: str, motif: str) -> dict:
    """Construit un rapport de commande au code 2 tant que la consommation reste NV."""
    resultat = _nv(retour_id, motif)
    resultat["code"] = 2
    return resultat


def _chemin_prive(prive: Path, relatif: str) -> Path:
    """Résout un fichier régulier dans l'atelier, sans traversée ni lien symbolique."""
    chemin_relatif = Path(relatif)
    if chemin_relatif.is_absolute() or ".." in chemin_relatif.parts or not chemin_relatif.parts:
        raise ValueError("chemin privé invalide")
    chemin = _sans_lien(prive / chemin_relatif)
    if not chemin.is_relative_to(prive.resolve()) or not chemin.is_file():
        raise ValueError("fichier privé absent ou hors atelier")
    if not stat.S_ISREG(chemin.stat().st_mode):
        raise ValueError("fichier privé non régulier")
    return chemin


def _lire_fiche_bornee(chemin: Path, *, limite=_MAX_FICHE) -> bytes:
    """Lit une fiche privée régulière jusqu’à sa limite en octets, sans lien."""
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > limite:
            raise ValueError("fiche non régulière ou au-delà de la limite de lecture")
        contenu = flux.read(limite + 1)
        apres = os.fstat(flux.fileno())
        chemin_apres = chemin.lstat()
        signature_avant = (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns)
        signature_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
        signature_chemin = (
            chemin_apres.st_dev, chemin_apres.st_ino, chemin_apres.st_size,
            chemin_apres.st_mtime_ns, chemin_apres.st_ctime_ns,
        )
        if (
            len(contenu) > limite
            or signature_avant != signature_apres
            or signature_avant != signature_chemin
            or stat.S_ISLNK(chemin_apres.st_mode)
        ):
            raise ValueError("fiche trop grande ou modifiée pendant sa lecture")
        return contenu


def _association(connexion: sqlite3.Connection, projet_id: str, preparation_id: str, fiche_sha: str,
                 fichier: str, envoi_sha: str) -> dict | None:
    """Retrouve l'unique association technique qui relie préparation et archive par SHA."""
    lignes = connexion.execute(
        "SELECT donnees_json FROM decisions WHERE projet_id=? "
        "AND json_extract(donnees_json,'$.type')=? "
        "AND json_extract(donnees_json,'$.preparation_id')=?",
        (projet_id, "association_fichier_preparation", preparation_id),
    ).fetchall()
    correspondances = []
    for ligne in lignes:
        evenement = _json_objet(ligne[0])
        envoi = evenement.get("envoi")
        if (
            evenement.get("fiche_sha256") == fiche_sha
            and isinstance(envoi, dict)
            and envoi.get("fichier") == fichier
            and envoi.get("sha256") == envoi_sha
        ):
            correspondances.append(evenement)
    return correspondances[0] if len(correspondances) == 1 else None


def _lire_retour(connexion: sqlite3.Connection, retour_id: str):
    """Lit une chaîne relationnelle retour → impression → préparation, sans migration."""
    lignes = connexion.execute(
        "SELECT r.id,r.impression_id,r.donnees_json AS retour_json,i.preparation_id,i.resultat,"
        "i.donnees_json AS impression_json,"
        "p.projet_id,p.nom,p.empreinte,p.donnees_json "
        "FROM retours AS r JOIN impressions AS i ON i.id=r.impression_id "
        "JOIN preparations AS p ON p.id=i.preparation_id WHERE r.id=?",
        (retour_id,),
    ).fetchall()
    if len(lignes) != 1:
        return None
    ligne = lignes[0]
    retour = _json_objet(ligne["retour_json"])
    impression = _json_objet(ligne["impression_json"])
    return ligne, retour, impression


def estimer_retour(base: Base, retour_id: str) -> dict:
    """Produit un plan en g par plaque et index G-code, sans écrire ni imputer.

    Entrées : atelier privé `Base` et identifiant de retour. Sortie : dict de statut
    NON VÉRIFIÉ, avec estimations du slicer en grammes uniquement si le retour est
    complet et réussi et si préparation, envoi et SHA concordent. Aucun réglage,
    bobine, stock ou fichier source n'est modifié ; l'exécution physique demeure
    inconnue même lorsque le calcul numérique est disponible.
    """
    if not isinstance(base, Base) or not isinstance(retour_id, str) or not retour_id.strip():
        raise ValueError("Atelier BENCHAB et identifiant de retour explicites requis.")
    return _estimer_retour(base.prive, base.connexion, retour_id)


def estimer_retour_ro(prive: Path | str, connexion: sqlite3.Connection, retour_id: str) -> dict:
    """Produit le même plan depuis une connexion SQLite RO en transaction query_only.

    Entrées : racine privée, connexion préalablement ouverte en mode ro et query_only,
    snapshot transactionnel et identifiant de retour. Sortie en g, toujours NON VÉRIFIÉ.
    Aucun dossier, fichier, réglage ou stock n'est créé ou modifié.
    """
    try:
        if not connexion.in_transaction or connexion.execute("PRAGMA query_only").fetchone()[0] != 1:
            return _nv(retour_id, "snapshot SQLite en lecture seule requis")
        return _estimer_retour(Path(prive), connexion, retour_id)
    except (sqlite3.Error, OverflowError, OSError, ValueError, TypeError) as erreur:
        return _nv(retour_id, f"source incomplète ou illisible : {type(erreur).__name__}")


def _comparer_duree(retour, details, contexte_envoi=None):
    """Compare des secondes déclarées et estimées pour une seule plaque du retour complet."""
    if len(details) != 1:
        return None
    observee = retour.get("duree_observee_s")
    estimee = details[0].get("duree_estimee_s")
    if any(type(v) not in (int, float) or not 0 < v <= sys.float_info.max for v in (observee, estimee)):
        return None
    contexte_envoi = contexte_envoi or {}
    matieres = contexte_envoi.get("filament_type")
    qualifiees = isinstance(matieres, list) and bool(matieres) and all(
        isinstance(matiere, str) and matiere.strip() for matiere in matieres
    )
    contexte = {
        "motif_clairseme_projet": contexte_envoi.get("sparse_infill_pattern"),
        "matieres_declarees_par_index": matieres if qualifiees else None,
        "version_asn_declaree": details[0].get("version_asn_declaree"),
    }
    # Un historique sans mesure exploitable ne reçoit aucune correction de planning (#51).
    return {
        "gcode": details[0]["gcode"], "duree_observee_s": observee, "duree_estimee_s": estimee,
        "ecart_s": observee - estimee, "nature": "DECLARATION_HUMAINE_ET_ESTIMATION_SLICER",
        "contexte": contexte,
    }


def _estimer_retour(prive: Path, connexion: sqlite3.Connection, retour_id: str) -> dict:
    """Lit les preuves relationnelles et l'envoi ; n'ouvre pas l'atelier et n'écrit rien."""
    try:
        relation = _lire_retour(connexion, retour_id)
        if relation is None:
            return _nv(retour_id, "retour, impression ou préparation introuvable")
        ligne, retour, impression = relation
        # Les lignes liées du parcours courant conservent la même déclaration.
        if impression != retour:
            return _nv(retour_id, "déclarations du retour et de l’impression divergentes")
        if (
            retour.get("id") != retour_id
            or retour.get("preparation") != ligne["nom"]
            or retour.get("resultat") != "reussie"
            or ligne["resultat"] != "reussie"
            or retour.get("portee") != "ENSEMBLE"
        ):
            return _nv(retour_id, "retour non réussi ou portée incomplète")
        fiche_sha = ligne["empreinte"]
        if not isinstance(fiche_sha, str) or _SHA256.fullmatch(fiche_sha) is None:
            return _nv(retour_id, "empreinte de préparation absente ou invalide")
        projet_id = ligne["projet_id"]
        chemin_projet = Path(projet_id)
        if chemin_projet.is_absolute() or ".." in chemin_projet.parts or not chemin_projet.parts:
            return _nv(retour_id, "identité de projet invalide")
        preparation = _chemin_prive(
            prive, str(Path(projet_id) / "preparations" / ligne["nom"] / "PREPARATION.json")
        )
        octets_fiche = _lire_fiche_bornee(preparation)
        if hashlib.sha256(octets_fiche).hexdigest() != fiche_sha:
            return _nv(retour_id, "fiche de préparation absente ou empreinte divergente")
        fiche = _json_objet(octets_fiche.decode("utf-8"))
        fiche_en_base = _json_objet(ligne["donnees_json"])
        if fiche.get("id") != ligne["nom"] or fiche != fiche_en_base:
            return _nv(retour_id, "identité de la fiche de préparation divergente")
        envois = retour.get("envois_identifies")
        if not isinstance(envois, list) or len(envois) != 1:
            return _nv(retour_id, "envoi absent ou ambigu dans le retour")
        envoi = envois[0]
        if not isinstance(envoi, dict):
            return _nv(retour_id, "déclaration d’envoi invalide")
        chemin_envoi, envoi_sha = envoi.get("chemin"), envoi.get("sha256")
        if not isinstance(chemin_envoi, str) or not isinstance(envoi_sha, str) or _SHA256.fullmatch(envoi_sha) is None:
            return _nv(retour_id, "identité ou empreinte d’envoi invalide")
        association = _association(connexion, projet_id, ligne["preparation_id"], fiche_sha, chemin_envoi, envoi_sha)
        if association is None:
            return _nv(retour_id, "association d’envoi absente ou ambiguë")
        chemin = _chemin_prive(prive, chemin_envoi)
        try:
            rapport = envois_slicer.lire(
                chemin, sha256_attendu=envoi_sha, inclure_durees=retour.get("duree_observee_s") is not None,
            )
        except (OSError, ValueError):
            # Préserver le motif de l’association rompue même si l’index ZIP ne s’ouvre plus (#223).
            return _nv(retour_id, "envoi absent, ambigu ou empreinte divergente")
        details = rapport.get("details_plaques")
        if not isinstance(details, list) or not details:
            return _nv(retour_id, "détails de plaque absents")
        plaques = []
        numeros = set()
        for detail in details:
            membre = detail.get("gcode")
            correspondance = _PLAQUE.fullmatch(membre) if isinstance(membre, str) else None
            masses = detail.get("filament_utilise_g")
            if correspondance is None or not isinstance(masses, list) or not masses:
                return _nv(retour_id, "masse ou identité d’une plaque absente")
            numero = int(correspondance[1])
            if numero < 1:
                return _nv(retour_id, "numéro de plaque inférieur à 1")
            masse_invalide = any(
                type(masse) not in (int, float) or not math.isfinite(masse) or masse < 0
                for masse in masses
            )
            if numero in numeros or masse_invalide:
                return _nv(retour_id, "plaque ambiguë ou masse invalide")
            numeros.add(numero)
            outils_declares = detail.get("filaments_utilises")
            if not isinstance(outils_declares, list) or any(
                type(outil) is not int or outil < 0 for outil in outils_declares
            ):
                return _nv(retour_id, "indices d’outils G-code absents ou invalides")
            if any(outil >= len(masses) for outil in outils_declares):
                return _nv(retour_id, "index d’outil hors du vecteur de masse")
            plaques.append({
                "numero": numero,
                "gcode": membre,
                "outils": [
                    {
                        "index_gcode": index,
                        "grammes_estimes": float(masse),
                        "indice_repere_dans_envoi": index in outils_declares,
                    }
                    for index, masse in enumerate(masses)
                ],
            })
        comparaison_duree = _comparer_duree(retour, details, rapport.get("reglages"))
        return {
            "schema": 1,
            "retour_id": retour_id,
            **({"comparaison_duree": comparaison_duree} if comparaison_duree is not None else {}),
            "preparation": {"id": ligne["preparation_id"], "sha256": fiche_sha},
            "envoi": {"chemin": chemin_envoi, "sha256": envoi_sha},
            "statut": "NON VÉRIFIÉ",
            "estimation": {
                "nature": "ESTIMATION_SLICER",
                "unite": "g",
                "plaques": sorted(plaques, key=lambda p: p["numero"]),
            },
            "affectation_bobine": None,
            "motifs": ["aucune correspondance validée avec un outil physique et une bobine mesurée"],
            "limites": [
                "Le retour est une déclaration humaine ; le fichier envoyé ne prouve pas le fichier exécuté.",
                "L’index du vecteur G-code n’identifie pas un emplacement ACE ni une bobine.",
                "Un index repéré peut provenir d’une masse positive sans commande T explicite.",
                "Les grammes du G-code sont une estimation du slicer, pas une mesure physique.",
            ],
        }
    except (OSError, ValueError, TypeError, KeyError, OverflowError, sqlite3.Error, json.JSONDecodeError) as erreur:
        return _nv(retour_id, f"source incomplète ou illisible : {erreur}")


def _signature_base(chemin: Path):
    """Retourne dev/inode et métadonnées de la base, sans suivre de lien."""
    info = chemin.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("Base SQLite non régulière ou liée")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _entete_sqlite(chemin: Path) -> bytes:
    """Lit 20 octets de l'entête SQLite sans suivre de lien ni ouvrir SQLite."""
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        if not stat.S_ISREG(os.fstat(flux.fileno()).st_mode):
            return b""
        return flux.read(20)


def _fichiers_auxiliaires_absents(chemin: Path) -> bool:
    """Vérifie l'absence de journaux auxiliaires, même sous forme de lien pendant."""
    for suffixe in ("-wal", "-shm", "-journal"):
        try:
            (Path(str(chemin) + suffixe)).lstat()
        except FileNotFoundError:
            continue
        return False
    return True


class _RefusPesee(ValueError):
    """Motif contrôlé qui peut être affiché sans exposer les chemins ni les octets lus."""


def _lier_pesee(prive, connexion, rapport, preuve, sha_preuve, *, exiger_depot=False):
    """Rapproche une déclaration de pesée et les octets du membre, sans imputation."""
    inconnu = {
        "statut": "NON VÉRIFIÉ", "variation_declaree_g": None,
        "imputation": False, "validation_physique": False,
    }
    try:
        if preuve is None or not isinstance(sha_preuve, str) or _SHA256.fullmatch(sha_preuve) is None:
            raise _RefusPesee("Fichier de déclaration et SHA-256 exact requis ensemble.")
        try:
            p = sans_lien(Path(preuve).expanduser().absolute(), racine=prive, strict=True)
        except FileNotFoundError as erreur:
            raise _RefusPesee("Fichier de déclaration absent.") from erreur
        except ValueError as erreur:
            raise _RefusPesee("Fichier de déclaration lié ou hors de l’atelier.") from erreur
        octets = _lire_fiche_bornee(p, limite=_MAX_PREUVE_PESEE)
        if hashlib.sha256(octets).hexdigest() != sha_preuve:
            raise _RefusPesee("SHA-256 de la déclaration différent.")
        d = _json_objet(octets.decode("utf-8"))
        if set(d) != _CHAMPS_PESEE or type(d["schema"]) is not int or d["schema"] != 1:
            raise _RefusPesee("Champs ou schéma de déclaration non reconnus.")
        estimation = rapport.get("estimation")
        envoi = rapport.get("envoi", {})
        if not estimation or d["retour_id"] != rapport["retour_id"] or d["envoi_sha256"] != envoi.get("sha256"):
            raise _RefusPesee("Retour complet et envoi identifié par le même SHA-256 requis.")
        plaque, outil = d["plaque"], d["index_gcode"]
        if type(plaque) is not int or plaque < 1 or type(outil) is not int or not 0 <= outil <= 999:
            raise _RefusPesee("Numéro de plaque ou index G-code non reconnu.")
        choix = next((p for p in estimation["plaques"] if p["numero"] == plaque), None)
        if choix is None or choix["gcode"] != d["membre_gcode"]:
            raise _RefusPesee("Plaque ou membre G-code différent du plan.")
        indice = next((v for v in choix["outils"] if v["index_gcode"] == outil), None)
        if indice is None or not indice["indice_repere_dans_envoi"]:
            raise _RefusPesee("Index G-code absent du plan de cette plaque.")
        for champ in ("emplacement_declare", "bobine_id"):
            valeur = d[champ]
            if not isinstance(valeur, str) or not valeur.strip() or len(valeur) > 120 or not valeur.isprintable():
                raise _RefusPesee("Emplacement et identifiant de bobine explicites requis.")
        if connexion.execute("SELECT 1 FROM bobines WHERE id=?", (d["bobine_id"],)).fetchone() is None:
            raise _RefusPesee("Bobine déclarée introuvable dans cet atelier.")
        avant, apres = d["masse_avant_g"], d["masse_apres_g"]
        if any(type(v) not in (float, int) or not 0 <= v <= 1_000_000 for v in (avant, apres)) or apres > avant:
            raise _RefusPesee("Masses déclarées incohérentes en grammes.")
        sha_gcode = d["gcode_sha256"]
        if not isinstance(sha_gcode, str) or _SHA256.fullmatch(sha_gcode) is None:
            raise _RefusPesee("SHA-256 du membre G-code invalide.")
        fichier = _chemin_prive(prive, envoi["chemin"])
        explicite = False
        depot = False
        lecteur = None
        if exiger_depot:
            from benchab.controles.gcode_interpreter import Interpreter

            lecteur = Interpreter(error_limit=16)
        empreinte = hashlib.sha256()
        # Même lecture pour l’empreinte, les lignes T explicites et le CRC.
        # Le vecteur d’estimation seul peut repérer un outil par masse positive.
        with Archive3MF(fichier) as archive:
            if archive.sha256() != envoi["sha256"]:
                raise _RefusPesee("L’envoi ne correspond plus à son empreinte.")
            with archive.ouvrir_flux(choix["gcode"]) as flux:
                while ligne := flux.readline(_MAX_LIGNE_GCODE + 1):
                    if len(ligne) > _MAX_LIGNE_GCODE:
                        raise _RefusPesee("Ligne G-code au-delà du budget de lecture.")
                    empreinte.update(ligne)
                    selection = _T_EXPLICITE.fullmatch(ligne)
                    if selection is not None and int(selection[1]) == outil:
                        explicite = True
                    if lecteur is not None:
                        mouvement = lecteur.feed_line(ligne.decode("utf-8"))
                        if explicite and mouvement and mouvement["tool"] == outil and mouvement["deposited_length"] > 0:
                            depot = True
            archive.verifier_source(fichier)
        if empreinte.hexdigest() != sha_gcode or not explicite:
            raise _RefusPesee("SHA du membre différent ou aucune sélection T explicite pour cet index.")
        if lecteur is not None and (not depot or lecteur.errors):
            raise _RefusPesee("Aucun dépôt vérifiable pour l’outil confirmé dans le G-code.")
        return {
            **inconnu, "statut": "DÉCLARATION LIÉE",
            "sha256_preuve": sha_preuve, "gcode_sha256": sha_gcode,
            "plaque": plaque, "index_gcode": outil, "membre_gcode": choix["gcode"],
            "emplacement_declare": d["emplacement_declare"], "bobine_id": d["bobine_id"],
            "masse_avant_g": float(avant), "masse_apres_g": float(apres),
            "variation_declaree_g": float(avant) - float(apres),
            "estimation_slicer_g": indice["grammes_estimes"],
            "limite": "Déclaration reliée aux octets et à une bobine connue ; aucune mesure authentifiée.",
        }
    except _RefusPesee as erreur:
        return {**inconnu, "raison": str(erreur)}
    except (BadZipFile, OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as erreur:
        # Les erreurs de bibliothèque peuvent contenir le chemin ou le nom d’un membre privé (#35, #77).
        return {**inconnu, "raison": f"Déclaration ou envoi illisible ({type(erreur).__name__})."}


def planifier_lecture(
    prive: Path | str | None, retour_id: str, *, preuve_pesee=None, sha256_preuve=None,
) -> dict:
    """Construit le rapport CLI depuis SQLite canonical en RO/query_only et snapshot borné.

    Entrées : racine privée éventuelle et identifiant de retour. Sortie JSON de code 2,
    statut NON VÉRIFIÉ (aucune consommation physique n'est établie). Ouvre seulement
    `benchab.sqlite` existant en mode ro, sans Base(), mesure, création ni écriture.
    Un fichier de déclaration et son SHA peuvent relier une variation en g
    à un retour et à un membre exacts, sans imputer ni valider une pesée.
    """
    try:
        racine = atelier_prive(prive).absolute()
        chemin = sans_lien(racine / "benchab.sqlite", racine=racine, strict=True)
        signature = _signature_base(chemin)
        entete = _entete_sqlite(chemin)
        if not entete.startswith(b"SQLite format 3\0") or len(entete) != 20:
            return _nv_cli(retour_id, "base SQLite non reconnue")
        if entete[18:20] != b"\x01\x01":
            return _nv_cli(retour_id, "mode journal WAL ou non qualifié refusé avant ouverture")
        if not _fichiers_auxiliaires_absents(chemin):
            return _nv_cli(retour_id, "fichiers auxiliaires SQLite présents ; source non qualifiée")
        with closing(sqlite3.connect(chemin.as_uri() + "?mode=ro", uri=True, timeout=1.0)) as connexion:
            connexion.row_factory = sqlite3.Row
            connexion.execute("PRAGMA query_only = ON")
            connexion.execute("PRAGMA busy_timeout = 1000")
            connexion.execute("BEGIN")
            identite = connexion.execute("PRAGMA application_id").fetchone()[0]
            version = connexion.execute("PRAGMA user_version").fetchone()[0]
            mode = connexion.execute("PRAGMA journal_mode").fetchone()[0].lower()
            if mode not in {"delete", "truncate", "persist"}:
                return _nv_cli(retour_id, "mode journal SQLite hors rollback refusé")
            if identite != APPLICATION_ID or version != 1:
                return _nv_cli(retour_id, "identité ou schéma BENCHAB non établi")
            rapport = estimer_retour_ro(racine, connexion, retour_id)
            if preuve_pesee is not None or sha256_preuve is not None:
                rapport["declaration_pesee"] = _lier_pesee(racine, connexion, rapport, preuve_pesee, sha256_preuve)
            if _signature_base(chemin) != signature:
                return _nv_cli(retour_id, "base modifiée pendant le snapshot")
        rapport["code"] = 2
        return rapport
    except (OSError, ValueError, TypeError, OverflowError, sqlite3.Error) as erreur:
        return _nv_cli(str(retour_id), f"source privée indisponible : {type(erreur).__name__}")


def resumer(rapport: dict, sortie: Path) -> dict:
    """Résume un rapport complet déjà publié, sans réinterpréter le verdict.

    Entrées : rapport de planifier_lecture et chemin JSON privé. Sortie :
    code et statut NON VÉRIFIÉ, motifs, nombre de plaques estimées et chemin ;
    aucun détail de masse en g, d'affectation ou de bobine n'est ajouté.
    N'écrit rien ; conserver le rapport intégral avant cet affichage (#35).
    """
    estimation = rapport["estimation"]
    resume = {
        "statut": rapport["statut"],
        "code": rapport["code"],
        "rapport": str(sortie),
        "plaques_estimees": len(estimation["plaques"]) if estimation is not None else 0,
        "motifs": rapport["motifs"],
    }
    declaration = rapport.get("declaration_pesee")
    if declaration is not None:
        # Ne pas perdre la cause exacte du refus, sans exposer les masses de la déclaration.
        resume["declaration_pesee"] = {
            cle: declaration[cle] for cle in ("statut", "raison") if cle in declaration
        }
    return resume
