"""Plan numérique des estimations de filament d'un retour ; aucune imputation."""

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path

from benchab.donnees.base import Base
from benchab.fabrication import envois_slicer
from benchab.qualite.ecritures import _sans_lien
from benchab.qualite.empreintes import sha256_fichier

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PLAQUE = re.compile(r"Metadata/plate_(\d+)\.gcode\Z")
_MAX_FICHE = 8 * 1024 * 1024


def _json_objet(texte: str) -> dict:
    """Décode un objet JSON privé sans accepter les constantes numériques non finies."""
    def constante(_: str):
        raise ValueError("constante JSON non finie")

    resultat = json.loads(texte, parse_constant=constante)
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


def _chemin_prive(base: Base, relatif: str) -> Path:
    """Résout un fichier régulier dans l'atelier, sans traversée ni lien symbolique."""
    chemin_relatif = Path(relatif)
    if chemin_relatif.is_absolute() or ".." in chemin_relatif.parts or not chemin_relatif.parts:
        raise ValueError("chemin privé invalide")
    chemin = _sans_lien(base.prive / chemin_relatif)
    if not chemin.is_relative_to(base.prive.resolve()) or not chemin.is_file():
        raise ValueError("fichier privé absent ou hors atelier")
    if not stat.S_ISREG(chemin.stat().st_mode):
        raise ValueError("fichier privé non régulier")
    return chemin


def _lire_fiche_bornee(chemin: Path) -> bytes:
    """Lit la fiche privée régulière en 8 Mio au plus, sans suivre de lien."""
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > _MAX_FICHE:
            raise ValueError("fiche non régulière ou supérieure à 8 Mio")
        contenu = flux.read(_MAX_FICHE + 1)
        apres = os.fstat(flux.fileno())
        chemin_apres = chemin.lstat()
        signature_avant = (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns)
        signature_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
        signature_chemin = (
            chemin_apres.st_dev, chemin_apres.st_ino, chemin_apres.st_size,
            chemin_apres.st_mtime_ns, chemin_apres.st_ctime_ns,
        )
        if (
            len(contenu) > _MAX_FICHE
            or signature_avant != signature_apres
            or signature_avant != signature_chemin
            or stat.S_ISLNK(chemin_apres.st_mode)
        ):
            raise ValueError("fiche trop grande ou modifiée pendant sa lecture")
        return contenu


def _association(base: Base, projet_id: str, preparation_id: str, fiche_sha: str,
                 fichier: str, envoi_sha: str) -> dict | None:
    """Retrouve l'unique association technique qui relie préparation et archive par SHA."""
    lignes = base.connexion.execute(
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


def _lire_retour(base: Base, retour_id: str):
    """Lit une chaîne relationnelle retour → impression → préparation, sans migration."""
    lignes = base.connexion.execute(
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
    try:
        relation = _lire_retour(base, retour_id)
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
            base, str(Path(projet_id) / "preparations" / ligne["nom"] / "PREPARATION.json")
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
        association = _association(base, projet_id, ligne["preparation_id"], fiche_sha, chemin_envoi, envoi_sha)
        if association is None:
            return _nv(retour_id, "association d’envoi absente ou ambiguë")
        chemin = _chemin_prive(base, chemin_envoi)
        if sha256_fichier(chemin) != envoi_sha:
            return _nv(retour_id, "envoi absent, ambigu ou empreinte divergente")
        rapport = envois_slicer.lire(chemin)
        if rapport.get("sha256") != envoi_sha:
            return _nv(retour_id, "empreinte relue de l’envoi divergente")
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
            plaques.append({
                "numero": numero,
                "gcode": membre,
                "outils": [
                    {
                        "index_gcode": index,
                        "grammes_estimes": float(masse),
                        "selectionne_dans_gcode": index in outils_declares,
                    }
                    for index, masse in enumerate(masses)
                ],
            })
        return {
            "schema": 1,
            "retour_id": retour_id,
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
                "Les grammes du G-code sont une estimation du slicer, pas une mesure physique.",
            ],
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as erreur:
        return _nv(retour_id, f"source incomplète ou illisible : {erreur}")
