"""Construit l’atelier de préparation depuis une référence conservée."""

import argparse
import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from zipfile import BadZipFile

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.gcode.debit import positif, section_nominale
from benchab.imports3d import importer, lecture_3mf
from benchab.qualite.ecritures import destination_neuve, renommer_exclusif
from benchab.qualite.empreintes import sha256_fichier
from benchab.visualisation import carte_risques
from benchab.visualisation.visionneuse import Visionneuse

MAX_SOURCE = 128 * 1024 * 1024
MAX_FACES_VUE = 120_000
MAX_EXPORT_NAVIGATEUR = 8 * 1024 * 1024
DEFAUTS_OBSERVATION = {"decollement", "sous-extrusion", "filage"}
CHAMPS = {
    "printer_model": "Imprimante",
    "printer_settings_id": "Profil machine",
    "print_settings_id": "Profil de procédé",
    "nozzle_diameter": "Buse (mm)",
    "filament_settings_id": "Profils filament",
    "curr_bed_type": "Plateau",
    "layer_height": "Couche (mm)",
    "wall_loops": "Parois",
    "sparse_infill_density": "Remplissage",
    "enable_support": "Supports (0/1)",
    "support_top_z_distance": "Écart Z supérieur des supports (mm)",
    "support_bottom_z_distance": "Écart Z inférieur des supports (mm)",
    "brim_type": "Bordure",
    "brim_width": "Largeur de bordure (mm)",
    "spiral_mode": "Mode vase (0/1)",
    "outer_wall_line_width": "Largeur de ligne extérieure (mm)",
}


def _metadonnees(element, cle):
    """Lit les déclarations XML correspondant à une clé, y compris les doublons."""
    return [m.get("value") for m in element.findall("metadata") if m.get("key") == cle]


def _plaques(xml, identites):
    """Associe les instances par leurs identifiants uniques ; ambiguïté explicitement conservée."""
    instances = Counter({i["build_item"]: i["objet_id_racine"] for i in identites}.values())
    if xml is None:
        return {}
    groupes = defaultdict(list)
    ids = Counter()
    for p in xml.findall("plate"):
        labels = _metadonnees(p, "plater_id")
        if len(labels) != 1 or not labels[0] or not labels[0].isdecimal() or len(labels[0]) > 6:
            return {}
        numero = int(labels[0])
        ids[numero] += 1
        for instance in p.findall("model_instance"):
            objets = _metadonnees(instance, "object_id")
            if len(objets) == 1:
                groupes[objets[0]].append(numero)
    return {
        objet: plaques[0]
        for objet, plaques in groupes.items()
        if len(plaques) == 1 and instances[objet] == 1 and ids[plaques[0]] == 1
    }


def _lire_3mf(source, *, annule=None):
    """Lit la pose native et les réglages typés ; ne déduit pas de profil d’un nom de fichier."""
    scene = lecture_3mf.lire(source, max_faces=1_000_000, max_parts=48, identites=True, annule=annule)
    identites = scene.metadata["identites_import"]
    with Archive3MF(source) as archive:
        noms = archive.noms()
        xml = archive.xml("Metadata/model_settings.config") if "Metadata/model_settings.config" in noms else None
        params = archive.reglages() if "Metadata/project_settings.config" in noms else {}
    plaques = _plaques(xml, identites)
    noms_objets = {}
    # Les surcharges locales priment sur les paramètres globaux à l’export 3MF.
    champs = {
        "layer_height",
        "inner_wall_speed",
        "sparse_infill_speed",
        "inner_wall_line_width",
        "sparse_infill_line_width",
        "extruder",
        "spiral_mode",
        "enable_support",
        "support_type",
        "wall_loops",
        "top_shell_layers",
        "top_shell_thickness",
        "sparse_infill_density",
        "outer_wall_speed",
    }
    overrides = bool(scene.metadata["modificateurs_non_geometriques"]) or any(
        "layer_config" in n or "layer_height_profile" in n for n in noms
    )
    if xml is not None:
        for objet in xml.findall("object"):
            noms_objets[objet.get("id")] = (_metadonnees(objet, "name") or [None])[0]
            for parametre in (*objet.findall("metadata"), *objet.findall("part/metadata")):
                cle = parametre.get("key")
                if cle in (champs - {"extruder"}) or (
                    cle == "extruder" and parametre.get("value") not in (None, "", "0", "1")
                ):
                    overrides = True
    objets = []
    for i, identite in enumerate(identites, 1):
        mesh = scene.geometry[f"Objet{i}"]
        objet = identite["objet_id_racine"]
        nom = noms_objets.get(objet) or f"Objet {objet}"
        objets.append((nom, mesh, plaques.get(objet), identite))
    return objets, params, scene.metadata["modificateurs_non_geometriques"], overrides


def _valeur(params, cle):
    """Expose une valeur typée telle qu’elle est embarquée, avec ses unités natives."""
    try:
        return reglages.valeur_brute(params, cle)
    except ValueError:
        return None


def _largeur(params):
    """Détermine le seuil d’épaisseur de l’atelier en mm et en indique l’origine."""
    brute = _valeur(params, "outer_wall_line_width")
    try:
        largeur = float(brute)
        if 0 < largeur <= 5:
            return 2 * largeur, "Deux largeurs de ligne extérieure déclarées"
    except (ValueError, TypeError, OverflowError):
        pass
    return 0.84, "Seuil de dépistage BENCHAB, largeur de ligne non renseignée en mm"


