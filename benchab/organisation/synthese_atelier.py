"""Synthèses de préparation et vues réelles, sans fabrication ni validation physique.

Les API de lecture n'écrivent rien. generer_vues est explicite, dans une sortie
neuve hors du dépôt ; les préparations Pnn ne sont jamais modifiées.
"""

import copy
import hashlib
import html
import io
import json
import math
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree.ElementTree import ParseError, fromstring

from benchab import reglages as schema_reglages
from benchab.controles.contact_support import ContactSupport
from benchab.controles.gcode_interpreter import Interpreter
from benchab.organisation.synthese_fichiers import (
    MAX_ARCHIVE,
    MAX_JSON,
    _archive_liee,
    _digest,
    _json,
    _lecture,
)
from benchab.organisation.synthese_fichiers import (
    _publier_vues as _publier_paquet,
)
from benchab.qualite.ecritures import renommer_exclusif
from benchab.qualite.empreintes import sha256_fichier
from benchab.visualisation.catalogue_visuel import interne

OUTILS = Path(__file__).resolve().parents[1]

MAX_MEMBRE = 64 * 1024 * 1024
MAX_GCODE = 128 * 1024 * 1024
REGLAGES = frozenset(
    (
        "layer_height",
        "initial_layer_print_height",
        "nozzle_temperature",
        "nozzle_temperature_initial_layer",
        "textured_plate_temp",
        "textured_plate_temp_initial_layer",
        "filament_type",
        "filament_colour",
        "filament_density",
        "filament_diameter",
        "filament_flow_ratio",
        "filament_max_volumetric_speed",
        "sparse_infill_density",
        "sparse_infill_pattern",
        "wall_loops",
        "curr_bed_type",
        "enable_support",
        "support_type",
        "support_style",
        "support_on_build_plate_only",
        "support_threshold_angle",
        "support_top_z_distance",
        "support_bottom_z_distance",
        "support_interface_top_layers",
        "support_interface_bottom_layers",
        "ace_t_color",
        "ace_t_filament",
        "ace_t_id",
    )
)
LIMITE = "Contrôle numérique de sources conservées ; aucune validation physique ni état réel de machine déduit."


def _racine(root, rel, projet=None):
    """Accepte les chemins du manifeste à la racine ou relatifs au projet."""
    root = Path(root).resolve()
    rel = str(rel)
    if not rel or Path(rel).is_absolute() or "\\" in rel or (".." in Path(rel).parts):
        raise ValueError("Chemin de source interdit")
    if projet is not None:
        from benchab.qualite.publication import chemin_projet

        return chemin_projet(root, projet, rel)
    return interne(root, rel)


def _projet(root, nom):
    if not isinstance(nom, str) or not re.fullmatch("[a-z0-9]+(?:-[a-z0-9]+)*", nom):
        raise ValueError("Projet invalide")
    p = interne(Path(root).resolve(), "projets/" + nom)
    from benchab.qualite.publication import charger

    q = charger(p)[0] if (Path(root) / "benchab.sqlite").is_file() else _json(interne(p, "qualite.json"))
    if not isinstance(q.get("suivi", {}), dict):
        raise ValueError("Suivi de projet malformé")
    return (p, q)


def statut_clair(suivi):
    """Le statut du projet n'attribue jamais ses retours à toutes ses pièces."""
    if not isinstance(suivi, dict):
        return "Métadonnées illisibles"
    statut = str(suivi.get("statut", "")).upper()
    physique = suivi.get("physique", {})
    imprime = isinstance(physique, dict) and physique.get("imprime") is True
    if suivi.get("type") == "historique" or statut == "HISTORIQUE":
        return "Historique — impression déclarée" if imprime else "Historique — résultat physique non renseigné"
    if statut == "PRET_NUMERIQUEMENT":
        return "Prêt numériquement — impression non validée"
    if statut in ("EN_PREPARATION", "CANDIDAT"):
        return "Candidat — références déjà imprimées" if imprime else "Candidat — non imprimé"
    if imprime:
        return "Impression déclarée — voir résultat et pièces concernées"
    if statut == "ATTENTE_HUMAINE":
        return "En attente de renseignements"
    return "État numérique à vérifier — impression non renseignée"


