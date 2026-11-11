"""Analyse numérique bornée des départs de boucles de paroi extérieure."""

from __future__ import annotations

import json
import math
import re
import zipfile
import zlib
from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import ouvrir_membre_3mf, plaques_gcode_archive
from benchab.reglages import definition

_MAX_REGIONS = 64
_MAX_SOMMETS = 256
_MAX_LIGNES = 2_000_000
_MAX_BOUCLES = 2_000
_MAX_LIGNE = 1024 * 1024
_MAX_RAPPORT_OCTETS = 2 * 1024 * 1024
_TYPES_PAROI = {"outer wall", "outer perimeter"}
_RE_SHA = re.compile(r"[0-9a-f]{64}")


def _nombre_fini(valeur):
    """Accepte les nombres JSON finis, sans booléens implicites."""
    return type(valeur) in (int, float) and math.isfinite(valeur)


def _aire_signee(points):
    """Calcule l’aire orientée XY du polygone, en mm²."""
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2


def _auto_intersection(points):
    """Détecte les croisements non adjacents sans tolérance géométrique implicite."""

    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def sur_segment(a, b, p):
        return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])

    nombre = len(points)
    for i in range(nombre):
        a, b = points[i], points[(i + 1) % nombre]
        for j in range(i + 1, nombre):
            if j in {i, (i + 1) % nombre} or (j + 1) % nombre in {i, (i + 1) % nombre}:
                continue
            c, d = points[j], points[(j + 1) % nombre]
            ab_c, ab_d = orientation(a, b, c), orientation(a, b, d)
            cd_a, cd_b = orientation(c, d, a), orientation(c, d, b)
            croisement = ab_c * ab_d < 0 and cd_a * cd_b < 0
            touche = (
                ab_c == 0
                and sur_segment(a, b, c)
                or ab_d == 0
                and sur_segment(a, b, d)
                or cd_a == 0
                and sur_segment(c, d, a)
                or cd_b == 0
                and sur_segment(c, d, b)
            )
            if croisement or touche:
                return True
    return False