def _propositions(diagnostic, params):
    """Associe les signaux à des vérifications explicites, sans générer de réglage ni de trajectoire."""
    calques = diagnostic["calques"]
    propositions = []
    if calques["surplomb"]["statut"] == "WARN":
        propositions.append(
            {
                "calque": "surplomb",
                "titre": "Vérifier les appuis après tranchage",
                "raison": f"{diagnostic['faces_detectees']} faces descendantes. Vérifier si elles sont appuyées.",
                "reference": "Supports (0/1)",
                "actuel": _valeur(params, "enable_support"),
            }
        )
    if calques["epaisseur"]["statut"] == "WARN":
        propositions.append(
            {
                "calque": "epaisseur",
                "titre": "Compter les lignes réellement déposées",
                "raison": "Une zone sous le seuil peut disparaître au tranchage. Vérifier les trajectoires.",
                "reference": "Parois",
                "actuel": _valeur(params, "wall_loops"),
            }
        )
    if calques["assise"]["statut"] == "WARN":
        propositions.append(
            {
                "calque": "assise",
                "titre": "Vérifier le contact au plateau",
                "raison": "Contact absent ou centre de masse projeté en bordure ou hors de l’assise. Relire la pose.",
                "reference": "Bordure",
                "actuel": _valeur(params, "brim_type"),
            }
        )
    for proposition in propositions:
        proposition["piece"] = diagnostic["piece"]
    return propositions


def _vitesse(params, overrides):
    """Estime le plafond volumique pour un profil unique et des réglages globaux explicites."""
    if not params:
        return [], "Ouvrez un 3MF contenant vos réglages pour estimer un plafond."
    if _valeur(params, "spiral_mode") in ("1", 1, True):
        return [], "La référence est en mode vase. Ses réglages restent protégés."
    if overrides:
        return [], "Le plafond global est non vérifié en présence de réglages locaux."
    filaments = _valeur(params, "filament_settings_id")
    if not isinstance(filaments, list) or len(filaments) != 1 or not filaments[0]:
        return [], "Sélectionner une préparation qui déclare un seul profil de filament."
    limites = _debit_declare(params)
    hauteur = positif(_valeur(params, "layer_height"))
    plafond = limites[0]["plafond_mm3_s"] if limites else None
    if plafond is None or hauteur is None:
        return [], "Hauteur de couche ou plafond volumique du filament absent ou ambigu."
    lignes = []
    for racine, titre in (("inner_wall", "Paroi intérieure"), ("sparse_infill", "Remplissage")):
        cle = racine + "_speed"
        valeur = _valeur(params, cle)
        vitesse = positif(valeur)
        largeur = positif(_valeur(params, racine + "_line_width"))
        aire = section_nominale(largeur, hauteur)
        if aire is None or vitesse is None:
            lignes.append(
                {
                    "titre": titre,
                    "actuel": valeur,
                    "raison": "Vitesse scalaire ou largeur de ligne non vérifiée.",
                }
            )
            continue
        # Arrondir vers le bas afin de ne pas réintroduire un écart au plafond déclaré.
        borne = plafond / aire
        cible = min(vitesse, int(borne * 100) / 100) if 0.01 <= borne < vitesse else vitesse
        if borne < 0.01 and borne < vitesse:
            lignes.append({"titre": titre, "actuel": valeur, "raison": "Plafond inférieur à 0,01 mm/s."})
            continue
        lignes.append(
            {
                "titre": titre,
                "avant_mm_s": vitesse,
                "apres_mm_s": cible,
                "plafond_mm3_s": plafond,
                "largeur_mm": largeur,
                "hauteur_mm": hauteur,
                "section_mm2": aire,
                "borne_mm_s": borne,
                "plafond_cle": limites[0]["cle"],
                "filament": filaments[0],
                "raison": "Estimation du plafond volumique avant tranchage.",
            }
        )
    return lignes, "Section de cordon estimée. Le débit commandé se vérifie sur le G-code (U25)."


def _debit_declare(params):
    """Expose la limite de chaque profil en mm³/s, sans inventer de plafond absent."""
    filaments = _valeur(params, "filament_settings_id")
    suffixe = {"brass": "_BRASS", "hardened_steel": "_HS"}.get(str(_valeur(params, "nozzle_type")), "")
    cle = "filament_max_volumetric_speed"
    if suffixe and cle + suffixe in params:
        cle += suffixe
    plafonds = _valeur(params, cle)
    if not isinstance(filaments, list) or not isinstance(plafonds, list) or len(filaments) != len(plafonds):
        return []
    return [
        {"filament": filament, "plafond_mm3_s": positif(plafond), "cle": cle}
        for filament, plafond in zip(filaments, plafonds, strict=True) if filament
    ]


def _profil_debit(prive, sha):
    """Lit le profil privé explicitement associé au SHA source et calcule son empreinte."""
    from hashlib import sha256

    from benchab.chemins import sans_lien

    if prive is None:
        return None, None
    chemin = sans_lien(Path(prive) / "config" / "profils-debit" / (sha + ".json"), racine=prive)
    if not chemin.is_file():
        return None, None
    with chemin.open("rb") as flux:
        brut = flux.read(65537)
    if len(brut) > 65536:
        raise ValueError("Profil haut débit privé supérieur à 64 Kio.")
    donnees = json.loads(brut)
    if not isinstance(donnees, dict):
        raise ValueError("Profil haut débit privé invalide.")
    return reglages.Reglages(donnees), sha256(brut).hexdigest()


