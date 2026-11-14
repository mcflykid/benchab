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
from benchab.archives_3mf import Archive3MF, decoder_xml

_BUDGET_XML_OCTETS = 8 * 1024 * 1024


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
    """Normalise une copie 3MF distincte avant le binaire natif, sans lancement.

    Reçoit source, destination, usage, catalogue optionnel et fournisseur de
    configuration ; les réglages en mm, s, °C et % passent par le dictionnaire typé.
    Renvoie le bilan des différences permises et de leur intégrité ; lève `ValueError`
    si l’archive, les budgets XML/JSON de 8 Mio ou les références sont invalides.
    Les membres sont lus avec CRC, copiés en flux en conservant ordre et métadonnées,
    puis contrôlés avant publication. Aucun réglage physique n’est inventé.
    Voir [intégrité 3MF](../../docs/reference/integrite-3mf.md).
    """
    modifications = []
    if usage not in {"impression", "temoin"}:
        raise ValueError("Usage autorisé : impression ou temoin.")
    with Archive3MF(source, taille_max_xml=_BUDGET_XML_OCTETS) as entree:
        noms = entree.noms()
        membre_modele = "Metadata/model_settings.config"
        membre_reglages = "Metadata/project_settings.config"
        absents = [membre for membre in (membre_modele, membre_reglages) if membre not in noms]
        if absents:
            raise ValueError(
                "Tranchage refusé : membres de configuration absents du 3MF : " + ", ".join(absents)
            )
        if entree.taille_membre(membre_modele) > _BUDGET_XML_OCTETS:
            raise ValueError(f"budget XML dépassé : {membre_modele}")
        modele_brut = entree.lire(membre_modele, limite_octets=_BUDGET_XML_OCTETS)
        modele = decoder_xml(
            modele_brut,
            limite_octets=_BUDGET_XML_OCTETS,
            commentaires=True,
            instructions_traitement=True,
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
        if entree.taille_membre(membre_reglages) > _BUDGET_XML_OCTETS:
            raise ValueError(f"budget réglages dépassé : {membre_reglages}")
        reglages_brut = entree.lire(membre_reglages, limite_octets=_BUDGET_XML_OCTETS)
        reglages = schema_reglages.decoder(reglages_brut)
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
        sources_remplacees = {
            "Metadata/model_settings.config": modele_brut,
            "Metadata/project_settings.config": reglages_brut,
        }
        for membre, contenu in remplacements.items():
            avant = hashlib.sha256(sources_remplacees[membre]).hexdigest()
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
                sortie.comment = entree.commentaire
                for nom in noms:
                    information = entree.information(nom)
                    if nom in remplacements:
                        # Ces deux membres sont déjà bornés en mémoire ; garder
                        # le chemin d’écriture testé en cas de panne de ZIP.
                        sortie.writestr(information, remplacements[nom])
                    else:
                        with entree.ouvrir_flux(nom) as flux, sortie.open(information, "w") as cible:
                            shutil.copyfileobj(flux, cible)
            integrite = integrite_3mf.verifier(source, temporaire, permis)
            entree.verifier_source(source)
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


def _identite_source(chemin):
    """Empreinte de métadonnées pour refuser une substitution avant publication."""
    info = chemin.lstat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _verifier_noms_restaures(chemin, empreintes, noms, commentaire):
    """Relit les CRC et l’empreinte de chaque membre avant publication."""
    membre_modele = "Metadata/model_settings.config"
    with Archive3MF(chemin, taille_max_xml=_BUDGET_XML_OCTETS) as archive:
        if archive.noms() != tuple(empreintes) or archive.commentaire != commentaire:
            raise ValueError("Réouverture du 3MF restauré invalide.")
        modele = None
        for membre, attendu in empreintes.items():
            if membre == membre_modele:
                brut = archive.lire(membre, limite_octets=_BUDGET_XML_OCTETS)
                obtenu = hashlib.sha256(brut).hexdigest()
                modele = decoder_xml(brut, limite_octets=_BUDGET_XML_OCTETS)
            else:
                with archive.ouvrir_flux(membre) as flux:
                    obtenu = hashlib.file_digest(flux, "sha256").hexdigest()
            if obtenu != attendu:
                raise ValueError(f"Membre modifié lors de la restauration : {membre}")
        if modele is None:
            raise ValueError("Modèle des plaques absent après réouverture.")
        obtenus = []
        for plaque in modele.findall("plate"):
            champs = [m for m in plaque.findall("metadata") if m.get("key") == "plater_name"]
            if len(champs) != 1:
                raise ValueError("Nom de plaque absent ou dupliqué après réouverture.")
            obtenus.append(champs[0].get("value"))
        if obtenus != noms:
            raise ValueError("Noms de plaques différents après réouverture.")
        empreinte_archive = archive.sha256()
    return obtenus, empreinte_archive


def restaurer_noms(chemin, noms, *, destination=None, empreinte_source=None):
    """Restaure les noms (sans unité) et contrôle la copie avant publication.

    Une destination neuve préserve le 3MF natif. Sans destination, l’appelant
    historique doit tenir son verrou ; le remplacement atomique refuse toute
    mutation détectée. L’empreinte source optionnelle lie la copie à son audit.
    Retourne la réouverture PASS ou lève ValueError/OSError ; aucun G-code ni
    réglage d’impression n’est modifié (docs/reference/integrite-3mf.md).
    """
    from benchab.chemins import sans_lien
    from benchab.qualite.ecritures import destination_neuve, renommer_exclusif
    from benchab.qualite.empreintes import sha256_fichier

    chemin = sans_lien(chemin, strict=True)
    cible = destination_neuve(destination) if destination is not None else chemin
    if not isinstance(noms, (tuple, list)) or not all(isinstance(nom, str) for nom in noms):
        raise ValueError("Liste des noms de plaque requise.")
    noms = list(noms)
    identite = _identite_source(chemin)
    membre_modele = "Metadata/model_settings.config"
    temporaire = None
    try:
        with Archive3MF(chemin, taille_max_xml=_BUDGET_XML_OCTETS) as entree:
            entree.verifier_source(chemin)
            if _identite_source(chemin) != identite:
                raise ValueError("Le 3MF source a changé avant le contrôle.")
            if empreinte_source is not None and entree.sha256() != empreinte_source:
                raise ValueError("Le 3MF source diffère de son empreinte contrôlée.")
            brut = entree.lire(membre_modele, limite_octets=_BUDGET_XML_OCTETS)
            # Le plan prend les valeurs typées du même XML, sans réécrire ses commentaires.
            avant = integrite_3mf.instantane_config({}, brut)
            modele = decoder_xml(
                brut, limite_octets=_BUDGET_XML_OCTETS, commentaires=True, instructions_traitement=True,
            )
            plaques = modele.findall("plate")
            if len(plaques) != len(noms):
                raise ValueError("Nombre de plaques modifié par le slicer : restauration refusée.")
            permis = []
            for i, (plaque, nom_origine) in enumerate(zip(plaques, noms, strict=True)):
                champs = [m for m in plaque.findall("metadata") if m.get("key") == "plater_name"]
                cle = f"plaque/{i}/plater_name"
                if len(champs) != 1 or cle not in avant:
                    raise ValueError("Nom de plaque absent ou dupliqué dans la sortie.")
                if avant[cle] != nom_origine:
                    permis.append(integrite_3mf.autorisation(cle, avant[cle], nom_origine))
                champs[0].set("value", nom_origine)

            fd, nom = tempfile.mkstemp(prefix=".noms-", suffix=".3mf", dir=cible.parent)
            temporaire = Path(nom)
            os.close(fd)
            empreintes = {}
            commentaire = entree.commentaire
            with ZipFile(temporaire, "w") as sortie:
                sortie.comment = commentaire
                for membre in entree.noms():
                    info = entree.information(membre)
                    calcul = hashlib.sha256()
                    if membre == membre_modele:
                        contenu = ET.tostring(modele, encoding="utf-8", xml_declaration=True)
                        calcul.update(contenu)
                        sortie.writestr(info, contenu)
                    else:
                        with entree.ouvrir_flux(membre) as lecture, sortie.open(info, "w") as ecriture:
                            while bloc := lecture.read(1024 * 1024):
                                calcul.update(bloc)
                                ecriture.write(bloc)
                    empreintes[membre] = calcul.hexdigest()
            identite_copie = _identite_source(temporaire)
            obtenus, empreinte_copie = _verifier_noms_restaures(temporaire, empreintes, noms, commentaire)
            integrite_3mf.verifier(chemin, temporaire, permis)
            entree.verifier_source(chemin)
        if _identite_source(chemin) != identite:
            raise ValueError("Le 3MF source a changé avant la publication.")
        if _identite_source(temporaire) != identite_copie:
            raise ValueError("La copie restaurée a changé avant la publication.")
        if destination is None:
            temporaire.replace(cible)
        else:
            renommer_exclusif(temporaire, cible)
        # Le renommage peut changer ctime ; relire tous les octets publiés.
        if _identite_source(cible)[:4] != identite_copie[:4] or sha256_fichier(cible) != empreinte_copie:
            raise ValueError("La copie publiée ne correspond plus à la réouverture contrôlée.")
        return {"noms": obtenus, "reouverture": "PASS", "membres_verifies": len(empreintes)}
    finally:
        if temporaire is not None:
            temporaire.unlink(missing_ok=True)
