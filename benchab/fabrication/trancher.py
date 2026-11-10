"""Lanceur unique : configuration temporaire, réglages conservés et tranchages sérialisés.

Le chemin du slicer vient du profil machine. Le client ne peut transmettre ni
shell, ni argument réseau, ni commande d’imprimante. La préparation travaille
sur une copie ; les noms de plaques sont restaurés et le résultat est relu.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from benchab import integrite_3mf
from benchab import reglages as schema_reglages
from benchab.machine.configuration import charger

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


def sha256(chemin):
    with Path(chemin).open("rb") as flux:
        return hashlib.file_digest(flux, "sha256").hexdigest()


def ecrire_json(chemin, donnees):
    """Remplace le rapport JSON UTF-8 ; aucun réglage d’impression n’est modifié."""
    temporaire = chemin.with_suffix(".tmp")
    temporaire.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporaire.replace(chemin)


def chemin_temporaire(chemin):
    """Valide une copie temporaire reconnue ; Path sans unité ou FAIL, aucun lien interne ni réglage modifié."""
    from benchab.chemins import est_temporaire, sans_lien

    valeur = sans_lien(chemin)
    if not est_temporaire(valeur):
        raise ValueError("Le slicer ne lit et n'écrit que dans une copie sous un temporaire reconnu "
                         "(/private/tmp ou temporaire système natif).")
    return valeur


def normaliser(source, destination, usage="impression", identifiants=None):
    """Normalise exclusivement une nouvelle copie de l'entrée, avant le binaire natif. Réglages en
    unités natives (mm, s, °C, %) lus via le dictionnaire typé ; catalogue exact, aucun réglage inconnu transformé."""
    modifications = []
    if usage not in {"impression", "temoin"}:
        raise ValueError("Usage autorisé : impression ou temoin.")
    with ZipFile(source) as entree:
        noms = entree.namelist()
        if len(noms) != len(set(noms)):
            raise ValueError("Membres ZIP dupliqués")
        modele = ET.fromstring(
            entree.read("Metadata/model_settings.config"),
            parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)),
        )
        plaques = modele.findall("plate")
        maximum = min(30, int(charger()["slicer"]["plaques_max"]))
        if not 1 <= len(plaques) <= maximum:
            raise ValueError("Un 3MF doit contenir entre 1 et 30 plaques.")
        noms_origine = []
        for index, plaque in enumerate(plaques):
            champs = [m for m in plaque.findall("metadata") if m.get("key") == "plater_name"]
            if len(champs) != 1:
                raise ValueError("Un unique nom de plaque est requis, même vide.")
            noms_origine.append(champs[0].get("value", ""))
            for metadata in plaque.findall("metadata"):
                if metadata.get("key") == "plater_name" and metadata.get("value"):
                    metadata.set("value", "")
                    modifications.append(
                        {
                            "champ": f"plaques[{index}].plater_name",
                            "avant": noms_origine[-1],
                            "apres": "",
                            "raison": "Nom retiré avant le moteur, à restaurer et vérifier après tranchage.",
                            "reglage_impression_modifie": False,
                        }
                    )
        reglages = schema_reglages.decoder(entree.read("Metadata/project_settings.config"))
        origine_reglages = dict(reglages)
        post_traitement = reglages.get("post_process", [])
        traitement_actif = any(post_traitement) if isinstance(post_traitement, list) else bool(post_traitement)
        if traitement_actif:
            raise ValueError("post_process externe interdit : seul le slicer peut être exécuté.")
        cles = ("filament_type", "filament_colour", "filament_ids", "filament_settings_id")
        listes = {}
        for cle in cles:
            valeur = reglages.get(cle)
            listes[cle] = valeur if isinstance(valeur, list) else [valeur] if valeur else []
        extrudeurs = [
            int(m.get("value"))
            for m in modele.iter("metadata")
            if m.get("key") == "extruder" and str(m.get("value")).isdigit()
        ]
        # Le lecteur CLI ne partage pas le comptage de PresetBundle ; déclarer le
        # consommateur, sans inventer les profils des couleurs résiduelles (docs/reference/reglages-et-integrite.md).
        nombre = schema_reglages.nombre_filaments_metadata(reglages, consommateur="cli", extrudeurs=extrudeurs)
        reglages = schema_reglages.Reglages(reglages, contexte={"consommateur_reglages": "cli",
                                                             "nombre_filaments_declares": nombre})
        provenances_ids = {}
        if identifiants:
            ids = list(listes["filament_ids"])
            for index, nom in enumerate(listes["filament_settings_id"]):
                entree_id = identifiants.get(nom) if isinstance(identifiants, dict) else None
                if index < len(ids) and ids[index]:
                    continue
                if isinstance(entree_id, list):
                    if len(entree_id) != 1:
                        raise ValueError(f"Correspondance absente ou ambiguë pour le profil {nom}.")
                    entree_id = entree_id[0]
                if entree_id is None:
                    raise ValueError(f"Correspondance absente du catalogue pour le profil {nom}.")
                if not isinstance(entree_id, dict):
                    raise ValueError(f"Entrée de catalogue invalide pour le profil {nom}.")
                valeur_id = entree_id.get("id")
                if not isinstance(valeur_id, str) or not valeur_id or "BENCHAB-temporaire-" in valeur_id:
                    raise ValueError("Identifiant de profil permanent explicite requis dans le catalogue")
                while len(ids) <= index:
                    ids.append(None)
                ids[index] = valeur_id
                provenances_ids[str(index)] = entree_id.get("sources", [])
            listes["filament_ids"] = ids
        if not listes["filament_type"] or any(not valeur for valeur in listes["filament_type"]):
            raise ValueError("Type de filament absent : aucun matériau ne peut être inventé.")
        for cle in cles:
            avant = reglages.get(cle)
            valeurs = listes[cle]
            if cle in {"filament_type", "filament_colour"}:
                if len(valeurs) < nombre or any(not v for v in valeurs[:nombre]):
                    raise ValueError(f"{cle} incomplet : aucun réglage d'impression ne sera inventé.")
                if not isinstance(avant, list):
                    raise ValueError(f"{cle} doit déjà être une liste explicite.")
                continue
            if usage == "impression" and (len(valeurs) < nombre or any(not v for v in valeurs[:nombre])):
                raise ValueError(f"{cle} incomplet : identifiants temporaires interdits pour l'impression.")
            resultat = []
            for index in range(nombre):
                valeur = valeurs[index] if index < len(valeurs) else valeurs[0] if len(valeurs) == 1 else None
                if not valeur:
                    if cle == "filament_colour":
                        raise ValueError("Couleur absente : préciser le profil source.")
                    if cle == "filament_type":
                        raise ValueError("Type de filament incomplet.")
                    if usage != "temoin":
                        raise ValueError(f"{cle} incomplet : identifiants temporaires interdits pour l'impression.")
                    valeur = f"BENCHAB-temporaire-{index + 1}"
                resultat.append(str(valeur))
            reglages[cle] = resultat
            if avant != resultat:
                modifications.append(
                    {
                        "champ": cle,
                        "avant": avant,
                        "apres": resultat,
                        "raison": "Métadonnées de profil, sans changement des paramètres de fabrication.",
                        "reglage_impression_modifie": False,
                        "sources_identifiants": provenances_ids if cle == "filament_ids" else {},
                    }
                )
        identifiant = reglages.get("filament_id")
        if "filament_id" in reglages and (not isinstance(identifiant, str) or not identifiant):
            reglages["filament_id"] = reglages["filament_ids"][0]
            modifications.append(
                {
                    "champ": "filament_id",
                    "avant": identifiant,
                    "apres": reglages["filament_id"],
                    "raison": "Métadonnée scalaire existante ; la liste demeure dans filament_ids.",
                    "reglage_impression_modifie": False,
                }
            )
        groupe = reglages.get("inherits_group")
        if groupe is not None and len(groupe) != nombre + 2:
            if not isinstance(groupe, list) or any(parent != "" for parent in groupe):
                raise ValueError("Groupe d'héritages nommé incohérent : conversion refusée.")
            reglages["inherits_group"] = [""] * (nombre + 2)
            modifications.append(
                {
                    "champ": "inherits_group",
                    "avant": groupe,
                    "apres": reglages["inherits_group"],
                    "raison": "Cardinalité des héritages vides ; aucun parent ou réglage n'est substitué.",
                    "reglage_impression_modifie": False,
                }
            )
        if usage == "impression" and any(
            "BENCHAB-temporaire-" in str(reglages.get(cle, "")) for cle in (*cles, "filament_id")
        ):
            raise ValueError("Identifiant BENCHAB-temporaire interdit dans un fichier destiné à l'impression.")
        # Les surcharges d’objet sont légitimes, y compris lorsqu’elles diffèrent du projet.
        # Aucun réglage d’impression n’est supprimé, déplacé ou inféré par ce lanceur.
        remplacements = {
            "Metadata/model_settings.config": ET.tostring(modele, encoding="utf-8", xml_declaration=True),
            "Metadata/project_settings.config": schema_reglages.encoder(reglages).encode(),
        }
        metadonnees_adaptees = {"filament_ids", "filament_id", "filament_settings_id", "inherits_group"}
        if {k: v for k, v in origine_reglages.items() if k not in metadonnees_adaptees} != {
            k: v for k, v in reglages.items() if k not in metadonnees_adaptees
        }:
            raise ValueError("Un réglage d'impression serait modifié : tranchage refusé.")
        for membre, contenu in remplacements.items():
            avant = hashlib.sha256(entree.read(membre)).hexdigest()
            apres = hashlib.sha256(contenu).hexdigest()
            if avant != apres:
                modifications.append(
                    {
                        "champ": membre,
                        "avant_sha256": avant,
                        "apres_sha256": apres,
                        "raison": "Sérialisation des seules modifications documentées ci-dessus.",
                        "reglage_impression_modifie": False,
                    }
                )
        permis = []
        for changement in modifications:
            if "avant" not in changement:
                continue
            champ = changement["champ"]
            if champ.startswith("plaques["):
                indice = int(champ.split("[", 1)[1].split("]", 1)[0])
                chemin_champ = f"plaque/{indice}/plater_name"
                present = True
            else:
                chemin_champ = "projet/" + champ
                present = champ in origine_reglages
            permis.append(
                integrite_3mf.autorisation(
                    chemin_champ, changement["avant"], changement["apres"], present_avant=present
                )
            )
        destination = Path(destination)
        if Path(source).resolve() == destination.resolve():
            raise ValueError("La normalisation requiert une copie distincte de la source.")
        # Le moteur ne doit jamais recevoir un ZIP incomplet, même si une
        # écriture échoue avant la relecture (docs/reference/integrite-3mf.md).
        fd, nom_temporaire = tempfile.mkstemp(prefix=".normalisation-", suffix=".3mf", dir=destination.parent)
        temporaire = Path(nom_temporaire)
        try:
            os.close(fd)
            with ZipFile(temporaire, "w") as sortie:
                sortie.comment = entree.comment
                for membre in entree.infolist():
                    if membre.filename in remplacements:
                        sortie.writestr(membre, remplacements[membre.filename])
                    else:
                        with entree.open(membre) as flux, sortie.open(membre, "w") as cible:
                            shutil.copyfileobj(flux, cible)
            integrite = integrite_3mf.verifier(source, temporaire, permis)
            temporaire.replace(destination)
        finally:
            temporaire.unlink(missing_ok=True)
    return {
        "plaques": len(plaques),
        "filaments": nombre,
        "modifications": modifications,
        "noms_origine": noms_origine,
        "usage": usage,
        "integrite_reglages": integrite,
        "controle_types": schema_reglages.verifier(reglages, {"nombre_plaques_declares": len(plaques)}),
    }


def restaurer_noms(chemin, noms):
    """Restaure les noms sans unité et relit tous les membres ; retourne PASS ou lève FAIL, sans changer le G-code."""
    chemin = Path(chemin)
    avant = integrite_3mf.instantane(chemin)
    permis = []
    for i, nom_origine in enumerate(noms):
        cle = f"plaque/{i}/plater_name"
        if cle not in avant:
            raise ValueError("Nom de plaque absent dans la sortie.")
        if avant[cle] != nom_origine:
            permis.append(integrite_3mf.autorisation(cle, avant[cle], nom_origine))
    fd, nom = tempfile.mkstemp(prefix=".noms-", suffix=".3mf", dir=chemin.parent)
    temporaire = Path(nom)
    try:
        os.close(fd)
        with ZipFile(chemin) as entree, ZipFile(temporaire, "w") as sortie:
            modele = ET.fromstring(
                entree.read("Metadata/model_settings.config"),
                parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)),
            )
            plaques = modele.findall("plate")
            if len(plaques) != len(noms):
                raise ValueError("Nombre de plaques modifié par le slicer : restauration refusée.")
            for plaque, nom_origine in zip(plaques, noms):
                champs = [m for m in plaque.findall("metadata") if m.get("key") == "plater_name"]
                if len(champs) != 1:
                    raise ValueError("Nom de plaque absent ou dupliqué dans la sortie.")
                champs[0].set("value", nom_origine)
            sortie.comment = entree.comment
            empreintes = {}
            for membre in entree.infolist():
                calcul = hashlib.sha256()
                if membre.filename == "Metadata/model_settings.config":
                    contenu = ET.tostring(modele, encoding="utf-8", xml_declaration=True)
                    calcul.update(contenu)
                    sortie.writestr(membre, contenu)
                else:
                    with entree.open(membre) as lecture, sortie.open(membre, "w") as ecriture:
                        while bloc := lecture.read(1024 * 1024):
                            calcul.update(bloc)
                            ecriture.write(bloc)
                empreintes[membre.filename] = calcul.hexdigest()
        with ZipFile(temporaire) as relu:
            if relu.testzip() is not None or len(relu.namelist()) != len(empreintes):
                raise ValueError("Réouverture du 3MF restauré invalide.")
            for membre, attendu in empreintes.items():
                with relu.open(membre) as flux:
                    obtenu = hashlib.file_digest(flux, "sha256").hexdigest()
                if obtenu != attendu:
                    raise ValueError(f"Membre modifié lors de la restauration : {membre}")
            modele = ET.fromstring(relu.read("Metadata/model_settings.config"))
            obtenus = [p.find("metadata[@key='plater_name']").get("value") for p in modele.findall("plate")]
            if obtenus != noms:
                raise ValueError("Noms de plaques différents après réouverture.")
        integrite_3mf.verifier(chemin, temporaire, permis)
        temporaire.replace(chemin)
        return {"noms": obtenus, "reouverture": "PASS", "membres_verifies": len(empreintes)}
    finally:
        temporaire.unlink(missing_ok=True)


def diagnostic(debut):
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


def _verifier_limite_causes(prive):
    """Partage le plafond de trois incidents entre inspection et tranchage (docs/reference/slicer-anycubic.md)."""
    echecs = {}
    for famille in ("tranchages", "options-slicer"):
        for ancien in (_prive(prive) / "mesures" / famille).glob("*.json"):
            if ancien.is_symlink() or ancien.stat().st_size > 8 * 1024**2:
                raise ValueError("Rapport d'incident invalide ; nouveau lancement refusé")
            cause = json.loads(ancien.read_text()).get("diagnostic", {}).get("cause")
            if cause:
                groupe = GROUPES_CAUSES.get(cause, cause)
                echecs[groupe] = echecs.get(groupe, 0) + 1
    if any(nombre >= 3 for nombre in echecs.values()):
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
    resultat = {"commande": commande, "journal": str(journal), "journal_natif": str(journal), "travail": str(travail)}
    from benchab.processus import executer

    with verrou_slicer(), journal.open("x") as flux:
        _verifier_limite_causes(prive)
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
    """Vérifie source (chemin 3MF) ; retourne None/PASS implicite ou lève ValueError/FAIL.

    Ne lit aucun profil installé et ne modifie aucun réglage embarqué.
    """
    try:
        with ZipFile(source) as archive:
            reglages = schema_reglages.decoder(archive.read("Metadata/project_settings.config").decode("utf-8"))
    except (OSError, KeyError, BadZipFile, UnicodeDecodeError, ValueError) as erreur:
        raise ValueError("Machine embarquée absente ou illisible : lancement refusé.") from erreur

    machine = charger()["machine"]
    alias_autorises = {"Anycubic Kobra S1", "Anycubic Kobra S1 Combo"}
    modele = reglages.get("printer_model")
    if modele not in alias_autorises or modele not in machine.get("modeles_slicer", []):
        raise ValueError("Modèle embarqué absent ou hors S1/S1 Combo : lancement refusé.")
    # Le schéma historique ne typait pas encore nozzle_diameter ; le décodeur
    # préserve sa représentation. On accepte uniquement les deux formes exactes
    # du contrat Source, sans conversion ni modification du réglage (voir règles).
    if machine.get("buse_mm") != 0.4 or reglages.get("nozzle_diameter") not in (["0.4"], [0.4]):
        raise ValueError("Buse embarquée différente de l’unique 0,4 mm : lancement refusé.")


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


def _executer(demande):
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
    _verifier_machine_embarquee(source)
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
    with (travail / "slicer.log").open("w") as journal:
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
        groupe_termine = True
        while True:
            termine, statut, ressources = os.wait4(processus.pid, os.WNOHANG)
            if termine:
                break
            if time.monotonic() > limite:
                expiration = True
                # Un slicer peut déléguer le calcul : le PID seul laisserait ses
                # descendants actifs. La session dédiée borne SIGTERM/SIGKILL à
                # ce lancement, jamais au processus BENCHAB parent.
                groupe_termine = _terminer_groupe_slicer(processus.pid)
                _, statut, ressources = os.wait4(processus.pid, 0)
                break
            time.sleep(0.1)
        code_processus = os.waitstatus_to_exitcode(statut)
        processus.returncode = 124 if expiration or not groupe_termine else code_processus
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
                resultat["integrite_apres_restauration_matrices"] = integrite_3mf.verifier(entree, fichier)
            else:
                resultat["integrite_sortie_moteur"] = integrite_3mf.verifier(entree, fichier)
            resultat["restauration_noms"] = restaurer_noms(fichier, normalisation["noms_origine"])
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
def verrou_slicer():
    """Le service et les appels directs utilisent le même verrou d’exécution."""
    if ECHANGE.is_symlink():
        raise ValueError("Dossier d’échange symbolique refusé")
    ECHANGE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ECHANGE.stat().st_uid != os.getuid():
        raise ValueError("Dossier d’échange appartenant à un autre utilisateur")
    verrou = ECHANGE / "execution.lock"
    if verrou.is_symlink():
        raise ValueError("Verrou symbolique refusé")
    with verrou.open("a") as flux:
        fcntl.flock(flux, fcntl.LOCK_EX)
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
    resultat["incident"] = {
        "dossier": str(dossier),
        "relance_automatique": False,
        "reglage_impression_modifie": False,
        "cause_etablie": False,
        "erreurs_archivage": erreurs,
        "note": "La signature de pile est une observation, y compris bridge_over_infill ; aucune causalité établie.",
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


def trancher(demande, *, prive=None, destination_finale=None):
    """Lance sous verrou et peut publier une sortie privée sans écrasement.

    `demande` contient la source et la cible temporaire; `prive` est la racine des preuves.
    `destination_finale` est facultative et doit rester dans l’espace privé ou `/private/tmp`.
    Renvoie le rapport avec code de processus/statuts, sans lancer l’imprimante.
    """
    rapports = _prive(prive) / "mesures/tranchages"
    rapports.mkdir(parents=True, exist_ok=True)
    destination = Path(destination_finale).resolve() if destination_finale is not None else None
    if destination is not None and not (
        destination.is_relative_to(_prive(prive)) or destination.is_relative_to(Path("/private/tmp"))
    ):
        raise ValueError("La sortie finale doit rester dans l’espace privé ou temporaire.")
    with verrou_slicer():
        _verifier_limite_causes(prive)
        # Le précontrôle initial peut dater d'avant l'attente du verrou; relire juste avant le moteur.
        if destination is not None and (destination.exists() or destination.is_symlink()):
            raise ValueError("Destination apparue avant le moteur ; aucun lancement effectué.")
        resultat = _executer(demande)
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
    identifiants=None,
):
    """Prépare un 3MF privé (mm) avec catalogue ; sortie/rapport, sans toucher aux profils installés."""
    source = Path(source).resolve(strict=True)
    sortie = Path(dossier_sortie).resolve() / (nom or f"{source.stem}.gcode.3mf")
    if Path(nom or sortie.name).name != (nom or sortie.name):
        raise ValueError("Nom simple exigé pour le 3MF de sortie")
    if not sortie.is_relative_to(_prive(prive)) and not sortie.is_relative_to(Path("/private/tmp")):
        raise ValueError("Le tranchage écrit seulement dans le dossier privé ou temporaire")
    if sortie.exists() or sortie == source:
        raise ValueError("Destination existante : aucun fichier n’est écrasé")
    if journal is not None:
        journal = Path(journal).resolve()
        if not journal.is_relative_to(_prive(prive)) and not journal.is_relative_to(Path("/private/tmp")):
            raise ValueError("Le journal écrit seulement dans le dossier privé ou temporaire")
        if journal.exists() or journal in {source, sortie}:
            raise ValueError("Journal existant ou confondu avec un 3MF : écriture refusée")
    with tempfile.TemporaryDirectory(prefix="benchab-preparation-", dir="/private/tmp") as dossier:
        entree = Path(dossier) / "source.3mf"
        cible = Path(dossier) / "tranche.3mf"
        shutil.copy2(source, entree)
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
        )
        resultat["source_originale"] = str(source)
        resultat["destination_finale"] = str(sortie)
        if journal is not None:
            journal.parent.mkdir(parents=True, exist_ok=True)
            with Path(resultat["journal_natif"]).open("rb") as lecture, journal.open("xb") as ecriture:
                shutil.copyfileobj(lecture, ecriture)
            resultat["journal_conserve"] = str(journal)
        ecrire_json(Path(resultat["rapport"]), resultat)
        if resultat["code"] != 0 or not cible.is_file():
            raise RuntimeError(f"Slicer en échec (code {resultat['code']}) ; consulter le rapport privé")
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
                        echecs = {}
                        for ancien in ECHANGE.glob("*.resultat.json"):
                            cause = json.loads(ancien.read_text()).get("diagnostic", {}).get("cause")
                            if cause:
                                echecs[cause] = echecs.get(cause, 0) + 1
                        if any(nombre >= 3 for nombre in echecs.values()):
                            raise ValueError("Trois essais atteints pour une cause : arrêt sans nouveau lancement.")
                        resultat = trancher(json.loads(requete.read_text()))
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
    from benchab.qualite.ecritures import _sans_lien

    source = _sans_lien(Path(source).absolute())
    racines = (_prive(prive), Path("/private/tmp"))
    if not any(source.is_relative_to(r) for r in racines):
        raise ValueError("Copie privée ou temporaire exigée pour la visualisation")
    with ZipFile(source) as archive:
        gcodes = [n for n in archive.namelist() if n.startswith("Metadata/") and n.endswith(".gcode")]
        if not 1 <= len(gcodes) <= 30 or archive.testzip():
            raise ValueError("Visualisation : 3MF déjà tranché requis, entre 1 et 30 plaques")
        try:
            modele = ET.fromstring(archive.read("Metadata/model_settings.config"))
        except (KeyError, ET.ParseError) as erreur:
            raise ValueError("Structure des plaques absente ou invalide") from erreur
        if not 1 <= len(modele.findall("plate")) <= 30:
            raise ValueError("Visualisation : entre 1 et 30 plaques déclarées exigées")
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
        echecs = {}
        for ancien in (_prive(args.prive) / "mesures/echecs").glob("*/incident.json"):
            cause = json.loads(ancien.read_text()).get("diagnostic", {}).get("cause")
            if cause:
                groupe = GROUPES_CAUSES.get(cause, cause)
                echecs[groupe] = echecs.get(groupe, 0) + 1
        if any(nombre >= 3 for nombre in echecs.values()):
            ecrire_json(accuse, {"code": 2, "erreur": "Trois essais atteints pour une cause : ouverture refusée"})
            return 2
        travail = Path(tmp)
        for nom in ("data", "sorties", "home", "tmp", "cache"):
            (travail / nom).mkdir()
        copie = travail / "tranche.3mf"
        shutil.copyfile(source, copie)
        attendu = args.sha256
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