def construire(source, *, unites=None, sha_attendu=None, nom_source=None, contexte=None, prive=None,
               annule=None, progression=None, plaque_cible=None, piece_cible=None):
    """Construit une visionneuse et sa carte (mm) ; SHA, pose et réglages source préservés.

    STL/OBJ exigent des unités explicites. Les plaques dont l’identité n’est
    pas établie restent sans attribution. Une mutation de la source est refusée.
    """
    source = Path(source)
    if source.stat().st_size > MAX_SOURCE:
        raise ValueError("Source supérieure à 128 Mio.")
    sha = sha256_fichier(source)
    if sha_attendu is not None and sha != sha_attendu:
        raise ValueError("Le 3MF a changé depuis la préparation conservée.")
    nom_source = nom_source or source.name
    if progression:
        progression("Lecture de la géométrie", 0, 0)
    if source.suffix.lower() == ".3mf":
        objets, params, ignores, overrides = _lire_3mf(source, annule=annule)
        ref_plan = "Plan Z = 0 mm du repère de placement 3MF."
    elif source.suffix.lower() in (".stl", ".obj"):
        mesh = importer.charger(source, unites)
        objets, params, ignores, overrides = [(nom_source, mesh, None, None)], {}, [], False
        ref_plan = "Plan d’étude Z = 0 mm dans le fichier, placement d’impression non déclaré."
    else:
        raise ValueError("Formats de l’atelier : STL, OBJ et 3MF.")
    if len(objets) > 48 or sum(len(mesh.faces) for _, mesh, _, _ in objets) > 1_000_000:
        raise ValueError("Budget de la carte dépassé : maximum 48 parties et 1 000 000 de triangles.")
    if (plaque_cible is not None or piece_cible is not None) and not any(
        (plaque_cible is None or plaque == plaque_cible) and (piece_cible is None or i == piece_cible)
        for i, (_, _, plaque, _) in enumerate(objets, 1)
    ):
        raise ValueError("Plaque ou pièce ciblée absente de la référence.")
    if annule is not None and annule():
        raise ValueError("Analyse annulée.")
    largeur, origine = _largeur(params)
    vue = Visionneuse("Atelier de préparation", nom_source, ref_plan, source={"sha256": sha})
    vue.diagnostics = []
    onglets, propositions = {}, []
    for i, (nom, mesh, plaque, identite) in enumerate(objets, 1):
        if annule is not None and annule():
            raise ValueError("Analyse annulée.")
        if progression:
            progression("Mesures par pièce", i - 1, len(objets))
        nom_piece = f"{nom} · {i}"
        vue.modele(f"partie_{i}", mesh, couleur="#b6c7df", max_faces=MAX_FACES_VUE)
        vue.piece(nom_piece, f"partie_{i}", info=f"Plaque {plaque}" if plaque is not None else "Plaque non vérifiée")
        onglets[nom_piece] = plaque
        try:
            if (plaque_cible is not None and plaque != plaque_cible) or (piece_cible is not None and i != piece_cible):
                raise ValueError("Pièce hors du périmètre demandé.")
            diagnostic = carte_risques.mesurer(
                mesh, nom_piece, nom_source, sha, seuil_epaisseur_mm=largeur, annule=annule,
            )
        except carte_risques.AnalyseAnnulee:
            raise
        except (ValueError, RuntimeError) as erreur:
            # Une mesure indisponible ne doit pas rendre les autres pièces inaccessibles.
            diagnostic = {
                "piece": nom_piece, "source": nom_source, "sha256": sha,
                "faces_detectees": 0, "anomalies": [], "calques": {
                    cle: {"statut": "NON VÉRIFIÉ", "detail": str(erreur)}
                    for cle in ("surplomb", "epaisseur", "assise")
                },
            }
        if plaque is not None and diagnostic.get("maillage_ferme"):
            try:
                diagnostic["orientation_indicative"] = carte_risques.orientation_indicative(mesh)
            except (ValueError, RuntimeError):
                diagnostic["orientation_indicative"] = None
        diagnostic.update(plaque=plaque, identite_3mf=identite, faces_source=len(mesh.faces),
                          affichage_allege=len(mesh.faces) > MAX_FACES_VUE)
        vue.diagnostics.append(diagnostic)
        propositions.extend(_propositions(diagnostic, params))
    plaques = sorted({p for p in onglets.values() if p is not None})
    if plaques:
        vue.mecanisme(
            "impression",
            "Plaques du 3MF",
            ["Toutes les pièces"] + [f"Plaque {p}" for p in plaques],
            lambda v: {} if v == "Toutes les pièces" else {n: None for n, p in onglets.items() if f"Plaque {p}" != v},
        )
    elif source.suffix.lower() == ".3mf":
        vue.note += " Attribution aux plaques non vérifiée."
    if ignores:
        vue.note += f" {len(ignores)} modificateurs exclus de l’analyse géométrique."
    if _valeur(params, "spiral_mode") in ("1", 1, True):
        vue.note += " Le mode vase de la référence est actif."
    vitesses, note_vitesse = _vitesse(params, overrides)
    variante = None
    procede = []
    intentions = []
    if source.suffix.lower() == ".3mf" and prive is not None:
        from benchab.visualisation.comparaison_atelier import (
            INTENTIONS,
            variante_intention,
            variante_locale,
            variantes_procede,
        )

        variante = variante_locale(prive, sha, params)
        procede = variantes_procede(params, overrides)
        try:
            profil_debit, _ = _profil_debit(prive, sha)
        except (OSError, ValueError) as erreur:
            vue.note += f" Profil haut débit indisponible : {erreur}"
            profil_debit = None
        intentions = [mode for mode in INTENTIONS if variante_intention(params, overrides, mode,
                                                                        profil_debit=profil_debit)]
        if _couches_admissibles(objets, params, overrides):
            intentions.append("couches-variables")
    vue.atelier = {
        "reference": nom_source,
        "reglages": [
            {"libelle": label, "cle": cle, "valeur": valeur}
            for cle, label in CHAMPS.items()
            if (valeur := _valeur(params, cle)) is not None
        ],
        "propositions": propositions,
        "seuil": origine,
        "plan": ref_plan,
        "vitesses": vitesses,
        "note_vitesse": note_vitesse,
        "debit_declare": _debit_declare(params),
        "contexte": contexte,
        "variante_locale": variante is not None,
        "variantes_procede": bool(procede),
        "intentions_comparables": intentions,
        "mode_conseille": (
            "diagnostic-debit" if variante is not None else
            "vitesses" if any(v.get("apres_mm_s", 0) < v.get("avant_mm_s", 0) for v in vitesses) else
            "haut-debit" if "haut-debit" in intentions else
            "qualite-supports" if procede else "reference" if params else None
        ),
        "pieces_orientables": [
            d["piece"] for d in vue.diagnostics
            if d.get("orientation_indicative") and d["plaque"] is not None
            and d["identite_3mf"]["unite_source"] == "millimeter"
            and d["identite_3mf"]["transformations"][0].get("transform_3mf") is not None
            and sum(o[3]["build_item"] == d["identite_3mf"]["build_item"] for o in objets) == 1
        ] if not overrides and all(o[2] is not None for o in objets) else [],
    }
    if annule is not None and annule():
        raise ValueError("Analyse annulée.")
    if sha256_fichier(source) != sha:
        raise ValueError("Source modifiée pendant l’analyse.")
    if progression:
        progression("Construction de l’aperçu", len(objets), len(objets))
    return vue


