"""Remplace la chaîne shell par les mêmes cinq contrôles, sans masquer un échec par un ancien rapport."""

import contextlib
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path

from benchab.controles import debit_gcode, disposition_reelle, plaques_interface, verifier_reglages
from benchab.controles.placement import lire as lire_placement
from benchab.qualite.ecritures import PublicationNeuve, _sans_lien, sortie_privee, verrou_publication
from benchab.qualite.statuts import code_statut


def controler(plaques, tranche, placement, sortie):
    """Contrôle les archives après validation du placement, sans régler l'impression.

    Entrées : chemins des plaques, tranche, placement et sortie, géométrie en mm.
    Sortie : statut PASS/FAIL, codes et chemins des rapports/logs réellement produits.
    Invariant : chaque rapport lu provient du rejeu courant ; aucun fichier antérieur n'est écrasé.
    """
    entrees = [_sans_lien(Path(p)) for p in (plaques, tranche, placement)]
    dossier = sortie_privee(Path(sortie))
    fichier_plaques, fichier_tranche, fichier_placement = map(str, entrees)
    etapes = [
        ("plaques-interface-plaques", plaques_interface.main, [fichier_plaques], "conforme"),
        ("plaques-interface-tranche", plaques_interface.main, [fichier_tranche], "conforme"),
        ("reglages-gcode", verifier_reglages.main, [fichier_tranche, fichier_placement], "tout_conforme"),
        ("disposition-reelle", disposition_reelle.main, [fichier_tranche, fichier_placement], "conforme"),
        ("debit-gcode", debit_gcode.main, [fichier_tranche, "--sortie"], "conforme"),
    ]
    for nom, _fonction, _arguments, _champ in etapes:
        for extension in ("json", "log"):
            cible = _sans_lien(dossier / f"{nom}.{extension}")
            if cible.resolve() in {f.resolve() for f in entrees}:
                raise ValueError("Collision entrée/rapport : aucun fichier source n’est modifié")
    verrou = _sans_lien(dossier / "routine.lock")
    if verrou.resolve() in {f.resolve() for f in entrees}:
        raise ValueError("Collision entrée/verrou : aucun fichier source n’est modifié")
    # Un contrat invalide doit échouer avant le premier contrôle et ses rapports (#104).
    lire_placement(entrees[2])
    resultats = []
    with tempfile.TemporaryDirectory(prefix="benchab-routine-") as temporaire:
        staging = sortie_privee(Path(temporaire))
        fichiers = []
        for nom, fonction, arguments, champ in etapes:
            rapport_temp = staging / f"{nom}.json"
            journal_temp = staging / f"{nom}.log"
            with journal_temp.open("w", encoding="utf-8") as journal:
                with contextlib.redirect_stdout(journal), contextlib.redirect_stderr(journal):
                    try:
                        code = fonction([*arguments, str(rapport_temp)])
                    except SystemExit as erreur:
                        code = int(erreur.code or 0)
                    except (OSError, ValueError, KeyError, TypeError) as erreur:
                        print(f"Contrôle interrompu : {erreur}")
                        code = 2
            conforme = False
            rapport_existe = rapport_temp.is_file() and not rapport_temp.is_symlink()
            if code == 0 and rapport_existe:
                try:
                    conforme = json.loads(rapport_temp.read_text(encoding="utf-8")).get(champ) is True
                except (OSError, ValueError, AttributeError):
                    pass
            fichiers.append((nom, rapport_temp if rapport_existe else None, journal_temp))
            resultats.append({
                "controle": nom,
                "code": code,
                "statut": "PASS" if code == 0 and conforme else "FAIL",
                "rapport": None,
                "journal": None,
            })

        # Le verrou sérialise les rejeux ; les fichiers identiques gardent leur inode et leur chemin.
        with verrou_publication(dossier, "routine.lock"):
            publications = []
            chemins_final = {}
            divergents = []
            for nom, rapport_temp, journal_temp in fichiers:
                for extension, source_temp in (("json", rapport_temp), ("log", journal_temp)):
                    if source_temp is None:
                        continue
                    cible = _sans_lien(dossier / f"{nom}.{extension}")
                    empreinte_nouvelle = _empreinte_regulier(source_temp)
                    existant = _empreinte_regulier(
                        cible, absent_ok=True, taille_attendue=empreinte_nouvelle[0]
                    )
                    if existant is not None and existant == empreinte_nouvelle:
                        chemins_final[(nom, extension)] = cible
                    elif existant is not None:
                        divergents.append((nom, extension, source_temp))
                    else:
                        publications.append((source_temp, cible, nom, extension))
            dossier_rejeu = None
            if divergents:
                for _ in range(8):
                    candidat = dossier / f"rejeu-{uuid.uuid4().hex}"
                    try:
                        candidat.mkdir(mode=0o700)
                        dossier_rejeu = candidat
                        break
                    except FileExistsError:
                        continue
                if dossier_rejeu is None:
                    raise FileExistsError("Impossible de réserver un dossier de rejeu neuf.")
                for nom, extension, source_temp in divergents:
                    cible = _sans_lien(dossier_rejeu / f"{nom}.{extension}")
                    publications.append((source_temp, cible, nom, extension))
            if publications:
                sources = [*entrees, *(source for source, _cible, _nom, _extension in publications)]
                cibles = [cible for _source, cible, _nom, _extension in publications]
                with PublicationNeuve(sources, cibles) as publication:
                    for source, cible, _nom, _extension in publications:
                        shutil.copyfile(source, publication.temporaire(cible))
                    publication.publier()
                for _source, cible, nom, extension in publications:
                    chemins_final[(nom, extension)] = cible
            for resultat in resultats:
                nom = resultat["controle"]
                rapport_temp = next((r for n, r, _j in fichiers if n == nom), None)
                resultat["rapport"] = (
                    str(chemins_final[(nom, "json")]) if rapport_temp is not None else None
                )
                resultat["journal"] = str(chemins_final[(nom, "log")])
    conforme = all(r["statut"] == "PASS" for r in resultats)
    return {
        "statut": "PASS" if conforme else "FAIL",
        "code": code_statut("PASS" if conforme else "FAIL"),
        "conforme": conforme,
        "resultats": resultats,
        "validation_physique": False,
    }


