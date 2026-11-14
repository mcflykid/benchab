"""Lanceur unique : configuration temporaire, réglages conservés et tranchages sérialisés.

Le chemin du slicer vient du profil machine. Le client ne peut transmettre ni
shell, ni argument réseau, ni commande d’imprimante. La préparation travaille
sur une copie ; les noms de plaques sont restaurés et le résultat est relu.
"""

import argparse
import fcntl
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from zipfile import BadZipFile

from benchab import integrite_3mf
from benchab import reglages as schema_reglages
from benchab.archives_3mf import Archive3MF
from benchab.fabrication.tranchage_copies import chemin_temporaire as chemin_temporaire
from benchab.fabrication.tranchage_copies import normaliser as normaliser_copie
from benchab.fabrication.tranchage_copies import restaurer_noms as restaurer_noms
from benchab.fabrication.tranchage_copies import sha256 as sha256
from benchab.gcode.lecture import plaques_gcode_archive
from benchab.machine.configuration import charger
from benchab.qualite.ecritures import sortie_privee

ECHANGE = Path(f"/private/tmp/benchab-slicer-{os.getuid()}")
CAUSES = (
    "generate_plate_name_texture",
    "expand_plate_extruders",
    "filaments_cnt",
    "ConfigOptionVector",
    "load_from_3mf_structure",
    "bridge_over_infill",
)
GROUPES_CAUSES = {
    "expand_plate_extruders": "multi-filament",
    "filaments_cnt": "multi-filament",
}
_CAUSE_CONTROLE_SORTIE = "validation_sortie"


def ecrire_json(chemin, donnees):
    """Remplace un rapport JSON UTF-8 atomiquement ; temporaire unique, aucun réglage modifié."""
    from benchab.qualite.ecritures import remplacer_texte

    remplacer_texte(chemin, json.dumps(donnees, ensure_ascii=False, indent=2) + "\n")


def normaliser(source, destination, usage="impression", identifiants=None):
    """Normalise une copie neuve ; réglages en unités natives, rapport d’intégrité, aucune écriture à la source."""
    # Le même fournisseur de configuration reste utilisé par le lanceur et les copies.
    return normaliser_copie(source, destination, usage, identifiants, configuration=charger)


