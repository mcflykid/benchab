"""

Débit commandé par objet et outil. Critère : p99 pondéré par longueur,
plafond déclaré +5 %. Sans dépôt mesurable ou plafond valide : non conforme.
usage : debit_gcode.py <tranche.3mf> [--plafonds 1=32,2=16] [--sortie fichier.json]


"""

import argparse
import collections
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

from benchab import reglages as schema_reglages
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import lire_texte_gcode_3mf
from benchab.qualite.statuts import code_statut

AIRE = math.pi * (1.75 / 2) ** 2


def positive(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) and v > 0 else None
    except (TypeError, ValueError):
        return None


def analyser(path, overrides=None, attendus=None):
    """Exécute analyser avec les entrées déclarées et renvoie le résultat du contrôle. Réglages en
    unités natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé."""
    with zipfile.ZipFile(path) as z:
        membres = z.namelist()
        if len(membres) != len(set(membres)) or z.testzip() is not None:
            raise ValueError("archive dupliquée ou corrompue")
        st = schema_reglages.decoder(z.read("Metadata/project_settings.config"))
        caps = {str(i + 1): positive(v) for i, v in enumerate(st.get("filament_max_volumetric_speed", []))}
        if overrides is not None:
            caps = {str(k): positive(v) for k, v in overrides.items()}
        si = E.fromstring(z.read("Metadata/slice_info.config"))
        res = {
            "fichier": Path(path).name,
            "plafonds_mm3_s": caps,
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
                return {
                    **res, "conforme": False, "statut": "NON VÉRIFIÉ", "code": code_statut("NON VÉRIFIÉ"),
                    "motif": f"slice_info.config : index absent, dupliqué ou invalide à la plaque XML {rang}",
                }
            indices.append(int(valeurs[0]))
        gc = {
            int(re.fullmatch(r"Metadata/plate_(\d+)\.gcode", n).group(1))
            for n in membres
            if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)
        }
        if (
            not indices
            or len(set(indices)) != len(indices)
            or set(indices) != gc
            or set(indices) != set(range(1, len(indices) + 1))
        ):
            raise ValueError("plaques G-code et slice_info absentes, dupliquées ou incohérentes")
        if attendus is not None and {int(k) for k in attendus} != set(indices):
            raise ValueError("plaques différentes du manifeste attendu")
        if "Metadata/model_settings.config" in membres:
            model = E.fromstring(z.read("Metadata/model_settings.config"))
            if len(model.findall("plate")) != len(indices):
                raise ValueError("plaques différentes du modèle")
        for plate in si.findall("plate"):
            md = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
            k = int(md["index"])
            fil = [f.get("id") for f in plate.findall("filament")]
            errors = []
            if not fil or len(set(fil)) != len(fil) or any(not i or not i.isdigit() or int(i) < 1 for i in fil):
                errors.append("filaments déclarés absents ou invalides")
            cap = min((caps[i] for i in fil), default=None) if fil and all(caps.get(i) for i in fil) else None
            if cap is None:
                errors.append("plafond absent ou invalide pour les filaments déclarés")
            it = Interpreter(tool=int(fil[0]) - 1 if len(fil) == 1 else None)
            samples = collections.defaultdict(list)
            g = lire_texte_gcode_3mf(path, f"Metadata/plate_{k}.gcode", erreurs="strict")
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

            def identity(name):
                if name in expected:
                    return name
                base = re.sub(r"_id_\d+_copy_\d+$", "", name)
                return base if base in expected else name

            for line in g.splitlines():
                m = it.feed_line(line)
                if not m or m["deposited"] <= 0 or m["deposited_length"] <= 0:
                    continue
                tool = str(m["tool"] + 1) if m["tool"] is not None else None
                if tool not in fil:
                    errors.append("outil extrudant absent ou contradictoire avec slice_info : " + str(tool))
                if m["feed"] <= 0:
                    errors.append("vitesse inconnue")
                    continue
                if m["object"]:
                    q = m["deposited"] * AIRE / (m["deposited_length"] / m["feed"] * 60)
                    samples[identity(m["object"])].append((q, m["deposited_length"], tool))
            missing = set(expected) - samples.keys()
            extra = samples.keys() - set(expected)
            if missing:
                errors.append("objets sans extrusion exploitable : " + ", ".join(sorted(missing)))
            if extra:
                errors.append("objets extrudés non attendus : " + ", ".join(sorted(extra)))
            errors = list(dict.fromkeys(errors + it.errors))
            objects = []
            for name, values in samples.items():
                ordered = sorted(values)
                total = sum(v[1] for v in values)
                acc = 0.0
                p99 = 0.0
                for q, length, _ in ordered:
                    acc += length
                    if acc >= 0.99 * total:
                        p99 = q
                        break
                # Chaque outil conserve son propre p99 ; une petite contribution
                # d'un second filament ne disparaît pas dans le percentile global.
                tools = []
                for tool in sorted({v[2] for v in values}, key=str):
                    subset = sorted((q, length) for q, length, t in values if t == tool)
                    length_total = sum(length for _, length in subset)
                    acc = 0.0
                    q99 = 0.0
                    for q, length in subset:
                        acc += length
                        if acc >= 0.99 * length_total:
                            q99 = q
                            break
                    limit = caps.get(tool)
                    tools.append(
                        {
                            "filament": tool,
                            "plafond_mm3_s": limit,
                            "debit_p99": q99,
                            "conforme": limit is not None and q99 <= 1.05 * limit,
                        }
                    )
                objects.append(
                    {
                        "objet": name,
                        "debit_max": round(max(v[0] for v in values), 3),
                        "debit_p99": round(p99, 3),
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
                    "objets_attendus": expected,
                    "source_objets": source,
                    "couverture_complete": not errors,
                    "mesure": "débit nominal commandé ; dynamique et firmware non simulés",
                    "filaments": fil,
                    "plafond": cap,
                    "objets": objects,
                    "erreurs": errors,
                    "conforme": bool(objects) and not errors and all(o["conforme"] for o in objects),
                }
            )
        res["conforme"] = bool(res["plaques"]) and all(p["conforme"] for p in res["plaques"])
        manque_plafond = any(p["plafond"] is None for p in res["plaques"])
        depassement_etabli = any(
            outil["plafond_mm3_s"] is not None and not outil["conforme"]
            for plaque in res["plaques"] for objet in plaque["objets"] for outil in objet.get("outils", [])
        )
        res["statut"] = "PASS" if res["conforme"] else (
            "NON VÉRIFIÉ" if manque_plafond and not depassement_etabli else "FAIL"
        )
        res["code"] = code_statut(res["statut"])
        return res


def main(args):
    """Contrôle U25 en mm³/s ; imprime le verdict et son code, sans modifier le 3MF."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--plafonds", help="Plafonds déclarés, sous la forme 1=32,2=16.")
    parser.add_argument("--sortie", type=Path)
    try:
        options = parser.parse_args(args)
    except SystemExit as arret:
        return arret.code
    try:
        overrides = dict(v.split("=") for v in options.plafonds.split(",")) if options.plafonds is not None else None
        result = analyser(options.source, overrides)
        if options.sortie:
            options.sortie.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
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
