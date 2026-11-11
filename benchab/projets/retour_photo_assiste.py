"""Conserve une suggestion photo privée, sans l’assimiler à un retour physique."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive, sans_lien
from benchab.gcode.lecture import plaques_gcode_archive
from benchab.qualite.ecritures import PublicationNeuve, renommer_exclusif, sortie_durable
from benchab.qualite.empreintes import sha256_fichier

_MAX_PHOTO_OCTETS = 50 * 1024 * 1024
_MAX_PIXELS = 40_000_000
_FORMATS = {".jpg", ".jpeg", ".png", ".heic"}
_SHA = set("0123456789abcdef")


def _sha_valide(valeur):
    """Valide une empreinte SHA-256 minuscule, sans accepter d’autre type."""
    return isinstance(valeur, str) and len(valeur) == 64 and set(valeur) <= _SHA


def _entree_prive(chemin):
    """Résout le fichier explicitement choisi, sans suivre de lien symbolique."""
    cible = sans_lien(chemin, strict=True)
    if not cible.is_file() or cible.stat().st_size > _MAX_PHOTO_OCTETS:
        raise ValueError("photo absente, non régulière ou supérieure à 50 Mio")
    if cible.suffix.lower() not in _FORMATS:
        raise ValueError("formats acceptés : HEIC, JPEG et PNG")
    return cible


def _apercu(photo, destination):
    """Produit un JPEG d’aperçu borné ; aucun classement ni OCR des pixels."""
    from PIL import Image, ImageOps

    chemin_lecture = photo
    temporaire_heic = None
    conversion_heic = False
    try:
        try:
            image = Image.open(photo)
        except Image.DecompressionBombError as erreur:
            raise ValueError("photo refusée par la limite de décodage Pillow") from erreur
        except (OSError, ValueError):
            if photo.suffix.lower() != ".heic":
                raise ValueError("image illisible par le décodeur local")
            try:
                dimensions = subprocess.run(
                    ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(photo)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except subprocess.TimeoutExpired as erreur:
                raise ValueError("inspection des dimensions HEIC expirée") from erreur
            largeur = re.search(r"pixelWidth:\s*(\d+)", dimensions.stdout)
            hauteur = re.search(r"pixelHeight:\s*(\d+)", dimensions.stdout)
            if dimensions.returncode != 0 or largeur is None or hauteur is None:
                raise ValueError("dimensions HEIC illisibles")
            if int(largeur[1]) <= 0 or int(hauteur[1]) <= 0 or int(largeur[1]) * int(hauteur[1]) > _MAX_PIXELS:
                raise ValueError("dimensions photo invalides ou supérieures à 40 mégapixels")
            fd, nom = tempfile.mkstemp(suffix=".jpg", prefix="benchab-heic-")
            os.close(fd)
            temporaire_heic = Path(nom)
            try:
                resultat = subprocess.run(
                    ["/usr/bin/sips", "-s", "format", "jpeg", str(photo), "--out", str(temporaire_heic)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except subprocess.TimeoutExpired as erreur:
                raise ValueError("conversion HEIC expirée") from erreur
            if resultat.returncode != 0 or not temporaire_heic.is_file():
                raise ValueError("conversion HEIC locale indisponible")
            chemin_lecture = temporaire_heic
            conversion_heic = True
            image = Image.open(chemin_lecture)
        with image:
            formats_attendus = {".jpg": {"JPEG"}, ".jpeg": {"JPEG"}, ".png": {"PNG"}, ".heic": {"HEIF", "HEIC"}}
            if conversion_heic:
                formats_attendus[".heic"].add("JPEG")
            if image.format not in formats_attendus[photo.suffix.lower()]:
                raise ValueError("extension et format photo différents")
            if image.width <= 0 or image.height <= 0 or image.width * image.height > _MAX_PIXELS:
                raise ValueError("dimensions photo invalides ou supérieures à 40 mégapixels")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((2048, 2048))
            image.save(destination, format="JPEG", quality=88, optimize=True)
        with Image.open(destination) as controle:
            controle.verify()
    finally:
        if temporaire_heic is not None:
            temporaire_heic.unlink(missing_ok=True)


def _lier_archive(archive_3mf, declaration, plaque, region_id):
    """Relie une déclaration région/Z à un 3MF et membres exacts, sinon NV."""
    resultat = {"statut": "NON VÉRIFIÉ", "motif": "archive ou déclaration absente"}
    if archive_3mf is None or declaration is None:
        return resultat
    attendu = {"schema", "source_sha256", "plaque", "region_id", "z_min_mm", "z_max_mm"}
    if not isinstance(declaration, dict) or set(declaration) != attendu:
        return {**resultat, "motif": "schéma de déclaration de zone absent ou inattendu"}
    if (
        type(declaration["schema"]) is not int
        or declaration["schema"] != 1
        or not _sha_valide(declaration["source_sha256"])
        or type(declaration["plaque"]) is not int
        or declaration["plaque"] < 1
        or not isinstance(declaration["region_id"], str)
        or not declaration["region_id"]
        or len(declaration["region_id"]) > 128
        or type(declaration["z_min_mm"]) not in (int, float)
        or type(declaration["z_max_mm"]) not in (int, float)
        or not math.isfinite(declaration["z_min_mm"])
        or not math.isfinite(declaration["z_max_mm"])
        or declaration["z_min_mm"] < 0
        or declaration["z_min_mm"] >= declaration["z_max_mm"]
        or declaration["z_max_mm"] > 1_000_000
    ):
        return {**resultat, "motif": "valeurs de la déclaration de zone invalides"}
    if (
        type(plaque) is not int
        or plaque < 1
        or not isinstance(region_id, str)
        or plaque != declaration["plaque"]
        or region_id != declaration["region_id"]
    ):
        return {**resultat, "motif": "plaque ou région demandée différente de la déclaration"}
    try:
        chemin = sans_lien(archive_3mf, strict=True)
        with Archive3MF(chemin) as archive:
            sha_archive = archive.sha256()
            if sha_archive != declaration["source_sha256"]:
                return {**resultat, "motif": "SHA du 3MF différent de la déclaration"}
            plaques = plaques_gcode_archive(archive)
            membres = [nom for numero, nom in plaques if numero == plaque]
            if len(membres) != 1:
                return {**resultat, "motif": "plaque G-code absente ou ambiguë"}
            membre_gcode = membres[0]
            h_gcode = hashlib.sha256()
            with archive.ouvrir_flux(membre_gcode) as flux:
                for bloc in iter(lambda: flux.read(64 * 1024), b""):
                    h_gcode.update(bloc)
            candidats_reglages = [
                nom for nom in ("Metadata/project_settings.config",) if nom in archive.noms()
            ]
            if len(candidats_reglages) != 1:
                return {**resultat, "motif": "membre de réglages absent ou ambigu"}
            membre_reglages = candidats_reglages[0]
            donnees_reglages = archive.lire(membre_reglages, limite_octets=archive.taille_max_xml)
            from benchab import reglages as schema_reglages

            schema_reglages.decoder(donnees_reglages)
            archive.verifier_source(chemin)
            return {
                "statut": "PASS",
                "source_3mf_sha256": sha_archive,
                "plaque": plaque,
                "region_id_declaree": region_id,
                "plage_z_mm_declaree": [float(declaration["z_min_mm"]), float(declaration["z_max_mm"])],
                "gcode_membre": membre_gcode,
                "gcode_sha256": h_gcode.hexdigest(),
                "reglages_membre": membre_reglages,
                "reglages_sha256": hashlib.sha256(donnees_reglages).hexdigest(),
                "origine_region": "déclaration explicite de l’utilisateur ; aucun lien déduit de la photo",
            }
    except (OSError, ValueError, KeyError, RuntimeError, EOFError, zipfile.BadZipFile) as erreur:
        return {**resultat, "motif": f"lecture 3MF non qualifiée : {type(erreur).__name__}"}


def creer_suggestion(
    photo, destination, *, prive=None, archive_3mf=None, declaration=None, plaque=None, region_id=None
):
    """Crée un candidat privé neuf avec photo conservée, aperçu JPEG et SHA ; aucun projet/DB écrit.

    Entrées : photo locale, destination de dossier privé, archive/déclaration facultatives.
    Sortie : manifeste CANDIDAT ; le lien photo, l’identité du modèle et le défaut restent NON VÉRIFIÉS.
    Les nombres Z déclarés sont en mm. Aucun diagnostic visuel ou validation physique n’est produit.
    """
    racine = atelier_prive(prive)
    source = _entree_prive(photo)
    cible = sortie_durable(destination, prive=racine)
    if cible.suffix or cible == racine or not cible.is_relative_to(racine):
        raise ValueError("une nouvelle destination de dossier sous l’atelier privé est requise")
    if cible.exists():
        raise FileExistsError("dossier de suggestion existant ; aucun écrasement")
    cible.parent.mkdir(parents=True, exist_ok=True)
    sans_lien(cible, racine=racine)
    source_sha = sha256_fichier(source)
    liaison = _lier_archive(archive_3mf, declaration, plaque, region_id)
    stage = Path(tempfile.mkdtemp(prefix=".retour-photo-", dir=cible.parent))
    identite_stage = (stage.lstat().st_dev, stage.lstat().st_ino)
    try:
        extension = source.suffix.lower()
        copie = stage / f"photo-source{extension}"
        shutil.copyfile(source, copie)
        if sha256_fichier(source) != source_sha or sha256_fichier(copie) != source_sha:
            raise ValueError("photo modifiée pendant sa copie")
        apercu = stage / "apercu.jpg"
        _apercu(copie, apercu)
        rapport = {
            "schema": 1,
            "suggestion": "CANDIDAT",
            "cree_utc": datetime.now(timezone.utc).isoformat(),
            "photo": {
                "source_fichier": copie.name,
                "source_sha256": source_sha,
                "apercu_fichier": apercu.name,
                "apercu_sha256": sha256_fichier(apercu),
                "format_apercu": "JPEG",
            },
            "liaison": liaison,
            "interpretation_photo": "NON VÉRIFIÉ",
            "limites": [
                "La photo ne permet pas ici d’identifier le défaut, le modèle, la plaque ni une impression.",
                "La région et la plage Z sont des déclarations utilisateur, non reconnues dans les pixels.",
                "Aucune mesure physique, comparaison ni écriture projet/SQLite n’est effectuée.",
            ],
        }
        fichier_rapport = stage / "suggestion.json"
        fichier_rapport.write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        if sha256_fichier(source) != source_sha:
            raise ValueError("photo source modifiée pendant la préparation")
        renommer_exclusif(stage, cible)
    except BaseException:
        if _stage_est_le_meme(stage, identite_stage):
            shutil.rmtree(stage)
        raise
    return {
        **rapport,
        "fiche_privee": str(cible / "suggestion.json"),
        "suggestion_sha256": sha256_fichier(cible / "suggestion.json"),
    }


def confirmer_suggestion(fiche, empreinte_suggestion, observation_declaree, sortie, *, prive=None):
    """Enregistre séparément une confirmation explicite liée au SHA exact du candidat.

    Entrées : fiche candidate, SHA-256 attendu, texte déclaré et JSON privé neuf.
    Sortie : capture de confirmation ; PASS signifie seulement que la confirmation a été enregistrée.
    Aucun projet, statut d’impression ou SQLite n’est modifié.
    """
    racine = atelier_prive(prive)
    chemin = sans_lien(fiche, racine=racine, strict=True)
    destination = sortie_durable(sortie, prive=racine)
    if destination.suffix.lower() != ".json":
        raise ValueError("une confirmation séparée au format JSON est requise")
    if not chemin.is_file() or not chemin.is_relative_to(racine) or chemin.name != "suggestion.json":
        raise ValueError("fiche suggestion privée requise")
    brut, sha_fiche = _lire_fiche_epinglee(chemin)
    if not isinstance(empreinte_suggestion, str) or sha_fiche != empreinte_suggestion:
        raise ValueError("empreinte de suggestion différente")
    if (
        not isinstance(observation_declaree, str)
        or not observation_declaree.strip()
        or len(observation_declaree) > 4000
    ):
        raise ValueError("observation déclarée non vide de 4 000 caractères maximum requise")
    suggestion = json.loads(
        brut.decode("utf-8"),
        object_pairs_hook=_json_sans_doublons,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constante JSON interdite")),
    )
    if (
        not isinstance(suggestion, dict)
        or suggestion.get("schema") != 1
        or suggestion.get("suggestion") != "CANDIDAT"
        or not isinstance(suggestion.get("photo"), dict)
    ):
        raise ValueError("la fiche n’est pas une suggestion candidate")
    noms_declares = [suggestion["photo"].get(cle) for cle in ("source_fichier", "apercu_fichier")]
    if noms_declares[0] == noms_declares[1]:
        raise ValueError("les fichiers source et aperçu doivent être distincts")
    fichiers_references = []
    identites_references = []
    empreintes_references = {}
    for cle, sha_cle in (("source_fichier", "source_sha256"), ("apercu_fichier", "apercu_sha256")):
        nom = suggestion["photo"].get(cle)
        sha = suggestion["photo"].get(sha_cle)
        if (
            not isinstance(nom, str)
            or Path(nom).name != nom
            or nom == "suggestion.json"
            or not _sha_valide(sha)
        ):
            raise ValueError("référence photo incomplète dans la suggestion")
        fichier = sans_lien(chemin.parent / nom, racine=racine, strict=True)
        if not fichier.is_file() or sha256_fichier(fichier) != sha:
            raise ValueError("fichier photo différent de la suggestion")
        fichiers_references.append(fichier)
        info = fichier.lstat()
        identites_references.append((info.st_dev, info.st_ino))
        empreintes_references[fichier] = sha
    if fichiers_references[0] == fichiers_references[1] or identites_references[0] == identites_references[1]:
        raise ValueError("les fichiers source et aperçu doivent être distincts")
    capture = {
        "schema": 1,
        "nature": "CONFIRMATION_HUMAINE_SEPAREE",
        "statut": "PASS",
        "suggestion_sha256": empreinte_suggestion,
        "confirmation_humaine": True,
        "observation_declaree": observation_declaree.strip(),
        "cree_utc": datetime.now(timezone.utc).isoformat(),
        "limite": (
            "Cette confirmation consigne une déclaration ; elle ne certifie ni photo, modèle, "
            "impression ni cause physique."
        ),
    }
    with PublicationNeuve([chemin, *fichiers_references], [destination]) as publication:
        if sha256_fichier(chemin) != sha_fiche or any(
            sha256_fichier(fichier) != attendu for fichier, attendu in empreintes_references.items()
        ):
            raise ValueError("fiche ou fichiers référencés modifiés avant confirmation")
        publication.temporaire(destination).write_text(
            json.dumps(capture, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        if sha256_fichier(chemin) != sha_fiche or any(
            sha256_fichier(fichier) != attendu for fichier, attendu in empreintes_references.items()
        ):
            raise ValueError("fiche ou fichiers référencés modifiés pendant la confirmation")
        publication.publier()
    return capture


def _json_sans_doublons(paires):
    """Refuse les clés JSON dupliquées dans les déclarations locales."""
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError("clé JSON dupliquée dans la déclaration")
        resultat[cle] = valeur
    return resultat


def _lire_fiche_epinglee(chemin):
    """Lit une fiche JSON privée bornée et calcule son SHA sur les mêmes octets épinglés."""
    plafond = 2 * 1024 * 1024
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > plafond:
            raise ValueError("fiche suggestion régulière de 2 Mio maximum requise")
        brut = flux.read(plafond + 1)
        apres = os.fstat(flux.fileno())
    try:
        courant = chemin.lstat()
    except FileNotFoundError as erreur:
        raise ValueError("fiche suggestion remplacée pendant la lecture") from erreur
    if (
        len(brut) > plafond
        or _signature(avant) != _signature(apres)
        or _signature(apres) != _signature(courant)
    ):
        raise ValueError("fiche suggestion trop grande ou modifiée pendant la lecture")
    return brut, hashlib.sha256(brut).hexdigest()


def _stage_est_le_meme(stage, identite):
    """Nettoie seulement si le nom de stage désigne encore le dossier créé par cette opération."""
    try:
        info = stage.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == identite


def _lire_declaration_privee(chemin, prive):
    """Lit au plus 1 Mio d’un JSON régulier privé et vérifie les clés dupliquées."""
    cible = sans_lien(chemin, racine=prive, strict=True)
    fd = os.open(cible, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > 1024 * 1024:
            raise ValueError("déclaration privée régulière de 1 Mio maximum requise")
        brut = flux.read(1024 * 1024 + 1)
        apres = os.fstat(flux.fileno())
    if len(brut) > 1024 * 1024 or _signature(avant) != _signature(apres):
        raise ValueError("déclaration trop grande ou modifiée pendant la lecture")
    return json.loads(
        brut.decode("utf-8"),
        object_pairs_hook=_json_sans_doublons,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constante JSON interdite")),
    )


def _signature(info):
    """Retourne l’identité stable d’un fichier ouvert, sans unité."""
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def main(argv=None, *, prive=None):
    """Expose l’import candidat et la confirmation séparée, codes 0/2 sans mesure SQLite."""
    commande = argparse.ArgumentParser(prog="benchab visualisation retour-photo")
    sous = commande.add_subparsers(dest="action", required=True)
    proposer = sous.add_parser("proposer", help="Copie une photo et prépare un candidat privé")
    proposer.add_argument("photo", type=Path)
    proposer.add_argument("--destination", type=Path, required=True)
    proposer.add_argument("--3mf", dest="archive_3mf", type=Path)
    proposer.add_argument("--declaration", type=Path, help="JSON privé liant SHA 3MF, plaque, région et plage Z")
    proposer.add_argument("--plaque", type=int)
    proposer.add_argument("--region")
    confirmer = sous.add_parser("confirmer", help="Consigne une déclaration humaine dans un JSON distinct")
    confirmer.add_argument("fiche", type=Path)
    confirmer.add_argument("--sha256", required=True, dest="empreinte")
    confirmer.add_argument("--observation", required=True)
    confirmer.add_argument("--sortie", type=Path, required=True)
    args = commande.parse_args(argv)
    try:
        if args.action == "proposer":
            declaration = None
            if args.declaration is not None:
                declaration = _lire_declaration_privee(args.declaration, atelier_prive(prive))
            rapport = creer_suggestion(
                args.photo,
                args.destination,
                prive=prive,
                archive_3mf=args.archive_3mf,
                declaration=declaration,
                plaque=args.plaque,
                region_id=args.region,
            )
            rapport["dossier_prive"] = str(args.destination)
        else:
            rapport = confirmer_suggestion(
                args.fiche,
                args.empreinte,
                args.observation,
                args.sortie,
                prive=prive,
            )
        print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as erreur:
        print(f"BENCHAB : {erreur}")
        return 2
