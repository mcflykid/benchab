"""Enveloppes réelles des objets extrudés par plaque dans un G-code."""

import argparse
import itertools
import json
import sys
import zipfile
from pathlib import Path

from benchab.controles.gcode_interpreter import Interpreter
from benchab.controles.rapports import RapportCourant
from benchab.gcode.lecture import lire_texte_gcode_3mf
from benchab.machine.configuration import charger as charger_configuration


def chemin(value):
    """Convertit une valeur en chemin absolu et harmonise l’alias macOS /var, sans lire le fichier."""
    path = Path(value).absolute()
    if sys.platform == "darwin" and path.is_relative_to("/var") and Path("/var").resolve() == Path("/private/var"):
        return Path("/private/var") / path.relative_to("/var")
    return path


def _plaque_conforme(plaque):
    return (
        not plaque["erreurs"]
        and plaque["dans_le_plateau"]
        and not plaque["recouvrements_enveloppes"]
        and plaque["objets"] == plaque["objets_attendus"]
    )


def analyser(fichier, placement, plateau=None):
    """Mesure les dépôts par nom d'objet et vérifie leur enveloppe sur le plateau."""
    dimensions = plateau or charger_configuration()["placement"]["plateau_xyz_mm"]
    if len(dimensions) != 3 or any(not isinstance(v, (int, float)) or v <= 0 for v in dimensions):
        raise ValueError("Dimensions finies et positives de plateau requises")
    resultats = {"source": Path(fichier).name, "plaques": []}
    for numero, plaque in enumerate(placement["plates"], 1):
        enveloppes = {}
        interpreteur = Interpreter()
        gcode = lire_texte_gcode_3mf(fichier, f"Metadata/plate_{numero}.gcode")
        for ligne in gcode.splitlines():
            mouvement = interpreteur.feed_line(ligne)
            if mouvement and mouvement["trajectory"] == "arc_g17_xy":
                interpreteur.error("arc analytique non évalué par les enveloppes linéaires")
                continue
            if not mouvement or mouvement["deposited_length"] <= 0:
                continue
            x0, y0 = mouvement["deposition_start"][:2]
            x1, y1 = mouvement["end"][:2]
            nom = mouvement["object"] or "hors objet"
            boite = enveloppes.setdefault(nom, [float("inf"), float("inf"), float("-inf"), float("-inf")])
            boite[:] = [min(boite[0], x0, x1), min(boite[1], y0, y1), max(boite[2], x0, x1), max(boite[3], y0, y1)]
        objets = {nom: boite for nom, boite in enveloppes.items() if nom != "hors objet"}
        recouvrements = []
        ecarts = []
        for (nom_a, a), (nom_b, b) in itertools.combinations(objets.items(), 2):
            dx = max(b[0] - a[2], a[0] - b[2])
            dy = max(b[1] - a[3], a[1] - b[3])
            ecarts.append(max(dx, dy))
            if dx < 0 and dy < 0:
                recouvrements.append((nom_a, nom_b))
        xs = [valeur for boite in objets.values() for valeur in (boite[0], boite[2])]
        ys = [valeur for boite in objets.values() for valeur in (boite[1], boite[3])]
        attendus = sum(1 for objet in placement["objects"] if objet["plate"] == numero)
        if not xs or not ys:
            resultats["plaques"].append(
                {
                    "plaque": plaque["name"],
                    "objets": 0,
                    "objets_attendus": attendus,
                    "dans_le_plateau": False,
                    "recouvrements_enveloppes": [],
                    "erreurs": interpreteur.errors + ["aucun dépôt par objet"],
                }
            )
            continue
        rapport_plaque = {
            "erreurs": interpreteur.errors,
            "plaque": plaque["name"],
            "objets": len(objets),
            "objets_attendus": attendus,
            "enveloppe_mm": [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)],
            "dans_le_plateau": bool(
                min(xs) >= 0 and min(ys) >= 0 and max(xs) <= dimensions[0] and max(ys) <= dimensions[1]
            ),
            "recouvrements_enveloppes": recouvrements,
            "ecart_min_entre_objets_mm": round(min(ecarts), 1) if ecarts else None,
            "hors_objet_ligne_de_purge_du_demarrage": [round(v, 1) for v in enveloppes["hors objet"]]
            if "hors objet" in enveloppes
            else None,
        }
        resultats["plaques"].append(rapport_plaque)
    resultats["conforme"] = bool(resultats["plaques"]) and all(map(_plaque_conforme, resultats["plaques"]))
    return resultats


def controler(fichier, emplacement, sortie):
    """Compare l’enveloppe linéaire déposée du 3MF au placement déclaré, en mm, et publie un rapport numérique."""
    source, path_placement, destination = map(chemin, (fichier, emplacement, sortie))
    publication = RapportCourant(destination, [source, path_placement], "disposition_reelle")
    placement = json.loads(path_placement.read_text())
    resultat = analyser(source, placement)
    publication.publier(resultat)
    return resultat


def main(argv=None):
    """Contrôle les enveloppes en mm et conserve le rapport numérique complet.

    Le résumé localise la première plaque en écart. Codes 0 conforme,
    1 écart ou 2 refus, sans modification du 3MF ni preuve physique.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("placement", type=Path)
    parser.add_argument("rapport", type=Path)
    parser.add_argument("--resume", action="store_true", help="Affiche le verdict et la première plaque en écart")
    args = parser.parse_args(argv)
    try:
        resultat = controler(args.source, args.placement, args.rapport)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, TypeError) as error:
        print("Contrôle refusé : " + str(error), file=sys.stderr)
        return 2
    if args.resume:
        resume = {
            "statut": "PASS" if resultat["conforme"] else "FAIL",
            "rapport": str(chemin(args.rapport)), "nombre_plaques": len(resultat["plaques"]),
        }
        for plaque in resultat["plaques"]:
            if not _plaque_conforme(plaque):
                resume["premiere_plaque_en_ecart"] = plaque
                break
        if not resultat["plaques"]:
            resume["raison"] = "Aucune plaque dans le placement déclaré."
        print(json.dumps(resume, ensure_ascii=False, allow_nan=False))
    else:
        for plaque in resultat["plaques"]:
            print(plaque)
        print("conforme", resultat["conforme"])
    return 0 if resultat["conforme"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