def _preparation(root, projet, q, ident=None):
    ident = ident or q.get("suivi", {}).get("preparation_courante")
    if ident is None:
        return (None, None, None)
    if not isinstance(ident, str) or not re.fullmatch("P\\d+", ident):
        raise ValueError("Identifiant de préparation invalide")
    path = interne(projet, "preparations/" + ident + "/PREPARATION.json")
    fiche, digest = _json(path, avecempreinte=True)
    if fiche.get("id") != ident:
        raise ValueError("Identifiant du manifeste incohérent")
    return (path, fiche, digest)


def _preuve(root, projet, rel, attendu=None):
    p = _racine(root, rel, projet)
    if not p.is_file():
        return {"fichier": str(p.relative_to(Path(root).resolve())), "statut": "ABSENT", "sha256": None}
    with _lecture(p) as stream:
        digest = _digest(stream)
    return {
        "fichier": p.relative_to(Path(root).resolve()).as_posix(),
        "sha256": digest,
        "attendu": attendu,
        "statut": "CONFORME" if attendu and digest == attendu else "MODIFIE" if attendu else "NON_LIE",
    }


def _membre(z, nom, limite=MAX_MEMBRE):
    info = z.getinfo(nom)
    if info.file_size > limite:
        raise ValueError("Membre d’archive trop volumineux")
    return z.read(nom)