def html(source, **options):
    """Rend la page autonome locale en octets ; le tampon temporaire est retiré après lecture."""
    with TemporaryDirectory(prefix="benchab-preparation-") as dossier:
        page = Path(dossier) / "atelier.html"
        construire(source, **options).ecrire(page)
        carte_risques.verifier_annulation(options.get("annule"))
        return page.read_bytes()


@lru_cache(maxsize=3)
def _preparation_en_cache(source, sha, nom, ident, root, variante_sha, profil_debit_sha):
    """Réemploie l’aperçu épinglé et la configuration courante, au plus trois petites pages."""
    page = html(source, sha_attendu=sha, prive=root, contexte={"projet": nom, "preparation": ident})
    if len(page) > 12 * 1024 * 1024:
        # L’exception transporte le résultat sans l’inscrire dans le cache.
        raise _PageHorsCache(page)
    return page


class _PageHorsCache(Exception):
    """Une page trop volumineuse est rendue sans être gardée en mémoire."""


def import_html(contenu, nom, unites, *, annule=None, progression=None, plaque_cible=None, piece_cible=None,
                prive=None, comparaison_auto=False):
    """Analyse les octets fournis ; tranche une copie temporaire du 3MF si demandé."""
    suffixe = Path(nom).suffix.lower()
    if not nom or len(nom) > 250 or Path(nom).name != nom or "\\" in nom or suffixe not in (".3mf", ".stl", ".obj"):
        raise ValueError("Nom ou format de modèle invalide.")
    with TemporaryDirectory(prefix="benchab-modele-") as dossier:
        source = Path(dossier) / ("modele" + suffixe)
        source.write_bytes(contenu)
        vue = construire(source, unites=unites, nom_source=nom, annule=annule, progression=progression,
                         plaque_cible=plaque_cible, piece_cible=piece_cible, prive=prive)
        if comparaison_auto and suffixe == ".3mf":
            mode = vue.atelier["mode_conseille"]
            with Archive3MF(source) as archive:
                deja_tranche = any(n.casefold().endswith(".gcode") for n in archive.noms())
            if deja_tranche:
                from benchab.visualisation.comparaison_atelier import bilan_tranchage_existant

                try:
                    vue.atelier["comparaison"] = bilan_tranchage_existant(source, vue.source["sha256"])
                except (ValueError, OSError, RuntimeError, BadZipFile, KeyError) as erreur:
                    vue.atelier["etat_comparaison"] = "Ce 3MF contient du G-code, mais le bilan est indisponible."
                    vue.atelier["erreur_comparaison"] = str(erreur)
            elif mode is None:
                vue.atelier["etat_comparaison"] = "Le 3MF ne contient pas de profil de tranchage."
            else:
                if progression:
                    progression("Comparaison au tranchage", 0, 0)
                try:
                    vue.atelier["comparaison"] = _comparaison_navigateur(
                        source, vue.source["sha256"], prive, False, mode, None, annule=annule,
                    )
                except carte_risques.AnalyseAnnulee:
                    raise
                except (ValueError, OSError, RuntimeError, BadZipFile, KeyError) as erreur:
                    vue.atelier["etat_comparaison"] = "Le tranchage de ce 3MF n’a pas abouti."
                    vue.atelier["erreur_comparaison"] = str(erreur)
        carte_risques.verifier_annulation(annule)
        page = Path(dossier) / "atelier.html"
        vue.ecrire(page)
        carte_risques.verifier_annulation(annule)
        return page.read_bytes()


