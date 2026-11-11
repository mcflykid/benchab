"""Compare les axes de pont déposés à la projection d’un îlot horizontal déclaré."""

import math

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from benchab.controles.gcode_interpreter import Interpreter

_ROLES = ("Bridge", "Internal Bridge")
_MAX_TRIANGLES = 2048
_MAX_LIGNES = 2_000_000
_MAX_LIGNE = 16_384
_TOLERANCE_PLAN_MM = 1e-6


def _projection(triangles_mm, translation_mm):
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


def rapprocher(triangles_mm, lignes, *, translation_mm, tolerance_z_mm=0.001):
    """Compare les dépôts linéaires de pont à un îlot horizontal en mm.

    La translation XYZ est déclarée par l’appelant, jamais inférée. Le
    rapport mesure les intersections d’axes XY et le niveau nominal
    Z − HEIGHT en mm ; PASS signifie que cette comparaison est couverte,
    NON VÉRIFIÉ qu’une trajectoire ou une donnée manque. Ne mesure ni la
    largeur des cordons, ni leur appui, ni la tenue ; aucune action machine.
    """
    if type(tolerance_z_mm) not in (float, int) or not 0.000001 <= tolerance_z_mm <= 0.05:
        raise ValueError("Tolérance Z finie entre 0,000001 et 0,05 mm requise.")
    projection, altitude, translation = _projection(triangles_mm, translation_mm)
    roles = {nom: {
        "segments_lus": 0, "segments_projection_xy": 0, "segments_niveau_concordant": 0,
        "longueur_projection_xy_mm": 0.0, "longueur_niveau_concordant_mm": 0.0,
    } for nom in _ROLES}
    exclusions = {
        "hauteur_non_verifiee": 0, "role_non_verifie": 0, "trajet_non_lineaire_ou_incline": 0,
        "budget_lignes": 0, "ligne_non_verifiee": 0,
    }
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
        "altitude_ilot_mm": round(altitude, 6),
        "tolerance_z_mm": tolerance_z_mm,
        "projection_mm2": round(projection.area, 6),
        "roles": roles,
        "exclusions": exclusions,
        "erreurs_interpreteur": lecteur.errors,
        "validation_physique": False,
        "limite": "Axes G-code seulement ; translation déclarée et dessous nominal Z − HEIGHT, aucun appui établi.",
    }
