"""Estime le voisinage géométrique entre cordons G-code échantillonnés.

Chaque point est ``(x, y, demi-largeur)`` en millimètres. L'outil évalue un
recouvrement nominal dans le plan ; il ne simule ni déformation, ni adhérence,
ni tenue physique. Les sources de cordons peuvent représenter une ou plusieurs
couches antérieures, selon le contrôle paramétré qui a construit les tableaux.
"""

import argparse
import json
import math
import sys
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
from scipy.spatial import cKDTree

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.geometrie_arc import enveloppe_deposee_xyz
from benchab.gcode.lecture import analyser_membre_gcode_3mf, plaques_gcode_archive

PAS_DEFAUT_MM = 0.15
RECOUVREMENT_DEFAUT_MM = 0.05
PORTEE_LIBRE_DEFAUT_MM = 10.0
MAX_SEGMENTS = 250_000
MAX_ECHANTILLONS = 100_000_000
MAX_ECHANTILLONS_ARC = 1_000_000
PIECE = {
    "Outer wall",
    "Inner wall",
    "Overhang wall",
    "Bottom surface",
    "Top surface",
    "Internal solid infill",
    "Sparse infill",
    "Gap infill",
    "Bridge",
    "Internal Bridge",
    "Solid infill",
}
SUPPORTS = {"Support", "Support interface"}


def mesurer_appui(cibles, supports, *, recouvrement_min_mm=0.05, pas_mm=0.15, voisins=4):
    """Mesure le meilleur recouvrement échantillonné de chaque point cible.

    ``supports`` accepte un tableau ou une séquence de tableaux; les sources
    sont réunies avant le calcul. Les lignes invalides, seuils non positifs et
    ensembles vides sont refusés pour éviter un résultat présenté comme mesuré.
    """
    c = _points(cibles, "cibles")
    sources = _sources(supports)
    if not math.isfinite(recouvrement_min_mm) or recouvrement_min_mm < 0:
        raise ValueError("recouvrement minimal fini et positif ou nul requis")
    if not math.isfinite(pas_mm) or pas_mm <= 0:
        raise ValueError("pas d'échantillonnage fini strictement positif requis")
    if not isinstance(voisins, int) or voisins < 1:
        raise ValueError("nombre de voisins entier strictement positif requis")

    nuage = _NuageAppui(np.concatenate([_points(source, "supports") for source in sources], axis=0))
    return _evaluer_nuages(c, [nuage], recouvrement_min_mm, pas_mm, voisins, 10.0)


def evaluer_parcours(
    points,
    supports_precedents,
    *,
    recouvrement_min_mm=0.0,
    longueur_libre_max_mm=10.0,
    pas_mm=0.15,
    sur_plateau=False,
    voisins=4,
    distances_mm=None,
):
    """Évalue l'ancrage et la portée libre d'un parcours de pièce/support.

    Chaque parcours est évalué séparément pour éviter de relier implicitement
    deux extrusions disjointes. Les supports précédents peuvent réunir la
    couche précédente et les parcours antérieurs déjà qualifiés. L'option
    ``distances_mm`` fournit une distance de parcours nominale par échantillon
    (mm), avec zéro au premier point, pour préserver la longueur analytique des arcs.
    """
    c = _points(points, "parcours")
    if not math.isfinite(longueur_libre_max_mm) or longueur_libre_max_mm < 0:
        raise ValueError("longueur libre maximale finie et positive ou nulle requise")
    sources = _sources(supports_precedents)
    nuages = [s if isinstance(s, _NuageAppui) else _NuageAppui(_points(s, "supports")) for s in sources]
    if distances_mm is not None:
        distances_mm = np.asarray(distances_mm, dtype=float)
        if (
            distances_mm.shape != (len(c),) or not np.isfinite(distances_mm).all()
            or np.any(distances_mm < 0) or distances_mm[0] != 0
            or np.any(distances_mm[1:] > pas_mm * 1.02)
        ):
            raise ValueError(
                "distances nominales (n,) en mm, finies, positives ou nulles et commençant à zéro requises"
            )
    return _evaluer_nuages(
        c, nuages, recouvrement_min_mm, pas_mm, voisins, longueur_libre_max_mm,
        sur_plateau, distances_mm,
    )