def _valider_declaration(declaration):
    """Valide les unités mm, les clés exactes et les limites de la déclaration publique."""
    if not isinstance(declaration, dict) or set(declaration) != {
        "schema",
        "source_sha256",
        "plaque",
        "objet_id",
        "tolerances_mm",
        "regions",
    }:
        raise ValueError("déclaration : clés attendues absentes ou inattendues")
    if (
        type(declaration["schema"]) is not int
        or declaration["schema"] != 1
        or not isinstance(declaration["source_sha256"], str)
        or not _RE_SHA.fullmatch(declaration["source_sha256"])
    ):
        raise ValueError("déclaration : schéma ou SHA-256 invalide")
    if type(declaration["plaque"]) is not int or declaration["plaque"] <= 0:
        raise ValueError("déclaration : numéro de plaque positif requis")
    objet = declaration["objet_id"]
    if not isinstance(objet, str) or not objet or len(objet) > 256 or any(c.isspace() or ord(c) < 32 for c in objet):
        raise ValueError("déclaration : identifiant objet exact requis")
    tolerances = declaration["tolerances_mm"]
    if (
        not isinstance(tolerances, dict)
        or set(tolerances) != {"fermeture_boucle", "classement_region", "alignement"}
        or any(not _nombre_fini(v) or v <= 0 or v > 10 for v in tolerances.values())
    ):
        raise ValueError("déclaration : tolérances positives et finies en mm requises")
    regions = declaration["regions"]
    if not isinstance(regions, list) or not regions or len(regions) > _MAX_REGIONS:
        raise ValueError("déclaration : nombre de régions hors limites")
    ids = set()
    total_sommets = 0
    normalisees = []
    for region in regions:
        if not isinstance(region, dict) or set(region) != {
            "id",
            "visibilite",
            "polygone_xy_mm",
            "z_min_mm",
            "z_max_mm",
        }:
            raise ValueError("région : clés attendues absentes ou inattendues")
        identifiant = region["id"]
        if (
            not isinstance(identifiant, str)
            or not identifiant
            or len(identifiant) > 128
            or any(ord(c) < 32 for c in identifiant)
            or identifiant in ids
        ):
            raise ValueError("région : identifiant vide, trop long ou dupliqué")
        ids.add(identifiant)
        if region["visibilite"] not in {"visible", "cachee"}:
            raise ValueError("région : visibilité explicite visible/cachee requise")
        polygone = region["polygone_xy_mm"]
        if not isinstance(polygone, list) or not 3 <= len(polygone) <= _MAX_SOMMETS:
            raise ValueError("région : polygone de 3 à 256 sommets requis")
        points = []
        for point in polygone:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or any(not _nombre_fini(v) or abs(v) > 1_000_000 for v in point)
            ):
                raise ValueError("région : sommet XY fini en mm requis")
            points.append([float(point[0]), float(point[1])])
        aire = _aire_signee(points)
        if (
            len({(p[0], p[1]) for p in points}) != len(points)
            or not math.isfinite(aire)
            or aire == 0
            or _auto_intersection(points)
        ):
            raise ValueError("région : polygone dégénéré ou auto-intersecté")
        total_sommets += len(points)
        if total_sommets > _MAX_SOMMETS:
            raise ValueError("déclaration : budget total de sommets dépassé")
        z_min, z_max = region["z_min_mm"], region["z_max_mm"]
        if (
            not _nombre_fini(z_min)
            or not _nombre_fini(z_max)
            or abs(z_min) > 1_000_000
            or abs(z_max) > 1_000_000
            or z_min >= z_max
        ):
            raise ValueError("région : intervalle Z invalide en mm")
        normalisees.append(
            {
                "id": identifiant,
                "visibilite": region["visibilite"],
                "polygone_xy_mm": points,
                "z_min_mm": float(z_min),
                "z_max_mm": float(z_max),
            }
        )
    return normalisees, {cle: float(valeur) for cle, valeur in tolerances.items()}