def accueil(root, projet=None):
    """Retourne le formulaire HTML local et les liens vers les préparations déclarées."""
    from benchab.visualisation.atelier_local import document, lien, preparations, projet_courant

    ressources = Path(__file__).parent / "ressources"
    corps = (ressources / "atelier-import.html").read_text(encoding="utf-8")
    liens = ""
    if projet:
        projet_courant(root, projet)
        liens = "".join(
            lien("/preparation?" + urlencode({"projet": projet, "preparation": ident}), "Ouvrir " + ident)
            for ident in preparations(root, projet)
        )
        if liens:
            liens = "<h2>Préparations conservées</h2><div class='actions'>" + liens + "</div>"
    corps = corps.replace("__PREPARATIONS__", liens)
    return document("Atelier de préparation", corps)


def bibliotheque(modele, cote_mm, jeu_mm, *, format_stl=False, format_fiche=False):
    """Renvoie l’aperçu ou les octets STL en mm, sans écrire dans le projet."""
    import trimesh

    from benchab.conception.bibliotheque_atelier import generer

    pieces = generer(modele, cote_mm, jeu_mm)
    contenu = trimesh.util.concatenate(list(pieces.values())).export(file_type="stl")
    if format_fiche:
        import hashlib

        rapport = {
            "schema": 1, "modele": modele, "cote_mm": cote_mm,
            "jeu_nominal_mm": jeu_mm if modele != "temoin" else None,
            "stl_sha256": hashlib.sha256(contenu).hexdigest(),
            "pieces": [
                {"nom": nom, "dimensions_mm": mesh.extents.tolist(), "volume_mm3": mesh.volume}
                for nom, mesh in pieces.items()
            ],
            "statut_assemblage": "NON VÉRIFIÉ",
            "operations": [
                {"type": operation} for operation in {
                    "boite": ("Créer le corps", "Soustraire le volume intérieur", "Ajouter l’emboîtement au couvercle"),
                    "clip": ("Construire la languette", "Ajouter la rampe"),
                    "temoin": ("Créer la plaque", "Soustraire les empreintes de la série de jeux"),
                }[modele]
            ] + [{"type": "Poser les pièces sur le plan Z = 0"}],
        }
        return json.dumps(rapport, indent=2, ensure_ascii=False, allow_nan=False).encode()
    if format_stl:
        return contenu
    titre = f"{modele.capitalize()} · cote {cote_mm:g} mm"
    titre += f" · jeu nominal {jeu_mm:g} mm" if modele != "temoin" else " · série de jeux étagés"
    return import_html(contenu, titre + ".stl", "mm")


def enregistrer_bibliotheque(root, modele, cote_mm, jeu_mm, parent=None):
    """Archive une exploration et sa filiation privée, avec STL et fiche cotée en mm."""
    import re
    from uuid import uuid4

    from benchab.chemins import sans_lien
    from benchab.conception.registre_variantes import verifier
    from benchab.qualite.ecritures import publier_dossier_neuf

    fiche = bibliotheque(modele, cote_mm, jeu_mm, format_fiche=True)
    stl = bibliotheque(modele, cote_mm, jeu_mm, format_stl=True)
    donnees = json.loads(fiche)
    from hashlib import sha256

    if sha256(stl).hexdigest() != donnees["stl_sha256"]:
        raise ValueError("La génération a changé entre la fiche et le STL.")
    identifiant = uuid4().hex
    base = sans_lien(Path(root) / "conceptions" / "bibliotheque", racine=root)
    base.mkdir(parents=True, exist_ok=True)
    ascendance = []
    if parent is not None:
        if not isinstance(parent, str) or not re.fullmatch(r"[0-9a-f]{32}", parent):
            raise ValueError("Version parente invalide.")
        chemin = sans_lien(base / parent / "registre.json", racine=base, strict=True)
        if chemin.stat().st_size > 1_048_576:
            raise ValueError("Le registre parent dépasse le budget.")
        ascendance = json.loads(chemin.read_text(encoding="utf-8"))
        verifier(base, ascendance)
        if not ascendance or ascendance[-1]["id"] != parent or len(ascendance) >= 1000:
            raise ValueError("Filiation du registre parent invalide.")
        fiche_parent = sans_lien(base / parent / "cotes.json", racine=base, strict=True)
        if ascendance[-1]["source"]["fichier"] != f"{parent}/cotes.json":
            raise ValueError("La fiche parente ne correspond pas au registre.")
        contenu_parent = fiche_parent.read_bytes()
        if sha256(contenu_parent).hexdigest() != ascendance[-1]["source"]["sha256"]:
            raise ValueError("L’empreinte de la fiche parente a changé.")
        if json.loads(contenu_parent)["modele"] != modele:
            raise ValueError("Les versions parentes doivent appartenir à la même famille.")
    ligne = {
        "id": identifiant, "parent": parent, "changement": f"{modele} à {cote_mm:g} mm",
        "decision": "EN ATTENTE", "raison": "",
        "source": {"fichier": f"{identifiant}/cotes.json", "sha256": sha256(fiche).hexdigest()},
        "apercu": {"fichier": f"{identifiant}/atelier.html", "sha256": ""},
    }
    apercu = import_html(stl, f"{modele}.stl", "mm")
    ligne["apercu"]["sha256"] = sha256(apercu).hexdigest()
    registre = [*ascendance, ligne]
    publier_dossier_neuf(base / identifiant, {
        "piece.stl": stl, "cotes.json": fiche, "atelier.html": apercu,
        "registre.json": json.dumps(registre, ensure_ascii=False, indent=2).encode(),
    }, prive=root)
    verifier(base, registre)
    return {"id": identifiant, "parent": parent}


