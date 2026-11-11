"""Zones de remplissage localisées et vérification de leur effet dans le G-code."""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import trimesh

from benchab import reglages as schema_reglages
from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.geometrie_arc import longueur_deposee_dans_boite
from benchab.gcode.lecture import iter_lignes_gcode_3mf, plaques_gcode_archive


def _reglage(densite: int, motif: str = "gyroid") -> dict[str, str]:
    return {"sparse_infill_density": f"{densite}%", "sparse_infill_pattern": motif}


ROLES = {
    "decoratif": (_reglage(8), "aspect seulement, aucune charge : remplissage allégé"),
    "structurel": (_reglage(25), "zone qui reprend les efforts connus de la pièce"),
    "fixation": (_reglage(50), "logement de vis ou de fixation : matière autour du perçage"),
    "insert_axe": (_reglage(50), "insert, axe ou pivot : matière autour de l'alésage"),
    "attache_clip": (_reglage(40), "attache ou clip : base de la languette"),
    "cle": (_reglage(50), "clé ou détrompeur d'assemblage"),
    "jonction": (_reglage(35), "jonction collée ou assemblée"),
    "charge": (_reglage(40), "zone qui reçoit une charge connue (déclarée par l'utilisateur)"),
    "attention": (_reglage(25), "zone à surveiller (retour physique, défaut connu)"),
    "inchange": ({}, "zone volontairement laissée aux réglages de la pièce"),
}
FORMES = ("boite", "cylindre")
_NOMBRE_GCODE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


def _gcodes_resultats(tranche: str | Path, resultats: list[dict]) -> dict[int, str]:
    """Lit les G-codes des plaques par index non ambigu ; contrôle CRC et garde la source stable."""
    codes = {}
    with Archive3MF(tranche) as archive:
        membres = dict(plaques_gcode_archive(archive))
        for resultat in resultats:
            index = resultat.get("index")
            if type(index) is not int or index < 1 or index not in membres or index in codes:
                raise ValueError("Résultat de plaque absent, invalide ou dupliqué dans l'archive.")
            archive.verifier_source(tranche)
            codes[index] = "".join(iter_lignes_gcode_3mf(tranche, membres[index], archive=archive))
            archive.verifier_source(tranche)
    return codes


def _nombre(valeur: object, nom: str, *, positif: bool = True) -> float:
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur):
        raise ValueError(f"{nom} : nombre fini requis")
    if positif and valeur <= 0:
        raise ValueError(f"{nom} : nombre positif requis")
    return float(valeur)


def volume(zone: dict) -> trimesh.Trimesh:
    """Construit le volume modificateur dans le repère de la pièce."""
    forme = zone.get("forme")
    if forme == "boite":
        minimum = [_nombre(v, "min", positif=False) for v in zone["min"]]
        maximum = [_nombre(v, "max", positif=False) for v in zone["max"]]
        if len(minimum) != 3 or len(maximum) != 3 or any(b <= a for a, b in zip(minimum, maximum)):
            raise ValueError(f"zone {zone.get('nom')} : boîte vide ou mal orientée")
        mesh = trimesh.creation.box(extents=np.subtract(maximum, minimum))
        mesh.apply_translation(np.add(minimum, maximum) / 2)
        return mesh
    if forme == "cylindre":
        centre = [_nombre(v, "centre", positif=False) for v in zone["centre"]]
        if len(centre) != 3:
            raise ValueError("centre : trois coordonnées requises")
        mesh = trimesh.creation.cylinder(
            radius=_nombre(zone["rayon"], "rayon"),
            height=_nombre(zone["hauteur"], "hauteur"),
            sections=48,
        )
        mesh.apply_translation(centre)
        return mesh
    raise ValueError(f"zone {zone.get('nom')} : forme {forme!r} inconnue (attendu : {', '.join(FORMES)})")


