"""Étudie des plafonds de vitesse et d'accélération sur une copie 3MF privée."""

import argparse
import copy
import datetime
import hashlib
import json
import math
import re
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from benchab import integrite_3mf
from benchab import reglages as reglages_types
from benchab.donnees.base import Base
from benchab.fabrication import anycubic
from benchab.machine.configuration import charger as charger_configuration
from benchab.machine.profil_maitre import charger_machine, limites_configurees
from benchab.qualite.ecritures import PublicationNeuve
from benchab.qualite.empreintes import sha256_fichier

MODES = ("qualite", "equilibre", "rapide", "resistance")
INTERIEURS = ("inner_wall_speed", "sparse_infill_speed", "internal_solid_infill_speed")
EXTERIEURS = ("outer_wall_speed", "top_surface_speed", "gap_infill_speed")
ACCELERATIONS = ("default_acceleration", "inner_wall_acceleration", "travel_acceleration")
VARIABLES = frozenset((*INTERIEURS, *EXTERIEURS, *ACCELERATIONS, "travel_speed"))
CONFIGURATION = "Metadata/project_settings.config"
OBJETS = "Metadata/model_settings.config"


def _fichier_prive(base: Base, chemin: str | Path) -> Path:
    brut = Path(chemin).expanduser()
    candidat = brut if brut.is_absolute() else base.prive / brut
    if any(element.is_symlink() for element in (candidat, *candidat.parents)):
        raise ValueError("Lien symbolique dans un chemin d’optimisation refusé.")
    if not candidat.resolve(strict=False).is_relative_to(base.prive.resolve()):
        raise ValueError("Les fichiers d’optimisation doivent rester dans base.prive.")
    try:
        resultat = candidat.resolve(strict=True)
    except OSError as erreur:
        raise ValueError("Fichier absent de l’espace privé.") from erreur
    if not resultat.is_relative_to(base.prive.resolve()) or not resultat.is_file():
        raise ValueError("Fichier ordinaire sous base.prive requis.")
    return resultat


def _projet_prive(base: Base, projet: str | Path) -> Path:
    """Résout un projet relatif à l’atelier ; Path sans unité, FAIL hors confinement ou sur lien."""
    from benchab.chemins import sans_lien

    brut = Path(projet).expanduser()
    candidat = brut if brut.is_absolute() else base.prive / brut
    # Le contrôle des liens précède resolve : sinon leur trace disparaît (#100).
    dossier = sans_lien(candidat, racine=base.prive, strict=True)
    if not dossier.is_dir():
        raise ValueError("Projet d’optimisation privé requis.")
    return dossier


def positif(valeur, nom: str, *, nul: bool = False) -> float:
    if isinstance(valeur, bool):
        raise ValueError(f"{nom} : booléen interdit.")
    try:
        resultat = float(valeur)
    except (TypeError, ValueError, OverflowError) as erreur:
        raise ValueError(f"{nom} : valeur numérique requise.") from erreur
    invalide = resultat < 0 if nul else resultat <= 0
    if not math.isfinite(resultat) or invalide:
        raise ValueError(f"{nom} : nombre fini {'positif ou nul' if nul else 'positif'} requis.")
    return resultat


def _valeur(valeur):
    if isinstance(valeur, list):
        if len(valeur) != 1:
            raise ValueError("Valeur unique requise.")
        return valeur[0]
    return valeur


def _valeur_indexee(reglages: dict, cle: str, index: int):
    """Lit la valeur filament typée à son index déclaré (mm/s, °C ou mm) ; refuse NON VÉRIFIÉ."""
    definition = reglages_types.definition(cle)
    # Les vecteurs de modes machine peuvent compter trois valeurs sans représenter trois filaments
    # (docs/reference/slicer-anycubic.md) ; le type du schéma prime sur toute indexation.
    if definition["type"] != "liste_filament":
        raise ValueError(f"{cle} n’est pas un vecteur filament typé.")
    valeur = reglages.get(cle)
    if valeur is None:
        return None
    contexte = {**reglages, **getattr(reglages, "contexte", {})}
    # Une clé suffixée exige ses propres identités, pas celles d'une autre variante (#103).
    attendu = reglages_types.longueur_attendue(cle, contexte)
    if attendu is None:
        raise ValueError(f"Correspondance filament NON VÉRIFIÉE : {cle}.")
    if not isinstance(valeur, list) or len(valeur) != attendu:
        raise ValueError(f"Cardinalité de réglage filament invalide : {cle}.")
    if index < 0 or index >= attendu:
        raise IndexError(f"Index filament hors correspondance déclarée : {cle}.")
    return valeur[index]


