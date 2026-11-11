"""Détermine la portée comparable de deux retours photo privés déjà confirmés."""

import hashlib
import json
import math
import os
import re
from pathlib import PurePosixPath
from zipfile import BadZipFile

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive, sans_lien
from benchab.gcode.lecture import plaques_gcode_archive
from benchab.projets.retour_photo_assiste import (
    _MAX_APERCU_OCTETS,
    _MAX_PHOTO_OCTETS,
    _json_sans_doublons,
    _lire_fiche_epinglee,
    _lire_membre_publie,
    _sha_valide,
)


def _chemin_prive(racine, valeur):
    """Résout un fichier explicitement déclaré sous l’atelier, sans lien."""
    if not isinstance(valeur, str) or len(valeur) > 1024:
        raise ValueError("Chemin privé relatif requis dans la paire.")
    relatif = PurePosixPath(valeur)
    if relatif.is_absolute() or not relatif.parts or ".." in relatif.parts:
        raise ValueError("Chemin privé relatif sans remontée requis dans la paire.")
    return sans_lien(racine / relatif, racine=racine, strict=True)


def _objet(chemin):
    """Lit un document privé borné et son SHA, sans mélange de lectures."""
    brut, sha = _lire_fiche_epinglee(chemin)
    try:
        contenu = json.loads(brut, object_pairs_hook=_json_sans_doublons)
    except (ValueError, RecursionError) as erreur:
        raise ValueError("Document de retour JSON invalide.") from erreur
    if not isinstance(contenu, dict) or type(contenu.get("schema")) is not int or contenu["schema"] != 1:
        raise ValueError("Schéma 1 attendu pour un document de retour.")
    return contenu, sha