def volumes(piece: trimesh.Trimesh, zones: list[dict]) -> tuple[list[dict], list[dict]]:
    """Valide des zones en mm et produit volumes modificateurs et bilan ; réglages typés, sans preuve physique."""
    modificateurs: list[dict] = []
    bilan: list[dict] = []
    noms: set[str] = set()
    for zone_declaree in zones:
        if not isinstance(zone_declaree, dict) or not zone_declaree.get("nom"):
            raise ValueError("zone sans nom")
        nom = zone_declaree["nom"]
        if nom in noms:
            raise ValueError(f"zone {nom} : nom en double")
        noms.add(nom)
        role = zone_declaree.get("role")
        if role not in ROLES:
            raise ValueError(f"zone {nom} : rôle {role!r} inconnu (attendu : {', '.join(ROLES)})")
        reglages_types = schema_reglages.Reglages(ROLES[role][0])
        reglages_types.update(zone_declaree.get("reglages", {}))
        reglages = dict(reglages_types)
        densite = str(reglages.get("sparse_infill_density", "0%"))
        if not re.fullmatch(r"\d{1,3}%", densite) or int(densite[:-1]) > 100:
            raise ValueError(f"zone {nom} : densité invalide {densite!r}")
        mesh = volume(zone_declaree)
        bas = np.maximum(mesh.bounds[0], piece.bounds[0])
        haut = np.minimum(mesh.bounds[1], piece.bounds[1])
        if np.any(haut <= bas):
            raise ValueError(f"zone {nom} : hors de la pièce")
        if piece.is_volume:
            intersection = trimesh.boolean.intersection([mesh, piece], engine="manifold")
            if intersection is None or intersection.is_empty or abs(intersection.volume) < 1e-3:
                raise ValueError(f"zone {nom} : ne recoupe pas la matière de la pièce")
        bilan.append(
            {
                "nom": nom,
                "role": role,
                "reglages": reglages,
                "bornes": np.round(mesh.bounds, 2).tolist(),
                "raison": ROLES[role][1],
            }
        )
        if reglages:
            modificateurs.append(
                {"name": f"zone-{nom}", "mesh": mesh, "subtype": "modifier_part", "settings": reglages}
            )
    return modificateurs, bilan


def analyser_gcode(
    gcode: str,
    regions: dict[str, list[float]],
    types: tuple[str, ...] = ("Sparse infill", "Internal solid infill", "Inner wall", "Outer wall"),
) -> dict[str, dict[str, float]]:
    """Mesure les longueurs déposées (mm) par rôle dans des boîtes 3D (mm).

    Prend les segments et arcs G17 qualifiés, refuse les mouvements ambigus
    et ne change ni réglages ni trajectoires. Les valeurs sont arrondies à 0,1 mm.
    """
    rapport = {nom: {type_ligne: 0.0 for type_ligne in types} for nom in regions}
    interpreteur = Interpreter()
    for ligne in gcode.splitlines():
        mouvement = interpreteur.feed_line(ligne)
        if not mouvement or mouvement["deposited_length"] <= 0 or mouvement["type"] not in types:
            continue
        debut, fin = mouvement["deposition_start"], mouvement["end"]
        for nom, bornes in regions.items():
            if mouvement["trajectory"] == "arc_g17_xy":
                rapport[nom][mouvement["type"]] += longueur_deposee_dans_boite(mouvement, bornes)
                continue
            x0, y0, x1, y1, z0, z1 = bornes
            minimum, maximum = 0.0, 1.0
            for origine, delta, bas, haut in zip(
                debut,
                (fin[i] - debut[i] for i in range(3)),
                (x0, y0, z0),
                (x1, y1, z1),
            ):
                if abs(delta) < 1e-12:
                    if not bas <= origine <= haut:
                        maximum = -1.0
                        break
                else:
                    a, b = sorted(((bas - origine) / delta, (haut - origine) / delta))
                    minimum = max(minimum, a)
                    maximum = min(maximum, b)
            if maximum > minimum:
                rapport[nom][mouvement["type"]] += mouvement["deposited_length"] * (maximum - minimum)
    if interpreteur.errors:
        raise ValueError("; ".join(interpreteur.errors))
    return {nom: {kind: round(value, 1) for kind, value in mesures.items()} for nom, mesures in rapport.items()}


