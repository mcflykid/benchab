"""Compare les axes de pont déposés à la projection d’un îlot horizontal déclaré."""

import argparse
import io
import json
import math
import re
import sys
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.controles.surplombs_connexes import _lire_source_stl, cartographier
from benchab.gcode.lecture import plaques_gcode_archive
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

_ROLES = ("Bridge", "Internal Bridge")
_MAX_TRIANGLES = 2048
_MAX_LIGNES = 2_000_000
_MAX_LIGNE = 16_384
_MAX_GCODE_OCTETS = 64 * 1024 * 1024
_TOLERANCE_PLAN_MM = 1e-6


def _projection(triangles_mm, translation_mm, rotation_z_deg):
    """Prépare une projection XY en mm² d’un îlot horizontal orienté vers le bas."""
    try:
        triangles = np.asarray(triangles_mm, dtype=float)
        translation = np.asarray(translation_mm, dtype=float)
    except (TypeError, ValueError, OverflowError) as erreur:
        raise ValueError("Triangles et translation numériques explicites requis.") from erreur
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not 1 <= len(triangles) <= _MAX_TRIANGLES:
        raise ValueError("Déclarer de 1 à 2 048 triangles XYZ en mm.")
    if translation.shape != (3,) or not np.isfinite(translation).all() or not np.isfinite(triangles).all():
        raise ValueError("Triangles et translation XYZ finis requis.")
    if np.max(np.abs(triangles)) > 100_000 or np.max(np.abs(translation)) > 100_000:
        raise ValueError("Cotes hors bornes : ±100 000 mm maximum.")
    if rotation_z_deg is not None:
        angle = math.radians(rotation_z_deg)
        cosinus, sinus = math.cos(angle), math.sin(angle)
        # Garder les arêtes sur leurs axes aux quarts de tour (#93).
        if rotation_z_deg % 90 == 0:
            cosinus, sinus = round(cosinus), round(sinus)
        rotation = np.array([[cosinus, sinus], [-sinus, cosinus]])
        triangles = triangles.copy()
        triangles[:, :, :2] = triangles[:, :, :2] @ rotation
    places = triangles + translation
    z = places[:, :, 2]
    if float(np.ptp(z)) > _TOLERANCE_PLAN_MM:
        raise ValueError("Seul un îlot horizontal est qualifié.")
    normales = np.cross(places[:, 1] - places[:, 0], places[:, 2] - places[:, 0])
    if np.any(normales[:, 2] >= 0):
        raise ValueError("Les triangles doivent avoir une aire positive et être orientés vers le bas.")
    projection = unary_union([Polygon(t[:, :2]) for t in places])
    if projection.is_empty or not projection.is_valid or not math.isfinite(projection.area):
        raise ValueError("Projection XY de l’îlot non qualifiée.")
    return projection, float((z.min() + z.max()) / 2), translation.tolist()