def _distance_segment_xy(point, a, b):
    """Retourne la distance XY minimale à un segment, en mm."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    norme = dx * dx + dy * dy
    t = 0.0 if norme == 0 else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / norme))
    return math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))


def _classement_region(point, regions, marge):
    """Classe un départ XY/Z dans un seul prisme déclaré, sinon conserve l’incertitude."""
    x, y, z = point
    candidats = []
    limite = False
    for region in regions:
        polygon = region["polygone_xy_mm"]
        distance = min(
            _distance_segment_xy((x, y), polygon[i], polygon[(i + 1) % len(polygon)]) for i in range(len(polygon))
        )
        dedans = False
        j = len(polygon) - 1
        for i, (xi, yi) in enumerate(polygon):
            xj, yj = polygon[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                dedans = not dedans
            j = i
        dans_z = region["z_min_mm"] < z < region["z_max_mm"]
        proche_z = min(abs(z - region["z_min_mm"]), abs(z - region["z_max_mm"])) <= marge
        if distance <= marge or proche_z:
            limite = True
        elif dedans and dans_z:
            candidats.append(region)
    if limite:
        return None, "frontiere_region"
    if len(candidats) == 1:
        return candidats[0], None
    if len(candidats) > 1:
        return None, "regions_chevauchantes"
    return None, "hors_region_declaree"


def _rapport_base(source_sha, plaque=None, objet=None):
    """Crée un résultat JSON stable ; une inconnue ne se transforme jamais en validation."""
    return {
        "schema": 1,
        "controle": "coutures_exterieures_declarees",
        "statut": "NON VÉRIFIÉ",
        "couverture_complete": False,
        "source_sha256": source_sha,
        "liaison_source": "NON VÉRIFIÉ",
        "unites": "mm",
        "repere": "coordonnees spatiales du G-code, régions déclarées par l’utilisateur",
        "plaque": plaque,
        "objet_id": objet,
        "regions": [],
        "reglages_couture_embarques": {
            "statut": "NON VÉRIFIÉ",
            "source_membre": "Metadata/project_settings.config",
            "valeurs_embarquees": {},
        },
        "boucles": [],
        "groupes_alignes": [],
        "inconnues": [],
        "limites": [
            "Analyse numérique seulement : aucun réglage n’est appliqué et aucune tenue physique n’est validée.",
            "Seules les boucles fermées Outer wall/Outer perimeter en G0/G1 à Z spatial constant sont classées.",
            "La visibilité provient exclusivement des régions explicites liées à la source par SHA-256.",
            "Budgets : 64 régions, 256 sommets, 2 000 boucles et 2 000 000 lignes G-code.",
            "Ligne G-code maximale : 1 Mio ; rapport maximal : 2 Mio.",
        ],
    }


def _finaliser_boucle(segments, rapport, regions, tol, numero_boucle):
    """Ajoute une boucle qualifiée ou une cause NON VÉRIFIÉE, sans interpoler sa trajectoire."""
    if not segments:
        return
    debut = segments[0]["debut"]
    fin = segments[-1]["fin"]
    z = debut[2]
    z_constant = all(s["debut"][2] == z and s["fin"][2] == z for s in segments)
    fermee = math.dist(debut, fin) <= tol["fermeture_boucle"]
    boucle = {
        "id": numero_boucle,
        "couche": segments[0]["couche"],
        "ligne_debut": segments[0]["ligne"],
        "ligne_fin": segments[-1]["ligne"],
        "z_mm": z,
        "point_depart_xyz_mm": list(debut),
        "point_fin_xyz_mm": list(fin),
        "longueur_deposee_mm": sum(s["longueur"] for s in segments),
        "fermee": fermee,
        "z_constant": z_constant,
        "region_id": None,
        "visibilite": "inconnue",
        "statut": "NON VÉRIFIÉ",
    }
    cause = None
    if not z_constant:
        cause = "z_spatial_variable"
    elif not fermee:
        cause = "boucle_non_fermee"
    elif segments[0]["couche"] < 0:
        cause = "couche_non_identifiee"
    else:
        region, cause = _classement_region(debut, regions, tol["classement_region"])
        if region is not None:
            boucle.update(region_id=region["id"], visibilite=region["visibilite"], statut="PASS")
    rapport["boucles"].append(boucle)
    if cause is not None:
        rapport["inconnues"].append(
            {"boucle_id": numero_boucle, "couche": boucle["couche"], "ligne": boucle["ligne_debut"], "cause": cause}
        )


def _former_groupes(rapport, tol_alignement):
    """Signale uniquement des départs visibles proches sur au moins deux couches distinctes."""
    candidats = [b for b in rapport["boucles"] if b["visibilite"] == "visible"]
    groupes = []
    affectes = set()
    for ancre in candidats:
        if ancre["id"] in affectes:
            continue
        proches = [
            b
            for b in candidats
            if b["region_id"] == ancre["region_id"]
            and math.dist(b["point_depart_xyz_mm"][:2], ancre["point_depart_xyz_mm"][:2]) <= tol_alignement
        ]
        couches = sorted({b["couche"] for b in proches})
        if len(couches) < 2:
            continue
        affectes.update(b["id"] for b in proches)
        groupe = {
            "id": f"alignement-{len(groupes) + 1}",
            "region_id": ancre["region_id"],
            "point_xy_mm": ancre["point_depart_xyz_mm"][:2],
            "boucles": [b["id"] for b in proches],
            "couches": couches,
            "statut": "WARN",
            "constat": "départs de boucles visibles alignés sur plusieurs couches distinctes",
        }
        groupes.append(groupe)
        # U31 (docs/reference/regles.md) : l’alignement décrit une couture potentielle, pas son effet mécanique.
        for boucle in proches:
            boucle["groupe_aligne_id"] = groupe["id"]
            boucle["statut"] = "WARN"
    return groupes


def analyser(source: str | Path, declaration: dict) -> dict:
    """Analyse une plaque/objet depuis un 3MF exact ; coordonnées et tolérances en mm.

    Entrées : chemin d’un 3MF régulier et déclaration JSON version 1. Sortie : rapport
    structuré PASS/WARN/NON VÉRIFIÉ, sans commande machine, écriture de source ou réglage
    appliqué. WARN est local aux départs visibles alignés sur au moins deux couches.
    Toute couverture incomplète reste explicitement NON VÉRIFIÉ. Les régions et trois
    réglages de couture embarqués éventuels sont restitués comme informations bornées.
    """
    rapport = _rapport_base(None)
    try:
        regions, tol = _valider_declaration(declaration)
        rapport["source_sha256_declare"] = declaration["source_sha256"]
        rapport["plaque"] = declaration["plaque"]
        rapport["objet_id"] = declaration["objet_id"]
        rapport["regions"] = regions
        with Archive3MF(source) as archive:
            empreinte = archive.sha256()
            rapport["source_sha256"] = empreinte
            if empreinte != declaration["source_sha256"]:
                raise ValueError("SHA-256 3MF différent de la déclaration")
            membres = dict(plaques_gcode_archive(archive))
            membre = membres.get(declaration["plaque"])
            if membre is None:
                raise ValueError("plaque déclarée absente du 3MF")
            if "Metadata/project_settings.config" in archive.noms():
                try:
                    reglages = archive.reglages("Metadata/project_settings.config")
                    reperes = ("seam_position", "wipe_on_loops", "wipe_before_external_loop")
                    if all(
                        cle not in reglages
                        or (
                            definition(cle, valeur=reglages[cle])["type"] == "chaine"
                            and isinstance(reglages[cle], str)
                            and len(reglages[cle]) <= 256
                        )
                        for cle in reperes
                    ):
                        rapport["reglages_couture_embarques"] = {
                            "statut": "INFORMATIF_BRUT",
                            "source_membre": "Metadata/project_settings.config",
                            "valeurs_embarquees": {cle: reglages[cle] for cle in reperes if cle in reglages},
                        }
                except (ValueError, KeyError, TypeError):
                    # Des options non lisibles n'annulent pas la preuve géométrique distincte.
                    pass
            analyseur = Interpreter(error_limit=64)
            segments = []
            ligne_count = 0
            num_boucle = 0
            saw_object = False
            saw_target_object = False
            target_object_starts = 0
            with ouvrir_membre_3mf(source, membre, archive=archive) as flux:
                while raw_line := flux.readline(_MAX_LIGNE + 1):
                    ligne_count += 1
                    if ligne_count > _MAX_LIGNES:
                        rapport["inconnues"].append({"cause": "budget_gcode_depasse", "ligne": ligne_count})
                        while flux.read(64 * 1024):
                            pass
                        break
                    if len(raw_line) > _MAX_LIGNE:
                        rapport["inconnues"].append({"cause": "budget_gcode_depasse", "ligne": ligne_count})
                        while chunk := flux.readline(64 * 1024):
                            if chunk.endswith(b"\n"):
                                break
                        continue
                    try:
                        ligne = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        rapport["inconnues"].append({"cause": "gcode_encodage_non_valide", "ligne": ligne_count})
                        continue
                    avant_objet = analyseur.object
                    mouvement = analyseur.feed_line(ligne)
                    if analyseur.object != avant_objet:
                        saw_object = True
                        if analyseur.object == declaration["objet_id"]:
                            saw_target_object = True
                            if avant_objet is None:
                                target_object_starts += 1
                        if segments:
                            num_boucle += 1
                            _finaliser_boucle(segments, rapport, regions, tol, num_boucle)
                            segments = []
                    qualifies = (
                        mouvement is not None
                        and isinstance(mouvement.get("type"), str)
                        and mouvement["type"].strip().lower() in _TYPES_PAROI
                        and mouvement.get("object") == declaration["objet_id"]
                        and mouvement.get("op") in {"G0", "G1"}
                        and mouvement.get("deposited_length", 0) > 0
                        and mouvement.get("deposition_start") is not None
                    )
                    if qualifies:
                        if segments and mouvement.get("layer") != segments[-1]["couche"]:
                            num_boucle += 1
                            _finaliser_boucle(segments, rapport, regions, tol, num_boucle)
                            segments = []
                        start = mouvement["deposition_start"]
                        segments.append(
                            {
                                "debut": tuple(start),
                                "fin": tuple(mouvement["spatial_end"]),
                                "longueur": float(mouvement["deposited_length"]),
                                "couche": mouvement["layer"],
                                "ligne": mouvement["line"],
                            }
                        )
                        if (
                            len(segments) >= 3
                            and math.dist(segments[0]["debut"], segments[-1]["fin"]) <= tol["fermeture_boucle"]
                        ):
                            num_boucle += 1
                            _finaliser_boucle(segments, rapport, regions, tol, num_boucle)
                            segments = []
                    elif segments:
                        num_boucle += 1
                        _finaliser_boucle(segments, rapport, regions, tol, num_boucle)
                        segments = []
                    if len(rapport["boucles"]) >= _MAX_BOUCLES:
                        rapport["inconnues"].append({"cause": "budget_boucles_depasse", "ligne": ligne_count})
                        while flux.read(64 * 1024):
                            pass
                        break
            if segments:
                num_boucle += 1
                _finaliser_boucle(segments, rapport, regions, tol, num_boucle)
            if analyseur.errors:
                rapport["inconnues"].extend(
                    {"cause": "gcode_non_couvert", "detail": erreur[:256]} for erreur in analyseur.errors[:64]
                )
            if not saw_object or not saw_target_object:
                rapport["inconnues"].append({"cause": "objet_gcode_non_identifie"})
            if not rapport["boucles"]:
                rapport["inconnues"].append({"cause": "boucle_exterieure_absente_ou_non_reconnue"})
            if target_object_starts > 1:
                rapport["inconnues"].append(
                    {"cause": "identite_objet_repetee_ou_ambigue", "occurrences": target_object_starts}
                )
            archive.verifier_source(source)
        rapport["liaison_source"] = "PASS"
        if target_object_starts == 1:
            rapport["groupes_alignes"] = _former_groupes(rapport, tol["alignement"])
        rapport["couverture_complete"] = not rapport["inconnues"] and bool(rapport["boucles"])
        if rapport["groupes_alignes"]:
            rapport["statut"] = "WARN"
        elif rapport["couverture_complete"]:
            rapport["statut"] = "PASS"
        if len(json.dumps(rapport, ensure_ascii=False, allow_nan=False).encode("utf-8")) > _MAX_RAPPORT_OCTETS:
            rapport["boucles"] = []
            rapport["groupes_alignes"] = []
            rapport["couverture_complete"] = False
            rapport["statut"] = "NON VÉRIFIÉ"
            rapport["inconnues"].append({"cause": "budget_rapport_depasse", "details_omis": "boucles et groupes omis"})
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        zipfile.BadZipFile,
        EOFError,
        zlib.error,
    ) as erreur:
        detail = str(erreur)[:256]
        cause = (
            "budget_declaration_depasse"
            if "budget" in detail or "nombre de régions" in detail
            else "source_sha_different"
            if "SHA-256" in detail
            else "source_ou_declaration_invalide"
        )
        rapport["inconnues"].append({"cause": cause, "detail": detail})
    return rapport