def evaluer_effet_densite(temoin, variante):
    """Compare deux mesures homologues en mm ; PASS, FAIL ou NON VÉRIFIÉ.

    Chaque dictionnaire fournit zone_mm et hors_zone_mm. Le contrôle compare
    une seule hausse de densité déclarée, pas une combinaison de réglages.
    Aucun verdict physique ni changement de réglage.
    """
    seuil = Decimal("0.1")
    rapport = {"statut": "NON VÉRIFIÉ", "seuil_mm": float(seuil),
               "delta_zone_mm": None, "delta_hors_zone_mm": None}
    champs = ("zone_mm", "hors_zone_mm")
    brutes = [r.get(c) if isinstance(r, dict) else None for r in (temoin, variante) for c in champs]
    try:
        if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in brutes):
            return {**rapport, "detail": "Mesures homologues manquantes ou non valides."}
    except OverflowError:
        return {**rapport, "detail": "Mesures hors capacité numérique."}
    a, controle_a, b, controle_b = (Decimal(str(x)) for x in brutes)
    delta, controle = b - a, controle_b - controle_a
    rapport.update(delta_zone_mm=float(delta), delta_hors_zone_mm=float(controle))
    if min(a, controle_a, controle_b) <= seuil:
        return {**rapport, "detail": "Dépôts de référence ou de contrôle insuffisants."}
    # Deux arrondis indépendants à 0,1 mm peuvent déplacer le delta de 0,1 mm (#82).
    bas, haut = delta - seuil, delta + seuil
    hors_bas, hors_haut = controle - seuil, controle + seuil
    rapport.update(intervalle_delta_zone_mm=[float(bas), float(haut)],
                   intervalle_delta_hors_zone_mm=[float(hors_bas), float(hors_haut)])
    if hors_bas < -seuil or hors_haut > seuil:
        return {**rapport, "detail": "La stabilité hors zone n'est pas établie avec cette précision."}
    if bas > seuil:
        return {**rapport, "statut": "PASS", "detail": "Hausse mesurée dans la zone, contrôle hors zone stable."}
    if haut < -seuil:
        return {**rapport, "statut": "FAIL", "detail": "Dépôt diminué malgré la hausse de densité."}
    if bas >= -seuil and haut <= seuil:
        return {**rapport, "statut": "FAIL", "detail": "Aucune hausse mesurable à cette précision."}
    return {**rapport, "detail": "L'incertitude de mesure couvre le seuil de l'effet attendu."}


def _sortie_privee(sortie: str | Path) -> Path:
    """Valide une sortie privée/temporaire reconnue ; FAIL si lien/existant, aucune création ni réglage changé."""
    from benchab.qualite.ecritures import destination_neuve

    cible = destination_neuve(sortie)
    cible.parent.resolve(strict=True)
    return cible


