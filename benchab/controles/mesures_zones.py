"""Recalcule des écarts signés déclarés entre cordons linéaires du G-code.

La projection est faite dans le repère XY du G-code sur l’axe unitaire local donné.
Les enveloppes projetées utilisent la demi-largeur fiable déclarée par `;WIDTH:`.
Ce calcul de fabrication numérique ne prouve ni contact, ni adhérence, ni appui physique.
"""

import hashlib
import math

from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import lire_membre_3mf, membres_gcode_3mf

ARRONDI_BBOX_MM = 0.0005


def verifier_mesures_declarees(tranche, zones, declarations):
    """Vérifie les déclarations liées à un G-code de plaque et ses empreintes.

    Les déclarations sont des objets portant zone_id, plaque, gcode_sha256 et
    les champs de ``recalculer_mesure``. Renvoie les résultats par zone ; une
    contradiction ou une source périmée est FAIL, une mesure incomplète reste
    NON VÉRIFIÉE. La lecture de l’archive ne modifie aucun réglage.
    """
    index = {zone.get("id"): zone for zone in zones}
    sorties = []
    if not isinstance(declarations, list):
        return [{"statut": "NON VÉRIFIÉ", "erreur": "mesures_recul_recouvrement doit être une liste"}]
    try:
        membres = set(membres_gcode_3mf(tranche))
    except (OSError, ValueError, KeyError, TypeError):
        return [
            {"zone_id": item.get("zone_id") if isinstance(item, dict) else None,
             "statut": "NON VÉRIFIÉ", "erreur": "archive G-code illisible"}
            for item in declarations
        ] or [{"statut": "NON VÉRIFIÉ", "erreur": "archive G-code illisible"}]

    for declaration in declarations:
        if not isinstance(declaration, dict):
            sorties.append({"statut": "NON VÉRIFIÉ", "erreur": "mesure déclarée non objet"})
            continue
        zone_id = declaration.get("zone_id")
        plaque = declaration.get("plaque")
        if not isinstance(zone_id, str) or not zone_id or type(plaque) is not int or plaque < 1:
            sorties.append({"zone_id": zone_id if isinstance(zone_id, str) else None,
                            "statut": "NON VÉRIFIÉ", "erreur": "zone_id/plaque invalide"})
            continue
        membre = f"Metadata/plate_{plaque}.gcode"
        zone = index.get(zone_id)
        if zone is None:
            sorties.append({"zone_id": zone_id, "statut": "NON VÉRIFIÉ", "erreur": "zone inconnue"})
            continue
        if zone.get("plaque") != plaque or membre not in membres:
            sorties.append({"zone_id": zone_id, "statut": "NON VÉRIFIÉ", "erreur": "zone ou plaque absente"})
            continue
        try:
            source = lire_membre_3mf(tranche, membre)
            empreinte = hashlib.sha256(source).hexdigest()
            if declaration.get("gcode_sha256") != empreinte:
                sorties.append({"zone_id": zone_id, "statut": "FAIL", "erreur": "SHA G-code contradictoire"})
                continue
            try:
                texte = source.decode("utf-8")
            except UnicodeDecodeError:
                sorties.append({"zone_id": zone_id, "statut": "NON VÉRIFIÉ", "erreur": "G-code non UTF-8"})
                continue
            mesure = recalculer_mesure(texte, declaration)
            if mesure.get("statut") == "PASS":
                cible = mesure.get("cible", {})
                source_info = mesure.get("source", {})
                bbox = _bbox(zone.get("bbox_xy_mm"))
                if zone.get("objet_ambigu"):
                    mesure = {"statut": "NON VÉRIFIÉ", "erreur": "identité d’objet de la zone ambiguë"}
                elif bbox is None or "objet" not in zone:
                    mesure = {"statut": "NON VÉRIFIÉ", "erreur": "bbox ou objet de zone absent"}
                elif (
                    cible.get("type") != zone.get("type")
                    or not _segment_dans_bbox(cible["debut"], cible["fin"], bbox)
                ):
                    mesure = {"statut": "NON VÉRIFIÉ", "erreur": "ligne cible hors de la bbox ou du type déclaré"}
                elif cible.get("objet") != zone.get("objet"):
                    mesure = {"statut": "NON VÉRIFIÉ", "erreur": "objet cible différent de la zone"}
                elif mesure.get("objets_declares") and (
                    source_info.get("objet") is None or source_info.get("objet") != cible.get("objet")
                ):
                    mesure = {"statut": "NON VÉRIFIÉ", "erreur": "objet source différent ou inconnu"}
                elif not _projections_orthogonales_se_couvrent(source_info, cible, mesure["axe_local_unitaire"]):
                    mesure = {"statut": "NON VÉRIFIÉ", "erreur": "cordons hors du voisinage local orthogonal"}
            sorties.append({"zone_id": zone_id, "plaque": plaque, **mesure})
        except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError):
            sorties.append({"zone_id": zone_id, "plaque": plaque, "statut": "NON VÉRIFIÉ",
                            "erreur": "mesure ou membre G-code illisible"})
    return sorties