def _empreinte_regulier(chemin, *, absent_ok=False, taille_attendue=None):
    """Compare taille et SHA-256 d'un fichier régulier en flux, sans suivre de lien.

    Entrée : chemin et taille attendue facultative en octets.
    Sortie : (octets, SHA-256), SHA omis si la taille diffère, None si absent autorisé.
    Refuse les objets non réguliers et une identité modifiée pendant la lecture.
    """
    chemin = Path(chemin)
    try:
        chemin = _sans_lien(chemin)
        fd = os.open(chemin, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        if absent_ok:
            return None
        raise
    with os.fdopen(fd, "rb") as fichier:
        avant = os.fstat(fichier.fileno())
        if not stat.S_ISREG(avant.st_mode):
            raise ValueError("Rapport ou journal non régulier ; aucune publication faite.")
        courant = chemin.lstat()
        identite_avant = (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns)
        identite_courante = (
            courant.st_dev, courant.st_ino, courant.st_size, courant.st_mtime_ns, courant.st_ctime_ns,
        )
        if identite_avant != identite_courante:
            raise ValueError("Rapport ou journal remplacé pendant la comparaison.")
        if taille_attendue is not None and avant.st_size != taille_attendue:
            return avant.st_size, None
        empreinte = hashlib.file_digest(fichier, "sha256").hexdigest()
        apres = os.fstat(fichier.fileno())
        courant = chemin.lstat()
        identite_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
        identite_courante = (
            courant.st_dev, courant.st_ino, courant.st_size, courant.st_mtime_ns, courant.st_ctime_ns,
        )
        if identite_avant != identite_apres or identite_avant != identite_courante:
            raise ValueError("Rapport ou journal modifié pendant la comparaison.")
        return avant.st_size, empreinte