def rapprocher(triangles_mm, lignes, *, translation_mm, rotation_z_deg=None, tolerance_z_mm=0.001, objet_id=None):
    """Compare les dépôts linéaires de pont à un îlot horizontal en mm.

    La rotation facultative autour de Z, en degrés, précède la translation
    XYZ en mm. L’appelant déclare les deux transformations. Le
    rapport mesure les intersections d’axes XY et le niveau nominal
    Z − HEIGHT en mm ; PASS signifie que cette comparaison est couverte,
    NON VÉRIFIÉ qu’une trajectoire ou une donnée manque. Avec objet_id,
    mesure seulement les axes dans les blocs G-code portant ce nom exact.
    Le nom déclaré n’authentifie pas le lien STL/objet. Aucun appui,
    largeur de cordon ou tenue ne sont mesurés. Aucune action machine.
    """
    if type(tolerance_z_mm) not in (float, int) or not 0.000001 <= tolerance_z_mm <= 0.05:
        raise ValueError("Tolérance Z finie entre 0,000001 et 0,05 mm requise.")
    placement = {}
    if rotation_z_deg is not None:
        if type(rotation_z_deg) not in (float, int) or not -360 <= rotation_z_deg <= 360:
            raise ValueError("Rotation Z finie entre −360 et 360 degrés requise.")
        placement["rotation_z_declaree_deg"] = rotation_z_deg
    projection, altitude, translation = _projection(triangles_mm, translation_mm, rotation_z_deg)
    roles = {nom: {
        "segments_lus": 0, "segments_projection_xy": 0, "segments_niveau_concordant": 0,
        "longueur_projection_xy_mm": 0.0, "longueur_niveau_concordant_mm": 0.0,
    } for nom in _ROLES}
    exclusions = {
        "hauteur_non_verifiee": 0, "role_non_verifie": 0, "trajet_non_lineaire_ou_incline": 0,
        "budget_lignes": 0, "ligne_non_verifiee": 0,
    }
    segments_autre_objet = 0
    if objet_id is not None:
        if (
            not isinstance(objet_id, str) or not 1 <= len(objet_id) <= 256
            or any(c.isspace() or ord(c) < 32 for c in objet_id)
        ):
            raise ValueError("Identifiant objet G-code exact requis.")
        exclusions.update(objet_non_attribue=0, objet_non_clos=0)
    lecteur = Interpreter(error_limit=16)
    hauteur = niveau_hauteur = None
    role_declare = None
    for numero, ligne in enumerate(lignes, 1):
        if numero > _MAX_LIGNES:
            exclusions["budget_lignes"] = 1
            break
        if not isinstance(ligne, str) or len(ligne) > _MAX_LIGNE:
            exclusions["ligne_non_verifiee"] = 1
            break
        texte = ligne.strip()
        if texte.startswith(";LAYER_CHANGE"):
            hauteur, niveau_hauteur, role_declare = None, None, None
        if texte.startswith(";TYPE:"):
            role_declare = texte.removeprefix(";TYPE:").strip()
        if texte.startswith(";HEIGHT:"):
            # Interpreter conserve sa dernière métadonnée entre les couches ;
            # ici le niveau d’un pont exige une hauteur explicite de sa couche.
            try:
                annoncee = float(texte.removeprefix(";HEIGHT:"))
            except ValueError:
                annoncee = math.nan
            hauteur = annoncee if math.isfinite(annoncee) and 0 < annoncee <= 2 else None
            niveau_hauteur = None
        mouvement = lecteur.feed_line(ligne)
        if not mouvement or mouvement["deposited_length"] <= 0:
            continue
        if hauteur is not None:
            # Sans nouveau HEIGHT, une autre altitude de dépôt ne permet pas
            # de transporter la hauteur déclarée à un autre niveau.
            niveau = mouvement["end"][2]
            if niveau_hauteur is None:
                niveau_hauteur = niveau
            elif abs(niveau - niveau_hauteur) > _TOLERANCE_PLAN_MM:
                hauteur = None
        role = mouvement["type"]
        if role not in roles:
            continue
        if objet_id is not None and mouvement["object"] != objet_id:
            if mouvement["object"] is None:
                exclusions["objet_non_attribue"] += 1
            else:
                segments_autre_objet += 1
            continue
        mesures = roles[role]
        mesures["segments_lus"] += 1
        if role_declare != role:
            exclusions["role_non_verifie"] += 1
            continue
        a, b = mouvement["deposition_start"], mouvement["end"]
        if mouvement["trajectory"] != "lineaire" or a is None or abs(a[2] - b[2]) > _TOLERANCE_PLAN_MM:
            exclusions["trajet_non_lineaire_ou_incline"] += 1
            continue
        longueur = projection.intersection(LineString((a[:2], b[:2]))).length
        if longueur <= 0:
            continue
        mesures["segments_projection_xy"] += 1
        mesures["longueur_projection_xy_mm"] += longueur
        if hauteur is None:
            exclusions["hauteur_non_verifiee"] += 1
            continue
        if abs((a[2] + b[2]) / 2 - hauteur - altitude) <= tolerance_z_mm:
            mesures["segments_niveau_concordant"] += 1
            mesures["longueur_niveau_concordant_mm"] += longueur

    portee_objet = {}
    if objet_id is not None:
        # Les marqueurs EXCLUDE_OBJECT délimitent la preuve ; un bloc ouvert ne la qualifie pas (#93).
        exclusions["objet_non_clos"] = int(lecteur.object is not None)
        portee_objet = {"objet_id_declare": objet_id, "segments_autre_objet": segments_autre_objet}
    verifie = not any(exclusions.values()) and not lecteur.errors and any(
        m["segments_lus"] for m in roles.values()
    )
    correspondance = any(m["segments_niveau_concordant"] for m in roles.values())
    for mesures in roles.values():
        for cle in ("longueur_projection_xy_mm", "longueur_niveau_concordant_mm"):
            mesures[cle] = round(mesures[cle], 6)
    return {
        "statut": "PASS" if verifie else "NON VÉRIFIÉ",
        "association": (
            ("CONCORDANCE MESURÉE" if correspondance else "AUCUNE CONCORDANCE MESURÉE") if verifie
            else "NON VÉRIFIÉE"
        ),
        "translation_declaree_mm": translation,
        **placement,
        **portee_objet,
        "altitude_ilot_mm": round(altitude, 6),
        "tolerance_z_mm": tolerance_z_mm,
        "projection_mm2": round(projection.area, 6),
        "roles": roles,
        "exclusions": exclusions,
        "erreurs_interpreteur": lecteur.errors,
        "validation_physique": False,
        "limite": (
            "Axes G-code seulement ; placement déclaré et dessous nominal Z − HEIGHT, aucun appui établi."
            if placement
            else "Axes G-code seulement ; translation déclarée et dessous nominal Z − HEIGHT, aucun appui établi."
        ),
    }