def _archives(root, projet, fiche):
    """Réglages 3MF déclarés et commandes réellement présentes restent distincts."""
    embarques = {}
    tranches = []
    supports = []
    alertes = []
    durees = []
    for cle in ("plaques_3mf", "tranche_3mf"):
        ref = fiche.get(cle, {})
        if not isinstance(ref, dict) or not ref.get("fichier"):
            continue
        preuve = _preuve(root, projet, ref["fichier"], ref.get("sha256"))
        if preuve["statut"] != "CONFORME":
            alertes.append(cle + " : source absente, modifiée ou non liée par empreinte")
            continue
        path = _racine(root, ref["fichier"], projet)
        try:
            debut_tranches = len(tranches)
            debut_supports = len(supports)
            with _archive_liee(path, ref.get("sha256")) as (z, _):
                if sum((x.file_size for x in z.infolist())) > MAX_ARCHIVE:
                    raise ValueError("Archive décompressée trop volumineuse")
                if len(z.namelist()) != len(set(z.namelist())):
                    raise ValueError("Membres d’archive dupliqués")
                if "Metadata/project_settings.config" in z.namelist():
                    cfg = schema_reglages.decoder(_membre(z, "Metadata/project_settings.config", MAX_JSON))
                    if not isinstance(cfg, dict):
                        raise ValueError("Réglages 3MF malformés")
                    embarques[cle] = {k: v for k, v in cfg.items() if k in REGLAGES}
                if cle != "tranche_3mf":
                    continue
                z_objets = []
                if "Metadata/model_settings.config" in z.namelist():
                    modele = fromstring(_membre(z, "Metadata/model_settings.config", MAX_JSON))
                    for obj in modele.findall("object"):
                        for element in [obj, *obj.findall("part")]:
                            valeurs = {
                                m.get("key"): m.get("value")
                                for m in element.findall("metadata")
                                if m.get("key")
                                in ("support_top_z_distance", "support_bottom_z_distance", "layer_height")
                            }
                            if valeurs:
                                z_objets.append(
                                    {
                                        "objet": obj.get("id"),
                                        "partie": element.get("id") if element is not obj else None,
                                        "reglages": valeurs,
                                    }
                                )
                try:
                    from benchab.fabrication import anycubic

                    # Garder le même descripteur lié au SHA ; une réouverture ne porterait pas cette preuve.
                    durees = anycubic.resultats_xml(_membre(z, "Metadata/slice_info.config", MAX_JSON))
                except (KeyError, ValueError, ParseError, zipfile.BadZipFile):
                    alertes.append("Durée et masse estimées du slicer indisponibles")
                for name in sorted(z.namelist()):
                    m = re.fullmatch("Metadata/plate_(\\d+)\\.gcode", name)
                    if not m:
                        continue
                    raw = _membre(z, name, MAX_GCODE)
                    index = m.group(1)
                    digest = hashlib.sha256(raw).hexdigest()
                    attendu = fiche.get("gcode", {}).get(index, {}).get("sha256")
                    if not attendu or digest != attendu:
                        alertes.append("G-code plaque " + index + " : empreinte absente ou différente")
                        continue
                    reglages = {}
                    commandes = {}
                    parser = Interpreter()
                    contact = ContactSupport()
                    ext = 0.0
                    bornes = None
                    bandes = {}
                    segments = 0
                    for ligne in io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace"):
                        setting = re.match("^;\\s*([A-Za-z0-9_]+)\\s*=\\s*(.*)$", ligne)
                        if setting and setting[1] in REGLAGES:
                            reglages[setting[1]] = setting[2].strip()
                        move = parser.feed_line(ligne)
                        if move and move["trajectory"] == "arc_g17_xy":
                            parser.error(
                                "arc analytique non pris en charge par la représentation géométrique synthétique"
                            )
                            move = None
                        contact.ajouter(move, parser.metadata)
                        ev = parser.last_event or {}
                        op = ev.get("command")
                        if op in ("G9111", "M104", "M109", "M140", "M190", "M900", "M106"):
                            param = ev.get("parameters", {})
                            if param not in commandes.setdefault(op, []):
                                if len(commandes[op]) < 100:
                                    commandes[op].append(param)
                        if not move or not (parser.kind or "").lower().startswith("support") or move["deposited"] <= 0:
                            continue
                        segments += 1
                        ext += move["deposited"]
                        point = move["end"]
                        if bornes is None:
                            bornes = [list(point), list(point)]
                        for endpoint in (move["deposition_start"], point):
                            for d in range(3):
                                bornes[0][d] = min(bornes[0][d], endpoint[d])
                                bornes[1][d] = max(bornes[1][d], endpoint[d])
                        band = str(int(math.floor(point[2] / 5)) * 5)
                        bandes[band] = bandes.get(band, 0) + move["deposited"]
                    tranches.append(
                        {
                            "plaque": int(index),
                            "sha256": digest,
                            "reglages_commentaires": reglages,
                            "commandes": commandes,
                            "couverture": "PARTIELLE" if parser.errors else "MODELE_LINEAIRE",
                            "erreurs_interpretation": parser.errors,
                        }
                    )
                    supports.append(
                        {
                            "plaque": int(index),
                            "segments": segments,
                            "filament_extrude_mm": round(ext, 4),
                            "bornes_mm": bornes,
                            "par_5mm_filament_mm": {
                                k: round(v, 4) for k, v in sorted(bandes.items(), key=lambda x: int(x[0]))
                            },
                            "surface_contact_mm2": None,
                            "separation_reelle_z_mm": None,
                            "projection_trajets": contact.resultat(parser.errors),
                            "z_nominal": {
                                "globaux": {
                                    k: v
                                    for k, v in embarques.get(cle, {}).items()
                                    if k
                                    in (
                                        "support_top_z_distance",
                                        "support_bottom_z_distance",
                                        "layer_height",
                                        "initial_layer_print_height",
                                    )
                                },
                                "objets": z_objets,
                                "source": (
                                    "Metadata/project_settings.config et model_settings.config du 3MF tranche, SHA "
                                )
                                + preuve["sha256"],
                                "limite": (
                                    "Réglages nominaux ; la hauteur de couche peut arrondir l’écart effec"
                                    "tif. Objets déclarés, affectation à la plaque non reconstruite."
                                ),
                            },
                            "limite": (
                                "Extrusion nominale des trajets Support identifiés ; boîte englobante"
                                ", pas surface de contact. Séparation physique non mesurée ; projecti"
                                "on numérique bornée détaillée séparément."
                            ),
                            "couverture": "PARTIELLE" if parser.errors else "MODELE_LINEAIRE",
                        }
                    )
        except (OSError, ValueError, KeyError, TypeError, ParseError, zipfile.BadZipFile) as exc:
            embarques.pop(cle, None)
            del tranches[debut_tranches:]
            del supports[debut_supports:]
            if cle == "tranche_3mf":
                durees = []
            alertes.append(cle + " : " + str(exc))
    return (embarques, tranches, supports, durees, alertes)


