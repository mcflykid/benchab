"""

Diagnostic indépendant des vitesses de paroi ; aucune commande machine ni transformation.

"""

import argparse
import collections
import hashlib
import html
import io
import json
import math
import os
import re
import stat
import sys
import zipfile
from pathlib import Path

from benchab.controles.gcode_interpreter import WORDS, Interpreter

VERSION = "1.0"
DEFAULTS = dict(
    mode="gaussien",
    fenetre=11,
    sigma_couches=2.0,
    seuil_delta_mm_s=5.0,
    max_iterations=1000,
    skip_first_layers=1,
    pente_max_mm_s_par_mm=50.0,
)
MAX_BYTES = 64 * 1024 * 1024
OUTER = {"Outer wall", "Outer perimeter"}
# Politique conservatrice du diagnostic : grammaire modale déléguée à Interpreter.
KNOWN = {
    "G0",
    "G1",
    "G4",
    "G21",
    "G28",
    "G29",
    "G80",
    "G90",
    "G91",
    "G92",
    "G9111",
    "M82",
    "M83",
    "M104",
    "M109",
    "M140",
    "M190",
    "M106",
    "M107",
    "M201",
    "M203",
    "M204",
    "M205",
    "M400",
    "M73",
    "M84",
    "M18",
    "M900",
    "M960",
    "M1002",
    "M1003",
    "M1005",
    "M1006",
    "SET_VELOCITY_LIMIT",
    "SET_PRESSURE_ADVANCE",
}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _options(options):
    cfg = dict(DEFAULTS)
    if options:
        if not isinstance(options, dict) or set(options) - set(cfg):
            raise ValueError("options inconnues")
        cfg.update(options)
    if cfg["mode"] not in {"gaussien", "gradient"}:
        raise ValueError("mode inconnu")
    for name, low, high in [("fenetre", 3, 51), ("max_iterations", 1, 2000), ("skip_first_layers", 1, 100)]:
        v = cfg[name]
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            raise ValueError(name + " hors limites")
    if cfg["fenetre"] % 2 != 1:
        raise ValueError("fenêtre impaire requise")
    for name, upper in [("sigma_couches", 20), ("seuil_delta_mm_s", 600), ("pente_max_mm_s_par_mm", 10000)]:
        v = cfg[name]
        if isinstance(v, bool):
            raise ValueError(name + " invalide")
        try:
            v = float(v)
        except (ValueError, TypeError):
            raise ValueError(name + " invalide")
        if not math.isfinite(v) or not 0 < v <= upper:
            raise ValueError(name + " hors limites")
        cfg[name] = v
    return cfg


def _quantile(samples, fraction):
    total = sum(length for _, length in samples)
    if not total:
        return None
    accum = 0.0
    for value, length in sorted(samples):
        accum += length
        if accum >= fraction * total:
            return value
    return samples[-1][0]