def _lignes(flux):
    """Décode des lignes UTF-8 sans matérialiser un G-code ni couper une longue ligne."""
    while brut := flux.readline(_MAX_LIGNE + 1):
        if len(brut) > _MAX_LIGNE:
            yield None
            return
        yield brut.decode("utf-8")


def _rapport_fichiers(args):
    """Lie le maillage et la plaque relus aux deux SHA déclarés, sans réécriture."""
    if not args.unite_mm or args.source.suffix.lower() != ".stl" or args.tranche.suffix.lower() != ".3mf":
        raise ValueError("STL déclaré en mm et archive tranchée 3MF requis.")
    if not 1 <= args.plaque <= 1000:
        raise ValueError("Numéro de plaque de 1 à 1 000 requis.")
    if any(re.fullmatch(r"[0-9a-f]{64}", v) is None for v in (args.sha_stl, args.sha_3mf)):
        raise ValueError("Les deux empreintes SHA-256 exactes sont requises.")
    contenu, sha_stl, nombre_faces = _lire_source_stl(args.source)
    if sha_stl != args.sha_stl:
        raise ValueError("Empreinte du STL différente de la déclaration.")
    mesh = trimesh.load_mesh(io.BytesIO(contenu), file_type="stl", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) != nombre_faces:
        raise ValueError("Nombre de triangles décodés différent du STL vérifié.")
    carte = cartographier(
        mesh, source=args.source.name, unite="mm", source_sha256=sha_stl,
        angle_max_deg=args.angle_max_deg, groupe_indices=args.ilot,
    )
    groupe = next((g for g in carte["groupes"] if g["id"] == args.ilot), None)
    if groupe is None:
        raise ValueError("Îlot absent de la carte pour le seuil déclaré.")
    triangles = mesh.triangles[groupe["indices_faces"]]
    with Archive3MF(args.tranche) as archive:
        sha_3mf = archive.sha256()
        if sha_3mf != args.sha_3mf:
            raise ValueError("Empreinte du 3MF différente de la déclaration.")
        membre = dict(plaques_gcode_archive(archive)).get(args.plaque)
        if membre is None or archive.taille_membre(membre) > _MAX_GCODE_OCTETS:
            raise ValueError("Plaque absente ou G-code de plus de 64 Mio.")
        with archive.ouvrir_flux(membre) as flux:
            resultat = rapprocher(
                triangles, _lignes(flux), translation_mm=args.translation_mm, rotation_z_deg=args.rotation_z_deg,
                objet_id=args.objet_id,
            )
        archive.verifier_source(args.tranche)
    return {
        **resultat, "source_sha256": sha_stl, "archive_sha256": sha_3mf, "plaque": args.plaque,
        "membre_gcode": membre, "ilot": args.ilot, "angle_max_deg": args.angle_max_deg,
    }


def main(argv=None, *, prive=None):
    """Rend une comparaison nominale en mm, facultativement dans un rapport privé neuf.

    Codes 0 si comparaison couverte, 2 si refus ou NON VÉRIFIÉ. Les deux SHA
    et le placement (translation en mm, rotation Z facultative en degrés)
    sont déclarés, ainsi que le nom G-code de l’objet si demandé.
    Aucun tranchage ou réglage modifié.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="STL exact de l’îlot")
    parser.add_argument("tranche", type=Path, help="3MF tranché exact")
    parser.add_argument("--unite-mm", action="store_true")
    parser.add_argument("--ilot", required=True, help="Identifiant de surplombs-connexes")
    parser.add_argument("--angle-max-deg", type=float, default=50.0)
    parser.add_argument("--plaque", type=int, required=True)
    parser.add_argument("--objet-id", help="Nom exact déclaré par EXCLUDE_OBJECT_START dans le G-code")
    parser.add_argument("--translation-mm", nargs=3, type=float, required=True)
    parser.add_argument("--rotation-z-deg", type=float, help="Rotation autour de l’origine du STL, avant translation")
    parser.add_argument("--sha-stl", required=True)
    parser.add_argument("--sha-3mf", required=True)
    parser.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    args = parser.parse_args(argv)
    try:
        resultat = _rapport_fichiers(args)
        if args.sortie is not None:
            cible = destination_neuve(args.sortie, prive=prive, temporaire=False)
            ecrire_json_neuf(cible, resultat, entrees=[args.source, args.tranche])
            affiche = {
                "statut": resultat["statut"], "association": resultat["association"],
                "ilot": args.ilot, "plaque": args.plaque, "rapport": str(cible),
            }
            if args.objet_id is not None:
                affiche["objet_id_declare"] = args.objet_id
        else:
            affiche = resultat
        print(json.dumps(affiche, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if resultat["statut"] == "PASS" else 2
    except (BadZipFile, KeyError, OSError, ValueError, RuntimeError) as erreur:
        print(f"BENCHAB : comparaison des ponts refusée : {erreur}", file=sys.stderr)
        return 2
