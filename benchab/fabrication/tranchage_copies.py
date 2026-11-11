"""Prépare et vérifie les copies 3MF du trancheur, sans lancer le moteur."""

import hashlib
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from benchab import integrite_3mf
from benchab import reglages as schema_reglages


def sha256(chemin):
    """Relit le fichier et renvoie son empreinte SHA-256 hexadécimale, sans écriture."""
    with Path(chemin).open("rb") as flux:
        return hashlib.file_digest(flux, "sha256").hexdigest()


def chemin_temporaire(chemin):
    """Valide une copie temporaire reconnue ; Path sans unité ou FAIL, aucun lien interne ni réglage modifié."""
    from benchab.chemins import est_temporaire, sans_lien

    valeur = sans_lien(chemin)
    if not est_temporaire(valeur):
        raise ValueError("Le slicer ne lit et n'écrit que dans une copie sous un temporaire reconnu "
                         "(/private/tmp ou temporaire système natif).")
    return valeur


def normaliser(source, destination, usage="impression", identifiants=None, *, configuration):
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
        maximum = min(30, int(configuration()["slicer"]["plaques_max"]))
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