def _proposer(speeds, zs, cfg):
    """
    Enveloppe descendante indépendante ; résidu après dernière itération explicite.
    """
    result = list(speeds)
    count = 0
    if len(result) < 2:
        return result, 0, True, 0.0
    if cfg["mode"] == "gradient":
        for i in range(1, len(result)):
            result[i] = min(result[i], result[i - 1] + cfg["pente_max_mm_s_par_mm"] * (zs[i] - zs[i - 1]))
        for i in range(len(result) - 2, -1, -1):
            result[i] = min(result[i], result[i + 1] + cfg["pente_max_mm_s_par_mm"] * (zs[i + 1] - zs[i]))
        residual = max(abs(result[i] - result[i - 1]) / (zs[i] - zs[i - 1]) for i in range(1, len(result)))
        return result, 2, residual <= cfg["pente_max_mm_s_par_mm"] + 1e-9, residual
    half = cfg["fenetre"] // 2
    kernel = [math.exp(-0.5 * (i / cfg["sigma_couches"]) ** 2) for i in range(-half, half + 1)]
    weight = sum(kernel)
    residual = max(abs(a - b) for a, b in zip(result, result[1:]))
    budget = min(cfg["max_iterations"], max(1, 10000000 // (len(result) * cfg["fenetre"])))
    while residual > cfg["seuil_delta_mm_s"] and count < budget:
        old = result
        result = [
            min(
                value,
                sum(kernel[j + half] * old[min(max(i + j, 0), len(old) - 1)] for j in range(-half, half + 1)) / weight,
            )
            for i, value in enumerate(old)
        ]
        count += 1
        residual = max(abs(a - b) for a, b in zip(result, result[1:]))
    return result, count, residual <= cfg["seuil_delta_mm_s"], residual


def _machine(maitre):
    from benchab.machine.profil_maitre import charger_machine, limites_configurees

    if isinstance(maitre, dict) and "volume_mm" in maitre:
        return maitre
    return limites_configurees(charger_machine(maitre))


def _octets(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("source non régulière refusée")
        if before.st_size > MAX_BYTES:
            raise ValueError("source trop grande (>64 Mio)")
        pieces = []
        total = 0
        while True:
            part = os.read(fd, min(256 * 1024, MAX_BYTES + 1 - total))
            if not part:
                break
            pieces.append(part)
            total += len(part)
            if total > MAX_BYTES:
                raise ValueError("source trop grande (>64 Mio)")
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("source changée pendant lecture")
        return b"".join(pieces)
    finally:
        os.close(fd)


def _lire(path):
    raw = _octets(path)
    snapshot = io.BytesIO(raw)
    if zipfile.is_zipfile(snapshot):
        with zipfile.ZipFile(snapshot) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if len(names) != len(set(names)):
                raise ValueError("membres archive dupliqués")
            if sum(i.file_size for i in infos) > 2 * MAX_BYTES:
                raise ValueError("archive décompressée trop grande")
            members = [n for n in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)]
            if not members:
                raise ValueError("aucun G-code de plaque")
            parts = [(n, z.read(n).decode("utf-8")) for n in sorted(members)]
        return raw, parts, True
    return raw, [(path.name, raw.decode("utf-8"))], False


def _rapport_initial(path, raw, cfg, machine):
    """Prépare les champs communs du rapport sans lire ni modifier une source supplémentaire."""
    return {
        "schema_version": 1,
        "version": VERSION,
        "source": str(path.resolve()),
        "source_sha256": _sha(raw),
        "options": cfg,
        "statut": "PASS",
        "validation_physique": "NON VÉRIFIÉ",
        "transformable": False,
        "raisons_refus_transformation": [
            "Ce module fournit un diagnostic et une proposition numérique ; aucun fichier transformé certifié."
        ],
        "limites_configurees": machine,
        "maitre_limites_sha256": _sha(json.dumps(machine, sort_keys=True, allow_nan=False).encode()),
        "series": [],
        "couverture": {"erreurs": [], "avertissements": []},
        "plaques": [],
        "proposition": {"temps_nominal_avant_s": 0.0, "temps_nominal_apres_s": 0.0, "converge": True},
        "limites": [
            "Vitesses F nominales, accélérations/jonctions et temps macros non modélisés.",
            "Aucune simulation thermique ; refroidissement à contrôler localement.",
            "Le rayon gaussien est en couches ; le gradient utilise Z réel en mm.",
            "Aucun gain de finition ou temps physique validé.",
            (
                "Budgets : 64 Mio source,128 Mio archive décompressée,200000 lignes,20000 groupes, "
                "10 millions opérations gaussiennes par plaque (budget d’itérations réduit si nécessaire)."
            ),
        ],
    }


def _controle_capacites(path, archive, machine, limites_filaments):
    """Retourne les contraintes de capacité sans les mélanger à la couverture des parois."""
    if not archive:
        return {
            "statut": "NON VÉRIFIÉ",
            "raison": "Archive, métadonnées filament et contrôles complets absents ; diagnostic brut seulement.",
        }
    from benchab.controles.capacites_machine import analyser as capacites

    cap = capacites(path, maitre=machine, limites_filaments=limites_filaments)
    return {
        "statut": cap["statut"],
        "decision": cap["decision"],
        "contraintes_non_pass": [c for c in cap.get("contraintes", []) if c["statut"] != "PASS"],
    }


def _etat_plaque():
    """Crée l’état local de lecture d’une plaque, sans persistance entre plaques."""
    return {
        "it": Interpreter(),
        "groups": {},
        "duration": 0.0,
        "block": 0,
        "layer_z": {},
        "bad_layers": set(),
        "events": [],
        "fan": {},
        "have_xyz": False,
        "xyz_mode": False,
        "layer_times": collections.Counter(),
        "flows": {},
        "excluded": collections.Counter(),
        "excluded_examples": [],
        "erreurs": [],
    }


def _traiter_evenement(member, line_no, event, etat):
    """Conserve les commandes opaques et les limites de grammaire dans l’ordre source."""
    if not event:
        return
    op = event["command"]
    if op not in {"G0", "G1"} and len(etat["events"]) < 2048:
        etat["events"].append({"ligne": line_no, "commande": op, "opaque": event.get("opaque", False)})
    if op.startswith("T") and etat["it"].tool not in range(4):
        etat["erreurs"].append(f"{member}:{line_no}: outil hors ACE quatre emplacements")
    if op in {"M82", "M83", "G90", "G91", "G21"} or op.startswith("T"):
        if event["raw"].upper() != op:
            etat["erreurs"].append(f"{member}:{line_no}: paramètres superflus {op}")
    if op in {"G0", "G1", "G92", "M201", "M203", "M204", "M205"}:
        keys = [k.upper() for k, _ in WORDS.findall(event["raw"][len(op) :])]
        if len(keys) != len(set(keys)):
            etat["erreurs"].append(f"{member}:{line_no}: paramètres dupliqués {op}")
    if op not in KNOWN and not re.fullmatch(r"T\d+", op):
        etat["erreurs"].append(f"{member}:{line_no}: commande non couverte {op}")
    if op in {"G90", "G91"}:
        etat["xyz_mode"] = True
    if op in {"G9111", "M600", "M0", "M1"} or op.startswith("T"):
        etat["block"] += 1
    if op in {"M106", "M107"}:
        etat["fan"][str(event["parameters"].get("P", 0))] = event["parameters"].get("S", 255) if op == "M106" else 0


def _mesurer_debit(move, limites_filaments, flows, speed):
    """Met à jour le débit volumique analytique selon les limites explicitement fournies."""
    if move["deposited"] <= 0 or move["deposited_length"] <= 0 or speed <= 0:
        return
    fid = str(move["tool"] + 1) if move["tool"] is not None else None
    limit = (limites_filaments or {}).get(fid, {})
    diameter = limit.get("diametre_mm")
    ceiling = limit.get("plafond_mm3_s")
    flow = flows.setdefault(
        str(fid),
        {"debit_max_mm3_s": None, "plafond_mm3_s": ceiling, "depassements": 0, "statut": "NON VÉRIFIÉ"},
    )
    if diameter is None or ceiling is None:
        return
    if any(
        isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0
        for value in (diameter, ceiling)
    ):
        raise ValueError("diamètre/plafond filament invalide")
    q = move["deposited"] * math.pi * (diameter / 2) ** 2 * speed / move["deposited_length"]
    flow["debit_max_mm3_s"] = max(flow["debit_max_mm3_s"] or 0, q)
    flow["depassements"] += int(q > ceiling + max(1e-9, ceiling * 1e-10))
    flow["statut"] = "FAIL" if flow["depassements"] else "PASS"


def _depot_eligible(member, line_no, move, etat):
    """Enregistre les zones exclues ou les défauts de couverture d’une paroi candidate."""
    if move["trajectory"] == "arc_g17_xy":
        # Le débit reste mesurable ; cette géométrie linéaire ne couvre pas l’arc.
        # Voir docs/reference/continuite-paroi.md.
        etat["erreurs"].append(f"{member}:{line_no}: arc analytique non couvert par la géométrie linéaire des parois")
        return False
    if move["deposited_length"] <= 0 or move["deposited"] <= 0 or move["type"] not in OUTER:
        etat["excluded"][move["type"] or "non identifié"] += 1
        if len(etat["excluded_examples"]) < 128:
            etat["excluded_examples"].append(
                {
                    "ligne": line_no,
                    "type": move["type"],
                    "objet": move["object"],
                    "outil": move["tool"],
                    "raison": "hors dépôt de paroi extérieure éligible",
                }
            )
        return False
    if (
        not etat["have_xyz"]
        or not etat["xyz_mode"]
        or move["layer"] < 0
        or etat["it"].relative_e is None
        or etat["it"].tool is None
        or not move["object"]
    ):
        etat["erreurs"].append(f"{member}:{line_no}: identité/couche/mode incomplet")
    return True


def _ajouter_mouvement(member, line_no, move, etat, limites_filaments):
    """Accumule temps, débit et échantillons des mouvements analysables en mm et s."""
    if not move:
        return
    if any(abs(a - b) > 1e-10 for a, b in zip(move["start"], move["end"])):
        etat["have_xyz"] = True
    if any(not math.isfinite(move[key]) for key in ("length", "deposited", "deposited_length", "feed", "extrusion")):
        raise ValueError("mouvement non fini ou trop grand")
    speed = move["feed"] / 60
    if speed > 0:
        elapsed = (move["length"] or abs(move["extrusion"])) / speed
        etat["duration"] += elapsed
        etat["layer_times"][move["layer"]] += elapsed
    _mesurer_debit(move, limites_filaments, etat["flows"], speed)
    # Les arcs gardent leurs mesures de temps/débit, mais ne prouvent pas la couverture.
    if not _depot_eligible(member, line_no, move, etat):
        return
    z = etat["layer_z"].get(move["layer"])
    if z is None or not math.isfinite(z):
        etat["bad_layers"].add(move["layer"])
        z = None
    if z is not None and abs(move["end"][2] - z) > 1e-6:
        etat["erreurs"].append(f"{member}:{line_no}: altitude déposée incohérente avec Z")
    height = move["metadata"].get("HEIGHT")
    if height is not None and height <= 0:
        etat["erreurs"].append(f"{member}:{line_no}: HEIGHT non positif")
    if abs(move["end"][2] - move["deposition_start"][2]) > 1e-8:
        etat["erreurs"].append(f"{member}:{line_no}: dépôt Z variable non couvert")
    if speed <= 0:
        etat["erreurs"].append(f"{member}:{line_no}: F absent/non positif")
        return
    key = (etat["block"], move["object"], move["tool"], move["layer"])
    groups = etat["groups"]
    if key not in groups and len(groups) >= 20000:
        raise ValueError("budget diagnostic dépassé : 20000 groupes de couches")
    group = groups.setdefault(
        key,
        {
            "index": move["layer"],
            "z_mm": z,
            "hauteur_mm": move["metadata"].get("HEIGHT"),
            "objet": move["object"],
            "outil": move["tool"],
            "troncon": etat["block"],
            "samples": [],
            "moves": [],
            "lignes": [],
            "protegee": move["layer"] < etat["skip_first_layers"],
        },
    )
    group["samples"].append((speed, move["deposited_length"]))
    group["moves"].append((speed, move["deposited_length"]))
    group["lignes"].append(line_no)


def _scanner_plaque(member, text, cfg, limites_filaments):
    """Lit les lignes d’une plaque et retourne ses mouvements, exclusions et erreurs."""
    etat = _etat_plaque()
    etat["skip_first_layers"] = cfg["skip_first_layers"]
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.startswith(";LAYER_CHANGE"):
            etat["have_xyz"] = False
        if re.match(r"^;\s*(spiral_mode|spiral_vase)\s*=\s*1\b", line, re.I):
            etat["erreurs"].append(member + ": mode vase non couvert")
        move = etat["it"].feed_line(line)
        event = etat["it"].last_event
        # ;Z suit LAYER_CHANGE ; HEIGHT est une épaisseur, jamais une altitude.
        if line.startswith(";Z:"):
            try:
                etat["layer_z"][etat["it"].layer] = float(line[3:])
            except ValueError:
                etat["bad_layers"].add(etat["it"].layer)
        _traiter_evenement(member, line_no, event, etat)
        if (etat["it"].kind or "") in {"Custom", "Prime tower", "Wipe tower"}:
            etat["block"] += 1
        _ajouter_mouvement(member, line_no, move, etat, limites_filaments)
    etat["erreurs"].extend(f"{member}: {erreur}" for erreur in etat["it"].errors)
    if etat["bad_layers"]:
        etat["erreurs"].append(f"{member}: Z absent/non fini aux couches {sorted(etat['bad_layers'])}")
    return etat


def _series_plaque(member, groups, layer_times):
    """Regroupe les couches consécutives par objet, outil et tronçon comme dans le rapport historique."""
    buckets = collections.defaultdict(list)
    for group in groups.values():
        buckets[(group["objet"], group["outil"], group["troncon"])].append(group)
    series = []
    for bucket in buckets.values():
        current = None
        last = None
        for group in sorted(bucket, key=lambda item: item["index"]):
            group["longueur_deposee_mm"] = sum(item[1] for item in group["samples"])
            group["v_min_mm_s"] = min(item[0] for item in group["samples"])
            group["v_max_mm_s"] = max(item[0] for item in group["samples"])
            group["v_mediane_ponderee_mm_s"] = _quantile(group["samples"], 0.5)
            group["temps_couche_nominal_s"] = layer_times[group["index"]]
            separated = (
                last is None
                or group["index"] != last["index"] + 1
                or group["z_mm"] is None
                or last["z_mm"] is None
                or group["z_mm"] <= last["z_mm"]
                or group["protegee"] != last["protegee"]
            )
            if separated:
                current = {
                    "plaque": member,
                    "objet": group["objet"],
                    "outil": group["outil"],
                    "troncon": len(series),
                    "couches": [],
                }
                series.append(current)
            comparable = (
                last is not None
                and group["index"] == last["index"] + 1
                and group["z_mm"] is not None
                and last["z_mm"] is not None
                and group["z_mm"] > last["z_mm"]
            )
            group["delta_v_mm_s"] = (
                None if not comparable else group["v_mediane_ponderee_mm_s"] - last["v_mediane_ponderee_mm_s"]
            )
            group["pente_v_par_mm"] = None if not comparable else group["delta_v_mm_s"] / (group["z_mm"] - last["z_mm"])
            current["couches"].append(group)
            last = group
    return series


def _proposer_serie(serie, cfg, calculation_cfg, report, layer_costs, after):
    """Construit une proposition numérique bornée sans augmenter les vitesses source."""
    layers = serie["couches"]
    speeds = [group["v_min_mm_s"] for group in layers]
    if layers[0]["protegee"] or any(group["z_mm"] is None for group in layers):
        targets, iterations, converged, residual = speeds, 0, True, 0.0
    else:
        targets, iterations, converged, residual = _proposer(
            speeds, [group["z_mm"] for group in layers], calculation_cfg
        )
    serie.update(
        iterations=iterations,
        converge=converged,
        residu=residual,
        unite_residu="mm/s" if cfg["mode"] == "gaussien" else "mm/s/mm",
    )
    report["proposition"]["converge"] &= converged
    for group, target in zip(layers, targets):
        group["v_cible_mm_s"] = None if group["protegee"] else target
        group["temps_nominal_avant_s"] = sum(length / speed for speed, length in group["moves"])
        group["temps_nominal_propose_s"] = sum(
            length / (speed if group["protegee"] else min(speed, target)) for speed, length in group["moves"]
        )
        cost = group["temps_nominal_propose_s"] - group["temps_nominal_avant_s"]
        after += cost
        layer_costs[group["index"]] += cost
        group["changement_brusque"] = (
            group["delta_v_mm_s"] is not None and abs(group["delta_v_mm_s"]) > cfg["seuil_delta_mm_s"]
        )
        del group["samples"]
        del group["moves"]
    return after


def _analyser_plaque(member, text, cfg, limites_filaments, report):
    """Ajoute les résultats d’une plaque au rapport, en conservant leur ordre historique."""
    etat = _scanner_plaque(member, text, cfg, limites_filaments)
    report["couverture"]["erreurs"].extend(etat["erreurs"])
    series = _series_plaque(member, etat["groups"], etat["layer_times"])
    duration = etat["duration"]
    after = duration
    calculation_cfg = dict(cfg)
    calculation_cfg["max_iterations"] = min(
        cfg["max_iterations"],
        max(1, 10000000 // max(1, sum(len(serie["couches"]) for serie in series) * cfg["fenetre"])),
    )
    layer_costs = collections.Counter()
    for serie in series:
        after = _proposer_serie(serie, cfg, calculation_cfg, report, layer_costs, after)
        report["series"].append(serie)
    report["proposition"]["temps_nominal_avant_s"] += duration
    report["proposition"]["temps_nominal_apres_s"] += after
    report["plaques"].append(
        {
            "temps_couches_nominaux": [
                {"couche": key, "avant_s": value, "propose_s": value + layer_costs[key]}
                for key, value in sorted(etat["layer_times"].items())
            ],
            "membre": member,
            "sha256": _sha(text.encode()),
            "temps_nominal_avant_s": duration,
            "temps_nominal_propose_s": after,
            "ventilateurs_commandes_fin": etat["fan"],
            "zones_exclues_compteurs": dict(etat["excluded"]),
            "zones_exclues_exemples": etat["excluded_examples"],
            "commandes_exemples": etat["events"],
            "commandes_exemples_limite": 2048,
            "debit_strict_brut_par_outil": etat["flows"],
            "refroidissement": {
                "statut": "NON VÉRIFIÉ",
                "raison": "Temps nominaux disponibles, température physique et ventilation locale non simulées.",
            },
        }
    )


def _finaliser_rapport(report):
    """Déduit les statuts globaux sans effacer les sous-statuts ni les limites."""
    if not report["series"]:
        report["couverture"]["erreurs"].append("aucune paroi extérieure identifiable")
    report["couverture"]["erreurs"] = list(dict.fromkeys(report["couverture"]["erreurs"]))
    report["couverture"]["statut"] = "NON VÉRIFIÉ" if report["couverture"]["erreurs"] else "PASS"
    if report["couverture"]["erreurs"]:
        report["raisons_refus_transformation"] += report["couverture"]["erreurs"]
    status = report["controle_capacites"]["statut"]
    if any(
        flow["statut"] == "FAIL"
        for plaque in report["plaques"]
        for flow in plaque["debit_strict_brut_par_outil"].values()
    ):
        status = "FAIL"
    report["statut"] = (
        "FAIL"
        if status == "FAIL"
        else "NON VÉRIFIÉ"
        if report["couverture"]["erreurs"] or status == "NON VÉRIFIÉ"
        else "WARN"
        if not report["proposition"]["converge"]
        else "PASS"
    )
    report["proposition"]["delta_nominal_s"] = (
        report["proposition"]["temps_nominal_apres_s"] - report["proposition"]["temps_nominal_avant_s"]
    )
    json.dumps(report, allow_nan=False)
    return report


def analyser(source, maitre=None, limites_filaments=None, options=None):
    """Analyse une source G-code/3MF ; positions mm, durées s et débits mm³/s.

    Renvoie PASS, WARN, FAIL ou NON VÉRIFIÉ. Les arcs ne prouvent pas la
    couverture des parois, mais leur durée et leur débit sont analytiques.
    Ne transforme ni archive, ni réglage, ni trajectoire ; pas de validation physique.
    """
    cfg = _options(options)
    if limites_filaments is not None and (
        not isinstance(limites_filaments, dict) or any(not isinstance(v, dict) for v in limites_filaments.values())
    ):
        raise ValueError("limites_filaments doit être un dictionnaire de déclarations")
    path = Path(source)
    raw, parts, archive = _lire(path)
    machine = _machine(maitre)
    if sum(len(text.splitlines()) for _, text in parts) > 200000:
        raise ValueError("budget diagnostic dépassé : 200000 lignes")
    report = _rapport_initial(path, raw, cfg, machine)
    report["controle_capacites"] = _controle_capacites(path, archive, machine, limites_filaments)
    for member, text in parts:
        _analyser_plaque(member, text, cfg, limites_filaments, report)
    if _octets(path) != raw:
        raise ValueError("source changée pendant analyse")
    return _finaliser_rapport(report)


def _neuve(path):
    p = Path(path)
    if p.exists() or p.is_symlink():
        raise ValueError("sortie existante ou lien refusé : " + str(p))
    if any(parent.is_symlink() for parent in p.absolute().parents):
        raise ValueError("ancêtre de sortie lié refusé : " + str(p))
    if not p.parent.is_dir():
        raise ValueError("dossier sortie absent")
    return p


def _ecrire(path, text):
    p = _neuve(path)
    with p.open("x", encoding="utf-8") as f:
        f.write(text)
    return {"fichier": str(p.resolve()), "sha256": _sha(p.read_bytes())}


def rendre_html(rapport, sortie):
    """Écrit la visualisation HTML des vitesses nominales (mm/s), hauteurs (mm) et durées (s) du rapport."""
    rows = []
    charts = []
    for s in rapport["series"]:
        layers = s["couches"]
        label = f"{s['plaque']} · {s['objet']} · outil {s['outil']} · tronçon {s['troncon']}"
        maximum = max((g["v_max_mm_s"] for g in layers), default=1) or 1

        def points(key):
            return " ".join(
                f"{20 + i * 660 / max(1, len(layers) - 1):.2f},"
                f"{160 - 140 * (g[key] if g[key] is not None else g['v_min_mm_s']) / maximum:.2f}"
                for i, g in enumerate(layers)
            )

        charts.append(
            "<h2>"
            + html.escape(label)
            + "</h2><p>Y : 0–"
            + str(round(maximum, 2))
            + " mm/s ; X : couches "
            + str(layers[0]["index"])
            + "–"
            + str(layers[-1]["index"])
            + '</p><svg role="img" aria-label="Vitesses nominales avant et proposées" '
            + 'viewBox="0 0 700 180"><polyline fill="none" stroke="#245aa5" points="'
            + points("v_mediane_ponderee_mm_s")
            + '"/><polyline fill="none" stroke="#aa4e17" points="'
            + points("v_cible_mm_s")
            + '"/>'
            + (
                '<circle cx="20" cy="'
                + str(160 - 140 * layers[0]["v_mediane_ponderee_mm_s"] / maximum)
                + '" r="4" fill="#245aa5"/>'
                if len(layers) == 1
                else ""
            )
            + "</svg>"
        )
        for g in layers:
            vals = [
                label,
                g["index"],
                None if g["z_mm"] is None else round(g["z_mm"], 6),
                None if g["hauteur_mm"] is None else round(g["hauteur_mm"], 6),
                round(g["v_mediane_ponderee_mm_s"], 3),
                None if g["v_cible_mm_s"] is None else round(g["v_cible_mm_s"], 3),
                "protégée" if g["protegee"] else "proposée",
                round(g["temps_nominal_propose_s"] - g["temps_nominal_avant_s"], 4),
            ]
            rows.append(
                "<tr>"
                + "".join(
                    "<td>" + html.escape(str(v) if v is not None else "inconnu / protégé") + "</td>" for v in vals
                )
                + "</tr>"
            )
    exclusions = "".join(
        "<h3>"
        + html.escape(p["membre"])
        + "</h3><p>Segments exclus : "
        + html.escape(json.dumps(p["zones_exclues_compteurs"], ensure_ascii=False))
        + "</p><p>Refroidissement : NON VÉRIFIÉ ; ventilateurs commandés finaux : "
        + html.escape(json.dumps(p["ventilateurs_commandes_fin"]))
        + "</p>"
        for p in rapport["plaques"]
    )
    diagnostics = list(rapport["raisons_refus_transformation"]) + list(rapport.get("couverture", {}).get("erreurs", []))
    cap = rapport.get("controle_capacites", {})
    diagnostics.extend(
        json.dumps(c, ensure_ascii=False) for c in cap.get("contraintes_non_pass", []) if c.get("statut") != "PASS"
    )
    if cap.get("raison"):
        diagnostics.append(str(cap["raison"]))
    for plaque in rapport.get("plaques", []):
        diagnostics.extend(
            json.dumps({"plaque": plaque["membre"], "outil": tool, "débit strict brut": flow}, ensure_ascii=False)
            for tool, flow in plaque.get("debit_strict_brut_par_outil", {}).items()
            if flow.get("statut") != "PASS"
        )
    errors = "".join("<li>" + html.escape(e) + "</li>" for e in dict.fromkeys(diagnostics))
    doc = (
        "".join(
            (
                '<!doctype html><html lang="fr"><meta charset="utf-8"><title>Finition des parois</title><style>bo',
                "dy{font:16px system-ui;max-width:1100px;margin:2em auto;padding:1em;color:#203040;background:#fa",
                "f9f5}table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;padding:.4em;text-ali",
                "gn:left}svg{width:100%;max-height:220px;background:white}h2{font-size:1.1em}polyline{stroke-widt",
                "h:2}</style><h1>Finition des parois — diagnostic</h1><p>Vitesses nominales, aucune validation ph",
                "ysique. Bleu : médiane avant ; orange : cible/enveloppe proposée. Couche protégée : aucune modif",
                "ication proposée.</p><p>Source SHA256 : ",
            )
        )
        + html.escape(rapport["source_sha256"])
        + " · Statut : "
        + html.escape(rapport["statut"])
        + "</p><p>Coût nominal proposé : "
        + str(round(rapport["proposition"]["delta_nominal_s"], 3))
        + " s ; aucune accélération ou macro simulée.</p>"
        + "".join(charts)
        + "<h2>Zones exclues et refroidissement</h2>"
        + exclusions
        + "<h2>Couverture et refus de transformation</h2><ul>"
        + errors
        + "</ul><h2>Données par couche</h2><table><thead><tr>"
        + "".join(
            "<th>" + x + "</th>"
            for x in ["Série", "Couche", "Z mm", "Hauteur mm", "Avant mm/s", "Cible mm/s", "Protection", "Coût s"]
        )
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></html>"
    )
    return _ecrire(sortie, doc)


def main(argv=None):
    """Analyse la paroi en mm et mm/s ; codes 1 FAIL, 2 refus, 0 sinon, aucune validation physique."""
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    a = sub.add_parser("analyser")
    a.add_argument("source")
    a.add_argument("--json")
    a.add_argument("--html")
    a.add_argument("--maitre")
    a.add_argument("--limites-filaments")
    for name, v in DEFAULTS.items():
        a.add_argument("--" + name.replace("_", "-"), default=v, type=type(v) if not isinstance(v, str) else str)
    args = p.parse_args(argv)
    try:
        destinations = [Path(x).absolute() for x in (args.json, args.html) if x]
        for dst in destinations:
            _neuve(dst)
        if len(destinations) != len({dst.resolve() for dst in destinations}):
            raise ValueError("sorties JSON/HTML identiques")
        limits = json.loads(Path(args.limites_filaments).read_text()) if args.limites_filaments else None
        report = analyser(
            args.source, maitre=args.maitre, limites_filaments=limits, options={k: getattr(args, k) for k in DEFAULTS}
        )
        if args.json:
            _ecrire(args.json, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        if args.html:
            rendre_html(report, args.html)
        print(
            json.dumps(
                {"statut": report["statut"], "series": len(report["series"]), "transformable": False},
                ensure_ascii=False,
            )
        )
        return 1 if report["statut"] == "FAIL" else 0
    except (ValueError, OSError, UnicodeError, zipfile.BadZipFile, KeyError, TypeError) as ex:
        print("Erreur : " + str(ex), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