def _segments(gcode):
    """Indexe les cordons linéaires par numéro de ligne physique et couche G-code."""
    it = Interpreter()
    largeur = None
    segments = {}
    objets_declares = False
    for numero, ligne in enumerate(gcode.splitlines(), 1):
        if ligne.startswith(("EXCLUDE_OBJECT_DEFINE", "EXCLUDE_OBJECT_START")):
            objets_declares = True
        mouvement = it.feed_line(ligne)
        if ligne.startswith(";WIDTH:"):
            try:
                largeur = float(ligne.split(":", 1)[1])
                if not math.isfinite(largeur) or largeur <= 0:
                    largeur = None
            except ValueError:
                largeur = None
        if not mouvement or mouvement["op"] not in {"G0", "G1"} or mouvement["deposited"] <= 0:
            continue
        debut = mouvement["deposition_start"] or mouvement["start"]
        fin = mouvement["end"]
        if math.hypot(fin[0] - debut[0], fin[1] - debut[1]) <= 1e-12:
            continue
        segments[numero] = {
            "debut": debut[:2],
            "fin": fin[:2],
            "largeur_mm": largeur,
            "couche": it.layer,
            "type": mouvement["type"],
            "objet": mouvement["object"],
        }
    return segments, it.errors, objets_declares


def _bbox(valeur):
    """Valide une bbox XY en mm et conserve le contour des lignes centrales."""
    if not isinstance(valeur, list) or len(valeur) != 4:
        return None
    try:
        x0, y0, x1, y1 = map(float, valeur)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 < x0 or y1 < y0:
        return None
    return x0, y0, x1, y1


def _segment_dans_bbox(debut, fin, bbox):
    """Vérifie la ligne centrale dans la bbox XY, tolérant son arrondi au 0,001 mm."""
    x0, y0, x1, y1 = bbox
    marge = ARRONDI_BBOX_MM
    return all(x0 - marge <= x <= x1 + marge and y0 - marge <= y <= y1 + marge for x, y in (debut, fin))


def _projections_orthogonales_se_couvrent(source, cible, axe):
    """Vérifie le voisinage local des enveloppes sur l’axe perpendiculaire déclaré."""
    if not all(key in source and key in cible for key in ("debut", "fin", "largeur_mm")):
        return False
    ax, ay = axe
    ox, oy = -ay, ax

    def intervalle(segment):
        valeurs = [x * ox + y * oy for x, y in (segment["debut"], segment["fin"])]
        demi = segment["largeur_mm"] / 2
        return min(valeurs) - demi, max(valeurs) + demi

    a, b = intervalle(source), intervalle(cible)
    return max(a[0], b[0]) <= min(a[1], b[1]) + 1e-9


def recalculer_mesure(gcode, declaration):
    """Recalcule l’écart signé déclaré entre deux lignes d’extrusion en mm.

    La déclaration exige les numéros de ligne source/cible, leurs couches, un axe
    local XY, la valeur attendue et une tolérance en mm. Renvoie PASS si l’écart
    correspond, FAIL en contradiction et NON VÉRIFIÉ si couverture insuffisante.
    Aucune commande ni donnée machine n’est modifiée.
    """
    try:
        ligne_source = declaration["ligne_source"]
        ligne_cible = declaration["ligne_cible"]
        source_couche = declaration["couche_source"]
        cible_couche = declaration["couche_cible"]
        axe = declaration["axe_local"]
        attendu = declaration["valeur_declaree_mm"]
        tolerance = declaration["tolerance_mm"]
        if any(type(v) is not int or v < 1 for v in (ligne_source, ligne_cible)):
            raise ValueError
        if any(type(v) is not int or v < 0 for v in (source_couche, cible_couche)):
            raise ValueError
        if not isinstance(axe, list) or len(axe) != 2:
            raise ValueError
        ax, ay = map(float, axe)
        norme = math.hypot(ax, ay)
        if not math.isfinite(norme) or norme <= 1e-12:
            raise ValueError
        ax, ay = ax / norme, ay / norme
        if any(not math.isfinite(float(v)) for v in (attendu, tolerance)) or tolerance < 0:
            raise ValueError
        attendu, tolerance = float(attendu), float(tolerance)
        if not isinstance(gcode, str) or not gcode:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        return {"statut": "NON VÉRIFIÉ", "erreur": "déclaration absente ou invalide"}

    segments, erreurs, objets_declares = _segments(gcode)
    source, cible = segments.get(ligne_source), segments.get(ligne_cible)
    if (erreurs or source is None or cible is None or source["largeur_mm"] is None
        or cible["largeur_mm"] is None or source["largeur_mm"] <= 0 or cible["largeur_mm"] <= 0
        or source["couche"] != source_couche or cible["couche"] != cible_couche
        or cible_couche <= source_couche):
        return {"statut": "NON VÉRIFIÉ", "erreur": "cordons, largeurs ou couches non vérifiables"}

    def intervalle(segment):
        projections = [x * ax + y * ay for x, y in (segment["debut"], segment["fin"])]
        demi = segment["largeur_mm"] / 2
        return min(projections) - demi, max(projections) + demi

    source_intervalle, cible_intervalle = intervalle(source), intervalle(cible)
    mesure = cible_intervalle[0] - source_intervalle[1]
    statut = "PASS" if abs(mesure - attendu) <= tolerance else "FAIL"
    return {
        "statut": statut,
        "methode": "ecart_signe_projection_enveloppes_cordons_lineaires",
        "repere": "XY plateau du G-code; axe local unitaire orienté source-vers-cible",
        "unite": "mm",
        "convention": "positif=recul; negatif=recouvrement",
        "source": {
            "ligne": ligne_source,
            "couche": source_couche,
            "largeur_mm": source["largeur_mm"],
            "objet": source["objet"],
            "debut": source["debut"],
            "fin": source["fin"],
        },
        "cible": {
            "ligne": ligne_cible,
            "couche": cible_couche,
            "largeur_mm": cible["largeur_mm"],
            "type": cible["type"],
            "objet": cible["objet"],
            "debut": cible["debut"],
            "fin": cible["fin"],
        },
        "objets_declares": objets_declares,
        "axe_local_unitaire": [ax, ay],
        "recalculee_mm": round(mesure, 9),
        "declaree_mm": attendu,
        "tolerance_mm": tolerance,
    }
