"""

Mesures de commandes et contraintes du profil maître, sans accès machine.

analyser(p3mf, maitre=None, limites_filaments=None) -> rapport JSON.
Les vitesses sont nominales ; accélérations/jonctions et macros ne sont jamais
transformées en preuve de vitesse atteinte ou de durée physique.


"""

import collections
import json
import math
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from benchab.archives_3mf import Archive3MF
from benchab.controles.capacites_entrees import _limites, _metadonnees_archive, _positif, _reglages_filaments, _sha
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode import debit, geometrie_arc
from benchab.gcode.lecture import lire_membre_3mf

RACINE = Path(__file__).resolve().parents[2]
MAX_EXEMPLES = 20


def _nouveau_groupe(tool, obj, kind):
    return {
        "outil": tool,
        "filament": tool + 1 if tool is not None else None,
        "objet": obj,
        "type": kind,
        "segments": 0,
        "longueur_mm": 0.0,
        "longueur_max_mm": 0.0,
        "longueur_deposee_mm": 0.0,
        "volume_depose_mm3": 0.0,
        "temps_nominal_s": 0.0,
        "vitesse_max_mm_s": 0.0,
        "vitesse_axes_max_mm_s": dict.fromkeys("XYZE", 0.0),
        "debit_max_mm3_s": 0.0,
        "retractions": 0,
        "retraction_mm": 0.0,
        "acceleration_commandee_mm_s2": None,
        "acceleration_limitee_axes_mm_s2": None,
        "depassements_debit": 0,
        "_vitesses": collections.Counter(),
        "_debits": collections.Counter(),
        "_longueurs": collections.Counter(),
    }


def _quantile(counter, fraction):
    total = sum(counter.values())
    acc = 0.0
    for value, weight in sorted(counter.items()):
        acc += weight
        if acc >= fraction * total:
            return value
    return None