def eprouvette(sortie: str | Path, base_3mf: str | Path) -> dict:
    """Prépare trois variantes en mm et les tranche via le lanceur commun ; retourne mesures en mm, g et s.

    Le statut décrit des estimations, jamais une validation physique. Réglages typés, profil source intact,
    sortie neuve limitée à prive/ ou /private/tmp (docs/reference/regles.md et docs/reference/integrite-3mf.md).
    """
    from benchab.fabrication.anycubic import ecrire_3mf, ranger, resultats
    from benchab.fabrication.trancher import trancher_fichier

    sortie = _sortie_privee(sortie)
    base = Path(base_3mf).expanduser().resolve(strict=True)
    with Archive3MF(base) as archive:
        membre_reglages = "Metadata/project_settings.config"
        noms = archive.noms()
        if membre_reglages not in noms:
            raise ValueError("Configuration de projet absente du 3MF source.")
        settings = archive.reglages()
        archive.verifier(membres=[nom for nom in noms if nom != membre_reglages])
        archive.verifier_source(base)

    piece = trimesh.creation.box(extents=(90, 20, 15))
    piece.apply_translation((45, 10, 7.5))
    reference = schema_reglages.Reglages(settings)
    reference.update({
        "sparse_infill_density": "10%",
        "sparse_infill_pattern": "gyroid",
        "wall_loops": "2",
        "top_shell_layers": "4",
        "bottom_shell_layers": "3",
        "enable_support": "0",
    })
    reference = dict(reference)
    zone = {
        "nom": "tiers droit",
        "role": "fixation",
        "forme": "boite",
        "min": [60, -1, -1],
        "max": [91, 21, 16],
    }
    zone_volumes, bilan = volumes(piece, [zone])
    variants = [
        ("A-homogene", []),
        ("B-localise", zone_volumes),
        ("C-localise-parois", [{**zone_volumes[0], "settings": {**zone_volumes[0]["settings"], "wall_loops": "5"}}]),
    ]
    plaques = []
    for index, (nom, modifiers) in enumerate(variants):
        item = {
            "name": nom,
            "mesh": piece,
            "w": 90.0,
            "h": 20.0,
            "extruder": 1,
            "settings": reference,
            "volumes": modifiers,
        }
        ranger([item], index, len(variants), 10.0, cli=True)
        plaques.append({"name": nom, "items": [item]})

    # La sortie est explicitement privée : aucune étude générée ne doit remplir le dépôt public.
    sortie.mkdir(parents=False, exist_ok=False)
    source_3mf = sortie / "eprouvette-plaques.3mf"
    # Le profil explicitement lu reste traçable malgré les volumes modificateurs (docs/reference/integrite-3mf.md).
    source = ecrire_3mf(
        plaques,
        settings,
        source_3mf,
        cli=True,
        canonique=True,
        nom_source=source_3mf.name,
    )
    tranche, rapport_tranchage = trancher_fichier(source, sortie, "eprouvette-tranche.3mf", usage="temoin")
    resultats_tranche = sorted(resultats(tranche), key=lambda item: item["index"])
    if [item.get("index") for item in resultats_tranche] != [1, 2, 3]:
        raise ValueError("résultats de tranchage incomplets, dupliqués ou inattendus")
    codes_gcode = _gcodes_resultats(tranche, resultats_tranche)
    mesures = []
    for (nom, _), resultat, plaque in zip(variants, resultats_tranche, plaques):
        item = plaque["items"][0]
        x0, y0 = item["x"], item["y"]
        gcode = codes_gcode[resultat["index"]]
        regions = {
            "zone (x 60–90)": [x0 + 62, y0 + 2, x0 + 88, y0 + 18, 3, 12],
            "hors zone (x 2–58)": [x0 + 2, y0 + 2, x0 + 58, y0 + 18, 3, 12],
        }
        longueurs = analyser_gcode(gcode, regions)
        hors_zone = longueurs["hors zone (x 2–58)"]["Sparse infill"]
        zone = longueurs["zone (x 60–90)"]["Sparse infill"]
        mesures.append(
            {
                "nom": nom,
                "grammes": round(resultat["grammes"], 2),
                "secondes": resultat["secondes"],
                "avertissements": resultat["avertissements"],
                "longueurs_mm": longueurs,
                "mesure_effet": {"zone_mm": zone, "hors_zone_mm": hors_zone},
                "densite_zone_sur_hors_zone": round((zone / 26) / max(1e-9, hors_zone / 56), 2),
            }
        )
    rapport = {
        "tranchage_s": rapport_tranchage["duree_s"],
        "zone": bilan,
        "reference": reference,
        "variantes": mesures,
        # La troisième plaque change aussi les parois : seule B/A isole l'effet de densité.
        "effet_densite_B_sur_A": evaluer_effet_densite(mesures[0]["mesure_effet"], mesures[1]["mesure_effet"]),
    }
    (sortie / "eprouvette.json").write_text(json.dumps(rapport, indent=1, ensure_ascii=False), encoding="utf-8")
    return rapport


def main(argv=None):
    """Prépare une étude A/B/C avec un profil de référence fourni en lecture seule."""
    parser = argparse.ArgumentParser(description=__doc__)
    commandes = parser.add_subparsers(dest="commande", required=True)
    etude = commandes.add_parser("eprouvette")
    etude.add_argument("--base", type=Path, required=True)
    etude.add_argument(
        "--sortie",
        type=Path,
        default=Path("prive") / "mesures" / f"{date.today().isoformat()}-remplissage-localise",
    )
    args = parser.parse_args(argv)
    rapport = eprouvette(args.sortie, args.base)
    print(json.dumps(rapport, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