def _verifier_images(chemin, photo):
    """Revérifie original et aperçu sous leurs budgets privés, sans décoder les pixels."""
    if (
        not isinstance(photo, dict)
        or not isinstance(photo.get("source_fichier"), str)
        or not re.fullmatch(r"photo-source\.(?:jpg|jpeg|png|heic)", photo["source_fichier"])
        or photo.get("apercu_fichier") != "apercu.jpg"
        or photo.get("format_apercu") != "JPEG"
    ):
        raise ValueError("Références d’images de la suggestion incomplètes.")
    fd = os.open(chemin.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        identites = []
        for nom, sha, budget in (
            (photo["source_fichier"], photo.get("source_sha256"), _MAX_PHOTO_OCTETS),
            ("apercu.jpg", photo.get("apercu_sha256"), _MAX_APERCU_OCTETS),
        ):
            if not _sha_valide(sha):
                raise ValueError("SHA d’une image absent de la suggestion.")
            info = os.stat(nom, dir_fd=fd, follow_symlinks=False)
            identites.append((info.st_dev, info.st_ino))
            _lire_membre_publie(fd, nom, (sha, info.st_size, budget))
        if identites[0] == identites[1] or not os.path.samestat(os.fstat(fd), chemin.parent.lstat()):
            raise ValueError("Images non distinctes ou dossier de suggestion remplacé.")
    finally:
        os.close(fd)


def _liaison(chemin, liaison):
    """Revérifie archive, plaque et réglages exacts ; extrait seulement le contexte comparable."""
    if not isinstance(liaison, dict) or liaison.get("statut") != "PASS":
        raise ValueError("Suggestion sans liaison 3MF exacte.")
    plaque, zone, plage = (liaison.get(cle) for cle in ("plaque", "region_id_declaree", "plage_z_mm_declaree"))
    if (
        type(plaque) is not int or plaque < 1 or not isinstance(zone, str) or not zone.strip()
        or len(zone) > 128 or not isinstance(plage, list) or len(plage) != 2
        or any(type(z) not in (float, int) or not 0 <= z <= 1_000_000 or not math.isfinite(z) for z in plage)
        or plage[0] >= plage[1]
        or liaison.get("reglages_membre") != "Metadata/project_settings.config"
    ):
        raise ValueError("Plaque, région ou plage Z de la suggestion invalide.")
    try:
        with Archive3MF(chemin) as archive:
            sha = archive.sha256()
            if sha != liaison.get("source_3mf_sha256"):
                raise ValueError("L’archive ne correspond plus au SHA de la suggestion.")
            membres = [nom for numero, nom in plaques_gcode_archive(archive) if numero == plaque]
            if len(membres) != 1 or membres[0] != liaison.get("gcode_membre"):
                raise ValueError("Le membre G-code de la suggestion est absent ou ambigu.")
            hachage = hashlib.sha256()
            with archive.ouvrir_flux(membres[0]) as flux:
                for bloc in iter(lambda: flux.read(64 * 1024), b""):
                    hachage.update(bloc)
            if hachage.hexdigest() != liaison.get("gcode_sha256"):
                raise ValueError("Le G-code diffère de la suggestion.")
            config = archive.lire("Metadata/project_settings.config", limite_octets=8 * 1024 * 1024)
            if hashlib.sha256(config).hexdigest() != liaison.get("reglages_sha256"):
                raise ValueError("Les réglages diffèrent de la suggestion.")
            options = reglages.decoder(config)
            archive.verifier_source(chemin)
    except (OSError, KeyError, BadZipFile) as erreur:
        raise ValueError("Archive de retour illisible.") from erreur
    machine, types = options.get("printer_model"), options.get("filament_type")
    contexte = reglages.contexte_filaments(options)
    famille = (
        (machine, types[0]) if isinstance(machine, str) and machine.strip()
        and isinstance(types, list) and len(types) == 1 and isinstance(types[0], str) and types[0].strip()
        and reglages.nombre_filaments_metadata(options) == 1
        and contexte["statut_contexte_filaments"] == "PASS" else None
    )
    return {
        "source_sha256": sha,
        "plaque": plaque,
        # Une empreinte permet de relier la déclaration sans recopier son libellé privé.
        "region_id_sha256": hashlib.sha256(zone.encode("utf-8")).hexdigest(),
        "plage_z_mm_declaree": plage,
        "gcode_sha256": hachage.hexdigest(),
        "reglages_sha256": hashlib.sha256(config).hexdigest(),
    }, famille


def _source(racine, declaration):
    """Relit les éléments privés de la paire et vérifie leur liaison déclarée."""
    if not isinstance(declaration, dict) or set(declaration) != {
        "suggestion", "confirmation", "confirmation_sha256", "archive",
    }:
        raise ValueError("Suggestion, confirmation, SHA de confirmation et archive requis.")
    suggestion = _chemin_prive(racine, declaration["suggestion"])
    confirmation = _chemin_prive(racine, declaration["confirmation"])
    archive = _chemin_prive(racine, declaration["archive"])
    candidat, sha_candidat = _objet(suggestion)
    observe, sha_confirmation = _objet(confirmation)
    if (
        candidat.get("suggestion") != "CANDIDAT" or suggestion.name != "suggestion.json"
        or sha_confirmation != declaration["confirmation_sha256"]
        or observe.get("statut") != "PASS" or observe.get("confirmation_humaine") is not True
        or observe.get("nature") != "CONFIRMATION_HUMAINE_SEPAREE"
        or observe.get("suggestion_sha256") != sha_candidat
        or not isinstance(observe.get("observation_declaree"), str) or not observe["observation_declaree"].strip()
    ):
        raise ValueError("Confirmation modifiée ou sans lien exact avec la suggestion.")
    _verifier_images(suggestion, candidat.get("photo"))
    liaison, famille = _liaison(archive, candidat.get("liaison"))
    return liaison, famille, sha_confirmation, sha_candidat


def comparer(declaration, *, prive=None):
    """Indique PASS de portée ou NON VÉRIFIÉ pour deux retours privés confirmés.

    Entrée : paire déclarée d’archives et preuves privées, avec plages Z en mm.
    Sortie : identité du contexte déclaré, sans lecture d’image, comparaison de
    géométrie imprimée, attribution de défaut ni écriture de projet.
    """
    racine = atelier_prive(prive)
    if (
        not isinstance(declaration, dict) or set(declaration) != {"schema", "sources"}
        or type(declaration["schema"]) is not int or declaration["schema"] != 1
        or not isinstance(declaration["sources"], list) or len(declaration["sources"]) != 2
    ):
        raise ValueError("Paire schema 1 contenant deux sources de retour requise.")
    a, b = (_source(racine, source) for source in declaration["sources"])
    if a[2] == b[2] or a[3] == b[3]:
        raise ValueError("Deux suggestions et confirmations distinctes sont nécessaires.")
    meme_zone = a[0] == b[0]
    meme_famille = a[1] is not None and a[1] == b[1]
    portee = (
        "MEME_SOURCE_ET_ZONE_DECLAREE" if meme_zone else
        "MACHINE_ET_MATIERE_DECLAREES" if meme_famille else "AUCUNE"
    )
    return {
        "schema": 1,
        "statut": "PASS" if meme_zone or meme_famille else "NON VÉRIFIÉ",
        "portee": portee,
        "contexte_geometrique_declare_identique": meme_zone,
        "comparaison_geometrique": False,
        "modele_machine_et_matiere_declares_identiques": meme_famille,
        "sources": [{**source[0], "confirmation_sha256": source[2]} for source in (a, b)],
        "validation_physique": False,
        **({"motif": "Les sources ou zones diffèrent et la même famille de machine/matière n’est pas établie."}
           if portee == "AUCUNE" else {}),
        "limite": (
            "Un fichier et une zone déclarée ou un modèle machine et une matière déclarés peuvent coïncider. "
            "Aucune identité de bobine, d’imprimante physique, d’exécution ou de défaut n’est déduite."
        ),
    }