def diagnostic(debut):
    """Résume le journal d’incident du slicer depuis un début en secondes Unix, sans conclure sur la cause."""
    dossier = Path.home() / "Library/Logs/DiagnosticReports"
    rapports = sorted(dossier.glob("AnycubicSlicerNext*.ips"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not rapports:
        return {"cause": "inconnue", "rapport_recent": False, "fonctions": []}
    recent = rapports[0].stat().st_mtime >= debut - 5
    texte = rapports[0].read_text(errors="replace")
    decodeur = json.JSONDecoder()
    entete, fin = decodeur.raw_decode(texte)
    restant = texte[fin:].strip()
    contenu = json.loads(restant) if restant else entete
    fils = contenu.get("threads", [])
    indice = contenu.get("faultingThread")
    fil = (
        fils[indice]
        if isinstance(indice, int) and 0 <= indice < len(fils)
        else next((fil for fil in fils if fil.get("triggered")), {})
    )
    fonctions = [cadre["symbol"] for cadre in fil.get("frames", []) if "symbol" in cadre]
    causes = [cause for cause in CAUSES if any(cause in fonction for fonction in fonctions)]
    return {
        "cause": causes[0] if causes and recent else "inconnue",
        "rapport_recent": recent,
        "rapport": str(rapports[0]),
        "fonctions": fonctions[:12],
        "pile_complete": fonctions,
        "interpretation": "Signature d’incident ; une fonction dans la pile ne prouve pas sa cause.",
    }


def _environnement_slicer(travail):
    """Isole HOME/cache/temp sous /tmp ; aucun profil utilisateur lu (docs/reference/slicer-anycubic.md)."""
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(travail / "home"),
        "TMPDIR": str(travail / "tmp"),
        "XDG_CACHE_HOME": str(travail / "cache"),
        "XDG_CONFIG_HOME": str(travail / "data"),
        "LC_ALL": "en_US.UTF-8",
    }


def _cle_incident(rapport, operation=None, *, mode=None):
    """Identifie une cause ou une inconnue reliée par opération et SHA/contexte explicite."""
    diagnostic_rapport = rapport.get("diagnostic", {})
    cause = diagnostic_rapport.get("cause") if isinstance(diagnostic_rapport, dict) else None
    if cause == _CAUSE_CONTROLE_SORTIE:
        # Un refus après succès du moteur se limite au même fichier, pas à toutes les sorties (#119).
        cause = "inconnue"
    if cause and cause != "inconnue":
        return ("cause", GROUPES_CAUSES.get(cause, cause))
    if cause != "inconnue":
        return None
    incident = rapport.get("incident", {})
    operation = operation or incident.get("operation") or rapport.get("operation")
    mode = mode or rapport.get("mode")
    if operation is None and mode == "visualisation":
        operation = "visualisation"
    elif operation is None and mode in {"aide", "defauts"}:
        operation = "options-slicer"
    if operation == "options-slicer":
        if mode in {"aide", "defauts"}:
            return ("inconnue", operation, mode)
        return None
    if operation in {"tranchage", "visualisation"}:
        empreinte = rapport.get("source_sha256")
        if isinstance(empreinte, str) and re.fullmatch(r"[0-9a-f]{64}", empreinte):
            return ("inconnue", operation, empreinte)
    return None


def _plafond_atteint(rapports, *, operation, source_sha256=None, mode=None):
    """Teste le plafond sur des rapports déjà chargés, sans confondre les contextes inconnus."""
    comptes = {}
    for rapport, operation_historique in rapports:
        cle = _cle_incident(rapport, operation_historique)
        if cle is not None:
            comptes[cle] = comptes.get(cle, 0) + 1
    if any(cle[0] == "cause" and nombre >= 3 for cle, nombre in comptes.items()):
        return True
    courant = _cle_incident(
        {"diagnostic": {"cause": "inconnue"}, "source_sha256": source_sha256},
        operation,
        mode=mode,
    )
    return courant is not None and comptes.get(courant, 0) >= 3


def _lire_rapport_limite(chemin, *, libelle="Rapport d'incident invalide"):
    """Lit jusqu'à 8 Mio de JSON ordinaire sans suivre de lien ; refuse fichiers changeants ou hors borne."""
    chemin = Path(chemin)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descripteur = os.open(chemin, flags)
    except OSError as erreur:
        raise ValueError(libelle) from erreur
    limite = 8 * 1024**2
    with os.fdopen(descripteur, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > limite:
            raise ValueError(libelle)
        contenu = flux.read(limite + 1)
        apres = os.fstat(flux.fileno())
        if len(contenu) > limite or len(contenu) != apres.st_size or (
            avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns
        ) != (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns):
            raise ValueError(libelle)
    try:
        return json.loads(contenu.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erreur:
        raise ValueError(libelle) from erreur


def _verifier_limite_causes(prive, *, operation="tranchage", source_sha256=None, mode=None):
    """Partage le plafond des causes établies ; les inconnues exigent même opération et contexte."""
    def rapports():
        familles = (("tranchages", "tranchage"), ("options-slicer", "options-slicer"))
        for famille, contexte in familles:
            for ancien in (_prive(prive) / "mesures" / famille).glob("*.json"):
                rapport = _lire_rapport_limite(
                    ancien, libelle="Rapport d'incident invalide ; nouveau lancement refusé"
                )
                yield rapport, contexte

    if _plafond_atteint(rapports(), operation=operation, source_sha256=source_sha256, mode=mode):
        raise ValueError("Trois essais atteints pour une cause : aucun nouveau lancement autorisé.")


def inspecter_options(*, prive=None, mode="aide"):
    """Lit l'aide ou exporte les défauts du binaire par le verrou unique ; renvoie code/rapport, sans 3MF modifié.

    Délai maximal 30 s ; un échec est FAIL et conserve son diagnostic privé.
    N'ouvre aucun profil installé et ne contacte jamais l'imprimante.
    """
    if mode not in {"aide", "defauts"}:
        raise ValueError("Inspection limitée à aide ou defauts")
    travail = Path(tempfile.mkdtemp(prefix="benchab-options-slicer-", dir="/private/tmp"))
    for nom in ("data", "sorties", "home", "tmp", "cache"):
        (travail / nom).mkdir()
    commande = [
        str(charger()["slicer"]["executable"]),
        "--datadir",
        str(travail / "data"),
        "--outputdir",
        str(travail / "sorties"),
    ]
    commande += ["--help"] if mode == "aide" else ["--export-settings", str(travail / "defauts.json")]
    debut = time.time()
    journal = travail / "options.log"
    resultat = {
        "commande": commande, "journal": str(journal), "journal_natif": str(journal),
        "travail": str(travail), "mode": mode,
    }
    from benchab.processus import executer

    with verrou_slicer(), journal.open("x") as flux:
        _verifier_limite_causes(prive, operation="options-slicer", mode=mode)
        # Même une demande d'aide peut engendrer des enfants : le groupe commun
        # garantit leur arrêt au délai (docs/reference/slicer-anycubic.md).
        proc = executer(commande, cwd=travail, env=_environnement_slicer(travail),
                        diagnostics=travail / "diagnostics", delai=30,
                        conserver_capture=True, fusion_stderr=True)
        flux.write(proc.stdout)
        resultat.update(code=proc.code_controle, diagnostic_processus=proc.diagnostic)
        if proc.code_controle == 124:
            resultat["erreur"] = "Délai de lecture des options dépassé (30 s)"
    resultat.update(statut="PASS" if resultat["code"] == 0 else "FAIL", duree_s=time.time() - debut)
    if mode == "defauts":
        if (travail / "defauts.json").is_file():
            resultat["defauts"] = str(travail / "defauts.json")
            resultat["defauts_sha256"] = sha256(travail / "defauts.json")
        else:
            resultat.update(statut="FAIL", erreur="Le binaire n'a pas exporté ses réglages par défaut")
    if resultat["code"]:
        resultat["diagnostic"] = diagnostic(debut)
        archiver_echec(resultat, {}, prive)
    dossier = _prive(prive) / "mesures/options-slicer"
    dossier.mkdir(parents=True, exist_ok=True)
    rapport = dossier / (uuid.uuid4().hex + ".json")
    resultat["rapport"] = str(rapport)
    ecrire_json(rapport, resultat)
    return resultat


def _verifier_machine_embarquee(source):
    """Vérifie le modèle et la buse en mm du 3MF ; retourne leurs valeurs ou lève ValueError.

    La lecture bornée refuse un projet ambigu. Aucun profil installé n'est lu ni aucun réglage modifié.
    """
    try:
        reglages = schema_reglages.lire_3mf(source)
    except (OSError, KeyError, BadZipFile, ValueError) as erreur:
        raise ValueError("Machine embarquée absente ou illisible : lancement refusé.") from erreur

    machine = charger()["machine"]
    alias_autorises = {"Anycubic Kobra S1", "Anycubic Kobra S1 Combo"}
    modele = reglages.get("printer_model")
    if not isinstance(modele, str) or modele not in alias_autorises or modele not in machine.get("modeles_slicer", []):
        raise ValueError("Modèle embarqué absent ou hors S1/S1 Combo : lancement refusé.")
    # Le schéma historique ne typait pas encore nozzle_diameter ; le décodeur
    # préserve sa représentation. On accepte uniquement les deux formes exactes
    # du contrat Source, sans conversion ni modification du réglage (voir règles).
    buse = reglages.get("nozzle_diameter")
    if machine.get("buse_mm") != 0.4 or buse not in (["0.4"], [0.4]):
        raise ValueError("Buse embarquée différente de l’unique 0,4 mm : lancement refusé.")
    return modele, tuple(buse)


def _terminer_groupe_slicer(groupe, *, grace_s=0.3):
    """Arrête le groupe POSIX créé pour le slicer (grâce en s) ; renvoie booléen de terminaison.

    Le groupe est isolé de BENCHAB ; False signifie terminaison NON VÉRIFIÉE, jamais succès.
    """
    from benchab.processus import _actif

    try:
        os.killpg(groupe, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    fin = time.monotonic() + grace_s
    while _actif(groupe) and time.monotonic() < fin:
        time.sleep(0.02)
    if _actif(groupe):
        try:
            os.killpg(groupe, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        fin = time.monotonic() + 1.0
        while _actif(groupe) and time.monotonic() < fin:
            time.sleep(0.02)
    return not _actif(groupe)


class TranchageAnnule(ValueError):
    """Interruption demandée pour un tranchage local, sans incident du moteur."""


def _verifier_annulation(annule):
    if annule is not None and annule():
        raise TranchageAnnule("Tranchage annulé.")


def _executer(demande, *, annule=None):
    _verifier_annulation(annule)
    timeout_s = demande.get("timeout_s", 1800)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s):
        raise ValueError("Délai du slicer : nombre fini exigé")
    if not 0 < timeout_s <= 1800:
        raise ValueError("Délai du slicer : supérieur à zéro et au plus 1800 secondes")
    source = chemin_temporaire(demande["source"])
    destination = chemin_temporaire(demande["destination"])
    if destination.suffix != ".3mf" or source == destination:
        raise ValueError("Sortie 3MF distincte de la source exigée")
    # Le contrôle porte sur les deux champs effectivement embarqués : aucun preset
    # installé n'est résolu ou lu (docs/reference/reglages-et-integrite.md).
    machine_source = _verifier_machine_embarquee(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Destination existante : aucune sortie n'est écrasée.")
    travail = Path(tempfile.mkdtemp(prefix="benchab-slice-", dir="/private/tmp"))
    for nom in ("data", "sorties", "home", "tmp", "cache"):
        (travail / nom).mkdir()
    entree = travail / "entree.3mf"
    normalisation = normaliser(
        source,
        entree,
        demande.get("usage", "impression"),
        identifiants=demande.get("identifiants"),
    )
    # Le moteur reçoit la copie, pas le chemin déjà vérifié avant la
    # normalisation. Un modèle admis mais différent reste un écart (#186).
    if _verifier_machine_embarquee(entree) != machine_source:
        raise ValueError("Machine embarquée différente entre source et copie : lancement refusé.")
    from benchab.fabrication import origine_3mf

    executable = str(charger()["slicer"]["executable"])
    # Déclarer la recomposition documentaire avant le moteur ; aucune différence découverte après coup
    # ne devient permise (docs/reference/integrite-3mf.md).
    plan_origine = origine_3mf.planifier(entree, binaire=executable)
    commande = [
        executable,
        "--datadir",
        str(travail / "data"),
        "--arrange",
        "0",
        "--orient",
        "0",
        "--slice",
        "0",
        "--outputdir",
        str(travail / "sorties"),
        "--export-3mf",
        destination.name,
        str(entree),
    ]
    environnement = _environnement_slicer(travail)
    debut = time.time()
    interruption = False
    groupe_termine = True
    with (travail / "slicer.log").open("w") as journal:
        if annule is not None and annule():
            interruption = True
        else:
            processus = subprocess.Popen(
                commande,
                cwd=travail,
                env=environnement,
                stdout=journal,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            limite = time.monotonic() + timeout_s
            expiration = False
            while True:
                termine, statut, ressources = os.wait4(processus.pid, os.WNOHANG)
                if termine:
                    break
                interruption = annule is not None and annule()
                expiration = time.monotonic() > limite
                if interruption or expiration:
                    # La session dédiée contient uniquement ce lancement et ses descendants.
                    groupe_termine = _terminer_groupe_slicer(processus.pid)
                    _, statut, ressources = os.wait4(processus.pid, 0)
                    break
                time.sleep(0.1)
            code_processus = os.waitstatus_to_exitcode(statut)
            processus.returncode = 124 if expiration or not groupe_termine else code_processus
    if interruption or (annule is not None and annule()):
        if groupe_termine:
            shutil.rmtree(travail)
        raise TranchageAnnule("Tranchage annulé.")
    resultat = {
        "code": processus.returncode,
        "code_processus": code_processus,
        "groupe_slicer_termine": groupe_termine,
        "commande": commande,
        "duree_s": time.time() - debut,
        "cpu_utilisateur_s": ressources.ru_utime,
        "cpu_systeme_s": ressources.ru_stime,
        "memoire_max_octets": ressources.ru_maxrss,
        "source_sha256": sha256(source),
        "entree_sha256": sha256(entree),
        "normalisation": normalisation,
        "modifications": normalisation["modifications"],
        "plan_matrices_origine": plan_origine,
        "travail": str(travail),
        "timeout_s": timeout_s,
        "delai_depasse": expiration,
        "journal_natif": str(travail / "slicer.log"),
        "reglages_attendus": demande.get("attendu"),
    }
    fichier = travail / "sorties" / destination.name
    if processus.returncode == 0 and fichier.is_file():
        try:
            from benchab.fabrication.anycubic import _valider_tranchage

            # Le moteur peut compléter ou réindexer des réglages. Ne jamais
            # livrer ces différences sur la foi d'un code zéro natif
            # (docs/reference/integrite-3mf.md). Aucun réglage moteur n'est
            # implicitement autorisé ; les noms seront restaurés ensuite.
            modifications_origine = []
            if plan_origine["statut"] == "PASS" and plan_origine["permis"]:
                archive_native = fichier
                restauree = travail / "origine-restauree.3mf"
                restauration = origine_3mf.restaurer(entree, archive_native, restauree, plan_origine)
                resultat["restauration_matrices_origine"] = restauration
                resultat["archive_native_avant_restauration"] = str(archive_native)
                resultat["integrite_sortie_moteur"] = restauration["integrite_natif"]
                fichier = restauree
                modifications_origine = [
                    {
                        "champ": liaison["chemin"],
                        "avant": liaison["matrice_source_texte"],
                        "sortie_native": liaison["matrice_predite_texte"],
                        "apres": liaison["matrice_source_texte"],
                        "raison": "Matrice documentaire source restituée selon le contrat antérieur au moteur.",
                        "reglage_impression_modifie": False,
                        "gcode_et_geometrie_octets_identiques_au_natif": True,
                    }
                    for liaison in plan_origine["liaisons"]
                    if liaison["matrice_predite_texte"] != liaison["matrice_source_texte"]
                ]
                resultat["integrite_apres_restauration_matrices"] = integrite_3mf.verifier(
                    entree, fichier, relecture_moteur=True,
                )
            else:
                resultat["integrite_sortie_moteur"] = integrite_3mf.verifier(entree, fichier, relecture_moteur=True)
            # Garder la sortie native intacte ; seule la copie restaurée est livrée (#182).
            avec_noms = travail / "noms-restaures.3mf"
            resultat["restauration_noms"] = restaurer_noms(
                fichier, normalisation["noms_origine"], destination=avec_noms,
            )
            fichier = avec_noms
            resultat["modifications"] = [
                *normalisation["modifications"],
                *modifications_origine,
                {
                    "champ": "plater_name sortie",
                    "avant": "noms retirés avant moteur",
                    "apres": normalisation["noms_origine"],
                    "raison": "Restauration après tranchage et réouverture vérifiée.",
                    "reglage_impression_modifie": False,
                },
            ]
            _valider_tranchage(fichier, normalisation["noms_origine"], demande.get("attendu"))
            _publier_sortie(fichier, destination)
            resultat["sortie_sha256"] = sha256(destination)
            resultat["validation_sortie"] = {"statut": "PASS"}
        except (OSError, ValueError, KeyError, BadZipFile, ET.ParseError) as erreur:
            resultat["code"] = 2
            resultat["validation_sortie"] = {"statut": "FAIL", "detail": str(erreur)}
            resultat["diagnostic"] = {"cause": _CAUSE_CONTROLE_SORTIE, "fonctions": []}
            resultat["sortie_refusee"] = str(fichier)
    else:
        if processus.returncode == 0:
            resultat["code"] = 2
        try:
            resultat["diagnostic"] = diagnostic(debut)
        except (OSError, ValueError, TypeError, KeyError):
            resultat["diagnostic"] = {
                "cause": "inconnue",
                "fonctions": [],
                "lecture_rapport": "échec",
            }
    return resultat


@contextmanager
def verrou_slicer(*, annule=None):
    """Partage le verrou local et autorise l’interruption pendant l’attente si demandée."""
    if ECHANGE.is_symlink():
        raise ValueError("Dossier d’échange symbolique refusé")
    ECHANGE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ECHANGE.stat().st_uid != os.getuid():
        raise ValueError("Dossier d’échange appartenant à un autre utilisateur")
    verrou = ECHANGE / "execution.lock"
    if verrou.is_symlink():
        raise ValueError("Verrou symbolique refusé")
    with verrou.open("a") as flux:
        if annule is None:
            fcntl.flock(flux, fcntl.LOCK_EX)
        else:
            while True:
                _verifier_annulation(annule)
                try:
                    fcntl.flock(flux, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.1)
        yield


def _prive(racine=None):
    from benchab.chemins import atelier_prive

    return atelier_prive(racine)


def archiver_echec(resultat, demande, prive):
    """Conserve l’incident et sa pile ; aucune variante de réglages ni relance n’est créée."""
    dossier = _prive(prive) / "mesures/echecs" / uuid.uuid4().hex
    dossier.mkdir(mode=0o700, parents=True)
    erreurs = []
    chemins = [
        (demande.get("source"), "entree-originale.3mf"),
        (resultat.get("journal_natif"), "slicer.log"),
        (resultat.get("diagnostic", {}).get("rapport"), "plantage.ips"),
        (resultat.get("sortie_refusee"), "sortie-refusee.3mf"),
    ]
    if resultat.get("travail"):
        chemins.append((Path(resultat["travail"]) / "entree.3mf", "entree-transmise.3mf"))
    for chemin, nom in chemins:
        if chemin is None:
            continue
        try:
            if Path(chemin).is_file():
                shutil.copy2(chemin, dossier / nom)
        except OSError:
            erreurs.append(f"Copie impossible : {nom}")
    diagnostic_incident = resultat.get("diagnostic", {})
    pile = diagnostic_incident.get("pile_complete", diagnostic_incident.get("fonctions", []))
    (dossier / "pile.txt").write_text("\n".join(pile) + "\n", encoding="utf-8")
    mode = resultat.get("mode")
    operation = (
        "options-slicer" if mode in {"aide", "defauts"}
        else mode if mode == "visualisation"
        else "tranchage" if demande.get("source") is not None
        else "options-slicer"
    )
    resultat["incident"] = {
        "dossier": str(dossier),
        "operation": operation,
        "relance_automatique": False,
        "reglage_impression_modifie": False,
        "cause_etablie": False,
        "erreurs_archivage": erreurs,
        "note": (
            "Le moteur a terminé. Le contrôle de sortie a refusé l’archive."
            if diagnostic_incident.get("cause") == _CAUSE_CONTROLE_SORTIE
            else "La signature de pile est une observation, y compris bridge_over_infill ; aucune causalité établie."
        ),
        "raison_volume": "Entrées, éventuelle sortie refusée et rapport IPS conservés pour documenter l’incident.",
    }
    ecrire_json(dossier / "incident.json", resultat)


def _publier_sortie(cible, destination):
    """Publie une sortie complétée par lien exclusif ; conserve toute cible déjà apparue."""
    cible = Path(cible)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Destination apparue avant publication ; fichier préservé.")
    with tempfile.TemporaryDirectory(prefix=".benchab-sortie-", dir=destination.parent) as dossier:
        temporaire = Path(dossier) / destination.name
        shutil.copyfile(cible, temporaire)
        with temporaire.open("rb") as flux:
            os.fsync(flux.fileno())
        # Le lien dur rend visible seulement le fichier complet et refuse tout écrasement.
        os.link(temporaire, destination, follow_symlinks=False)


def trancher(demande, *, prive=None, destination_finale=None, annule=None):
    """Lance sous verrou et peut publier une sortie privée sans écrasement.

    `demande` contient la source et la cible temporaire; `prive` est la racine des preuves.
    `destination_finale` est facultative et doit rester dans l’atelier choisi ou le temporaire système.
    Renvoie le rapport avec code de processus/statuts, sans lancer l’imprimante.
    """
    destination = sortie_privee(destination_finale, prive=prive) if destination_finale is not None else None
    rapports = _prive(prive) / "mesures/tranchages"
    rapports.mkdir(parents=True, exist_ok=True)
    with (verrou_slicer() if annule is None else verrou_slicer(annule=annule)):
        source = demande.get("source")
        empreinte_source = sha256(chemin_temporaire(source)) if source is not None else None
        _verifier_limite_causes(prive, operation="tranchage", source_sha256=empreinte_source)
        # Le précontrôle initial peut dater d'avant l'attente du verrou; relire juste avant le moteur.
        if destination is not None and (destination.exists() or destination.is_symlink()):
            raise ValueError("Destination apparue avant le moteur ; aucun lancement effectué.")
        resultat = _executer(demande) if annule is None else _executer(demande, annule=annule)
        if annule is not None and annule():
            if resultat["groupe_slicer_termine"]:
                shutil.rmtree(resultat["travail"])
            raise TranchageAnnule("Tranchage annulé.")
        if resultat["code"] != 0:
            archiver_echec(resultat, demande, prive)
        preuve = rapports / f"{uuid.uuid4().hex}.json"
        resultat["rapport"] = str(preuve)
        ecrire_json(preuve, resultat)
        if destination is not None and resultat["code"] == 0:
            cible = Path(demande["destination"])
            if cible.is_file():
                try:
                    _publier_sortie(cible, destination)
                except OSError as erreur:
                    resultat.update(
                        code=2,
                        sortie_refusee=str(cible),
                        publication_sortie={"statut": "FAIL", "erreur": type(erreur).__name__},
                    )
                    ecrire_json(preuve, resultat)
                    archiver_echec(resultat, demande, prive)
                    raise
                resultat["publication_sortie"] = {"statut": "PASS", "chemin": str(destination)}
                ecrire_json(preuve, resultat)
        progression_path = _prive(prive) / "chantier/PROGRESSION.md"
        if progression_path.is_file():
            cause = resultat.get("diagnostic", {}).get("cause", "aucune")
            with progression_path.open("a") as progression:
                progression.write(
                    f"- {time.strftime('%Y-%m-%d')}, slicer unique : code {resultat['code']}, "
                    f"{resultat.get('duree_s', 0):.2f} s ; cause {cause} ; preuve {preuve.name}.\n"
                )
        return resultat


def trancher_fichier(
    source, dossier_sortie, nom=None, *, usage="impression", prive=None, timeout_s=1800, journal=None, attendu=None,
    identifiants=None, validation_visuelle=None, annule=None,
):
    """Prépare un 3MF (mm) avec catalogue et publie dans l’atelier choisi ou le temporaire système.

    Retourne la sortie et le rapport, sans modifier les profils installés.
    En usage impression, une source dans projets/<projet>/candidats exige PASS
    pour son aperçu avant toute copie. Le témoin reste libre.
    Le rappel facultatif `annule` interrompt l’attente ou le moteur local.
    """
    preuve_visuelle = None
    if usage == "impression":
        from benchab.projets.validation_apercu import verifier_candidate

        preuve_visuelle = verifier_candidate(source, prive=prive, fiche=validation_visuelle)
    source = Path(source).resolve(strict=True)
    sortie = Path(dossier_sortie) / (nom or f"{source.stem}.gcode.3mf")
    if Path(nom or sortie.name).name != (nom or sortie.name):
        raise ValueError("Nom simple exigé pour le 3MF de sortie")
    sortie = sortie_privee(sortie, prive=prive)
    if sortie.exists() or sortie == source:
        raise ValueError("Destination existante : aucun fichier n’est écrasé")
    if journal is not None:
        journal = sortie_privee(journal, prive=prive)
        if journal.exists() or journal in {source, sortie}:
            raise ValueError("Journal existant ou confondu avec un 3MF : écriture refusée")
    with tempfile.TemporaryDirectory(prefix="benchab-preparation-", dir="/private/tmp") as dossier:
        entree = Path(dossier) / "source.3mf"
        cible = Path(dossier) / "tranche.3mf"
        shutil.copy2(source, entree)
        if preuve_visuelle is not None and sha256(entree) != preuve_visuelle["source_sha256"]:
            raise ValueError("La source a changé après la validation de l’aperçu.")
        resultat = trancher(
            {
                "source": str(entree),
                "destination": str(cible),
                "usage": usage,
                "timeout_s": timeout_s,
                "attendu": attendu,
                "identifiants": identifiants,
            },
            prive=prive,
            destination_finale=sortie,
            **({"annule": annule} if annule is not None else {}),
        )
        if preuve_visuelle is not None:
            resultat["validation_visuelle"] = preuve_visuelle
        resultat["source_originale"] = str(source)
        resultat["destination_finale"] = str(sortie)
        if journal is not None:
            journal.parent.mkdir(parents=True, exist_ok=True)
            with Path(resultat["journal_natif"]).open("rb") as lecture, journal.open("xb") as ecriture:
                shutil.copyfileobj(lecture, ecriture)
            resultat["journal_conserve"] = str(journal)
        ecrire_json(Path(resultat["rapport"]), resultat)
        if resultat["code"] != 0 or not cible.is_file():
            diagnostic = resultat.get("diagnostic", {})
            cause = diagnostic.get("cause") if isinstance(diagnostic, dict) else None
            # Le message donne la preuve privée utile, sans recopier des détails de diagnostic arbitraires.
            cause = cause if isinstance(cause, str) and cause in (*CAUSES, _CAUSE_CONTROLE_SORTIE) else "inconnue"
            validation = resultat.get("validation_sortie", {})
            natif = resultat.get("code_processus")
            etape = "sortie refusée par BENCHAB" if natif == 0 else "échec du moteur ou de sa préparation"
            detail = validation.get("detail") if natif == 0 and isinstance(validation, dict) else None
            incident = resultat.get("incident")
            dossier = incident.get("dossier") if isinstance(incident, dict) else None
            message = (
                f"Slicer en échec (code {resultat['code']}, processus {natif}) : {etape} ; "
                f"rapport privé : {resultat['rapport']} ; cause : {cause}"
            )
            if detail:
                message += f" ; détail : {detail}"
            if dossier:
                message += f" ; incident : {dossier}"
            raise RuntimeError(message)
        if not sortie.is_file():
            raise RuntimeError("Slicer en échec : aucune sortie privée publiée.")
    return sortie, resultat


def servir():
    """Surveille les demandes privées et consigne leurs résultats, sans commande imprimante."""
    ECHANGE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ECHANGE / "unique.lock").open("a") as verrou:
        fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (ECHANGE / "arreter").unlink(missing_ok=True)
        (ECHANGE / "pret").write_text(str(os.getpid()), encoding="utf-8")
        print("Slicer unique prêt ; calcul natif avec configuration temporaire et verrou commun.", flush=True)
        try:
            while not (ECHANGE / "arreter").exists():
                for requete in sorted(ECHANGE.glob("*.demande.json")):
                    reponse = requete.with_name(requete.name.replace(".demande.json", ".resultat.json"))
                    if reponse.exists():
                        continue
                    try:
                        if requete.is_symlink() or requete.stat().st_size > 8 * 1024**2:
                            raise ValueError("Demande slicer invalide ; lecture refusée")
                        demande_donnees = json.loads(requete.read_text())

                        def rapports_existants():
                            for ancien in ECHANGE.glob("*.resultat.json"):
                                yield _lire_rapport_limite(
                                    ancien, libelle="Rapport d'incident invalide ; nouveau lancement refusé"
                                ), "tranchage"

                        source_demande = demande_donnees.get("source")
                        empreinte_source = (
                            sha256(chemin_temporaire(source_demande)) if source_demande is not None else None
                        )
                        if _plafond_atteint(
                            rapports_existants(), operation="tranchage", source_sha256=empreinte_source
                        ):
                            raise ValueError("Trois essais atteints pour une cause : arrêt sans nouveau lancement.")
                        resultat = trancher(demande_donnees)
                    except Exception as erreur:
                        resultat = {"code": 2, "erreur": str(erreur), "modifications": []}
                    ecrire_json(reponse, resultat)
                    print(
                        f"Tranchage terminé : code {resultat['code']}, {resultat.get('duree_s', 0):.2f} s",
                        flush=True,
                    )
                time.sleep(0.1)
        finally:
            (ECHANGE / "pret").unlink(missing_ok=True)


def client(arguments):
    """Transmet les arguments historiques à la file locale et attend son code au plus 1 800 s."""
    parser = argparse.ArgumentParser(description="Adaptateur limité aux arguments de tranchage historique")
    parser.add_argument("--datadir")
    parser.add_argument("--arrange", choices=["0"])
    parser.add_argument("--orient", choices=["0"])
    parser.add_argument("--slice", choices=["0"], required=True)
    parser.add_argument("--outputdir", required=True)
    parser.add_argument("--export-3mf", required=True)
    parser.add_argument("--usage", choices=["impression", "temoin"], default="impression")
    parser.add_argument(
        "--identifiants",
        type=Path,
        default=os.environ.get("BENCHAB_IDENTIFIANTS_SLICER"),
        help="Catalogue explicite d’identifiants de profils existants",
    )
    parser.add_argument("source")
    options = parser.parse_args(arguments)
    if Path(options.export_3mf).name != options.export_3mf:
        raise ValueError("Nom simple exigé pour l'export")
    if not (ECHANGE / "pret").exists():
        raise ValueError("Le service de tranchage unique doit être démarré avec --servir.")
    identifiant = uuid.uuid4().hex
    demande = ECHANGE / f"{identifiant}.demande.json"
    reponse = ECHANGE / f"{identifiant}.resultat.json"
    ecrire_json(
        demande,
        {
            "source": str(chemin_temporaire(options.source)),
            "destination": str(chemin_temporaire(Path(options.outputdir) / options.export_3mf)),
            "usage": options.usage,
            "identifiants": json.loads(options.identifiants.read_text())["filaments"] if options.identifiants else None,
        },
    )
    limite = time.monotonic() + 1800
    while not reponse.exists():
        if time.monotonic() > limite:
            raise TimeoutError("Le slicer unique n'a pas répondu sous 30 minutes.")
        time.sleep(0.1)
    resultat = json.loads(reponse.read_text())
    print(json.dumps(resultat, ensure_ascii=False))
    return resultat["code"]


def _fichier_visualisable(source, prive):
    """Valide une archive tranchée privée : plaques 1–30, numéros uniques et CRC complet."""
    source = sortie_privee(source, prive=prive)
    with Archive3MF(source) as archive:
        noms = archive.noms()
        plaques = plaques_gcode_archive(archive)
        if not 1 <= len(plaques) <= 30:
            raise ValueError("Visualisation : 3MF déjà tranché requis, entre 1 et 30 plaques")
        try:
            modele = archive.xml("Metadata/model_settings.config")
        except (KeyError, ET.ParseError, ValueError) as erreur:
            raise ValueError("Structure des plaques absente ou invalide") from erreur
        if modele.tag != "config":
            raise ValueError("Structure des plaques absente ou invalide")
        plaques_xml = modele.findall("plate")
        nombre_xml = len(plaques_xml)
        if not 1 <= nombre_xml <= 30:
            raise ValueError("Visualisation : entre 1 et 30 plaques déclarées exigées")
        indices = {numero for numero, _ in plaques}
        if len(plaques) != nombre_xml or indices != set(range(1, nombre_xml + 1)):
            raise ValueError("Visualisation : G-code et plaques déclarées incohérents")
        archive.verifier(membres=(nom for nom in noms if nom != "Metadata/model_settings.config"))
    return source


def demander_ouverture(source, *, prive=None, delai=20, sha_attendu=None):
    """Superviseur indépendant : le verrou reste pris jusqu'à fermeture de la fenêtre."""
    source = _fichier_visualisable(source, prive)
    attendu = sha_attendu or sha256(source)
    if sha256(source) != attendu:
        raise ValueError("Fichier différent de la préparation contrôlée")
    dossier = _prive(prive) / "mesures/ouvertures-slicer"
    dossier.mkdir(parents=True, exist_ok=True)
    accuse = dossier / (uuid.uuid4().hex + ".json")
    with accuse.with_suffix(".log").open("wb") as journal:
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "benchab.fabrication.trancher",
                "--visualiser",
                str(source),
                "--prive",
                str(_prive(prive)),
                "--accuse",
                str(accuse),
                "--sha256",
                attendu,
            ],
            stdout=journal,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    limite = time.monotonic() + delai
    while not accuse.exists():
        if time.monotonic() > limite:
            raise subprocess.TimeoutExpired("superviseur-slicer", delai)
        time.sleep(0.05)
    resultat = json.loads(accuse.read_text())
    if resultat.get("code") != 0:
        raise ValueError(resultat.get("erreur", "Ouverture refusée"))
    return resultat


def _visualiser(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--visualiser", type=Path, required=True)
    parser.add_argument("--prive", type=Path, required=True)
    parser.add_argument("--accuse", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args(arguments)
    source = _fichier_visualisable(args.visualiser, args.prive)
    from benchab.qualite.ecritures import _sans_lien

    accuse = _sans_lien(args.accuse.absolute())
    if not accuse.is_relative_to(_prive(args.prive)) or accuse.exists():
        raise ValueError("Accusé privé neuf exigé")
    debut = time.time()
    with verrou_slicer(), tempfile.TemporaryDirectory(prefix="benchab-visualiser-", dir="/private/tmp") as tmp:
        def rapports_echecs():
            for ancien in (_prive(args.prive) / "mesures/echecs").glob("*/incident.json"):
                yield _lire_rapport_limite(ancien, libelle="Rapport d'incident invalide ; ouverture refusée"), None

        attendu = args.sha256
        if _plafond_atteint(rapports_echecs(), operation="visualisation", source_sha256=attendu):
            ecrire_json(accuse, {"code": 2, "erreur": "Trois essais atteints pour une cause : ouverture refusée"})
            return 2
        travail = Path(tmp)
        for nom in ("data", "sorties", "home", "tmp", "cache"):
            (travail / nom).mkdir()
        copie = travail / "tranche.3mf"
        shutil.copyfile(source, copie)
        if sha256(source) != attendu or sha256(copie) != attendu:
            raise ValueError("Copie de visualisation divergente")
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(travail / "home"),
            "TMPDIR": str(travail / "tmp"),
            "XDG_CACHE_HOME": str(travail / "cache"),
            "XDG_CONFIG_HOME": str(travail / "data"),
            "LC_ALL": "en_US.UTF-8",
        }
        commande = [
            str(charger()["slicer"]["executable"]),
            "--datadir",
            str(travail / "data"),
            "--outputdir",
            str(travail / "sorties"),
            str(copie),
        ]
        try:
            with accuse.with_suffix(".moteur.log").open("wb") as journal:
                moteur = subprocess.Popen(commande, cwd=travail, env=env, stdout=journal, stderr=subprocess.STDOUT)
                ecrire_json(
                    accuse,
                    {
                        "code": 0,
                        "ouverture": "DEMANDEE",
                        "pid": moteur.pid,
                        "source_sha256": attendu,
                        "modifications": [],
                    },
                )
                code = moteur.wait()
            rapport = {
                "code": code,
                "mode": "visualisation",
                "source_sha256": attendu,
                "source_conservee": sha256(source) == attendu,
                "modifications": [],
                "duree_s": time.time() - debut,
            }
            if code:
                rapport["diagnostic"] = diagnostic(debut)
                archiver_echec(rapport, {"source": str(source)}, args.prive)
            ecrire_json(accuse.with_suffix(".fin.json"), rapport)
            progression = _prive(args.prive) / "chantier/PROGRESSION.md"
            if progression.is_file():
                with progression.open("a") as journal:
                    journal.write(
                        f"- {time.strftime('%Y-%m-%d')}, visualisation slicer unique : code {code}, "
                        f"réglages et noms de plaques conservés ; rapport {accuse.with_suffix('.fin.json').name}.\n"
                    )
            return code
        except OSError as erreur:
            ecrire_json(accuse, {"code": 2, "erreur": str(erreur), "modifications": []})
            return 2


def main(arguments=None):
    """Lance le service local, son arrêt, le client ou la visualisation et renvoie leur code."""
    arguments = sys.argv[1:] if arguments is None else arguments
    if arguments and arguments[0] == "--visualiser":
        return _visualiser(arguments)
    if arguments == ["--servir"]:
        servir()
    elif arguments == ["--arreter"]:
        ECHANGE.mkdir(mode=0o700, parents=True, exist_ok=True)
        (ECHANGE / "arreter").touch()
    else:
        return client(arguments)
    return 0


if __name__ == "__main__":
    sys.exit(main())