def connaissances(root, nom):
    root = Path(root).resolve()
    projet, q = _projet(root, nom)
    if (root / "benchab.sqlite").is_file():
        import sqlite3

        from benchab.connaissances.sqlite import lire_connexion

        db = sqlite3.connect((root / "benchab.sqlite").as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            data = lire_connexion(db)
        finally:
            db.close()
    else:
        reg = _racine(root, "donnees/connaissances/registre.json")
        if not reg.is_file():
            return {"elements": [], "avertissements": ["Registre de connaissances absent"]}
        data = _json(reg)
    ids = set(q.get("suivi", {}).get("connaissances", []))
    elements = []
    for item in data.get("connaissances", []):
        if not isinstance(item, dict):
            continue
        sources = item.get("sources", [])
        contexte = item.get("contexte", {})
        rels = [str(s.get("chemin", "")).split("#")[0] for s in sources if isinstance(s, dict)]
        if (
            item.get("id") not in ids
            and (not any((p.startswith("projets/" + nom + "/") or nom in Path(p).name for p in rels)))
            and (item.get("projet") != nom)
            and (contexte.get("projet") != nom)
        ):
            continue
        preuves = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            rel = source.get("chemin", "").split("#")[0]
            if source.get("revision_originale"):
                preuves.append({"fichier": rel, "statut": "HISTORIQUE_GIT_NON_REVERIFIE"})
                continue
            try:
                preuve = _preuve(root, None, rel, source.get("sha256"))
            except (OSError, ValueError):
                preuve = {"fichier": rel, "statut": "SOURCE_INVALIDE"}
            preuves.append(preuve)
        statut = (
            "SOURCE_A_REVOIR"
            if not preuves or any((p["statut"] not in ("CONFORME", "HISTORIQUE_GIT_NON_REVERIFIE") for p in preuves))
            else "SOURCE_INCHANGEE"
        )
        incompatibles = []
        for key in ("filament", "buse_mm", "couche_mm"):
            if (
                key in contexte
                and key in q.get("suivi", {}).get("contexte", {})
                and (contexte[key] != q["suivi"]["contexte"][key])
            ):
                incompatibles.append(key)
        elements.append(
            {
                "id": item.get("id"),
                "titre": item.get("titre"),
                "niveau_preuve": item.get("niveau_preuve"),
                "validite": item.get("validite"),
                "sources": preuves,
                "statut": statut,
                "contexte": "INCOMPATIBLE" if incompatibles else "A_CONFIRMER",
                "ecarts_contexte": incompatibles,
                "limites": item.get("exclusions"),
                "retour_physique_conserve": str(item.get("niveau_preuve", "")).startswith(
                    ("OBSERVATION_UTILISATEUR", "MESURE_PHYSIQUE")
                ),
            }
        )
    return {
        "elements": elements,
        "avertissements": [
            (
                "Une source inchangée ne démontre pas la compatibilité avec une nouve"
                "lle préparation. Les observations physiques ne sont pas effacées par"
                " une source périmée."
            )
        ],
    }


def bilan(root, nom, preparation=None):
    root = Path(root).resolve()
    projet, q = _projet(root, nom)
    suivi = q.get("suivi", {})
    path, fiche, fiche_sha = _preparation(root, projet, q, preparation)
    controles = []
    alertes = []
    refs = []
    if fiche is None:
        embarques, tranches, supports, durees = ({}, {}, [], [])
        alertes.append("Aucune préparation courante déclarée")
    else:
        for cle in ("plaques_3mf", "tranche_3mf"):
            ref = fiche.get(cle, {})
            if isinstance(ref, dict) and ref.get("fichier"):
                refs.append({"role": cle, **_preuve(root, projet, ref["fichier"], ref.get("sha256"))})
        for rel, info in fiche.get("controles", {}).items():
            preuve = _preuve(root, projet, rel, info.get("sha256") if isinstance(info, dict) else None)
            record = {**preuve, "statuts_declares": info.get("statuts", {}) if isinstance(info, dict) else {}}
            if preuve["statut"] == "CONFORME":
                try:
                    record["rapport"] = _json(_racine(root, rel, projet), attendu=preuve["sha256"])
                except (OSError, ValueError):
                    record["statut"] = "MODIFIE_OU_ILLISIBLE"
            controles.append(record)
        embarques, tranches, supports, durees, alertes_archives = _archives(root, projet, fiche)
        alertes += alertes_archives
    vues = []
    for rel in ("preuves/visualisation/projet-3d.html", "preuves/visualisation/apercu-gcode.html"):
        f = interne(projet, rel)
        if f.is_file():
            vues.append({**_preuve(root, projet, rel), "correspondance_preparation": "NON_REVERIFIEE"})
    return {
        "schema": 1,
        "projet": nom,
        "titre": suivi.get("titre", nom),
        "statut": statut_clair(suivi),
        "preparation": {
            "id": fiche.get("id"),
            "date": fiche.get("date"),
            "statut": fiche.get("statut"),
            "manifeste": path.relative_to(root).as_posix(),
            "sha256": fiche_sha,
        }
        if fiche
        else None,
        "reglages": {
            "intention": {
                "provenance": "Déclaration actuelle du projet ; historique de la préparation non déduit",
                "filaments": suivi.get("filaments", {}),
                "parametres": fiche.get("parametres", {}) if fiche else {},
            },
            "embarques": embarques,
            "tranches": tranches,
        },
        "sources": refs,
        "controles": controles,
        "supports": supports,
        "estimations_slicer": durees,
        "vues": vues,
        "connaissances": connaissances(root, nom),
        "avertissements": alertes,
        "limite": LIMITE,
    }


def _differences(a, b, prefix=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out.extend(_differences(a.get(k), b.get(k), prefix + "." + str(k) if prefix else str(k)))
        return out
    return [] if a == b else [{"champ": prefix, "avant": a, "apres": b}]


def comparer_preparations(root, nom, a, b):
    pa = bilan(root, nom, a)
    pb = bilan(root, nom, b)
    return {
        "projet": nom,
        "a": a,
        "b": b,
        "ecarts": {key: _differences(pa[key], pb[key]) for key in ("reglages", "estimations_slicer", "supports")},
        "avertissements": {"a": pa["avertissements"], "b": pb["avertissements"]},
        "limite": LIMITE,
    }


def dependances_vues(root, nom, preparation=None):
    """Entrées lues par la génération ; les chemins optionnels absents restent déclarés.

    Le consommateur du cache doit représenter une absence par une empreinte
    distincte, et comparer également la liste des chemins.
    """
    root = Path(root).resolve()
    projet, q = _projet(root, nom)
    chemins = {
        interne(projet, "qualite.json"),
        interne(projet, q.get("manifeste", "candidats/assemblage.json")),
        interne(projet, "preuves/placement.json"),
        interne(projet, "preuves/tranchage.json"),
    }
    manifeste = interne(projet, q.get("manifeste", "candidats/assemblage.json"))
    if manifeste.is_file():
        man = _json(manifeste)
        models = man.get("models") or man.get("pieces", {})
        for name in models:
            if not re.fullmatch("[A-Za-z][A-Za-z0-9_]*", str(name)):
                raise ValueError("Identifiant de modèle invalide")
            chemins.add(interne(projet, str(Path(q.get("stl_assemblage", "candidats")) / (name + ".stl"))))
            chemins.add(interne(projet, "candidats/" + name + ".stl"))
    path, fiche, _ = _preparation(root, projet, q, preparation)
    if path is not None:
        chemins.add(path)
    placement = interne(projet, "preuves/placement.json")
    if placement.is_file():
        for obj in _json(placement).get("objects", []):
            name = obj.get("model")
            if not isinstance(name, str) or not re.fullmatch("[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError("Modèle de placement invalide")
            chemins.add(interne(projet, "candidats/" + name + ".stl"))
    chemins.add(interne(projet, "candidats/tranche.3mf"))
    chemins.add(interne(projet, "preuves/visualisation/apercu-gcode.html"))
    for ref in q.get("suivi", {}).get("references_imprimees", []):
        for f in ref.get("fichiers", []):
            if f.get("chemin"):
                chemins.add(interne(projet, f["chemin"]))
    for f in (projet / "reference").rglob("*.gcode.3mf"):
        chemins.add(interne(projet, f.relative_to(projet)))
    if fiche and isinstance(fiche.get("tranche_3mf"), dict) and fiche["tranche_3mf"].get("fichier"):
        chemins.add(_racine(root, fiche["tranche_3mf"]["fichier"], projet))
    return sorted((p.relative_to(root).as_posix() for p in chemins))


def _publier_vues(stage, out):
    """Publie le paquet privé ; conserve la frontière de contrôle du renommage."""
    return _publier_paquet(stage, out, renommer=renommer_exclusif)


def placement_preparation(root, projet, fiche):
    """Plan privé dérivé de la source Pnn vérifiée, jamais écrit dans Pnn."""
    ref = (fiche or {}).get("plaques_3mf", {})
    if not ref.get("fichier"):
        return None
    with _archive_liee(_racine(root, ref["fichier"], projet), ref.get("sha256")) as (archive, _):
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("Membres source dupliqués")
        if sum((i.file_size for i in archive.infolist())) > MAX_ARCHIVE:
            raise ValueError("Source volumineuse")
        if "Metadata/benchab_geometry.json" not in archive.namelist():
            return None
        meta = json.loads(_membre(archive, "Metadata/benchab_geometry.json", MAX_JSON))
    if not isinstance(meta, dict) or meta.get("schema") != 1 or meta.get("unite") != "millimeter":
        raise ValueError("Provenance source invalide")
    objets = meta.get("objets")
    if not isinstance(objets, list) or not objets or len(objets) > 1000:
        raise ValueError("Objets source invalides")
    plan = []
    for o in objets:
        if not isinstance(o, dict) or not re.fullmatch("[A-Za-z][A-Za-z0-9_]*", str(o.get("piece", ""))):
            raise ValueError("Pièce source invalide")
        xy = o.get("xy_plaque_mm")
        plaque = o.get("plaque")
        if (
            not isinstance(xy, list)
            or len(xy) != 2
            or any((type(x) not in (int, float) or not math.isfinite(x) or (not 0 <= x <= 250) for x in xy))
        ):
            raise ValueError("Pose source invalide")
        if type(plaque) is not int or not 1 <= plaque <= 100:
            raise ValueError("Plaque source invalide")
        plan.append(
            {
                "object_id": o.get("object_id"),
                "name": o["piece"],
                "model": o["piece"],
                "plate": plaque,
                "xy_interface": xy,
                "rot_z_deg": 0,
            }
        )
    return {
        "objects": plan,
        "source_3mf": dict(ref),
        "limite": (
            "STL candidats normalisés ; leur identité doit être vérifiée après chargement avant de lier les vues à Pnn."
        ),
    }


def correspondance_geometrie(root, projet, fiche, objets):
    """Compare les identités avant allègement aux métadonnées du 3MF source lié.

    La triangulation quantifiée est attestée par le producteur de l'archive
    source, distincte du 3MF tranché. Le contrôle ne prouve pas son impression.
    """
    result = {
        "statut": "NON_DEMONTREE",
        "source_3mf": None,
        "objets": [],
        "portee": "OBJETS_IMPRESSION_AVANT_ALLEGEMENT",
        "methode": (
            "Identité triangles orientés, quantum 1e-5 mm, unité et poses ; métad"
            "onnées du producteur source liées par SHA-256."
        ),
        "limite": (
            "Ni géométrie allégée d’affichage, ni G-code, ni état physique. Modif"
            "icateurs du slicer conservés dans la source, non représentés par les"
            " STL candidats."
        ),
    }
    ref = (fiche or {}).get("plaques_3mf", {})
    if not ref.get("fichier") or not objets:
        return result
    with _archive_liee(_racine(root, ref["fichier"], projet), ref.get("sha256")) as (z, _):
        if "Metadata/benchab_geometry.json" not in z.namelist():
            return result
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError("Membres de provenance dupliqués")
        meta = json.loads(_membre(z, "Metadata/benchab_geometry.json", MAX_JSON))
        if not isinstance(meta, dict):
            raise ValueError("Provenance 3MF malformée")
        result["source_3mf"] = dict(ref)
        if meta.get("schema") != 1 or meta.get("unite") != "millimeter":
            raise ValueError("Identité 3MF : schéma ou unité inconnue")
        xml = fromstring(_membre(z, "3D/3dmodel.model"))
        ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        if xml.get("unit") != "millimeter":
            raise ValueError("Unité réelle du 3MF différente")
        poses = {
            int(x.get("objectid")): [float(v) for v in x.get("transform", "").split()]
            for x in xml.findall("m:build/m:item", ns)
        }
        config = fromstring(_membre(z, "Metadata/model_settings.config", MAX_JSON))
        noms = {
            int(o.get("id")): next((m.get("value") for m in o.findall("metadata") if m.get("key") == "name"), None)
            for o in config.findall("object")
        }
        from benchab.fabrication import anycubic

        nb_plaques = len(config.findall("plate"))
        if not 1 <= nb_plaques <= 100:
            raise ValueError("Nombre de plaques provenance invalide")
        colonnes = anycubic.colonnes(nb_plaques)
        pas = float(meta.get("pas_plaques_mm", 0))
        if pas not in (300.0, 314.4):
            raise ValueError("Pas des plaques source inconnu")
        identites = meta.get("objets", [])
        if not isinstance(identites, list) or any(
            (
                not isinstance(o, dict)
                or not isinstance(o.get("geometrie"), dict)
                or type(o.get("plaque")) is not int
                or (not 1 <= o["plaque"] <= nb_plaques)
                for o in identites
            )
        ):
            raise ValueError("Identités de provenance malformées")
        if len(identites) != len(objets):
            result["statut"] = "DIVERGENTE"
            return result
        if any(
            (
                not re.fullmatch("[0-9a-f]{64}", str(o.get("geometrie", {}).get("sha256_triangles", "")))
                for o in [*identites, *objets]
            )
        ):
            return result
        restants = list(objets)
        for ident in identites:
            matches = [o for o in restants if o["piece"] == ident.get("piece") and o["plaque"] == ident.get("plaque")]
            if len(matches) != 1:
                result["statut"] = "DIVERGENTE"
                return result
            o = matches[0]
            restants.remove(o)
            k = int(o["plaque"]) - 1
            xy = o["xy_plaque_mm"]
            pose_attendue = [1, 0, 0, 0, 1, 0, 0, 0, 1, xy[0] + k % colonnes * pas, xy[1] - k // colonnes * pas, 0]
            pose = poses.get(ident.get("object_id"), [])
            pose_ok = len(pose) == 12 and all(
                (math.isfinite(a) and abs(a - b) <= 1e-06 for a, b in zip(pose, pose_attendue))
            )
            ok = (
                o.get("geometrie") == ident.get("geometrie")
                and o.get("xy_plaque_mm") == ident.get("xy_plaque_mm")
                and pose_ok
                and (noms.get(ident.get("object_id")) == o["piece"])
                and (ident.get("transform_3mf") == pose)
            )
            result["objets"].append(
                {
                    "piece": o["piece"],
                    "plaque": o["plaque"],
                    "object_id": ident.get("object_id"),
                    "geometrie": o.get("geometrie"),
                    "xy_plaque_mm": o.get("xy_plaque_mm"),
                    "transform_3mf": ident.get("transform_3mf"),
                    "conforme": ok,
                }
            )
        result["statut"] = "CONFORME_SOURCE" if all((o["conforme"] for o in result["objets"])) else "DIVERGENTE"
    return result


def generer_vues(root, nom, sortie, preparation=None):
    """Produit ensemble quatre vues 3D à caméras explicites et un sommaire traçable.

    Aucun fichier d'entrée modifié ; sortie neuve hors dépôt. Une vue plateau
    manque explicitement si le projet ne fournit aucune pose d'impression.
    """
    root = Path(root).resolve()
    projet, q = _projet(root, nom)
    out = Path(sortie).absolute()
    if (
        ".." in out.parts
        or out.exists()
        or any((x.is_symlink() for x in (out, *out.parents)))
        or (
            out.resolve().is_relative_to(root)
            and not ((root / "benchab.sqlite").is_file() and out.resolve().is_relative_to(root / "mesures"))
        )
    ):
        raise ValueError("La sortie doit être neuve, sans liens, hors du dépôt")
    specs = [
        ("piece", "Pièce — vue isométrique", [1, -1.4, 0.9]),
        ("plateau", "Plateau — vue du dessus", [0, -0.01, 1]),
        ("supports", "Supports — vue latérale", [1, -1.4, 0.5]),
        ("risques", "Sous-faces et zones à revoir", [1, -1.4, -0.9]),
    ]
    report = {
        "schema": 1,
        "projet": nom,
        "preparation": preparation or q.get("suivi", {}).get("preparation_courante"),
        "vues": [],
        "limite": LIMITE,
    }
    try:
        from benchab.visualisation.catalogue_visuel import scene

        _, fiche, _ = _preparation(root, projet, q, preparation)
        tranche = fiche.get("tranche_3mf") if fiche else None
        if tranche and _preuve(root, projet, tranche["fichier"], tranche.get("sha256"))["statut"] != "CONFORME":
            raise ValueError("Tranchage absent ou modifié")
        plan_pose = placement_preparation(root, projet, fiche)
        placement_path = interne(projet, "preuves/placement.json")
        if not plan_pose and placement_path.is_file():
            pl = _json(placement_path)
            if any((not re.fullmatch("[A-Za-z][A-Za-z0-9_]*", str(x.get("model", ""))) for x in pl.get("objects", []))):
                raise ValueError("Modèle de placement invalide")
        page = scene(root, nom, tranche=tranche, placement=plan_pose).decode("utf-8")
        match = re.search('(<script type="application/json" id="donnees">)(.*?)(</script>)', page, re.S)
        if not match:
            raise ValueError("Contrat de scène absent")
        data = json.loads(match[2])
        data["genere_le"] = "Sources conservées — date de génération non utilisée comme preuve"
        placement = bool(plan_pose) or interne(projet, "preuves/placement.json").is_file()
        for diagnostic in data.get("diagnostics", []):
            diagnostic.pop("date", None)
        data["source"]["controle_standard"] = True
        data["source"]["preparation_choisie"] = report["preparation"]
        correspondance = correspondance_geometrie(root, projet, fiche, data["source"].get("objets_impression", []))
        report["correspondance"] = correspondance
        data["source"]["correspondance_preparation"] = correspondance
        report["supports_qualite"] = {k: v for k, v in data["source"].items() if k.startswith("supports_plaque_")}
        data["source"]["limite_preparation"] = (
            "Objets de la vue impression : "
            + correspondance["statut"]
            + " avec le 3MF source. Assemblage candidat distinct, non lié par ce contrôle ; aucune preuve physique."
        )
        if correspondance["statut"] == "DIVERGENTE":
            placement = False
    except (ValueError, OSError, KeyError, TypeError, ParseError, zipfile.BadZipFile, ImportError) as exc:
        page = None
        report["erreur_geometrie"] = str(exc)
    with tempfile.TemporaryDirectory(prefix="controle-vues-") as prive:
        stage = Path(prive).resolve()
        for ident, label, direction in specs:
            row = {
                "id": ident,
                "titre": label,
                "camera_direction": direction,
                "statut": "INDISPONIBLE",
                "correspondance_preparation": report.get("correspondance", {}).get("statut", "NON_DEMONTREE")
                if ident in ("plateau", "supports")
                else "ASSEMBLAGE_CANDIDAT_NON_LIE",
            }
            if (
                page is not None
                and (ident not in ("plateau", "supports") or placement)
                and (ident != "supports" or tranche)
            ):
                vue = copy.deepcopy(data)
                vue["vue_initiale"] = direction
                if ident in ("plateau", "supports"):
                    for m in vue["mecanismes"]:
                        if m.get("id") == "impression":
                            m["defaut"] = 1
                payload = json.dumps(vue, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
                body = page[: match.start(2)] + payload + page[match.end(2) :]
                f = stage / (ident + ".html")
                f.write_text(body, encoding="utf-8")
                row.update(statut="DISPONIBLE", fichier=f.name, sha256=sha256_fichier(f))
            report["vues"].append(row)
        disponibles = sum((v["statut"] == "DISPONIBLE" for v in report["vues"]))
        report["statut"] = "PASS" if disponibles == 4 else "WARN" if disponibles else "INDISPONIBLE"
        report["supports"] = (
            "Consulter les supports du G-code et les distances embarquées dans le"
            " bilan ; aucune surface de contact physique inventée."
        )
        (stage / "VUES.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        links = "".join(
            (
                "<li>"
                + html.escape(x["titre"])
                + " : "
                + (
                    '<a href="' + x["fichier"] + '">ouvrir la vue</a>'
                    if x["statut"] == "DISPONIBLE"
                    else "indisponible"
                )
                + "</li>"
                for x in report["vues"]
            )
        )
        (stage / "index.html").write_text(
            (
                '<!doctype html><html lang="fr"><meta charset="utf-8"><title>Vues de '
                "contrôle</title><h1>Vues de contrôle — "
            )
            + html.escape(nom)
            + "</h1><ul>"
            + links
            + "</ul><p>"
            + html.escape(LIMITE)
            + "</p></html>",
            encoding="utf-8",
        )
        _publier_vues(stage, out)
    return report
