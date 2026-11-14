"""Répartit les dépôts d’un 3MF tranché par plaque, objet, rôle et altitude."""

import argparse
import json
import math
from bisect import bisect_right
from collections import Counter
from pathlib import Path

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import iter_lignes_gcode_3mf, plaques_gcode_archive
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.qualite.statuts import code_statut


def _masse_lineaire(config, outil):
    """Convertit le diamètre (mm) et la densité (g/cm³) en masse linéaire (g/mm)."""
    if outil is None:
        return None
    diametre, densite = (
        reglages.valeur_indice(config, cle, outil, lecteur="index_exact")
        for cle in ("filament_diameter", "filament_density")
    )
    if any(v is None or not math.isfinite(v) or v <= 0 for v in (diametre, densite)):
        return None
    return math.pi * (diametre / 2) ** 2 * densite / 1000


def analyser(source, *, bornes_z=()):
    """Renvoie le bilan des dépôts (filament mm, masse g, temps nominal s) sans modifier le 3MF.

    Les bornes Z en mm, croissantes, partagent les dépôts selon leur altitude de fin.
    PASS signifie que la masse et le temps de tous les dépôts détectés sont calculables.
    Une donnée manquante donne NON VÉRIFIÉ en conservant les autres mesures.
    """
    bornes = list(bornes_z)
    if (len(bornes) > 100 or any(not math.isfinite(v) for v in bornes)
            or any(a >= b for a, b in zip(bornes, bornes[1:]))):
        raise ValueError("Les bornes Z doivent être finies et strictement croissantes (au plus 100).")
    groupes = {}
    erreurs = []
    massiques = {}
    with Archive3MF(source) as archive:
        config = archive.reglages()
        plaques = plaques_gcode_archive(archive)
        info = archive.xml("Metadata/slice_info.config") if "Metadata/slice_info.config" in archive.noms() else None
        filaments = {}
        if info is not None:
            indices = Counter()
            for plaque in info.findall("plate"):
                index = plaque.find("metadata[@key='index']")
                valeur = index.get("value", "") if index is not None else ""
                if not valeur.isascii() or not valeur.isdecimal() or len(valeur) > 4:
                    continue
                numero = int(valeur)
                indices[numero] += 1
                ids = [f.get("id", "") for f in plaque.findall("filament")]
                if len(ids) == 1 and ids[0].isascii() and ids[0].isdecimal() and 0 < len(ids[0]) <= 4:
                    filaments[numero] = int(ids[0]) or None
            filaments = {k: v for k, v in filaments.items() if indices[k] == 1}
        for numero, membre in plaques:
            mono = filaments.get(numero)
            lecteur = Interpreter(tool=mono - 1 if mono else None, error_limit=50)
            for ligne in iter_lignes_gcode_3mf(source, membre, archive=archive):
                mouvement = lecteur.feed_line(ligne)
                if not mouvement or mouvement["deposited"] <= 0 or mouvement["deposited_length"] <= 0:
                    continue
                outil = mouvement["tool"]
                zone = bisect_right(bornes, mouvement["end"][2])
                objet, type_trajet = mouvement["object"], mouvement["type"]
                cle = (numero, outil, objet, type_trajet, zone)
                if cle not in groupes and len(groupes) >= 20000:
                    raise ValueError("Le bilan dépasse le budget de 20 000 groupes de dépôts.")
                if outil not in massiques:
                    massiques[outil] = _masse_lineaire(config, outil)
                masse = massiques[outil]
                poste = groupes.setdefault(cle, dict(
                    plaque=numero, filament=outil + 1 if outil is not None else None,
                    objet=objet, type=type_trajet, zone_z=zone, filament_mm=0.0,
                    matiere_g=0.0 if masse is not None else None, temps_depot_nominal_s=0.0,
                ))
                poste["filament_mm"] += mouvement["deposited"]
                if masse is not None:
                    poste["matiere_g"] += mouvement["deposited"] * masse
                # La partie du mouvement qui désamorce la rétraction n’est pas du dépôt.
                if mouvement["feed"] > 0 and poste["temps_depot_nominal_s"] is not None:
                    poste["temps_depot_nominal_s"] += mouvement["deposited_length"] / mouvement["feed"] * 60
                else:
                    poste["temps_depot_nominal_s"] = None
            erreurs.extend(f"Plaque {numero} : {e}" for e in lecteur.errors)
        archive.verifier_source(source)
    lignes = list(groupes.values())
    statut = "PASS" if lignes and not erreurs and all(
        r["matiere_g"] is not None and r["temps_depot_nominal_s"] is not None for r in lignes
    ) else "NON VÉRIFIÉ"
    return dict(statut=statut, code=code_statut(statut), bornes_z_mm=bornes, groupes=lignes, erreurs=erreurs,
                temps_source="Dépôt à l’avance programmée, sans déplacements à vide ni accélérations")


def main(argv=None, *, prive=None):
    """Écrit le bilan privé en mm, g et s, puis affiche un résumé ou le détail.

    Le code conserve PASS ou NON VÉRIFIÉ. Les sources et sorties existantes
    restent intactes.
    """
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("source", type=Path)
    parseur.add_argument("--borne-z", type=float, action="append", default=[])
    parseur.add_argument("--sortie", type=Path, required=True)
    parseur.add_argument("--detail", action="store_true", help="Affiche le même JSON complet que le fichier")
    args = parseur.parse_args(argv)
    try:
        cible = destination_neuve(args.sortie, prive=prive)
        rapport = analyser(args.source, bornes_z=args.borne_z)
        ecrire_json_neuf(cible, rapport, entrees=(args.source,))
    except (OSError, ValueError, KeyError) as erreur:
        parseur.error(str(erreur))
    if args.detail:
        print(json.dumps(rapport, ensure_ascii=False, allow_nan=False))
        return rapport["code"]
    temps = [r for r in rapport["groupes"] if r["temps_depot_nominal_s"] is not None]
    matiere = [r for r in rapport["groupes"] if r["matiere_g"] is not None]
    resume = dict(
        statut=rapport["statut"], rapport=str(cible), groupes=len(rapport["groupes"]),
        poste_temps=max(temps, key=lambda r: r["temps_depot_nominal_s"], default=None),
        poste_matiere=max(matiere, key=lambda r: r["matiere_g"], default=None),
    )
    if rapport["statut"] == "NON VÉRIFIÉ":
        if rapport["erreurs"]:
            resume["raison"] = rapport["erreurs"][0]
        elif not rapport["groupes"]:
            resume["raison"] = "Aucun dépôt exploitable."
        else:
            resume["raison"] = "Masse ou durée nominale indéterminée pour au moins un groupe."
    print(json.dumps(resume, ensure_ascii=False))
    return rapport["code"]