def _analyser(p3mf, maitre=None, limites_filaments=None):
    path = Path(p3mf)
    data = path.read_bytes()
    rapport = {
        "schema": 1,
        "statut": "NON VÉRIFIÉ",
        "decision": "À revoir",
        "entrees": {"tranche": str(path), "tranche_sha256": _sha(data), "membres": {}},
        "plaques": [],
        "contraintes": [],
        "metriques": {},
        "portee": "Conformité numérique des commandes couvertes ; GO ne garantit aucune impression physique.",
        "limites": [
            "Commandes nominales, pas vitesses/accélérations physiques mesurées.",
            "G9111, T et macros ne sont pas déroulés : leur durée interne reste inconnue.",
            "La vitesse aux jonctions est inconnue ; aucun plafond physique déduit de chaque petit segment.",
            "Aucune affectation physique ACE prouvée par un profil ou un outil T.",
            "XYZ initialise au repère de commande zéro ; homing G28 non simulé.",
        ],
    }
    contraintes = rapport["contraintes"]
    index_contraintes = {}

    def note(code, statut, detail, *, plaque=None, outil=None, objet=None, ligne=None, source=None, obligatoire=True):
        key = (code, statut, plaque, outil, objet)
        if key in index_contraintes:
            row = index_contraintes[key]
            row["occurrences"] += 1
            if ligne is not None and len(row["lignes"]) < MAX_EXEMPLES:
                row["lignes"].append(ligne)
            return
        row = {
            "controle": code,
            "statut": statut,
            "detail": detail,
            "plaque": plaque,
            "outil": outil,
            "objet": objet,
            "occurrences": 1,
            "lignes": [ligne] if ligne is not None else [],
            "source": source,
            "obligatoire": obligatoire,
        }
        index_contraintes[key] = row
        contraintes.append(row)

    try:
        machine, provenance = _limites(maitre)
        rapport["entrees"]["maitre"] = provenance
        rapport["limites_configurees"] = machine
    except (ImportError, OSError) as exc:
        machine = {}
        note("profil maître", "NON VÉRIFIÉ", str(exc))
    except (ValueError, TypeError, KeyError) as exc:
        machine = {}
        note("profil maître", "FAIL", str(exc))
    with Archive3MF(path) as archive:
        archive.verifier()
        settings = archive.reglages()
        if not isinstance(settings, dict):
            raise ValueError("réglages projet objet JSON requis")
        rapport["entrees"]["settings_sha256"] = _sha(archive.lire("Metadata/project_settings.config"))
        filaments = _reglages_filaments(settings, machine, limites_filaments, rapport, note)
        painting, allowed_objects, plate_objects, slice_filaments = _metadonnees_archive(archive)
        import re

        members = sorted(
            (int(m.group(1)), n) for n in archive.noms() if (m := re.fullmatch(r"Metadata/plate_(\d+)\.gcode", n))
        )
        root_v = dict.fromkeys("XYZE", 0.0)
        root_acc = {"axes": {}, "M204": {}, "M205": {}, "SET_VELOCITY_LIMIT": {}}
        total_time = total_volume = support_volume = purge_volume = 0.0
        changes = 0
        all_groups = []
        for number, member in members:
            raw = lire_membre_3mf(path, member)
            text = raw.decode("utf-8", errors="replace")
            rapport["entrees"]["membres"][member] = _sha(raw)
            implicit = (
                settings.get("printer_model") == "Anycubic Kobra S1"
                and len(filaments) == 1
                and set(filaments) == {"1"}
                and not re.search(r"^\s*T\d+(?:\s|;|$)", text, re.M)
                and len(re.findall(r"^\s*G9111\b", text, re.M)) == 1
                and all(tools <= {0} for tools in allowed_objects.values())
            )
            it = Interpreter(tool=0 if implicit else None)
            native_single_nozzle = settings.get("printer_model") == "Anycubic Kobra S1"
            shared_temperature = None
            temperatures = {}
            temperature_counts = collections.Counter()
            fans = {}
            acceleration = {}
            axis_limits = {}
            groups = {}
            events = []
            ptime = pvolume = psupport = ppurge = 0.0
            pchanges = 0
            adhesion = unclassified = 0.0
            last_tool = it.tool
            seen_objects = set()
            deposited_moves = 0
            purge_active = False
            for line in text.splitlines():
                if line.strip() == "; PURGE LINE":
                    purge_active = True
                if line.startswith(("; AFTER_LAYER_CHANGE", "EXCLUDE_OBJECT_START")):
                    purge_active = False
                move = it.feed_line(line)
                event = it.last_event
                if event:
                    command = event["command"]
                    params = event["parameters"]
                    line_no = event["line"]
                    if len(events) < MAX_EXEMPLES and command in {
                        "G9111",
                        "M201",
                        "M203",
                        "M204",
                        "M205",
                        "SET_VELOCITY_LIMIT",
                    }:
                        events.append(event)
                    if command.startswith("T") and command[1:].isdigit():
                        new = int(command[1:])
                        if last_tool is not None and new != last_tool:
                            pchanges += 1
                        last_tool = new
                    if command == "G9111":
                        if "extruderTemp" in params and math.isfinite(params["extruderTemp"]):
                            temperatures[it.tool] = params["extruderTemp"]
                            if native_single_nozzle:
                                shared_temperature = params["extruderTemp"]
                            temperature_counts[(it.tool, params["extruderTemp"], "G9111")] += 1
                        if "bedTemp" in params and _positif(machine.get("temperature_plateau_constructeur_max_c")):
                            limit = machine["temperature_plateau_constructeur_max_c"]
                            value = params["bedTemp"]
                            note(
                                "température plateau maximale",
                                "FAIL" if value > limit else "PASS",
                                {"commande_c": value, "limite_c": limit},
                                plaque=number,
                                ligne=line_no,
                            )
                        # Opaque by design : no movement or time fabricated.
                    elif command in {"M104", "M109"}:
                        target = params.get("T", it.tool)
                        if target is not None and (
                            isinstance(target, bool) or float(target) < 0 or not float(target).is_integer()
                        ):
                            note(
                                "outil température", "FAIL", "outil T de chauffe invalide", plaque=number, ligne=line_no
                            )
                        elif "S" in params or "R" in params:
                            target = int(target) if target is not None else None
                            temperatures[target] = params.get("S", params.get("R"))
                            if native_single_nozzle:
                                if "T" not in params or target == it.tool:
                                    shared_temperature = temperatures[target]
                                else:
                                    shared_temperature = None
                                    note(
                                        "cible température buse unique",
                                        "NON VÉRIFIÉ",
                                        "M104/M109 cible outil non actif : effet firmware non modélisé",
                                        plaque=number,
                                        outil=target,
                                        ligne=line_no,
                                    )
                            temperature_counts[(target, temperatures[target], command)] += 1
                    elif command in {"M106", "M107"}:
                        fans[str(params.get("P", 0))] = params.get("S", 255) if command == "M106" else 0
                    elif (
                        command in {"M140", "M190"}
                        and "S" in params
                        and _positif(machine.get("temperature_plateau_constructeur_max_c"))
                    ):
                        limit = machine["temperature_plateau_constructeur_max_c"]
                        value = params["S"]
                        note(
                            "température plateau maximale",
                            "FAIL" if value > limit else "PASS",
                            {"commande_c": value, "limite_c": limit},
                            plaque=number,
                            ligne=line_no,
                        )
                    elif command == "SET_VELOCITY_LIMIT":
                        root_acc["SET_VELOCITY_LIMIT"].update(params)
                        for key, value in params.items():
                            canonical = {"VELOCITY": "vitesse_xy_mm_s", "ACCEL": "acceleration_xy_mm_s2"}.get(key)
                            if canonical:
                                limit = _positif(machine.get(canonical))
                                note(
                                    "SET_VELOCITY_LIMIT " + key,
                                    "FAIL"
                                    if limit is not None and value > limit + 1e-8
                                    else "PASS"
                                    if limit is not None
                                    else "NON VÉRIFIÉ",
                                    {
                                        "commande": value,
                                        "limite_configuree": limit,
                                        "application_firmware": "NON VÉRIFIÉ",
                                    },
                                    plaque=number,
                                    ligne=line_no,
                                )
                    elif command in {"M201", "M203"}:
                        unit = "mm_s2" if command == "M201" else "mm_s"
                        stem = "acceleration" if command == "M201" else "vitesse"
                        for axis, value in params.items():
                            if axis not in "XYZE":
                                note(
                                    "paramètre " + command,
                                    "NON VÉRIFIÉ",
                                    "axe/paramètre non modélisé " + axis,
                                    plaque=number,
                                    ligne=line_no,
                                )
                                continue
                            key = stem + "_" + ("xy" if axis in "XY" else axis.lower()) + "_" + unit
                            limit = _positif(machine.get(key))
                            if command == "M201":
                                root_acc["axes"][axis] = max(root_acc["axes"].get(axis, 0), value)
                            axis_limits[(command, axis)] = value
                            status = (
                                "FAIL"
                                if value <= 0 or (limit is not None and value > limit + 1e-8)
                                else "PASS"
                                if limit is not None
                                else "NON VÉRIFIÉ"
                            )
                            note(
                                command + " " + axis,
                                status,
                                {"commande": value, "limite": limit, "unite": unit},
                                plaque=number,
                                ligne=line_no,
                                source=machine.get("sources", {}).get(key),
                            )
                    elif command == "M204":
                        acceleration.update(params)
                        for key, value in params.items():
                            root_acc["M204"][key] = max(root_acc["M204"].get(key, 0), value)
                            category = {"P": "extruding", "T": "travel", "R": "retracting"}.get(key)
                            limits = (
                                [_positif(machine.get("acceleration_" + category + "_mm_s2"))]
                                if category
                                else [
                                    _positif(machine.get("acceleration_extruding_mm_s2")),
                                    _positif(machine.get("acceleration_travel_mm_s2")),
                                ]
                                if key == "S"
                                else []
                            )
                            limits = [x for x in limits if x is not None]
                            limit = min(limits) if limits else None
                            status = (
                                "FAIL"
                                if value <= 0 or (limit is not None and value > limit + 1e-8)
                                else "PASS"
                                if limit is not None
                                else "NON VÉRIFIÉ"
                            )
                            note(
                                "M204 " + key,
                                status,
                                {
                                    "commande": value,
                                    "limite": limit,
                                    "portee": "paramètre commandé ; effet firmware et jonctions non mesurés",
                                },
                                plaque=number,
                                ligne=line_no,
                            )
                    elif command == "M205":
                        root_acc["M205"].update(params)
                if not move:
                    continue
                arc = move["trajectory"] == "arc_g17_xy"
                if arc:
                    note(
                        "trajectoire géométrique",
                        "NON VÉRIFIÉ",
                        "vitesses et accélérations par axe exigent une cinématique courbe ; "
                        "aucune corde n'est utilisée",
                        plaque=number,
                        outil=move["tool"],
                        objet=move["object"],
                        ligne=move["line"],
                    )
                length = move["length"]
                feed = move["feed"] / 60
                tool = move["tool"]
                obj = move["object"]
                kind = move["type"]
                if length <= 0 and abs(move["extrusion"]) <= 0:
                    continue
                if feed <= 0:
                    note(
                        "vitesse nominale",
                        "NON VÉRIFIÉ",
                        "avance F absente/non positive sur déplacement",
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                    )
                group_key = (tool, obj, kind)
                if group_key not in groups:
                    groups[group_key] = _nouveau_groupe(tool, obj, kind)
                group = groups[group_key]
                group["segments"] += 1
                group["longueur_mm"] += length
                group["longueur_max_mm"] = max(group["longueur_max_mm"], length)
                bounds = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
                group["_longueurs"][next((b for b in bounds if length <= b), 1000.0)] += 1
                group["vitesse_max_mm_s"] = max(group["vitesse_max_mm_s"], feed)
                acc_category = (
                    "R" if length <= 0 and abs(move["extrusion"]) > 0 else "P" if move["deposited"] > 0 else "T"
                )
                current_acc = acceleration.get(acc_category, acceleration.get("S"))
                if current_acc is not None and not arc:
                    group["acceleration_commandee_mm_s2"] = max(
                        group["acceleration_commandee_mm_s2"] or 0.0, current_acc
                    )
                    ratios = (
                        {axis: abs(b - a) / length for axis, a, b in zip("XYZ", move["start"], move["end"])}
                        if length > 0
                        else dict.fromkeys("XYZ", 0.0)
                    )
                    ratios["E"] = abs(move["extrusion"]) / length if length > 0 else 1.0
                    projected = [current_acc]
                    complete = True
                    for axis, ratio in ratios.items():
                        if ratio <= 0:
                            continue
                        key = "acceleration_" + ("xy" if axis in "XY" else axis.lower()) + "_mm_s2"
                        bounds = [_positif(machine.get(key)), _positif(axis_limits.get(("M201", axis)))]
                        bounds = [v for v in bounds if v is not None]
                        if bounds:
                            projected.append(min(bounds) / ratio)
                        else:
                            complete = False
                    if complete:
                        group["acceleration_limitee_axes_mm_s2"] = max(
                            group["acceleration_limitee_axes_mm_s2"] or 0.0, min(projected)
                        )
                if length > 0 and feed > 0:
                    duration = length / feed
                    if duration <= 0 or not math.isfinite(duration):
                        note(
                            "calcul nominal",
                            "FAIL",
                            "durée non représentable numériquement",
                            plaque=number,
                            ligne=move["line"],
                        )
                        continue
                    group["_vitesses"][round(feed, 3)] += length
                    speeds = (
                        dict.fromkeys("XYZ", None)
                        if arc
                        else {axis: abs(b - a) / duration for axis, a, b in zip("XYZ", move["start"], move["end"])}
                    )
                    speeds["E"] = abs(move["extrusion"]) / duration
                elif abs(move["extrusion"]) > 0 and feed > 0:
                    duration = abs(move["extrusion"]) / feed
                    speeds = dict.fromkeys("XYZ", 0.0)
                    speeds["E"] = feed
                else:
                    duration = 0.0
                    speeds = dict.fromkeys("XYZE", 0.0)
                group["temps_nominal_s"] += duration
                ptime += duration
                for axis, speed in speeds.items():
                    if speed is None:
                        group["vitesse_axes_max_mm_s"][axis] = None
                        root_v[axis] = None
                        continue
                    group["vitesse_axes_max_mm_s"][axis] = (
                        max(group["vitesse_axes_max_mm_s"][axis], speed)
                        if group["vitesse_axes_max_mm_s"][axis] is not None
                        else None
                    )
                    root_v[axis] = max(root_v[axis], speed) if root_v[axis] is not None else None
                    if speed <= 0:
                        continue
                    key = "vitesse_" + ("xy" if axis in "XY" else axis.lower()) + "_mm_s"
                    limit = _positif(machine.get(key))
                    note(
                        "vitesse " + axis,
                        "NON VÉRIFIÉ" if limit is None or speed > limit + 1e-8 else "PASS",
                        {
                            "demande_nominale_mm_s": speed,
                            "limite_configuree_mm_s": limit,
                            "limite_M203_emise_mm_s": axis_limits.get(("M203", axis)),
                            "application_limites_firmware": "NON VÉRIFIÉ",
                            "portee": "projection de F nominal ; dépassement ne prouve pas vitesse physique réalisée",
                        },
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                        source=machine.get("sources", {}).get(key),
                    )
                if move["extrusion"] < 0:
                    group["retractions"] += 1
                    group["retraction_mm"] -= move["extrusion"]
                if move["deposited"] <= 0 or move["deposited_length"] <= 0:
                    continue
                deposited_moves += 1
                group["longueur_deposee_mm"] += move["deposited_length"]
                volume_limits = machine.get("volume_mm")
                if obj:
                    valid_volume = (
                        isinstance(volume_limits, (list, tuple))
                        and len(volume_limits) == 3
                        and all(_positif(v) is not None for v in volume_limits)
                    )
                    enveloppe = None
                    if valid_volume:
                        try:
                            if arc:
                                enveloppe = geometrie_arc.enveloppe_deposee_xyz(move)
                            else:
                                points = (move["deposition_start"], move["end"])
                                enveloppe = tuple(
                                    (min(point[axe] for point in points), max(point[axe] for point in points))
                                    for axe in range(3)
                                )
                        except (KeyError, TypeError, ValueError, OverflowError):
                            enveloppe = None
                    inside = valid_volume and enveloppe is not None and all(
                        minimum >= -1e-6 and maximum <= float(bound) + 1e-6
                        for (minimum, maximum), bound in zip(enveloppe or (), volume_limits or ())
                    )
                    statut_volume = (
                        "NON VÉRIFIÉ"
                        if not valid_volume or enveloppe is None
                        else "PASS"
                        if inside
                        else "FAIL"
                    )
                    detail_volume = {
                        "bornes_mm": volume_limits,
                        "debut": move["deposition_start"],
                        "fin": move["end"],
                    }
                    if arc:
                        detail_volume.update(
                            {
                                "enveloppe_xyz_mm": enveloppe,
                                "debut_depot_fraction": move.get("arc_g17", {}).get("debut_depot_fraction"),
                                "tolerance_mm": 1e-6,
                            }
                        )
                    note(
                        "volume XYZ déposé",
                        statut_volume,
                        detail_volume,
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                        source=machine.get("sources", {}).get("volume_mm"),
                    )
                if obj:
                    seen_objects.add(re.sub(r"_id_\d+_copy_\d+$", "", obj))
                fid = str(tool + 1) if tool is not None else None
                filament = filaments.get(fid, {})
                identity = re.sub(r"_id_\d+_copy_\d+$", "", obj) if obj else None
                if tool is None or fid not in filaments:
                    note(
                        "attribution filament",
                        "NON VÉRIFIÉ",
                        "dépôt sans outil/filament identifiable",
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                    )
                    continue
                if number in slice_filaments and fid not in slice_filaments[number]:
                    note(
                        "cohérence outil/plaque",
                        "FAIL",
                        "outil absent de slice_info",
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                    )
                if identity in allowed_objects and allowed_objects[identity] and tool not in allowed_objects[identity]:
                    note(
                        "cohérence outil/objet",
                        "NON VÉRIFIÉ" if painting else "FAIL",
                        {
                            "outil_observe": tool,
                            "outils_objet_declares": sorted(allowed_objects[identity]),
                            "peinture_facettes_non_resolue": painting,
                        },
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                    )
                if identity and allowed_objects and identity not in allowed_objects:
                    note(
                        "identité objet",
                        "NON VÉRIFIÉ",
                        "objet non rattaché à model_settings",
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                    )
                diameter = filament.get("diametre_mm")
                cap = filament.get("plafond_mm3_s")
                if diameter is None or cap is None:
                    note(
                        "débit strict",
                        "NON VÉRIFIÉ",
                        "diamètre filament/plafond absent ou invalide",
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                        source=filament.get("source"),
                    )
                    continue
                mesure_debit = debit.mesurer(move, diameter)
                volume = mesure_debit["volume_mm3"]
                if volume is None:
                    note(
                        "calcul débit",
                        "NON VÉRIFIÉ",
                        "dépôt nominal non mesurable avec les données déclarées",
                        plaque=number,
                        ligne=move["line"],
                    )
                    continue
                group["volume_depose_mm3"] += volume
                pvolume += volume
                if (kind or "").startswith("Support"):
                    psupport += volume
                elif kind in {"Skirt", "Brim"}:
                    adhesion += volume
                elif purge_active or kind in {"Prime tower", "Wipe tower", "Custom"}:
                    ppurge += volume
                elif obj is None:
                    unclassified += volume
                if duration <= 0 or feed <= 0:
                    note(
                        "débit strict",
                        "NON VÉRIFIÉ",
                        "durée nominale non mesurable",
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                    )
                    continue
                q = mesure_debit["debit_mm3_s"]
                group["debit_max_mm3_s"] = max(group["debit_max_mm3_s"], q)
                group["_debits"][round(q, 4)] += move["deposited_length"]
                # U25 : même seuil d'arrondi dans tous les parcours, sans marge physique.
                over = debit.verdict(q, cap) == "FAIL"
                group["depassements_debit"] += int(over)
                note(
                    "débit strict",
                    "FAIL" if over else "PASS",
                    {"debit_mm3_s": q, "plafond_mm3_s": cap, "tolerance": "arrondi numérique uniquement"},
                    plaque=number,
                    outil=tool,
                    objet=obj,
                    ligne=move["line"],
                    source=filament.get("source"),
                )
                desired = filament.get("temp_1re_c" if move["layer"] <= 0 else "temperature_c")
                actual = shared_temperature if native_single_nozzle else temperatures.get(tool)
                if actual is not None and _positif(machine.get("temperature_buse_constructeur_max_c")):
                    limit = machine["temperature_buse_constructeur_max_c"]
                    note(
                        "température buse maximale",
                        "FAIL" if actual > limit else "PASS",
                        {"commande_c": actual, "limite_c": limit},
                        plaque=number,
                        outil=tool,
                        ligne=move["line"],
                    )
                if desired is not None:
                    note(
                        "température commandée",
                        "NON VÉRIFIÉ"
                        if actual is None
                        else "FAIL"
                        if not math.isclose(actual, desired, abs_tol=0.01)
                        else "PASS",
                        {
                            "commande_c": actual,
                            "declare_c": desired,
                            "portee": "consigne, aucune mesure thermique physique",
                        },
                        plaque=number,
                        outil=tool,
                        objet=obj,
                        ligne=move["line"],
                        source=filament.get("source"),
                    )
            for error in it.errors:
                invalid = any(word in error for word in ("opcode", "paramètre", "métadonnée", "non positive"))
                note("couverture G-code", "FAIL" if invalid else "NON VÉRIFIÉ", error, plaque=number)
            if not deposited_moves:
                note("dépôts analysables", "NON VÉRIFIÉ", "aucun dépôt avec déplacement", plaque=number)
            for missing in plate_objects.get(number, set()) - seen_objects:
                note(
                    "objet attendu", "NON VÉRIFIÉ", "objet absent des dépôts : " + missing, plaque=number, objet=missing
                )
            exported = []
            for group in groups.values():
                group["vitesse_p99_mm_s"] = _quantile(group.pop("_vitesses"), 0.99)
                group["debit_p99_mm3_s"] = _quantile(group.pop("_debits"), 0.99)
                group["longueurs_histogramme_mm"] = [
                    {"borne_superieure_mm": v if v < 1000 else None, "segments": n}
                    for v, n in sorted(group.pop("_longueurs").items())
                ]
                exported.append(group)
            plaque = {
                "plaque": number,
                "gcode_sha256": _sha(raw),
                "outil_initial_implicite": implicit,
                "groupes": exported,
                "commandes_exemples": events,
                "temps_nominal_s": ptime,
                "volume_depose_mm3": pvolume,
                "support_mm3": psupport,
                "purge_mm3": ppurge,
                "adhesion_mm3": adhesion,
                "hors_objet_non_classe_mm3": unclassified,
                "changements_filament": pchanges,
                "temperatures_commandes_c": [
                    {"outil": t, "temperature_c": v, "commande": c, "occurrences": n}
                    for (t, v, c), n in temperature_counts.items()
                ],
                "ventilateurs_commandes": fans,
                "limites_vitesse_emises_mm_s": {
                    axis: value for (cmd, axis), value in axis_limits.items() if cmd == "M203"
                },
                "application_limites_firmware": {
                    "statut": "NON VÉRIFIÉ",
                    "raison": "limites émises/configurées distinguées de la demande nominale ; aucune mesure firmware",
                },
                "temps_macros": {"statut": "NON VÉRIFIÉ", "raison": "G9111/T/M600 non déroulés"},
                "cinematique": {
                    "statut": "NON VÉRIFIÉ",
                    "raison": "vitesses entrée/sortie, jonctions et effet firmware non mesurés",
                },
            }
            rapport["plaques"].append(plaque)
            all_groups.extend(exported)
            total_time += ptime
            total_volume += pvolume
            support_volume += psupport
            purge_volume += ppurge
            changes += pchanges
        if not members:
            note("plaques tranchées", "NON VÉRIFIÉ", "aucun G-code de plaque")
        rapport["metriques"] = {
            "temps_nominal_s": total_time,
            "volume_depose_mm3": total_volume,
            "support_mm3": support_volume,
            "purge_mm3": purge_volume,
            "changements_filament": changes,
            "vitesses_mm_s": {
                "axes_max": root_v,
                "axes_nominales_max": root_v,
                "statut_application_firmware": "NON VÉRIFIÉ",
                "nominale_max": max((g["vitesse_max_mm_s"] for g in all_groups), default=0.0),
            },
            "accelerations_mm_s2": root_acc,
            "groupes": all_groups,
        }
    if _sha(path.read_bytes()) != rapport["entrees"]["tranche_sha256"]:
        raise ValueError("source modifiée pendant lecture")
    statuses = {c["statut"] for c in contraintes if c["obligatoire"]}
    rapport["statut"] = (
        "FAIL"
        if "FAIL" in statuses
        else "NON VÉRIFIÉ"
        if "NON VÉRIFIÉ" in statuses
        else "WARN"
        if "WARN" in statuses
        else "PASS"
    )
    rapport["decision"] = {"FAIL": "NO GO", "NON VÉRIFIÉ": "À revoir", "WARN": "À revoir", "PASS": "GO"}[
        rapport["statut"]
    ]
    json.dumps(rapport, allow_nan=False)
    return rapport


def analyser(p3mf, maitre=None, limites_filaments=None):
    """
    Façade JSON totale : une entrée illisible est NO GO, jamais un rapport vide PASS.
    """
    try:
        return _analyser(p3mf, maitre, limites_filaments)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        ZeroDivisionError,
        zipfile.BadZipFile,
        ET.ParseError,
    ) as exc:
        return {
            "schema": 1,
            "statut": "FAIL",
            "decision": "NO GO",
            "entrees": {"tranche": str(p3mf)},
            "plaques": [],
            "contraintes": [{"controle": "lecture/analyse", "statut": "FAIL", "detail": str(exc), "obligatoire": True}],
            "metriques": {
                "temps_nominal_s": None,
                "volume_depose_mm3": None,
                "support_mm3": None,
                "changements_filament": None,
                "vitesses_mm_s": {},
                "accelerations_mm_s2": {},
            },
            "limites": ["Analyse impossible ; aucune mesure effectuée ni validation physique."],
        }