def _echantillonner_arc(mouvement, pas_mm):
    """Échantillonne un dépôt G17 planaire sur le cercle exact, en millimètres.

    Entrée : mouvement analytique de l'interpréteur et pas maximal XY en mm.
    Sortie : points (X, Y, demi-largeur) exacts aux paramètres choisis et
    distances XY de chaque intervalle, en mm. Échec : ValueError si hélice,
    géométrie absente/invalide ou budget de 1 000 000 points dépassé.
    """
    if not math.isfinite(pas_mm) or pas_mm <= 0:
        raise ValueError("pas d'échantillonnage fini strictement positif requis")
    try:
        arc = mouvement["arc_g17"]
        start, end = mouvement["start"], mouvement["end"]
        centre_x, centre_y = map(float, arc["centre_xy_mm"])
        rayon = float(arc["rayon_mm"])
        angle = float(arc["angle_debut_rad"])
        balayage = float(arc["balayage_signe_rad"])
        debut = float(arc["debut_depot_fraction"])
        largeur = float(mouvement["metadata"]["WIDTH"])
        longueur_deposee = float(mouvement["deposited_length"])
    except (KeyError, TypeError, ValueError, OverflowError) as erreur:
        raise ValueError("géométrie ou largeur d'arc G17 absente") from erreur
    enveloppe_deposee_xyz(mouvement)
    valeurs = (centre_x, centre_y, rayon, angle, balayage, debut, largeur, longueur_deposee)
    if (
        not all(math.isfinite(valeur) for valeur in valeurs)
        or rayon <= 0 or balayage == 0 or not 0 <= debut < 1
        or not 0 < largeur <= 10 or longueur_deposee <= 0
        or start[2] != end[2]
    ):
        raise ValueError("arc hélicoïdal, arrondi ou dépôt G17 non qualifié")
    tolerance_xy = 8 * math.ulp(max(1.0, abs(start[0]), abs(start[1]), abs(end[0]), abs(end[1]), rayon))
    debut_calcule = (centre_x + rayon * math.cos(angle), centre_y + rayon * math.sin(angle))
    fin_calcule = (
        centre_x + rayon * math.cos(angle + balayage),
        centre_y + rayon * math.sin(angle + balayage),
    )
    if (
        math.dist(debut_calcule, start[:2]) > tolerance_xy
        or math.dist(fin_calcule, end[:2]) > tolerance_xy
    ):
        raise ValueError("extrémités arrondies ou incohérentes avec le cercle exact")
    longueur_cercle_deposee = rayon * abs(balayage) * (1 - debut)
    if not math.isclose(longueur_cercle_deposee, longueur_deposee, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("longueur déposée incohérente avec l'arc G17")
    ratio_pas = longueur_deposee / pas_mm
    if not math.isfinite(ratio_pas) or ratio_pas >= MAX_ECHANTILLONS_ARC:
        raise ValueError("budget d'échantillons d'arc dépassé (1 000 000)")
    nombre = max(2, int(math.ceil(ratio_pas)) + 1)
    if nombre > MAX_ECHANTILLONS_ARC:
        raise ValueError("budget d'échantillons d'arc dépassé (1 000 000)")
    fractions = np.linspace(debut, 1.0, nombre)
    angles = angle + balayage * fractions
    points = np.column_stack((
        centre_x + rayon * np.cos(angles),
        centre_y + rayon * np.sin(angles),
        np.full(nombre, largeur / 2),
    ))
    distances = np.full(nombre, longueur_deposee / (nombre - 1))
    distances[0] = 0.0
    if not np.isfinite(points).all():
        raise ValueError("échantillons d'arc hors capacité numérique")
    return points, distances


def _points(valeur, nom):
    p = np.asarray(valeur, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or not len(p) or not np.isfinite(p).all() or np.any(p[:, 2] <= 0):
        raise ValueError(f"{nom}: tableau non vide (n, 3), fini, avec demi-largeurs positives requis")
    return p


def _sources(supports):
    sources = [supports] if isinstance(supports, np.ndarray) else list(supports)
    if not sources:
        raise ValueError("au moins une source de cordons est requise")
    return sources


class _NuageAppui:
    def __init__(self, points):
        self.points = _points(points, "supports")
        self.arbre = cKDTree(self.points[:, :2])


def _evaluer_nuages(cibles, nuages, seuil, pas, voisins, portee_max, sur_plateau=False, distances_mm=None):
    c = _points(cibles, "cibles")
    meilleur = np.full(len(c), -np.inf)
    for nuage in nuages:
        distance, index = nuage.arbre.query(c[:, :2], k=min(voisins, len(nuage.points)), workers=1)
        if distance.ndim == 1:
            distance, index = distance[:, None], index[:, None]
        recouvrement = nuage.points[index, 2] + c[:, None, 2] - distance
        meilleur = np.maximum(meilleur, recouvrement.max(axis=1))
    if sur_plateau:
        meilleur[:] = max(float(np.max(meilleur)), seuil)
    appui = meilleur >= seuil
    if distances_mm is not None:
        marges = meilleur - seuil
        if sur_plateau:
            marges[:] = math.inf
        covered = (marges[:-1] >= distances_mm[1:] / 2) & (marges[1:] >= distances_mm[1:] / 2)
        longueur = _longueur_intervalles_non_couverts(covered, distances_mm)
        extremites = bool(marges[0] >= 0 and marges[-1] >= 0)
        ancre = bool(sur_plateau or covered.all() or (extremites and longueur <= portee_max))
    else:
        longueur = _longueur_sans_appui(c[:, :2], appui, pas)
        extremites = bool(appui[0] and appui[-1])
        ancre = bool(sur_plateau or appui.all() or (extremites and longueur <= portee_max))
    return {
        "points": int(len(c)),
        "points_avec_appui": int(appui.sum()),
        "points_avec_appui_pct": float(appui.mean() * 100),
        "recouvrement_min_echantillonne_mm": float(meilleur.min()),
        "longueur_max_parcours_sans_appui_mm": float(longueur),
        "deux_extremites_ancrees": extremites,
        "ancre_nominale": ancre,
        "portee_libre_max_mm": float(longueur),
        "portee_libre_max_mm_parametre": float(portee_max),
        "limite": "Voisinage nominal échantillonné ; ne prédit ni affaissement, ni adhérence, ni tenue physique.",
    }


def _longueur_sans_appui(xy, appui, pas, distances_mm=None):
    maximum = courant = 0.0
    if distances_mm is not None and (len(distances_mm) != len(xy) or distances_mm[0] != 0):
        raise ValueError("distances d'échantillons incompatibles avec le parcours")
    for i in range(1, len(xy)):
        distance = (
            float(distances_mm[i]) if distances_mm is not None
            else float(np.linalg.norm(xy[i] - xy[i - 1]))
        )
        if distance > pas * 1.02:
            courant = 0.0
        elif appui[i]:
            courant = 0.0
        else:
            courant += distance
            maximum = max(maximum, courant)
    return maximum


def _longueur_intervalles_non_couverts(couverts, distances_mm):
    """Cumule les longueurs exactes nominales des intervalles non couverts, en mm."""
    maximum = courant = 0.0
    for index, couvert in enumerate(couverts, start=1):
        if couvert:
            courant = 0.0
        else:
            courant += float(distances_mm[index])
            maximum = max(maximum, courant)
    return maximum


def _continuite_echantillons(precedent, debut, pas_mm, arcs_planaires):
    """Évite de joindre deux dépôts disjoints ; les arcs opt-in exigent la continuité machine."""
    if arcs_planaires:
        echelle = max(1.0, *(abs(float(v)) for v in (*precedent[:2], *debut[:2])))
        seuil = 8 * math.ulp(echelle)
    else:
        seuil = pas_mm * 1.02
    return float(np.linalg.norm(debut[:2] - precedent[:2])) <= seuil


def analyser_lignes(
    lignes,
    *,
    mode="local",
    pas_mm=PAS_DEFAUT_MM,
    recouvrement_min_mm=RECOUVREMENT_DEFAUT_MM,
    longueur_libre_max_mm=PORTEE_LIBRE_DEFAUT_MM,
    types_cibles=None,
    z_min_mm=None,
    z_max_mm=None,
    arcs_planaires=False,
):
    """Agrège les parcours imprimés et estime leur appui, sans interpréter le slicer.

    ``local`` examine n-1, ``anterieur`` ajoute les extrusions précédentes de la
    même couche, et ``causal`` n'ajoute que les parcours antérieurs qualifiés.
    Chaque échantillon conserve la demi-largeur déclarée par le G-code. Le mode
    opt-in ``arcs_planaires`` échantillonne les dépôts G17 exacts, planaires et
    à extrémités compatibles ; les hélices, extrémités arrondies et données
    manquantes restent NON VÉRIFIÉES.
    """
    if type(arcs_planaires) is not bool:
        raise ValueError("arcs_planaires doit être booléen")
    if mode not in {"local", "anterieur", "causal"}:
        raise ValueError("mode attendu : local, anterieur ou causal")
    if not math.isfinite(pas_mm) or pas_mm <= 0:
        raise ValueError("pas fini strictement positif requis")
    if not math.isfinite(recouvrement_min_mm) or recouvrement_min_mm < 0:
        raise ValueError("recouvrement fini positif ou nul requis")
    if not math.isfinite(longueur_libre_max_mm) or longueur_libre_max_mm < 0:
        raise ValueError("portée libre finie positive ou nulle requise")

    interpreter = Interpreter()
    nombre_couches = 0
    couche = None
    parcours = None
    previous = None
    rows = []
    total_echantillons = 0
    total_echantillons_arcs = 0
    total_parcours = 0
    types_cibles = set(types_cibles) if types_cibles else None
    for borne in (z_min_mm, z_max_mm):
        if borne is not None and not math.isfinite(borne):
            raise ValueError("bornes Z finies requises")
    if z_min_mm is not None and z_max_mm is not None and z_min_mm > z_max_mm:
        raise ValueError("borne Z minimale supérieure à la borne maximale")

    def fermer_parcours():
        nonlocal parcours
        if parcours and parcours["segments"]:
            arr = np.concatenate(parcours["segments"], axis=0)
            chemin = {"type": parcours["type"], "objet": parcours["objet"], "points": arr}
            if arcs_planaires:
                chemin["distances_mm"] = np.concatenate(parcours["distances_mm"], axis=0)
            couche["parcours"].append(chemin)
        parcours = None

    def fermer_couche():
        nonlocal couche, previous, nombre_couches
        if couche is None:
            return
        fermer_parcours()
        immediate = [] if previous is None else [_NuageAppui(previous)]
        earlier = []
        qualified = []
        for path in couche["parcours"]:
            p = path["points"]
            if path["type"] in SUPPORTS:
                if immediate:
                    result = evaluer_parcours(
                        p,
                        immediate,
                        recouvrement_min_mm=recouvrement_min_mm,
                        longueur_libre_max_mm=longueur_libre_max_mm,
                        pas_mm=pas_mm,
                        distances_mm=path["distances_mm"] if arcs_planaires else None,
                    )
                else:
                    result = evaluer_parcours(
                        p,
                        [p],
                        recouvrement_min_mm=recouvrement_min_mm,
                        longueur_libre_max_mm=longueur_libre_max_mm,
                        pas_mm=pas_mm,
                        sur_plateau=True,
                        distances_mm=path["distances_mm"] if arcs_planaires else None,
                    )
                if mode == "anterieur":
                    earlier.append(p)
                elif mode == "causal" and result["ancre_nominale"]:
                    qualified.append(p)
            else:
                sources = list(immediate)
                if mode == "anterieur":
                    sources.extend(earlier)
                elif mode == "causal":
                    sources.extend(qualified)
                if not sources:
                    result = evaluer_parcours(
                        p,
                        [p],
                        recouvrement_min_mm=recouvrement_min_mm,
                        longueur_libre_max_mm=longueur_libre_max_mm,
                        pas_mm=pas_mm,
                        sur_plateau=True,
                        distances_mm=path["distances_mm"] if arcs_planaires else None,
                    )
                else:
                    result = evaluer_parcours(
                        p,
                        sources,
                        recouvrement_min_mm=recouvrement_min_mm,
                        longueur_libre_max_mm=longueur_libre_max_mm,
                        pas_mm=pas_mm,
                        distances_mm=path["distances_mm"] if arcs_planaires else None,
                    )
                if mode == "anterieur":
                    earlier.append(p)
                elif mode == "causal" and result["ancre_nominale"]:
                    qualified.append(p)
            inclus = types_cibles is None or path["type"] in types_cibles
            inclus = inclus and (couche["z_mm"] is None or z_min_mm is None or couche["z_mm"] >= z_min_mm)
            inclus = inclus and (couche["z_mm"] is None or z_max_mm is None or couche["z_mm"] <= z_max_mm)
            if inclus:
                rows.append(
                    {
                        "plaque": None,
                        "couche": couche["index"],
                        "z_mm": couche["z_mm"],
                        "type": path["type"],
                        "objet": path["objet"],
                        "points": result["points"],
                        "points_avec_appui": result["points_avec_appui"],
                        "points_avec_appui_pct": result["points_avec_appui_pct"],
                        "recouvrement_min_echantillonne_mm": result["recouvrement_min_echantillonne_mm"],
                        "portee_libre_max_mm": result["portee_libre_max_mm"],
                        "deux_extremites_ancrees": result["deux_extremites_ancrees"],
                        "ancre_nominale": result["ancre_nominale"],
                    }
                )
        layer_points = [path["points"] for path in couche["parcours"]]
        if layer_points:
            previous = np.concatenate(layer_points, axis=0)
        nombre_couches += 1
        couche = None

    for line in lignes:
        if line.startswith(";LAYER_CHANGE"):
            fermer_couche()
            couche = {"index": interpreter.layer + 1, "z_mm": None, "parcours": []}
        if couche is None:
            # Les déplacements et commandes de configuration initiaux sont
            # toujours passés à l'interpréteur pour établir les modes XYZ/E.
            move = interpreter.feed_line(line)
            continue
        if line.startswith(";Z:"):
            try:
                couche["z_mm"] = round(float(line.split(":", 1)[1]), 4)
            except ValueError:
                interpreter.error("métadonnée Z invalide pour le parcours")
            if z_max_mm is not None and couche["z_mm"] > z_max_mm:
                couche = None
                break
        move = interpreter.feed_line(line)
        if not move:
            continue
        if move["trajectory"] == "arc_g17_xy":
            if not arcs_planaires:
                interpreter.error("arc analytique non pris en charge par l'intersection linéaire des appuis")
                fermer_parcours()
                continue
            kind = move["type"]
            if not move["deposited_length"] or kind not in PIECE | SUPPORTS:
                fermer_parcours()
                continue
            try:
                sample, distances = _echantillonner_arc(move, pas_mm)
            except ValueError as erreur:
                interpreter.error(str(erreur))
                fermer_parcours()
                continue
            n = len(sample)
            total_echantillons += n
            total_echantillons_arcs += n
            if total_echantillons > MAX_ECHANTILLONS:
                raise ValueError("budget de points dépassé (100 millions)")
            if total_echantillons_arcs > MAX_ECHANTILLONS_ARC:
                raise ValueError("budget total d'échantillons d'arc dépassé (1 000 000)")
            identite = (kind, move["object"])
            suite = parcours is not None and (parcours["type"], parcours["objet"]) == identite
            if suite:
                precedent = parcours["segments"][-1][-1]
                suite = _continuite_echantillons(precedent, sample[0], pas_mm, arcs_planaires)
            if not suite:
                fermer_parcours()
                total_parcours += 1
                if total_parcours > MAX_SEGMENTS:
                    raise ValueError("budget de parcours dépassé (250 000)")
                parcours = {"type": kind, "objet": move["object"], "segments": [], "distances_mm": []}
            parcours["segments"].append(sample[1:] if suite else sample)
            parcours["distances_mm"].append(distances[1:] if suite else distances)
            continue
        kind = move["type"]
        if not move["deposited_length"] or kind not in PIECE | SUPPORTS:
            fermer_parcours()
            continue
        if arcs_planaires and "WIDTH" not in move["metadata"]:
            interpreter.error("largeur de cordon G-code manquante pour le mode d'appuis G17")
            fermer_parcours()
            continue
        start = np.asarray(move["deposition_start"][:2], dtype=float)
        end = np.asarray(move["end"][:2], dtype=float)
        length = float(np.linalg.norm(end - start))
        if length <= 1e-9:
            continue
        width = float(move["metadata"].get("WIDTH", 0.42))
        if not math.isfinite(width) or width <= 0 or width > 10:
            interpreter.error("largeur de ligne G-code invalide")
            fermer_parcours()
            continue
        n = max(2, int(math.ceil(length / pas_mm)) + 1)
        total_echantillons += n
        if total_echantillons > MAX_ECHANTILLONS:
            raise ValueError("budget de points dépassé (100 millions)")
        points = start + (end - start) * np.linspace(0, 1, n)[:, None]
        sample = np.column_stack((points, np.full(n, width / 2)))
        if arcs_planaires:
            distances = np.zeros(n)
            distances[1:] = np.linalg.norm(np.diff(points, axis=0), axis=1)
        identite = (kind, move["object"])
        suite = parcours is not None and (parcours["type"], parcours["objet"]) == identite
        if suite:
            precedent = parcours["segments"][-1][-1]
            suite = _continuite_echantillons(precedent, sample[0], pas_mm, arcs_planaires)
        if not suite:
            fermer_parcours()
            total_parcours += 1
            if total_parcours > MAX_SEGMENTS:
                raise ValueError("budget de parcours dépassé (250 000)")
            parcours = {"type": kind, "objet": move["object"], "segments": []}
            if arcs_planaires:
                parcours["distances_mm"] = []
        parcours["segments"].append(sample[1:] if suite else sample)
        if arcs_planaires:
            parcours["distances_mm"].append(distances[1:] if suite else distances)
    fermer_couche()

    par_couche = []
    groupes = {}
    for row in rows:
        key = (row["couche"], row["z_mm"])
        groupe = groupes.setdefault(
            key,
            {
                "couche": key[0],
                "z_mm": key[1],
                "parcours": 0,
                "points": 0,
                "points_avec_appui": 0,
                "recouvrement_min_echantillonne_mm": None,
                "portee_libre_max_mm": 0.0,
            },
        )
        groupe["parcours"] += 1
        groupe["points"] += row["points"]
        groupe["points_avec_appui"] += row["points_avec_appui"]
        minimum = row["recouvrement_min_echantillonne_mm"]
        groupe["recouvrement_min_echantillonne_mm"] = (
            minimum
            if groupe["recouvrement_min_echantillonne_mm"] is None
            else min(groupe["recouvrement_min_echantillonne_mm"], minimum)
        )
        groupe["portee_libre_max_mm"] = max(groupe["portee_libre_max_mm"], row["portee_libre_max_mm"])
    for groupe in groupes.values():
        groupe["points_avec_appui_pct"] = (
            groupe["points_avec_appui"] / groupe["points"] * 100 if groupe["points"] else 0.0
        )
        par_couche.append(groupe)
    status = "NON VÉRIFIÉ" if interpreter.errors or not rows else "PASS"
    return {
        "statut": status,
        "mode": mode,
        "parametres": {
            "pas_echantillonnage_mm": pas_mm,
            "recouvrement_min_mm": recouvrement_min_mm,
            "portee_libre_max_mm": longueur_libre_max_mm,
            **({
                "arcs_planaires": True,
                "budget_echantillons_arcs_max": MAX_ECHANTILLONS_ARC,
            } if arcs_planaires else {}),
        },
        "couches": nombre_couches,
        "couches_analysees": len(par_couche),
        "parcours": len(rows),
        "avec_appui_nominal": sum(r["ancre_nominale"] for r in rows),
        "sans_appui_nominal": sum(not r["ancre_nominale"] for r in rows),
        "recouvrement_min_global_mm": min((r["recouvrement_min_echantillonne_mm"] for r in rows), default=None),
        "portee_libre_max_global_mm": max((r["portee_libre_max_mm"] for r in rows), default=None),
        "lignes": rows,
        "par_couche": par_couche,
        "erreurs_interpreteur": interpreter.errors,
        "limite": (
            "Échantillons analytiques G17 ; chaque intervalle requiert une marge de recouvrement "
            "d'au moins la moitié de sa longueur à chaque extrémité, sinon sa longueur entière "
            "est comptée sans appui. Aucune simulation physique."
            if arcs_planaires else
            "Projection nominale des cordons G-code ; aucune simulation d'affaissement, adhérence ou tenue physique."
        ),
    }


def analyser_fichier(
    chemin,
    *,
    mode="local",
    pas_mm=PAS_DEFAUT_MM,
    recouvrement_min_mm=RECOUVREMENT_DEFAUT_MM,
    longueur_libre_max_mm=PORTEE_LIBRE_DEFAUT_MM,
    types_cibles=None,
    z_min_mm=None,
    z_max_mm=None,
    plaques=None,
    arcs_planaires=False,
):
    """Mesure les appuis nominaux en mm dans un G-code ou des plaques 3MF.

    Les numéros de plaque sont sans unité. Sortie PASS ou NON VÉRIFIÉ selon
    la couverture ; refuse un index ambigu même en cache. Aucun fichier ni
    réglage n’est modifié et aucune adhérence réelle n’est déduite. L'option
    ``arcs_planaires`` ne qualifie que les arcs G17 analytiques et planaires.
    """
    path = Path(chemin)
    options = {
        "mode": mode,
        "pas_mm": pas_mm,
        "recouvrement_min_mm": recouvrement_min_mm,
        "longueur_libre_max_mm": longueur_libre_max_mm,
        "types_cibles": sorted(set(types_cibles)) if types_cibles else None,
        "z_min_mm": z_min_mm,
        "z_max_mm": z_max_mm,
    }
    if arcs_planaires:
        options["arcs_planaires"] = True
    if path.suffix.lower() == ".3mf":
        results = []
        with Archive3MF(path) as archive:
            # Refuser l’index ambigu avant le filtre de sélection et le cache.
            membres = sorted(plaques_gcode_archive(archive), key=lambda plaque_membre: plaque_membre[1])
            # Le SHA du rapport porte sur le descripteur qui fournit les plaques.
            digest = archive.sha256()

            def run(lines):
                return analyser_lignes(lines, **options)

            for plaque, membre in membres:
                if plaques is not None and plaque not in plaques:
                    continue
                result = analyser_membre_gcode_3mf(
                    path, membre, options, run,
                    version="appuis-gcode-arc-1" if arcs_planaires else "appuis-gcode-1",
                    archive=archive,
                )
                for row in result["lignes"]:
                    row["plaque"] = plaque
                results.append(result)
            archive.verifier_source(path)
        return {
            "fichier": path.name,
            "sha256": digest,
            "plaques": results,
            "statut": "NON VÉRIFIÉ" if not results or any(r["statut"] == "NON VÉRIFIÉ" for r in results) else "PASS",
            "mode": mode,
            "parametres": options,
        }
    if path.suffix.lower() != ".gcode":
        raise ValueError("entrée attendue : fichier .gcode ou paquet .3mf")
    with path.open("r", encoding="utf-8", errors="replace") as flux:
        result = analyser_lignes(flux, **options)
    result["fichier"] = path.name
    result["sha256"] = _sha256(path)
    result["plaques"] = [{k: v for k, v in result.items() if k != "plaques"}]
    return result


def _sha256(path):
    import hashlib

    with Path(path).open("rb") as flux:
        return hashlib.file_digest(flux, "sha256").hexdigest()


def _resumer(rapport, destination):
    """Garde le verdict, le SHA et les compteurs agrégés, sans parcours ni coordonnées."""
    plaques = rapport["plaques"]
    return {
        "statut": rapport["statut"],
        "mode": rapport["mode"],
        "sha256": rapport["sha256"],
        "rapport": str(destination),
        "plaques": len(plaques),
        "plaques_non_verifiees": sum(p["statut"] == "NON VÉRIFIÉ" for p in plaques),
        **{
            cle: sum(p[cle] for p in plaques)
            for cle in ("couches_analysees", "parcours", "avec_appui_nominal", "sans_appui_nominal")
        },
        "erreurs_interpreteur": sum(len(p["erreurs_interpreteur"]) for p in plaques),
    }


def main(argv=None, *, prive=None):
    """Contrôle les appuis nominaux en mm et publie sur demande un rapport privé neuf.

    Codes 0 (mesure disponible) ou 2 (refus ou NON VÉRIFIÉ) ; les sources
    restent intactes et aucun réglage n’est modifié.
    """
    parser = argparse.ArgumentParser(description="Mesure les appuis nominaux de parcours G-code par couche.")
    parser.add_argument("fichier", type=Path)
    parser.add_argument("--mode", choices=("local", "anterieur", "causal"), default="local")
    parser.add_argument("--pas-mm", type=float, default=PAS_DEFAUT_MM)
    parser.add_argument("--recouvrement-min-mm", type=float, default=RECOUVREMENT_DEFAUT_MM)
    parser.add_argument("--portee-libre-max-mm", type=float, default=PORTEE_LIBRE_DEFAUT_MM)
    parser.add_argument("--type", dest="types", action="append", help="Type d'extrusion ciblé; répétable.")
    parser.add_argument("--z-min-mm", type=float)
    parser.add_argument("--z-max-mm", type=float)
    parser.add_argument("--plaque", type=int, action="append", help="Numéro de plaque; répétable.")
    parser.add_argument(
        "--arcs-planaires", action="store_true",
        help="Échantillonne les dépôts G17 planaires exacts ; hélices restent NON VÉRIFIÉES.",
    )
    parser.add_argument("--json", type=Path, help="Chemin privé neuf pour le rapport complet.")
    parser.add_argument("--resume", action="store_true", help="Affiche les compteurs ; exige --json.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.resume and args.json is None:
        print("BENCHAB : --resume exige --json pour conserver le rapport complet.", file=sys.stderr)
        return 2
    try:
        result = analyser_fichier(
            args.fichier,
            mode=args.mode,
            pas_mm=args.pas_mm,
            recouvrement_min_mm=args.recouvrement_min_mm,
            longueur_libre_max_mm=args.portee_libre_max_mm,
            types_cibles=args.types,
            z_min_mm=args.z_min_mm,
            z_max_mm=args.z_max_mm,
            plaques=set(args.plaque) if args.plaque else None,
            arcs_planaires=args.arcs_planaires,
        )
    except (OSError, BadZipFile, ValueError) as erreur:
        print(f"BENCHAB : contrôle des appuis refusé : {erreur}", file=sys.stderr)
        return 2
    destination = None
    if args.json:
        from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

        destination = destination_neuve(args.json.expanduser(), prive=atelier_prive(prive), temporaire=False)
        ecrire_json_neuf(destination, result, entrees=[args.fichier])
    affiche = _resumer(result, destination) if args.resume else result
    if args.resume:
        print(json.dumps(affiche, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    else:
        print(json.dumps(affiche, ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if result["statut"] == "NON VÉRIFIÉ" else 0