def _reference_projet(root, nom, ident):
    """Résout la référence et le SHA du manifeste de préparation."""
    from benchab.organisation import automatisation

    projet, _, _, preparation = automatisation.preparation(root, nom, ident)
    fichier = preparation["plaques_3mf"]
    cible = automatisation.source(root, projet, fichier["fichier"])
    return cible, fichier["sha256"], preparation["id"]


def enregistrer_observation(root, nom, ident, sha, piece, defaut, zone, photo, texte):
    """Enregistre un constat humain lié au 3MF privé, sans valider de cause ni modifier ses réglages."""
    import re
    from datetime import datetime
    from uuid import uuid4

    from benchab.chemins import sans_lien
    from benchab.qualite.ecritures import ecrire_json_neuf

    if (
        not isinstance(defaut, str) or defaut not in DEFAUTS_OBSERVATION
        or not isinstance(piece, str) or not 0 < len(piece) <= 256
        or not isinstance(texte, str) or not 0 < len(texte.strip()) <= 2000
        or (zone is not None and (not isinstance(zone, str) or len(zone) > 256))
    ):
        raise ValueError("Pièce, défaut et constat explicite requis.")
    if photo is not None and (
        not isinstance(photo, dict) or set(photo) != {"sha256", "octets"}
        or not isinstance(photo["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", photo["sha256"])
        or type(photo["octets"]) is not int or not 0 < photo["octets"] <= 16 * 1024 * 1024
    ):
        raise ValueError("Empreinte de photo invalide.")
    cible, attendu, preparation = _reference_projet(root, nom, ident)
    if sha != attendu or sha256_fichier(cible) != attendu:
        raise ValueError("La préparation a changé. Rouvrez-la avant d’enregistrer le constat.")
    objets, params, _, _ = _lire_3mf(cible)
    selection = next(((mesh, plaque) for index, (libelle, mesh, plaque, _) in enumerate(objets, 1)
                      if f"{libelle} · {index}" == piece), None)
    if selection is None:
        raise ValueError("Pièce absente de la préparation.")
    mesh, plaque = selection
    repere = None
    if zone is not None:
        seuil, _ = _largeur(params)
        diagnostic = carte_risques.mesurer(mesh, piece, cible.name, attendu, seuil_epaisseur_mm=seuil)
        anomalie = next((a for a in diagnostic["anomalies"] if a["id"] == zone), None)
        if anomalie is None:
            raise ValueError("Repère absent de cette pièce. Sélectionnez à nouveau la zone.")
        repere = {"id": zone, "type": anomalie["type"], "point_mm": anomalie["point_modele"]}
    observation = {
        "id": uuid4().hex, "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "preparation": preparation, "source_sha256": attendu, "piece": piece, "plaque": plaque,
        "defaut": defaut, "texte": texte.strip(), "zone": repere, "photo": photo,
        "profils_filament_declares": _valeur(params, "filament_settings_id"),
        "confirmation_humaine": True, "causalite": "NON VÉRIFIÉ",
    }
    dossier = sans_lien(Path(root) / "projets" / nom / "preuves" / "observations-atelier", racine=root)
    dossier.mkdir(parents=True, exist_ok=True)
    if sha256_fichier(cible) != attendu:
        raise ValueError("La préparation a changé pendant la vérification.")
    ecrire_json_neuf(dossier / (observation["id"] + ".json"), observation, entrees=(cible,))
    return observation


def preparation_projet(root, nom, ident):
    """Ouvre le 3MF épinglé d’une préparation déclarée et renvoie le HTML local."""
    cible, sha, ident = _reference_projet(root, nom, ident)
    if sha256_fichier(cible) != sha:
        raise ValueError("Le 3MF a changé depuis la préparation conservée.")
    fichier = Path(root) / "config" / "variantes-comparateur.json"
    variante_sha = sha256_fichier(fichier) if fichier.is_file() else None
    try:
        _, profil_debit_sha = _profil_debit(root, sha)
    except (OSError, ValueError):
        # La page indique le refus ; ne pas mettre une erreur de profil en cache.
        return html(cible, sha_attendu=sha, prive=root, contexte={"projet": nom, "preparation": ident})
    # Au-delà de 2 Mio source, éviter de retenir une page bien plus lourde que son ZIP.
    if cible.stat().st_size <= 2 * 1024 * 1024:
        try:
            return _preparation_en_cache(cible, sha, nom, ident, root, variante_sha, profil_debit_sha)
        except _PageHorsCache as page:
            return page.args[0]
    return html(cible, sha_attendu=sha, prive=root, contexte={"projet": nom, "preparation": ident})


def _choisir_pose(objets, surcharges, piece=None):
    """Résout l’instance demandée et les voisines immobiles de sa plaque, en mm."""
    if surcharges or not objets or any(o[2] is None for o in objets):
        raise ValueError("Les plaques doivent être identifiées et les surcharges résolues.")
    choix = [(n, o) for n, o in enumerate(objets, 1) if piece == f"{o[0]} · {n}"]
    if piece is None and len(objets) == 1:
        choix = [(1, objets[0])]
    if len(choix) != 1:
        raise ValueError("Sélectionnez une seule pièce à réorienter.")
    indice, (_, mesh, plaque, identite) = choix[0]
    if identite["unite_source"] != "millimeter" or identite["transformations"][0].get("transform_3mf") is None:
        raise ValueError("La pièce doit déclarer une pose explicite en millimètres.")
    if sum(o[3]["build_item"] == identite["build_item"] for o in objets) != 1:
        raise ValueError("La pièce sélectionnée partage son instance avec un autre maillage.")
    proposition = carte_risques.orientation_indicative(mesh)
    if not proposition:
        raise ValueError("Aucune autre pose vérifiable dans la zone d’étude.")
    plaques = {o[2] for o in objets}
    if plaques != set(range(1, len(plaques) + 1)):
        raise ValueError("Numérotation des plaques ambiguë.")
    voisines = [o[1] for n, o in enumerate(objets, 1) if n != indice and o[2] == plaque]
    return (mesh, identite, proposition), {"plaque": plaque, "plaques": len(plaques), "voisines": voisines}


def _couches_admissibles(objets, params, surcharges):
    """Identifie le cas borné d’un profil de couche global connu, en mm."""
    return (
        not surcharges and bool(objets) and _valeur(params, "spiral_mode") == "0"
        and _valeur(params, "nozzle_diameter") == ["0.4"]
        and _valeur(params, "layer_height") == "0.2"
        and len({o[3]["objet_id_racine"] for o in objets}) == len(objets)
        and all(o[2] is not None and o[1].bounds[0, 2] >= -0.02 and o[1].extents[2] <= 250 for o in objets)
    )


def _profils_couches(objets, params, surcharges):
    """Calcule en mm les plages de hauteur pour les objets identifiés de la préparation."""
    from benchab.fabrication.couches_variables import profil

    if not _couches_admissibles(objets, params, surcharges):
        raise ValueError("Hauteurs variables indisponibles pour cette préparation.")
    return {o[3]["objet_id_racine"]: profil(o[1]) for o in objets}


def comparaison_projet(root, nom, ident, sha, marge, mode="vitesses", piece=None):
    """Retranche la préparation déclarée et renvoie les écarts en secondes et grammes."""
    cible, attendu, _ = _reference_projet(root, nom, ident)
    if sha != attendu:
        raise ValueError("La préparation a changé. Rouvrez-la avant de comparer.")
    return _comparaison_navigateur(cible, attendu, root, marge, mode, piece)


def _comparaison_navigateur(cible, attendu, root, marge, mode, piece, *, annule=None):
    """Joint les exports contrôlés au rapport local, dans la limite d’octets de la visionneuse."""
    from base64 import b64encode

    with TemporaryDirectory(prefix="benchab-export-web-") as tmp:
        dossier = Path(tmp)
        resultat = _comparer_source(cible, attendu, root, marge, mode, piece, annule=annule, dossier_exports=dossier)
        reste = MAX_EXPORT_NAVIGATEUR
        for variante in resultat["variantes"]:
            export = variante.get("export")
            if export is None:
                continue
            fichier = dossier / export["fichier"]
            taille = fichier.stat().st_size
            if taille > reste:
                export["limite_navigateur"] = True
                continue
            export["octets_base64"] = b64encode(fichier.read_bytes()).decode("ascii")
            reste -= taille
        return resultat


def _comparer_source(cible, attendu, root, marge, mode, piece, *, annule=None, dossier_exports=None):
    """Compare la source épinglée selon l’intention, avec des écarts en secondes et grammes."""
    from benchab.visualisation.comparaison_atelier import (
        INTENTIONS,
        MODES,
        comparer,
        variante_intention,
        variante_locale,
        variantes_procede,
    )

    if mode not in MODES:
        raise ValueError("Comparaison inconnue.")

    if sha256_fichier(cible) != attendu:
        raise ValueError("Le fichier ne correspond plus à la préparation conservée.")
    options = {"prive": root, "annule": annule, "dossier_exports": dossier_exports}
    if mode == "reference":
        return comparer(cible, attendu, [], reference_seule=True, **options)
    objets, params, _, surcharges = _lire_3mf(cible, annule=annule)
    if mode == "orientation":
        return comparer(cible, attendu, [], orientation=_choisir_pose(objets, surcharges, piece), **options)
    if mode == "qualite-supports":
        return comparer(cible, attendu, [], procede=variantes_procede(params, surcharges), **options)
    if mode == "couches-variables":
        return comparer(cible, attendu, [], couches=_profils_couches(objets, params, surcharges), **options)
    if mode in INTENTIONS:
        profil, empreinte = _profil_debit(root, attendu) if mode == "haut-debit" else (None, None)
        return comparer(cible, attendu, [],
                        procede=variante_intention(params, surcharges, mode, profil_debit=profil),
                        profil_debit_sha=empreinte, **options)
    if mode == "diagnostic-debit":
        return comparer(cible, attendu, [], variante=variante_locale(root, attendu, params), **options)
    vitesses, _ = _vitesse(params, surcharges)
    return comparer(cible, attendu, vitesses, marge=marge, variante=variante_locale(root, attendu, params), **options)


def main(argv=None, *, prive=None):
    """Publie une page et une preuve JSON neuves dans l’atelier ou dans un dossier temporaire."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--unites", choices=("mm", "cm", "m"))
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--comparer", action="store_true", help="Trancher l’original et le plafonnement.")
    parser.add_argument("--avec-marge", action="store_true", help="Ajouter l’essai à 90 % du plafond.")
    parser.add_argument("--qualite-supports", action="store_true", help="Comparer les couches fixes et les supports.")
    parser.add_argument("--orientation", action="store_true", help="Comparer une pose sur une autre face.")
    parser.add_argument("--piece", help="Nom affiché de la pièce à réorienter, suffixe compris.")
    parser.add_argument("--analyser-plaque", type=int, help="Limiter les mesures à cette plaque.")
    parser.add_argument("--analyser-piece", type=int, help="Limiter les mesures à cette pièce (à partir de 1).")
    from benchab.visualisation.comparaison_atelier import INTENTIONS

    parser.add_argument("--intention", choices=(*INTENTIONS, "couches-variables"))
    parser.add_argument("--exporter-variantes", action="store_true", help="Conserver les 3MF de préparation contrôlés.")
    parser.add_argument("--exporter-reference", action="store_true", help="Copier le 3MF de référence à l’identique.")
    args = parser.parse_args(argv)
    try:
        options = (args.avec_marge, args.qualite_supports, args.orientation, args.exporter_variantes, args.intention)
        if not args.comparer and any(options):
            raise ValueError("Les options de comparaison nécessitent --comparer.")
        if sum((args.avec_marge, args.qualite_supports, args.orientation, bool(args.intention))) > 1:
            raise ValueError("Choisissez un seul type de comparaison.")
        if args.piece and not args.orientation:
            raise ValueError("--piece nécessite --orientation.")
        cible = destination_neuve(args.sortie, prive=prive)
        vue = construire(args.source, unites=args.unites, plaque_cible=args.analyser_plaque,
                         piece_cible=args.analyser_piece)
        with TemporaryDirectory(dir=cible.parent, prefix=".atelier-") as tmp:
            dossier = Path(tmp)
            if args.exporter_reference:
                from shutil import copyfile

                if args.source.suffix.casefold() != ".3mf":
                    raise ValueError("L’export de référence nécessite un 3MF.")
                with Archive3MF(args.source) as original:
                    original.verifier()
                    reference = dossier / "reference.3mf"
                    copyfile(args.source, reference)
                    original.verifier_source(args.source)
                if sha256_fichier(reference) != vue.source["sha256"]:
                    raise ValueError("L’export ne correspond pas à la référence analysée.")
                vue.atelier["export_reference"] = {
                    "fichier": reference.name, "sha256": vue.source["sha256"], "statut": "PASS",
                }
            if args.comparer:
                from benchab.visualisation.comparaison_atelier import (
                    comparer,
                    variante_intention,
                    variante_locale,
                    variantes_procede,
                )

                variante = None
                orientation = None
                couches = None
                profil_debit_sha = None
                procede = [] if args.qualite_supports else None
                if (args.orientation or args.intention) and args.source.suffix.casefold() != ".3mf":
                    raise ValueError("Cette comparaison nécessite un 3MF de préparation.")
                if args.source.suffix.casefold() == ".3mf":
                    objets, params, _, surcharges = _lire_3mf(args.source)
                    if args.orientation:
                        orientation = _choisir_pose(objets, surcharges, args.piece)
                    elif args.qualite_supports:
                        procede = variantes_procede(params, surcharges)
                    elif args.intention == "couches-variables":
                        couches = _profils_couches(objets, params, surcharges)
                    elif args.intention:
                        profil, profil_debit_sha = (
                            _profil_debit(prive, vue.source["sha256"]) if args.intention == "haut-debit"
                            else (None, None)
                        )
                        procede = variante_intention(params, surcharges, args.intention, profil_debit=profil)
                    else:
                        variante = variante_locale(prive, vue.source["sha256"], params)
                vue.atelier["comparaison"] = comparer(
                    args.source, vue.source["sha256"], vue.atelier["vitesses"],
                    marge=args.avec_marge, prive=prive, variante=variante, procede=procede,
                    dossier_exports=dossier if args.exporter_variantes else None,
                    orientation=orientation,
                    couches=couches,
                    profil_debit_sha=profil_debit_sha,
                )
            vue.ecrire(dossier / "atelier.html", prive=prive)
            (dossier / "rapport.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "reference": vue.atelier,
                        "diagnostics": vue.diagnostics,
                    },
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            renommer_exclusif(dossier, cible)
    except (ValueError, OSError, RuntimeError, BadZipFile) as exc:
        parser.error(str(exc))
    print(cible / "atelier.html")
    return 0
