"""Contrôle le débit commandé par objet et outil, en mm³/s.

U25 donne FAIL pour chaque dépassement établi du plafond déclaré et NON VÉRIFIÉ
si les données sont insuffisantes. Le p99 pondéré par longueur reste informatif.
Commande : debit_gcode.py <tranche.3mf> [--plafonds 1=32,2=16] [--sortie fichier.json]
"""

import argparse
import collections
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

from benchab import reglages as schema_reglages
from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode import debit
from benchab.gcode.lecture import lire_texte_gcode_3mf, plaques_gcode_archive
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.qualite.statuts import code_statut


def positive(value):
    """Lit une déclaration positive finie ; float ou None, unités de la déclaration conservées."""
    return debit.positif(value)


def _nom_moteur(nom):
    """Normalise un nom sans unité comme le moteur Anycubic, en conservant les accents."""
    nom = re.sub(r"""[ !@#$%^&*()=+\[\]{};:",']+""", "_", nom)
    return nom.removeprefix("_").removesuffix("_")


def analyser(path, overrides=None, attendus=None):
    """Contrôle U25 par objet et outil, depuis le membre exact de chaque plaque.

    Reçoit un 3MF tranché, des plafonds optionnels en mm³/s et les objets
    attendus par plaque. Retourne mesures et statut PASS, FAIL ou NON VÉRIFIÉ ;
    des membres ambigus sont refusés. Les réglages passent par le dictionnaire
    typé et ni l’archive ni ses réglages ne sont modifiés.
    """
    with Archive3MF(path) as z:
        membres = z.noms()
        # U25 doit mesurer les octets de la plaque identifiée, même si son nom
        # comporte un zéro initial (docs/reference/debit.md).
        gc = dict(plaques_gcode_archive(z))
        st = z.reglages()
        caps_embarques = {
            str(i + 1): positive(v) for i, v in enumerate(st.get("filament_max_volumetric_speed", []))
        }
        caps = caps_embarques
        if overrides is not None:
            caps = {str(k): positive(v) for k, v in overrides.items()}
        si = z.xml("Metadata/slice_info.config")
        res = {
            "fichier": Path(path).name,
            "plafonds_mm3_s": caps,
            "source_plafond_embarque": "3MF project_settings.filament_max_volumetric_speed",
            "source_plafonds": "--plafonds"
            if overrides is not None
            else "3MF project_settings.filament_max_volumetric_speed",
            "plaques": [],
        }
        indices = []
        for rang, plaque in enumerate(si.findall("plate"), start=1):
            valeurs = [m.get("value") for m in plaque.findall("metadata") if m.get("key") == "index"]
            if len(valeurs) != 1 or not valeurs[0] or not re.fullmatch(r"[0-9]+", valeurs[0]):
                # U25 exige l'identité de la plaque avant d'interpréter son débit.
                # Voir docs/reference/controles.md.
                z.verifier_source(path)
                return {
                    **res, "conforme": False, "statut": "NON VÉRIFIÉ", "code": code_statut("NON VÉRIFIÉ"),
                    "motif": f"slice_info.config : index absent, dupliqué ou invalide à la plaque XML {rang}",
                }
            indices.append(int(valeurs[0]))
        if (
            not indices
            or len(set(indices)) != len(indices)
            or set(indices) != gc.keys()
            or set(indices) != set(range(1, len(indices) + 1))
        ):
            raise ValueError("plaques G-code et slice_info absentes, dupliquées ou incohérentes")
        if attendus is not None and {int(k) for k in attendus} != set(indices):
            raise ValueError("plaques différentes du manifeste attendu")
        if "Metadata/model_settings.config" in membres:
            model = z.xml("Metadata/model_settings.config")
            if len(model.findall("plate")) != len(indices):
                raise ValueError("plaques différentes du modèle")
        for plate in si.findall("plate"):
            md = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
            k = int(md["index"])
            fil = [f.get("id") for f in plate.findall("filament")]
            errors = []
            if not fil or len(set(fil)) != len(fil) or any(
                not i or not re.fullmatch(r"[0-9]+", i) or int(i) < 1 for i in fil
            ):
                errors.append("filaments déclarés absents ou invalides")
            cap = min((caps[i] for i in fil), default=None) if fil and all(caps.get(i) for i in fil) else None
            if cap is None:
                errors.append("plafond absent ou invalide pour les filaments déclarés")
            it = Interpreter(tool=int(fil[0]) - 1 if len(fil) == 1 and not errors else None)
            samples = collections.defaultdict(list)
            pics = {}
            depassements_embarques = collections.Counter()
            depots_supports = 0
            trajets_supports_mm = 0.0
            longueurs_embarquees = collections.Counter()
            g = lire_texte_gcode_3mf(path, gc[k], erreurs="strict", archive=z)
            declared = [o.get("name") for o in plate.findall("object") if o.get("skipped", "false") != "true"]
            definitions = re.findall(r"^EXCLUDE_OBJECT_DEFINE\s+NAME=(\S+)", g, re.M)
            expected = (
                list(attendus.get(k, attendus.get(str(k), []))) if attendus is not None else declared or definitions
            )
            source = (
                "manifeste explicite"
                if attendus is not None
                else "slice_info.object"
                if declared
                else "G-code EXCLUDE_OBJECT_DEFINE"
            )
            if not expected or any(not n for n in expected) or len(set(expected)) != len(expected):
                errors.append("couverture des objets attendus absente ou ambiguë")

            noms_moteur = collections.defaultdict(list)
            for nom in expected:
                if nom:
                    noms_moteur[_nom_moteur(nom)].append(nom)
            # Le mode vase d’ASN peut omettre les balises d’objet (docs/reference/debit.md).
            vase_unique = (
                "spiral_mode" in st and schema_reglages.valeur_brute(st, "spiral_mode") == "1"
                and len(expected) == 1 and bool(expected[0]) and not definitions
                and not re.search(r"^EXCLUDE_OBJECT_START\b", g, re.M)
            )
            if vase_unique:
                source += " (objet unique, mode vase par plaque)"
            ambigu = False

            def identity(name):
                base = re.sub(r"_id_\d+_copy_\d+$", "", name)
                if base == name and name in expected:
                    return name
                candidats = noms_moteur.get(base, [])
                if len(candidats) > 1:
                    return None
                if name in expected:
                    return name
                return candidats[0] if candidats else name

            for line in g.splitlines():
                m = it.feed_line(line)
                if not m or m["deposited"] <= 0 or m["deposited_length"] <= 0:
                    continue
                # Les supports peuvent être déposés en dehors des blocs d’objet du trancheur.
                if (m["type"] or "").casefold() in {"support", "support interface", "support transition"}:
                    depots_supports += 1
                    trajets_supports_mm += m["deposited_length"]
                if it.first_object_error:
                    # Les dépôts restent lisibles. Leur attribution après la rupture du bloc ne l’est plus (#330).
                    continue
                nom = m["object"]
                if nom is not None:
                    nom = identity(nom)
                    if nom is None:
                        ambigu = True
                        continue
                elif vase_unique and m["type"] in {
                    "Outer wall", "Outer perimeter", "Inner wall", "Internal perimeter",
                    "Bottom surface", "Solid infill", "Internal solid infill", "Gap infill", "Gap fill",
                }:
                    # Les purges et les bordures ne décrivent pas une extrusion de l’objet.
                    nom = expected[0]
                tool = str(m["tool"] + 1) if m["tool"] is not None else None
                if tool not in fil:
                    errors.append("outil extrudant absent ou contradictoire avec slice_info : " + str(tool))
                if nom:
                    diametre = schema_reglages.valeur_indice(
                        st, "filament_diameter", int(tool) - 1, lecteur="index_exact"
                    ) if tool is not None else None
                    q = debit.mesurer(m, diametre)["debit_mm3_s"]
                    longueur = m["deposited_length"]
                    samples[nom].append((q, longueur, tool))
                    cle = (nom, tool)
                    if debit.verdict(q, caps_embarques.get(tool)) == "FAIL":
                        depassements_embarques[cle] += 1
                        longueurs_embarquees[cle] += longueur
                    if q is not None and (cle not in pics or q > pics[cle]["debit_mm3_s"]):
                        # Un seul mouvement par outil et objet suffit à localiser le maximum sans gonfler le rapport.
                        pics[cle] = {
                            "debit_mm3_s": q, "ligne": m["line"], "couche_index": m["layer"],
                            "z_mm": m["end"][2], "type_trajet": m["type"], "longueur_mm": longueur,
                            "extrusion_mm": m["deposited"], "avance_mm_min": m["feed"],
                        }
            if ambigu:
                errors.append("identité moteur ambiguë")
            missing = {nom for nom in expected if nom} - samples.keys()
            extra = samples.keys() - set(expected)
            if missing:
                errors.append("objets sans extrusion exploitable : " + ", ".join(sorted(missing)))
            if extra:
                errors.append("objets extrudés non attendus : " + ", ".join(sorted(extra)))
            errors = list(dict.fromkeys(errors + it.errors))
            objects = []
            for name, values in samples.items():
                global_objet = debit.bilan([(q, length) for q, length, _ in values], cap)
                # U25 compare chaque outil à son plafond propre ; le p99 reste informatif.
                # Voir docs/reference/debit.md : aucun diamètre implicite ni marge de 5 %.
                tools = []
                for tool in sorted({v[2] for v in values}, key=str):
                    subset = [(q, length) for q, length, t in values if t == tool]
                    limit = caps.get(tool)
                    resultat = debit.bilan(subset, limit)
                    tools.append(
                        {
                            "filament": tool,
                            "plafond_mm3_s": limit,
                            "plafond_embarque_mm3_s": caps_embarques.get(tool),
                            "depassements_plafond_embarque": (
                                depassements_embarques[name, tool] if caps_embarques.get(tool) is not None else None
                            ),
                            "longueur_hors_plafond_embarque_mm": (
                                longueurs_embarquees[name, tool] if caps_embarques.get(tool) is not None else None
                            ),
                            "longueur_deposee_mm": sum(length for _, length in subset),
                            "pic": pics.get((name, tool)),
                            "debit_p99": resultat["p99_mm3_s"],
                            "debit_max": resultat["maximum_mm3_s"],
                            "depassements": resultat["depassements"],
                            "statut": resultat["statut"],
                            "conforme": resultat["statut"] == "PASS",
                        }
                    )
                objects.append(
                    {
                        "objet": name,
                        "debit_max": (
                            round(global_objet["maximum_mm3_s"], 3)
                            if global_objet["maximum_mm3_s"] is not None else None
                        ),
                        "debit_p99": (
                            round(global_objet["p99_mm3_s"], 3) if global_objet["p99_mm3_s"] is not None else None
                        ),
                        "outils": tools,
                        "conforme": not errors and all(t["conforme"] for t in tools),
                    }
                )
            for name in sorted(missing):
                objects.append({"objet": name, "conforme": False, "erreur": "aucune extrusion exploitable"})
            if not objects:
                errors.append("aucun dépôt par objet analysable")
            res["plaques"].append(
                {
                    "plaque": k,
                    "segments_depot_support": depots_supports,
                    "trajets_supports_mm": round(trajets_supports_mm, 3),
                    "lecture_depots_supports_complete": not it.errors,
                    "objets_attendus": expected,
                    "source_objets": source,
                    "couverture_complete": not errors,
                    "mesure": "débit nominal commandé ; dynamique et firmware non simulés",
                    "filaments": fil,
                    "plafond": cap,
                    "objets": objects,
                    "erreurs": errors,
                    **({"premier_bloc_objet_incoherent": {"plaque": k, **it.first_object_error}}
                       if it.first_object_error else {}),
                    "mesure_incomplete": ambigu or bool(missing) or not samples or bool(it.errors),
                    "incoherence_etablie": any(
                        erreur not in it.errors
                        and not erreur.startswith("objets sans extrusion exploitable")
                        and erreur not in {"aucun dépôt par objet analysable", "identité moteur ambiguë"}
                        and erreur != "plafond absent ou invalide pour les filaments déclarés"
                        for erreur in errors
                    ),
                    "conforme": bool(objects) and not errors and all(o["conforme"] for o in objects),
                }
            )
        res["conforme"] = bool(res["plaques"]) and all(p["conforme"] for p in res["plaques"])
        incomplete = any(p["plafond"] is None or p["mesure_incomplete"] for p in res["plaques"]) or any(
            outil["statut"] == "NON VÉRIFIÉ"
            for plaque in res["plaques"] for objet in plaque["objets"] for outil in objet.get("outils", [])
        )
        depassement_etabli = any(
            outil["statut"] == "FAIL"
            for plaque in res["plaques"] for objet in plaque["objets"] for outil in objet.get("outils", [])
        )
        res["statut"] = "PASS" if res["conforme"] else (
            "NON VÉRIFIÉ" if incomplete and not depassement_etabli
            and not any(p["incoherence_etablie"] for p in res["plaques"]) else "FAIL"
        )
        res["code"] = code_statut(res["statut"])
        z.verifier_source(path)
        return res