def _digest(valeur: dict) -> str:
    contenu = json.dumps(valeur, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(contenu).hexdigest()


def lire_3mf(source: str | Path):
    """Lit un 3MF et renvoie réglages typés, XML et empreintes (unités natives) ; lecture seule."""
    fichier = Path(source)
    with zipfile.ZipFile(fichier) as archive:
        noms = archive.namelist()
        if archive.testzip() or len(noms) != len(set(noms)):
            raise ValueError("3MF corrompu ou membres dupliqués.")
        if any(re.fullmatch(r"Metadata/plate_\d+\.gcode", nom) for nom in noms):
            raise ValueError("Une source non tranchée est requise.")
        if noms.count(CONFIGURATION) != 1 or noms.count(OBJETS) != 1:
            raise ValueError("Réglages de projet ou objets absents/dupliqués dans le 3MF.")
        reglages = reglages_types.decoder(archive.read(CONFIGURATION).decode("utf-8"))
        # Les identités et résidus restent qualifiés par le dictionnaire commun (#103).
        reglages.contexte.update(reglages_types.contexte_filaments(reglages))
        xml = ET.fromstring(archive.read(OBJETS))
        geometrie = {
            nom: hashlib.sha256(archive.read(nom)).hexdigest()
            for nom in noms
            if nom.startswith("3D/") or nom in ("_rels/.rels", "[Content_Types].xml")
        }
    if not geometrie or not xml.findall("object"):
        raise ValueError("Réglages, objets ou géométrie absents.")
    return reglages, xml, geometrie


def charger_fabrication(projet: str | Path, objectif: str | None = None) -> dict:
    chemin = Path(projet) / "FABRICATION.json"
    donnees = json.loads(chemin.read_text(encoding="utf-8")) if chemin.is_file() else {"schema": 1}
    if not isinstance(donnees, dict) or type(donnees.get("schema")) is not int or donnees["schema"] != 1:
        raise ValueError("FABRICATION.json doit déclarer le schéma 1.")
    mode = objectif or donnees.get("objectif", "equilibre")
    if mode not in MODES:
        raise ValueError("Objectif inconnu : " + ", ".join(MODES))
    resultat = copy.deepcopy(donnees)
    resultat["objectif"] = mode
    for cle in ("machine", "filaments", "contraintes", "optimisation"):
        if not isinstance(resultat.get(cle, {}), dict):
            raise ValueError(f"{cle} doit être un objet JSON.")
    return resultat


def _lire_maitre(base: Base, config: dict, maitre):
    """Choisit le maître selon la priorité API, fabrication puis config générale ; renvoie profil et limites."""
    chemin = maitre if maitre is not None else config.get("machine", {}).get("profil")
    origine = "argument API/CLI" if maitre is not None else "FABRICATION.json: machine.profil"
    if chemin is None:
        origine = "configuration générale: machine.profil_maitre"
        chemin = charger_configuration().get("machine", {}).get("profil_maitre")
        if chemin is not None:
            chemin = _fichier_prive(base, chemin)
    if isinstance(chemin, dict) and chemin.get("_dossier_sources"):
        dossier_sources = Path(chemin["_dossier_sources"]).expanduser().resolve(strict=True)
        if not dossier_sources.is_relative_to(base.prive.resolve()):
            raise ValueError("Les snapshots du profil maître doivent rester dans base.prive.")
    elif chemin is not None and not isinstance(chemin, dict):
        chemin = _fichier_prive(base, chemin)
    profil = charger_machine(chemin)
    profil["_origine_maitre"] = origine
    limites = limites_configurees(
        profil,
        configuration_id=config.get("machine", {}).get("configuration_id"),
        mode_index=config.get("machine", {}).get("mode_index", 0),
    )
    return profil, limites


def _chemin_reference_historique(base: Base, relatif: str | None) -> Path | None:
    if relatif is None:
        return None
    if not isinstance(relatif, str) or not relatif:
        raise ValueError("Référence historique invalide.")
    chemin = Path(relatif)
    if chemin.is_absolute() or ".." in chemin.parts:
        raise ValueError("Les références historiques doivent être relatives à base.prive.")
    candidat = base.prive / chemin
    if any(element.is_symlink() for element in (candidat, *candidat.parents)):
        raise ValueError("Lien symbolique dans une référence historique refusé.")
    try:
        resolu = candidat.resolve(strict=True)
    except FileNotFoundError:
        return None
    if not resolu.is_relative_to(base.prive.resolve()) or not resolu.is_file():
        raise ValueError("Référence historique hors base.prive ou non ordinaire.")
    return resolu


def _observations_historiques(base: Base, maitre: dict) -> list[dict]:
    observations = maitre.get("observations_contextuelles", [])
    if not isinstance(observations, list):
        raise ValueError("observations_contextuelles doit être une liste.")
    resultats = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("Observation contextuelle invalide.")
        chemin = _chemin_reference_historique(base, observation.get("reference"))
        if chemin is None:
            continue
        avant = chemin.stat()
        contenu = chemin.read_bytes()
        apres = chemin.stat()
        if (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns) != (
            apres.st_dev,
            apres.st_ino,
            apres.st_size,
            apres.st_mtime_ns,
            apres.st_ctime_ns,
        ):
            raise ValueError("Référence historique modifiée pendant sa lecture.")
        historique = json.loads(contenu)
        if not isinstance(historique, dict) or not isinstance(historique.get("references", []), list):
            raise ValueError("Référence historique : liste references requise.")
        resultats.append(
            {
                "observation": observation,
                "fichier": chemin.relative_to(base.prive.resolve()).as_posix(),
                "sha256": hashlib.sha256(contenu).hexdigest(),
                "references": historique.get("references", []),
            }
        )
    return resultats


def indices_historiques(base: Base, reglages: dict, filaments_profil: dict, maitre: dict) -> list[dict]:
    """Présente des références structurées privées sans transfert automatique ni lecture de journaux."""
    sorties = []
    for historique in _observations_historiques(base, maitre):
        observation = historique["observation"]
        for reference in historique["references"]:
            if not isinstance(reference, dict):
                raise ValueError("Entrée de référence historique invalide.")
            anciens = reference.get("settings", {})
            if not isinstance(anciens, dict):
                raise ValueError("Réglages historiques invalides.")
            ecarts = []
            filament_id = observation.get("filament_id")
            if not filaments_profil or any(item.get("id") != filament_id for item in filaments_profil.values()):
                ecarts.append("matière différente ou mélange non établi")
            for cle in ("layer_height", "nozzle_temperature", "nozzle_temperature_initial_layer"):
                if anciens.get(cle) != reglages.get(cle):
                    ecarts.append(cle + " différent ou absent")
            ecarts.extend(
                (
                    "buse historique non établie",
                    "similarité de géométrie non mesurée",
                    "vitesses atteintes et accélérations historiques non mesurées",
                )
            )
            fichier_source = _chemin_reference_historique(base, reference.get("file"))
            sorties.append(
                {
                    "reference": historique["fichier"],
                    "sha256_reference": historique["sha256"],
                    "nom": reference.get("name"),
                    "retour": reference.get("reported_result"),
                    "source_disponible": fichier_source is not None,
                    "conditions_non_etablies": ecarts,
                    "transfert_automatique": False,
                    "limite": reference.get("evidence_limit"),
                }
            )
    return sorties


def _mesurer_geometrie(source: Path) -> dict:
    """Lit les maillages du 3MF via XML standard, sans moteur d'import facultatif."""
    import trimesh

    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    mesure_archive = []
    with zipfile.ZipFile(source) as archive:
        modele = ET.fromstring(archive.read("3D/Objects/pieces.model"))
        if modele.get("unit", "millimeter") != "millimeter":
            raise ValueError("Unité de maillage 3MF non millimétrique.")
        for objet in modele.findall(f".//{ns}object"):
            maillage = objet.find(f"{ns}mesh")
            if maillage is None:
                continue
            sommets = maillage.find(f"{ns}vertices")
            triangles = maillage.find(f"{ns}triangles")
            if sommets is None or triangles is None:
                continue
            vertices = [
                [float(sommet.get(axe)) for axe in ("x", "y", "z")] for sommet in sommets.findall(f"{ns}vertex")
            ]
            faces = [[int(face.get(f"v{index}")) for index in (1, 2, 3)] for face in triangles.findall(f"{ns}triangle")]
            mesure_archive.append(trimesh.Trimesh(vertices=vertices, faces=faces, process=False))
    mesures = []
    for maillage in mesure_archive:
        if not len(maillage.faces):
            continue
        local = maillage.copy()
        local.apply_translation(-local.bounds[0])
        mesures.append(
            {
                "dimensions_mm": [float(valeur) for valeur in local.extents],
                "volume_mm3": float(local.volume),
                "faces": len(local.faces),
                "ferme": bool(local.is_watertight and local.is_volume),
            }
        )
    if not mesures:
        raise ValueError("Aucun maillage triangulé lisible dans le 3MF.")
    return {
        "mesures": mesures,
        "source": "géométrie triangulée issue du 3MF",
        "limites": [
            "mesures par géométrie locale, pas placement des instances",
            "épaisseur locale et détails fins non mesurés",
            "appui géométrique ne prouve ni adhésion ni supports tranchés",
        ],
    }


def filaments(st: dict, config: dict, base: Base) -> tuple[dict, list[str]]:
    ids = st.get("filament_settings_id")
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("Identifiants des filaments embarqués absents.")
    registres = {
        ligne["id"]: json.loads(ligne["donnees_json"])
        for ligne in base.connexion.execute("SELECT id, donnees_json FROM filaments")
    }
    suffixe = (
        "_BRASS" if st.get("nozzle_type") == "brass" else "_HS" if st.get("nozzle_type") == "hardened_steel" else ""
    )
    resultat, alertes = {}, []
    for index, nom_profil in enumerate(ids):
        numero = str(index + 1)
        declaration = config.get("filaments", {}).get(numero, {})
        if not isinstance(declaration, dict):
            raise ValueError("Déclaration de filament invalide : " + numero)
        ident = declaration.get("id")
        fiche = registres.get(ident) if ident else None
        if ident and fiche is None:
            raise ValueError("Filament absent de la base privée : " + str(ident))
        plafond = _valeur_indexee(st, "filament_max_volumetric_speed" + suffixe, index)
        if plafond is None:
            plafond = _valeur_indexee(st, "filament_max_volumetric_speed", index)
        if plafond is None:
            alertes.append(f"Plafond volumique absent pour le filament {numero}.")
        else:
            plafond = positif(plafond, "plafond volumique " + numero)
        temperature = _valeur_indexee(st, "nozzle_temperature" + suffixe, index)
        if temperature is None:
            temperature = _valeur_indexee(st, "nozzle_temperature", index)
        temperature_initiale = _valeur_indexee(st, "nozzle_temperature_initial_layer" + suffixe, index)
        if temperature_initiale is None:
            temperature_initiale = _valeur_indexee(st, "nozzle_temperature_initial_layer", index)
        diametre = _valeur_indexee(st, "filament_diameter", index)
        resultat[numero] = {
            "id": ident,
            "nom_profil": nom_profil,
            "plafond_mm3_s": plafond,
            "diametre_mm": positif(diametre, "diamètre filament") if diametre is not None else None,
            "temperature_c": positif(temperature, "température") if temperature is not None else None,
            "temp_1re_c": positif(temperature_initiale, "température initiale")
            if temperature_initiale is not None
            else None,
            "source": "réglages embarqués du 3MF",
            "ace_declare": declaration.get("ace_declare"),
            "ace_physique_verifie": False,
        }
        if fiche is None:
            alertes.append("Bobine physique non identifiée pour le profil " + nom_profil + ".")
        else:
            stock = str(fiche.get("stock", {}).get("etat", ""))
            if any(mot in stock.lower() for mot in ("épuis", "non reç", "command")):
                alertes.append("Disponibilité du filament " + str(ident) + " à vérifier : " + stock)
            nom = str(fiche.get("nom", "")) + " " + str(fiche.get("fabricant", ""))
            if any(mot in nom.lower() for mot in ("glow", "luminous", "carbone", "abrasif")) and suffixe == "_BRASS":
                alertes.append("Matière potentiellement abrasive avec buse laiton : " + nom.strip())
    return resultat, alertes


def proposer(base: Base, projet: str | Path, source: str | Path, objectif: str | None = None, maitre=None) -> dict:
    """Calcule un candidat privé non imprimable ; rapport en mm, mm/s et mm/s², aucune source modifiée.

    Le projet relatif part de base.prive ; données absentes ou confinement invalide refusés explicitement.
    """
    dossier = _projet_prive(base, projet)
    if (dossier / "FABRICATION.json").exists():
        _fichier_prive(base, dossier / "FABRICATION.json")
    source = _fichier_prive(base, source)
    configuration = charger_fabrication(dossier, objectif)
    profil, limites = _lire_maitre(base, configuration, maitre)
    st, xml, empreintes_geometrie = lire_3mf(source)
    if st.get("printer_model") not in ("Anycubic Kobra S1", "Anycubic Kobra S1 Combo"):
        raise ValueError("Le 3MF ne déclare pas une Kobra S1.")
    filaments_profil, alertes = filaments(st, configuration, base)
    volume = limites.get("volume_mm")
    if not isinstance(volume, list) or len(volume) != 3:
        raise ValueError("Volume du profil maître absent.")
    contour = st.get("printable_area")
    hauteur = positif(st.get("printable_height"), "hauteur plateau")
    try:
        points = [tuple(float(x) for x in point.split("x")) for point in contour]
    except (TypeError, ValueError) as erreur:
        raise ValueError("Contour de plateau invalide.") from erreur
    if len(points) < 3 or any(len(point) != 2 or any(not math.isfinite(x) or x < 0 for x in point) for point in points):
        raise ValueError("Contour de plateau invalide.")
    if hauteur > float(volume[2]) or any(x > float(volume[index]) for point in points for index, x in enumerate(point)):
        raise ValueError("Volume du 3MF incompatible avec le profil maître.")
    diametre_buse = positif(_valeur(st.get("nozzle_diameter")), "diamètre de buse")
    if abs(diametre_buse - float(limites["buse_mm"])) > 1e-9 or st.get("nozzle_type") != limites["buse_matiere"]:
        raise ValueError("Configuration de buse du 3MF différente du profil maître.")
    geometrie = _mesurer_geometrie(source)
    couche = positif(_valeur(st.get("layer_height")), "hauteur de couche")
    plafond_xy = positif(limites.get("vitesse_xy_mm_s"), "limite XY configurée")
    plafond_acc = positif(limites.get("acceleration_xy_mm_s2"), "accélération XY configurée")
    recommandee = positif(limites.get("acceleration_constructeur_recommandee_mm_s2"), "accélération recommandée")
    declarations = configuration.get("contraintes", {}).get("objets", {})
    noms = {
        next(
            (element.get("value") for element in objet.findall("metadata") if element.get("key") == "name"),
            objet.get("id"),
        )
        for objet in xml.findall("object")
    }
    if set(declarations) - noms:
        raise ValueError("Contrainte déclarée pour un objet absent.")
    mode = configuration["objectif"]
    changements_globaux, objets = {}, []
    for objet in xml.findall("object"):
        metadonnees = {item.get("key"): item.get("value") for item in objet.findall("metadata")}
        nom = metadonnees.get("name", objet.get("id"))
        declaration = declarations.get(nom, {})
        if not isinstance(declaration, dict):
            raise ValueError("Contrainte objet invalide : " + str(nom))
        verrouilles = declaration.get("reglages_verrouilles", [])
        if not isinstance(verrouilles, list) or any(not isinstance(x, str) for x in verrouilles):
            raise ValueError("reglages_verrouilles doit être une liste de noms.")
        try:
            outil = str(int(metadonnees.get("extruder", "0")))
        except ValueError as erreur:
            raise ValueError("Extrudeur d’objet invalide.") from erreur
        filaments_objet = [filaments_profil[outil]] if outil in filaments_profil else list(filaments_profil.values())
        if outil not in filaments_profil:
            alertes.append("Outil automatique ou multiple pour " + str(nom) + "; plafond minimal utilisé.")
        plafond_debit = (
            min(x["plafond_mm3_s"] for x in filaments_objet)
            if all(x["plafond_mm3_s"] for x in filaments_objet)
            else None
        )
        protection = bool(
            declaration.get("details_fins") or declaration.get("decorative") or declaration.get("fonctionnelle")
        )
        reglages_objet, justifications = {}, []
        for cle in (*INTERIEURS, *EXTERIEURS):
            valeur_base = metadonnees.get(cle, st.get(cle))
            if valeur_base is None or cle in verrouilles or plafond_debit is None:
                continue
            valeur_base = positif(_valeur(valeur_base), cle)
            largeur_cle = cle.replace("_speed", "_line_width")
            largeur = _valeur(metadonnees.get(largeur_cle, st.get(largeur_cle, st.get("line_width", diametre_buse))))
            if str(largeur) in ("0", "0.0"):
                largeur = _valeur(st.get("line_width", diametre_buse))
            if str(largeur) in ("0", "0.0"):
                largeur = diametre_buse
                alertes.append("Largeur automatique approximée par le diamètre de buse.")
            largeur = positif(largeur, largeur_cle)
            if largeur < couche:
                raise ValueError("Largeur inférieure à la hauteur de couche.")
            section = couche * (largeur - couche) + math.pi * couche**2 / 4
            plafond = min(plafond_xy, plafond_debit / section)
            if declaration.get("vitesse_max_mm_s") is not None:
                plafond = min(plafond, positif(declaration["vitesse_max_mm_s"], "vitesse objet"))
            simple = all(
                x["faces"] <= 24 and min(x["dimensions_mm"]) >= 5 * diametre_buse for x in geometrie["mesures"]
            )
            eligible = (
                cle in INTERIEURS
                or (mode == "rapide" and not protection)
                or (mode == "equilibre" and simple and not protection)
            )
            cible = plafond if eligible else min(valeur_base, plafond)
            if abs(cible - valeur_base) > 0.01:
                reglages_objet[cle] = f"{cible:.3f}"
                justifications.append(
                    {
                        "cle": cle,
                        "avant": valeur_base,
                        "apres": cible,
                        "plafond_debit_mm3_s": plafond_debit,
                        "section_mm2": section,
                        "raison": (
                            "Limites de débit et de configuration ; zones extérieures conservées selon l’objectif."
                        ),
                        "approximation": "Section rectangulaire à extrémités arrondies ; vitesse nominale.",
                    }
                )
        for cle in ("default_acceleration", "inner_wall_acceleration"):
            if cle in verrouilles:
                continue
            valeur_actuelle = positif(_valeur(metadonnees.get(cle, st.get(cle, 0))), cle, nul=True)
            plafond = min(plafond_acc, positif(limites.get("acceleration_extruding_mm_s2"), "accélération extrusion"))
            if declaration.get("acceleration_max_mm_s2") is not None:
                plafond = min(plafond, positif(declaration["acceleration_max_mm_s2"], "accélération objet"))
            cible = min(plafond, max(valeur_actuelle, recommandee))
            if valeur_actuelle != cible:
                reglages_objet[cle] = str(cible)
        objets.append(
            {
                "id": objet.get("id"),
                "nom": nom,
                "outils": [outil],
                "reglages": reglages_objet,
                "justifications": justifications,
                "protections": declaration,
            }
        )
    verrou_travel = any("travel_speed" in x.get("reglages_verrouilles", []) for x in declarations.values())
    vitesses_limite = [
        plafond_xy,
        *(
            positif(x["vitesse_max_mm_s"], "vitesse objet")
            for x in declarations.values()
            if x.get("vitesse_max_mm_s") is not None
        ),
    ]
    if "travel_speed" in st and not verrou_travel:
        actuelle = positif(_valeur(st["travel_speed"]), "vitesse de déplacement")
        cible = min(vitesses_limite)
        if abs(actuelle - cible) > 0.01:
            changements_globaux["travel_speed"] = str(cible)
    if mode == "resistance":
        alertes.append("Les efforts déclarés doivent être audités ; aucune résistance n’est déduite de la vitesse.")
    return {
        "schema": 1,
        "objectif": mode,
        "machine": {
            "id": profil.get("profil_id", profil.get("id", profil.get("identifiant"))),
            "origine": profil.get("_origine_maitre", "profil fourni par l’API"),
            "configuration_id": configuration.get("machine", {}).get("configuration_id")
            or profil.get("configuration_par_defaut"),
            "limites": limites,
            "profil_sha256": _digest({k: v for k, v in profil.items() if not k.startswith("_")}),
        },
        "filaments": filaments_profil,
        "reglages": {"global": changements_globaux, "objets": objets},
        "geometrie": geometrie,
        "alertes": sorted(set(alertes)),
        "invariants": ["géométrie et placement", "matière et température", "couche et largeur", "refroidissement"],
        "indices_historiques": indices_historiques(base, st, filaments_profil, profil),
        "estimation_reussite": {"type": "heuristique non calibrée", "probabilite": None},
        "source": {"sha256": sha256_fichier(source), "geometrie_sha256": empreintes_geometrie},
    }


def clone(source: str | Path, cible: str | Path, proposition: dict | None = None) -> tuple[dict, dict]:
    """Publie un clone contrôlé : SHA et statut PASS ; vitesses mm/s, accélérations mm/s², géométrie inchangée."""
    source, cible = Path(source), Path(cible)
    reglages, xml, geometrie = lire_3mf(source)
    if proposition is not None:
        changements_globaux = proposition.get("reglages", {}).get("global", {})
        changements_objets = proposition.get("reglages", {}).get("objets", [])
        if set(changements_globaux) - VARIABLES or any(
            set(item.get("reglages", {})) - VARIABLES for item in changements_objets
        ):
            raise ValueError("Réglage hors allowlist de vitesse et accélération.")
        avant_etat = integrite_3mf.instantane(source)
        reglages.update(changements_globaux)
        index = {item["id"]: item["reglages"] for item in changements_objets}
        for objet in xml.findall("object"):
            for cle, valeur in index.get(objet.get("id"), {}).items():
                meta = next((item for item in objet.findall("metadata") if item.get("key") == cle), None)
                if meta is None:
                    meta = ET.SubElement(objet, "metadata", key=cle)
                meta.set("value", str(valeur))
        # Le plan exact interdit les changements collatéraux dans un 3MF sérialisé (docs/reference/regles.md).
        permis = []
        for cle, valeur in changements_globaux.items():
            chemin = "projet/" + cle
            avant = avant_etat.get(chemin)
            apres = reglages[cle]
            if avant != apres:
                permis.append(integrite_3mf.autorisation(chemin, avant, apres, present_avant=chemin in avant_etat))
        for objet in xml.findall("object"):
            identite = objet.find("metadata[@key='name']")
            identifiant = objet.get("id")
            nom = identite.get("value") if identite is not None else identifiant
            for cle, valeur in index.get(identifiant, {}).items():
                chemin = f"objet/{nom}/{cle}"
                avant = avant_etat.get(chemin)
                apres = str(valeur)
                if avant != apres:
                    permis.append(integrite_3mf.autorisation(chemin, avant, apres, present_avant=chemin in avant_etat))
    else:
        permis = []
    with PublicationNeuve([source], [cible]) as publication:
        temporaire = publication.temporaire(cible)
        with zipfile.ZipFile(source) as entree, zipfile.ZipFile(temporaire, "w") as sortie:
            sortie.comment = entree.comment
            for info in entree.infolist():
                if info.filename == CONFIGURATION:
                    contenu = reglages_types.encoder(reglages, ensure_ascii=False, allow_nan=False).encode("utf-8")
                elif info.filename == OBJETS:
                    contenu = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
                else:
                    contenu = entree.read(info)
                sortie.writestr(copy.copy(info), contenu)
        _, _, apres = lire_3mf(temporaire)
        if geometrie != apres:
            raise ValueError("Géométrie modifiée : comparaison refusée.")
        integrite = integrite_3mf.verifier(source, temporaire, permis)
        with zipfile.ZipFile(source) as entree, zipfile.ZipFile(temporaire) as sortie:
            for nom in entree.namelist():
                if nom not in (CONFIGURATION, OBJETS) and entree.read(nom) != sortie.read(nom):
                    raise ValueError("Membre inattendu modifié : " + nom)
        publication.publier()
    return {"fichier": str(cible), "sha256": sha256_fichier(cible)}, {
        "conforme": True,
        "statut": integrite["statut"],
        "geometrie_sha256": geometrie,
    }


def decision_candidat(capacites: dict, controles: dict, alertes: list[str]) -> tuple[str, str]:
    obligatoires = {"routine", "premiere_couche", "zones_locales", "resistance", "geometrie"}
    if not obligatoires.issubset(controles) or not controles["premiere_couche"] or not controles["zones_locales"]:
        return "À revoir", "NON VÉRIFIÉ"
    requis = {cle: valeur for cle, valeur in controles.items() if cle != "resistance"}
    if controles["resistance"].get("obligatoire"):
        requis["resistance"] = controles["resistance"]["rapport"]
    from benchab.qualite.validation import agregation_statut

    statut = agregation_statut(requis)
    if capacites.get("statut") == "FAIL" or statut == "FAIL":
        return "NO GO", "FAIL"
    if capacites.get("decision") != "GO" or statut != "PASS" or alertes:
        return "À revoir", "WARN"
    return "GO", "PASS"


def _publier_json(cible: Path, donnees: dict) -> None:
    with PublicationNeuve([], [cible]) as publication:
        temporaire = publication.temporaire(cible)
        temporaire.write_text(json.dumps(donnees, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        json.loads(temporaire.read_text(encoding="utf-8"))
        publication.publier()


def _sortie_privee(base: Base, chemin: str | Path) -> Path:
    candidat = Path(chemin).expanduser()
    if not candidat.is_absolute():
        candidat = base.prive / candidat
    if any(element.is_symlink() for element in (candidat, *candidat.parents)):
        raise ValueError("Lien symbolique dans la sortie d’optimisation refusé.")
    if not candidat.resolve(strict=False).is_relative_to(base.prive.resolve()):
        raise ValueError("Sortie d’optimisation hors base.prive.")
    if any(re.fullmatch(r"P\d+", partie) for partie in candidat.parts):
        raise ValueError("Étude interdite dans une préparation figée Pnn.")
    return candidat


def _contexte(base: Base, projet: Path, source: Path, config: dict, profil: dict, trancher: bool, budget: int) -> dict:
    empreintes = {}
    for nom in ("FABRICATION.json", "src/parametres.py", "EFFORTS.json", "BESOIN.md", "candidats/assemblage.json"):
        fichier = projet / nom
        if fichier.is_file():
            empreintes[nom] = sha256_fichier(fichier)
    modules = (
        Path(__file__),
        Path(anycubic.__file__),
        Path(__import__("benchab.machine.profil_maitre", fromlist=["charger_machine"]).__file__),
        Path(__import__("benchab.controles.capacites_machine", fromlist=["analyser"]).__file__),
        Path(__import__("benchab.controles.routine", fromlist=["controler"]).__file__),
        Path(__import__("benchab.controles.premiere_couche", fromlist=["controler"]).__file__),
        Path(__import__("benchab.controles.zones_locales", fromlist=["analyser_gcode"]).__file__),
        Path(__import__("benchab.fabrication.resistance", fromlist=["auditer_projet"]).__file__),
    )
    code = {fichier.name: sha256_fichier(fichier) for fichier in modules}
    filaments_prives = [
        dict(ligne) for ligne in base.connexion.execute("SELECT id, donnees_json FROM filaments ORDER BY id")
    ]
    references_historiques = [
        {
            "fichier": reference["fichier"],
            "sha256": reference["sha256"],
            "references": [
                {
                    "name": item.get("name"),
                    "file_disponible": _chemin_reference_historique(base, item.get("file")) is not None,
                }
                for item in reference["references"]
                if isinstance(item, dict)
            ],
        }
        for reference in _observations_historiques(base, profil)
    ]
    return {
        "declarations_projet": empreintes,
        "source_sha256": sha256_fichier(source),
        "configuration": config,
        "maitre_sha256": _digest({cle: valeur for cle, valeur in profil.items() if not cle.startswith("_")}),
        "references_historiques": references_historiques,
        "code_sha256": sha256_fichier(Path(__file__)),
        "modules_sha256": code,
        "filaments_prives": filaments_prives,
        "python": [__import__("sys").version_info.major, __import__("sys").version_info.minor],
        "trancher": trancher,
        "budget_candidats": budget,
    }


def _cache(projet: Path, empreinte: str) -> dict | None:
    racine = projet / "candidats/optimisation"
    if not racine.is_dir() or racine.is_symlink():
        return None
    for fichier in sorted(racine.glob("*/rapport.json"), reverse=True):
        try:
            if fichier.is_symlink() or (fichier.parent / "SCELLE.json").is_symlink():
                continue
            sceau = json.loads((fichier.parent / "SCELLE.json").read_text(encoding="utf-8"))
            contenu = fichier.read_bytes()
            if sceau.get("rapport_sha256") != hashlib.sha256(contenu).hexdigest():
                continue
            donnees = json.loads(contenu)
            if donnees.get("empreinte_cache") != empreinte:
                continue
            preuves = [
                donnees["source"],
                {"fichier": donnees["machine"]["profil"], "sha256": donnees["machine"]["profil_sha256"]},
            ]
            preuves.extend(donnees["machine"].get("preuves_sources", []))
            for candidat in donnees["candidats"]:
                if candidat.get("tranche_3mf") and not candidat.get("controle_fichier"):
                    raise ValueError("preuve de contrôle absente")
                preuves.extend(
                    preuve
                    for preuve in (
                        candidat.get("plaques_3mf"),
                        candidat.get("tranche_3mf"),
                        candidat.get("controle_fichier"),
                    )
                    if preuve
                )
            for preuve in preuves:
                fichier_preuve = Path(preuve["fichier"])
                if (
                    fichier_preuve.is_symlink()
                    or not fichier_preuve.resolve().is_relative_to(fichier.parent.resolve())
                    or sha256_fichier(fichier_preuve) != preuve["sha256"]
                ):
                    raise ValueError("preuve modifiée")
            donnees["cache_reutilise"] = True
            return donnees
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


def _snapshot_profil(dossier: Path, profil: dict) -> list[dict]:
    propre = {cle: valeur for cle, valeur in profil.items() if not cle.startswith("_")}
    racine_sources = Path(profil["_dossier_sources"]) if profil.get("_dossier_sources") else None
    preuves = []
    for fait in propre.get("sources", {}).values():
        relatif = fait.get("snapshot")
        if not relatif:
            continue
        chemin = Path(relatif)
        if chemin.is_absolute() or ".." in chemin.parts or racine_sources is None:
            raise ValueError("Snapshot du profil maître hors périmètre.")
        source = (racine_sources / chemin).resolve(strict=True)
        if not source.is_relative_to(racine_sources.resolve()) or sha256_fichier(source) != fait.get("sha256_snapshot"):
            raise ValueError("Snapshot du profil maître absent ou altéré.")
        cible = dossier / chemin
        cible.parent.mkdir(parents=True, exist_ok=True)
        with PublicationNeuve([source], [cible]) as publication:
            temporaire = publication.temporaire(cible)
            temporaire.write_bytes(source.read_bytes())
            if sha256_fichier(temporaire) != fait["sha256_snapshot"]:
                raise ValueError("Snapshot du profil maître altéré pendant sa copie.")
            publication.publier()
        preuves.append({"fichier": str(cible), "sha256": sha256_fichier(cible), "relatif": chemin.as_posix()})
    cible_profil = dossier / "profil-maitre.json"
    _publier_json(cible_profil, propre)
    return preuves


def comparer(
    base: Base,
    projet: str | Path,
    source: str | Path,
    sortie: str | Path | None = None,
    objectif: str | None = None,
    maitre=None,
    *,
    trancher: bool = True,
    max_candidats: int = 3,
) -> dict:
    """Compare référence et candidat privés ; rapports PASS/WARN/FAIL/NON VÉRIFIÉ, mesures en mm et s.

    Le projet relatif part de base.prive ; copies traçables, aucune source ni profil installé modifié.
    """
    if type(max_candidats) is not int or not 2 <= max_candidats <= 5:
        raise ValueError("Le budget doit être un entier entre 2 et 5.")
    projet = _projet_prive(base, projet)
    source = _fichier_prive(base, source)
    config = charger_fabrication(projet, objectif)
    profil, _limites = _lire_maitre(base, config, maitre)
    empreinte = _digest(_contexte(base, projet, source, config, profil, trancher, max_candidats))
    if sortie is None:
        cache = _cache(projet, empreinte)
        if cache is not None:
            return cache
        horodatage = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        sortie = projet / "candidats/optimisation" / (horodatage + "-" + uuid.uuid4().hex[:8])
    dossier = _sortie_privee(base, sortie)
    if dossier.exists():
        raise ValueError("Dossier de sortie déjà existant.")
    dossier.mkdir(parents=True, exist_ok=False)
    _config_3mf, _xml, _geometrie = lire_3mf(source)
    proposition = proposer(base, projet, source, objectif, profil)
    if _digest(_contexte(base, projet, source, config, profil, trancher, max_candidats)) != empreinte:
        raise ValueError("Contexte ou référence historique modifié pendant la proposition.")
    reference_sha = sha256_fichier(source)
    source_copie = dossier / "source.3mf"
    with PublicationNeuve([source], [source_copie]) as publication:
        temporaire = publication.temporaire(source_copie)
        temporaire.write_bytes(source.read_bytes())
        if sha256_fichier(temporaire) != reference_sha:
            raise ValueError("Source modifiée pendant la copie.")
        publication.publier()
    preuves = _snapshot_profil(dossier, profil)
    cible_profil = dossier / "profil-maitre.json"
    rapport = {
        "schema": 1,
        "objectif": proposition["objectif"],
        "source": {"fichier": str(source_copie), "sha256": sha256_fichier(source_copie), "origine": str(source)},
        "machine": {
            "id": proposition["machine"]["id"],
            "origine": proposition["machine"]["origine"],
            "limites": proposition["machine"]["limites"],
            "profil": str(cible_profil),
            "profil_sha256": sha256_fichier(cible_profil),
            "preuves_sources": preuves,
        },
        "empreinte_cache": empreinte,
        "cache_reutilise": False,
        "candidats": [],
        "recommande": None,
        "gain_pct": None,
        "alertes": proposition["alertes"],
        "estimation_reussite": proposition["estimation_reussite"],
    }
    for ident, variante in (("reference", None), ("proposition", proposition)):
        dossier_candidat = dossier / ident
        dossier_candidat.mkdir()
        plaque = dossier_candidat / "plaques.3mf"
        description, conforme = clone(source_copie, plaque, variante)
        entree = {
            "id": ident,
            "plaques_3mf": description,
            "comparaison_equitable": conforme,
            "tranche_3mf": None,
            "controle_fichier": None,
            "decision": "À revoir",
            "statut": "NON VÉRIFIÉ",
            "mesures": None,
        }
        if trancher:
            tranche, _duree = anycubic.trancher(
                plaque,
                dossier_candidat,
                "tranche.3mf",
                usage="impression",
                prive=base.prive,
            )
            resultats = anycubic.resultats(tranche)
            if not resultats:
                raise ValueError("Le slicer n’a retourné aucune estimation exploitable.")
            secondes = [positif(ligne.get("secondes"), "temps de tranchage") for ligne in resultats]
            grammes = [positif(ligne.get("grammes"), "masse de tranchage", nul=True) for ligne in resultats]
            from benchab.controles.capacites_machine import analyser

            capacites = analyser(
                tranche,
                maitre=proposition["machine"]["limites"],
                limites_filaments=proposition["filaments"],
            )
            controles = controles_candidat(projet, plaque, tranche, dossier_candidat, proposition)
            decision, statut = decision_candidat(capacites, controles, proposition["alertes"])
            rapport_controle = {"capacites": capacites, "controles": controles}
            fichier_controle = dossier_candidat / "controle.json"
            _publier_json(fichier_controle, rapport_controle)
            entree.update(
                tranche_3mf={"fichier": str(tranche), "sha256": sha256_fichier(tranche)},
                controle_fichier={"fichier": str(fichier_controle), "sha256": sha256_fichier(fichier_controle)},
                mesures={
                    "secondes": sum(secondes),
                    "grammes": sum(grammes),
                    "capacites": capacites,
                },
                decision=decision,
                statut=statut,
            )
        rapport["candidats"].append(entree)
    reference, variante = rapport["candidats"]
    if reference["mesures"] is not None and variante["mesures"] is not None:
        seuil = config.get("optimisation", {}).get("gain_min_pct", 2)
        seuil = positif(seuil, "gain minimal", nul=True)
        rapport["gain_pct"] = round(
            100
            * (reference["mesures"]["secondes"] - variante["mesures"]["secondes"])
            / reference["mesures"]["secondes"],
            3,
        )
        admissibles = [item for item in rapport["candidats"] if item["decision"] == "GO"]
        if admissibles:
            if variante in admissibles and rapport["gain_pct"] >= seuil:
                rapport["recommande"] = "proposition"
            elif reference in admissibles:
                rapport["recommande"] = "reference"
            else:
                rapport["recommande"] = variante["id"]
        else:
            revisables = [item for item in rapport["candidats"] if item["decision"] == "À revoir"]
            if revisables:
                rapport["a_revoir"] = min(revisables, key=lambda item: item["mesures"]["secondes"])["id"]
    rapport["decision"] = (
        "GO"
        if rapport["recommande"]
        else "À revoir"
        if any(item["decision"] != "NO GO" for item in rapport["candidats"])
        else "NO GO"
    )
    rapport["statut"] = {"GO": "PASS", "À revoir": "WARN", "NO GO": "FAIL"}[rapport["decision"]]
    rapport["limites"] = [
        "Estimations issues du slicer ; finition et résistance physiques non évaluées.",
        "Durée des changements firmware non déduite.",
        "Toute adoption exige une revue humaine et les preuves exécutées.",
    ]
    if _digest(_contexte(base, projet, source, config, profil, trancher, max_candidats)) != empreinte:
        raise ValueError("Contexte ou référence historique modifié pendant la comparaison.")
    rapport_path = dossier / "rapport.json"
    rapport["rapport"] = str(rapport_path)
    _publier_json(rapport_path, rapport)
    _publier_json(dossier / "SCELLE.json", {"rapport_sha256": sha256_fichier(rapport_path)})
    return rapport


def controles_candidat(projet: Path, plaques: Path, tranche: Path, dossier: Path, proposition: dict) -> dict:
    """Exécute les contrôles BENCHAB sur les copies de la candidate."""
    from benchab.controles import premiere_couche, routine, zones_locales
    from benchab.fabrication.resistance import auditer_projet
    from benchab.gcode.lecture import iter_lignes_gcode_3mf

    _settings, xml, _hashes = lire_3mf(plaques)
    objets = {
        objet.get("id"): {element.get("key"): element.get("value") for element in objet.findall("metadata")}
        for objet in xml.findall("object")
    }
    placement = {"plates": [], "objects": [], "portee_xy": "identité seulement ; revue de placement distincte"}
    for index, plaque in enumerate(xml.findall("plate"), 1):
        placement["plates"].append({"index": index, "name": f"plaque{index}"})
        for instance in plaque.findall("model_instance"):
            metadonnees = {element.get("key"): element.get("value") for element in instance.findall("metadata")}
            donnees_objet = objets.get(metadonnees.get("object_id"), {})
            placement["objects"].append(
                {
                    "name": donnees_objet.get("name", metadonnees.get("object_id")),
                    "plate": index,
                    "xy": [0, 0],
                    "size": [0, 0],
                    "reglages": {
                        cle: valeur for cle, valeur in donnees_objet.items() if cle not in ("name", "extruder")
                    },
                }
            )
    placement_path = dossier / "placement-identites.json"
    _publier_json(placement_path, placement)
    resultat_routine = routine.controler(plaques, tranche, placement_path, dossier / "controles")
    premieres, zones = {}, {}
    for plaque, lignes in premiere_couche.gcodes(tranche):
        texte = "\n".join(iter_lignes_gcode_3mf(tranche, f"Metadata/plate_{plaque}.gcode"))
        premieres[str(plaque)] = premiere_couche.controler(texte)
        bilan_zones = zones_locales.analyser_gcode(texte, plaque)
        zones[str(plaque)] = {
            "statut": "FAIL" if bilan_zones["erreurs"] else "WARN" if bilan_zones["zones"] else "PASS",
            **bilan_zones,
        }
    materiaux = projet.parents[1] / "donnees/materiaux/resistance.json"
    registre = None
    if materiaux.is_file() and not materiaux.is_symlink():
        registre = json.loads(materiaux.read_text(encoding="utf-8"))
    resistance = auditer_projet(projet, tranche, registre)
    obligatoire = proposition["objectif"] == "resistance" or resistance.get("statut") == "FAIL"
    geometries = proposition["geometrie"]["mesures"]
    conforme_geometrie = bool(geometries) and all(item["ferme"] for item in geometries)
    return {
        "routine": resultat_routine,
        "premiere_couche": premieres,
        "zones_locales": zones,
        "resistance": {"obligatoire": obligatoire, "rapport": resistance},
        "geometrie": {
            "statut": "PASS" if conforme_geometrie else "FAIL",
            "limite": "Fermeture du maillage mesurée ; appuis et finition restent à contrôler.",
        },
    }


def main(argv: list[str] | None = None, *, base: Base | None = None) -> int:
    """Compare des variantes privées (s, g, mm) ; statuts conservés, aucune impression ni base créée implicitement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prive", type=Path)
    commandes = parser.add_subparsers(dest="commande", required=True)
    calcul = commandes.add_parser("proposer")
    calcul.add_argument("--projet", type=Path, required=True)
    calcul.add_argument("--source", type=Path, required=True)
    calcul.add_argument("--objectif", choices=MODES)
    calcul.add_argument("--profil", type=Path, help="Profil maître explicite (prioritaire sur les configurations)")
    comparaison = commandes.add_parser("comparer")
    comparaison.add_argument("--projet", type=Path, required=True)
    comparaison.add_argument("--source", type=Path, required=True)
    comparaison.add_argument("--objectif", choices=MODES)
    comparaison.add_argument("--profil", type=Path, help="Profil maître explicite (prioritaire sur les configurations)")
    comparaison.add_argument("--sortie", type=Path)
    comparaison.add_argument("--sans-tranchage", action="store_true")
    comparaison.add_argument("--max-candidats", type=int, default=3)
    args = parser.parse_args(argv)
    if base is None:
        if args.prive is None:
            parser.error("--prive requis hors appel depuis le CLI BENCHAB.")
        base = Base(args.prive, creer=False)
        fermer = True
    else:
        fermer = False
    try:
        if args.commande == "proposer":
            resultat = proposer(base, args.projet, args.source, args.objectif, args.profil)
        else:
            resultat = comparer(
                base,
                args.projet,
                args.source,
                args.sortie,
                args.objectif,
                args.profil,
                trancher=not args.sans_tranchage,
                max_candidats=args.max_candidats,
            )
        print(json.dumps(resultat, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, RuntimeError) as erreur:
        parser.error(str(erreur))
    finally:
        if fermer:
            base.fermer()
    return 2
