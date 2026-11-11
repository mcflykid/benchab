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
from benchab.gcode.lecture import analyser_membre_gcode_3mf, empreinte_3mf, plaques_gcode_archive

PAS_DEFAUT_MM = 0.15
RECOUVREMENT_DEFAUT_MM = 0.05
PORTEE_LIBRE_DEFAUT_MM = 10.0
MAX_SEGMENTS = 250_000
MAX_ECHANTILLONS = 100_000_000
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
):
    """Évalue l'ancrage et la portée libre d'un parcours de pièce/support.

    Chaque parcours est évalué séparément pour éviter de relier implicitement
    deux extrusions disjointes. Les supports précédents peuvent réunir la
    couche précédente et les parcours antérieurs déjà qualifiés.
    """
    c = _points(points, "parcours")
    if not math.isfinite(longueur_libre_max_mm) or longueur_libre_max_mm < 0:
        raise ValueError("longueur libre maximale finie et positive ou nulle requise")
    sources = _sources(supports_precedents)
    nuages = [s if isinstance(s, _NuageAppui) else _NuageAppui(_points(s, "supports")) for s in sources]
    return _evaluer_nuages(c, nuages, recouvrement_min_mm, pas_mm, voisins, longueur_libre_max_mm, sur_plateau)


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


def _evaluer_nuages(cibles, nuages, seuil, pas, voisins, portee_max, sur_plateau=False):
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


def _longueur_sans_appui(xy, appui, pas):
    maximum = courant = 0.0
    for i in range(1, len(xy)):
        distance = float(np.linalg.norm(xy[i] - xy[i - 1]))
        if distance > pas * 1.02:
            courant = 0.0
        elif appui[i]:
            courant = 0.0
        else:
            courant += distance
            maximum = max(maximum, courant)
    return maximum


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
):
    """Agrège les parcours imprimés et estime leur appui, sans interpréter le slicer.

    ``local`` examine n-1, ``anterieur`` ajoute les extrusions précédentes de la
    même couche, et ``causal`` n'ajoute que les parcours antérieurs qualifiés.
    Chaque échantillon conserve la demi-largeur déclarée par le G-code.
    """
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
            couche["parcours"].append({"type": parcours["type"], "objet": parcours["objet"], "points": arr})
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
                    )
                else:
                    result = evaluer_parcours(
                        p,
                        [p],
                        recouvrement_min_mm=recouvrement_min_mm,
                        longueur_libre_max_mm=longueur_libre_max_mm,
                        pas_mm=pas_mm,
                        sur_plateau=True,
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
                    )
                else:
                    result = evaluer_parcours(
                        p,
                        sources,
                        recouvrement_min_mm=recouvrement_min_mm,
                        longueur_libre_max_mm=longueur_libre_max_mm,
                        pas_mm=pas_mm,
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
            interpreter.error("arc analytique non pris en charge par l'intersection linéaire des appuis")
            continue
        kind = move["type"]
        if not move["deposited_length"] or kind not in PIECE | SUPPORTS:
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
        identite = (kind, move["object"])
        suite = parcours is not None and (parcours["type"], parcours["objet"]) == identite
        if suite:
            precedent = parcours["segments"][-1][-1]
            suite = np.linalg.norm(sample[0, :2] - precedent[:2]) <= pas_mm * 1.02
        if not suite:
            fermer_parcours()
            total_parcours += 1
            if total_parcours > MAX_SEGMENTS:
                raise ValueError("budget de parcours dépassé (250 000)")
            parcours = {"type": kind, "objet": move["object"], "segments": []}
        parcours["segments"].append(sample[1:] if suite else sample)
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
):
    """Mesure les appuis nominaux en mm dans un G-code ou des plaques 3MF.

    Les numéros de plaque sont sans unité. Sortie PASS ou NON VÉRIFIÉ selon
    la couverture ; refuse un index ambigu même en cache. Aucun fichier ni
    réglage n’est modifié et aucune adhérence réelle n’est déduite.
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
    if path.suffix.lower() == ".3mf":
        results = []
        with Archive3MF(path) as archive:
            # Refuser l’index ambigu avant le filtre de sélection et le cache.
            membres = sorted(plaques_gcode_archive(archive), key=lambda plaque_membre: plaque_membre[1])
            digest = empreinte_3mf(path)

            def run(lines):
                return analyser_lignes(lines, **options)

            for plaque, membre in membres:
                if plaques is not None and plaque not in plaques:
                    continue
                result = analyser_membre_gcode_3mf(
                    path, membre, options, run, version="appuis-gcode-1", archive=archive,
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


def main(argv=None, *, prive=None):
    """Contrôle les appuis (mm) et affiche PASS/WARN/FAIL/NON VÉRIFIÉ ; ne modifie jamais le G-code."""
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
    parser.add_argument("--json", type=Path, help="Chemin privé pour enregistrer le rapport.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
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
        )
    except (OSError, BadZipFile, ValueError) as erreur:
        print(f"BENCHAB : contrôle des appuis refusé : {erreur}", file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.json:
        prive = atelier_prive(prive)
        destination = args.json.expanduser().resolve()
        if not destination.is_relative_to(prive):
            raise ValueError("le rapport personnel doit rester sous BENCHAB_PRIVE")
        from benchab.qualite.ecritures import _sans_lien

        _sans_lien(args.json.absolute())
        args.json.parent.mkdir(parents=True, exist_ok=True)
        with args.json.open("x") as flux:
            flux.write(text)
    print(text, end="")
    return 2 if result["statut"] == "NON VÉRIFIÉ" else 0