def _resume(resultat, cible):
    # Donner d’abord la localisation des écarts ; le rapport neuf reste intégral (#35).
    plaques = sorted(resultat["plaques"], key=lambda plaque: plaque["conforme"])
    selection = []
    for plaque in plaques[:3]:
        erreurs = plaque["erreurs"]
        ligne = {
            "plaque": plaque["plaque"], "conforme": plaque["conforme"],
            "erreurs": [erreur[:240] for erreur in erreurs[:3]],
        }
        if diagnostic := plaque.get("premier_bloc_objet_incoherent"):
            ligne["premier_bloc_objet_incoherent"] = {
                cle: valeur[:120] if isinstance(valeur, str) else valeur for cle, valeur in diagnostic.items()
            }
        if len(erreurs) > 3 or any(len(erreur) > 240 for erreur in erreurs[:3]):
            ligne["erreurs_tronquees"] = True
        premier_ecart = next(
            ((objet, outil) for objet in plaque["objets"] for outil in objet.get("outils", [])
             if outil["statut"] == "FAIL"), None,
        )
        if premier_ecart is not None:
            objet, outil = premier_ecart
            ligne["premier_depassement"] = {
                "objet": objet["objet"][:120], "filament": outil["filament"],
                "maximum_mm3_s": outil["debit_max"], "plafond_mm3_s": outil["plafond_mm3_s"],
            }
        selection.append(ligne)
    resume = {
        "statut": resultat["statut"], "rapport": str(cible),
        "plaques": selection, "plaques_omises": len(plaques) - len(selection),
    }
    if resultat.get("motif"):
        resume["motif"] = resultat["motif"][:240]
    return resume


def main(args):
    """Contrôle U25 en mm³/s ; affiche le verdict et conserve le 3MF intact.

    Reçoit les arguments CLI, dont la destination neuve du rapport optionnel.
    Retourne le code historique de PASS, FAIL ou NON VÉRIFIÉ ; l’affichage
    résumé/détaillé exige une preuve JSON écrite et ne modifie pas les mesures.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--plafonds", help="Plafonds déclarés, sous la forme 1=32,2=16.")
    parser.add_argument("--sortie", type=Path, help="Rapport JSON neuf dans l’atelier privé ou un temporaire système.")
    affichage = parser.add_mutually_exclusive_group()
    affichage.add_argument("--resume", action="store_true", help="trois plaques au plus et chemin du rapport")
    affichage.add_argument("--detail", action="store_true", help="afficher le rapport intégral")
    try:
        options = parser.parse_args(args)
        if (options.resume or options.detail) and options.sortie is None:
            parser.error("--resume et --detail exigent --sortie RAPPORT.json")
    except SystemExit as arret:
        return arret.code
    try:
        overrides = dict(v.split("=") for v in options.plafonds.split(",")) if options.plafonds is not None else None
        # Un résultat U25, même FAIL, est une preuve distincte du 3MF examiné.
        cible = destination_neuve(options.sortie) if options.sortie is not None else None
        result = analyser(options.source, overrides)
        if cible is not None:
            ecrire_json_neuf(cible, result, entrees=[options.source])
        if options.resume or options.detail:
            affichage = _resume(result, cible) if options.resume else result
            print(json.dumps(affichage, ensure_ascii=False))
            return result["code"]
        for p in result["plaques"]:
            print("plaque", p["plaque"], "conforme", p["conforme"], "erreurs", p["erreurs"])
        if result.get("motif"):
            print(result["statut"], result["motif"])
        print("conforme", result["conforme"])
        return result["code"]
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, E.ParseError) as ex:
        print("Erreur technique :", ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
