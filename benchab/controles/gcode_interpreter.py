"""

Interpréteur modal linéaire partagé. Aucun accès machine.

XYZ absolus au départ ; E exige M82/M83 pour une mesure certifiable.
Positions E et dette de rétraction par outil. G92 change le repère sans effacer
la dette. Les arcs et commandes qui changent le mouvement sans modèle explicite
invalident la couverture ; les commandes de chauffe/ventilation sont ignorées.
Les limites de dynamique et la pressure advance sont relevées, sans simuler
accélérations ni compensation du moteur : les mesures restent nominales.
La grammaire reconnue ne prouve jamais son application par le firmware.


"""

import math
import re

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
WORDS = re.compile(r"([A-Z])\s*(" + NUMBER + r")", re.I)


class Interpreter:
    def __init__(self, tool=None):
        self.xyz = (0.0, 0.0, 0.0)
        self.relative_xyz = False
        self.relative_e = None
        self.tool = tool
        self.e = {}
        self.retracted = {}
        self.feed = 0.0
        self.kind = None
        self.object = None
        self.errors = []
        self.last_event = None
        self.line_number = 0
        self.layer = -1
        self.metadata = {}
        self.dialect = "Anycubic/Orca nominal, modes XYZ et E indépendants"

    def error(self, message):
        if message not in self.errors:
            self.errors.append(message)

    def feed_line(self, line):
        self.line_number += 1
        self.last_event = None
        line = line.strip()
        if line.startswith(";LAYER_CHANGE"):
            self.layer += 1
        for key in ("Z", "HEIGHT", "WIDTH"):
            if line.startswith(";" + key + ":"):
                try:
                    value = float(line.split(":", 1)[1])
                    if not math.isfinite(value):
                        raise ValueError()
                    self.metadata[key] = value
                except ValueError:
                    self.error("métadonnée non finie/invalide : " + key)
        if line.startswith(";TYPE:"):
            self.kind = line[6:].strip()
        c = line.split(";", 1)[0].strip()
        c = re.sub(r"\([^)]*\)", "", c).strip()
        c = re.sub(r"^N\d+\s*", "", c, flags=re.I).split("*", 1)[0]
        if not c:
            return None
        if c.startswith("EXCLUDE_OBJECT_START"):
            m = re.fullmatch(r"EXCLUDE_OBJECT_START\s+NAME=(\S+)", c)
            if not m:
                self.error("début objet invalide : " + c)
                return None
            if self.object is not None:
                self.error("début objet imbriqué : " + c)
                return None
            self.object = m.group(1)
            return None
        if c.startswith("EXCLUDE_OBJECT_END"):
            m = re.fullmatch(r"EXCLUDE_OBJECT_END(?:\s+NAME=(\S+))?", c)
            if not m or self.object is None or (m.group(1) and m.group(1) != self.object):
                self.error("fin objet invalide ou contradictoire : " + c)
                return None
            self.object = None
            return None
        if c.startswith("EXCLUDE_OBJECT_DEFINE"):
            return None
        command = c.split()[0].upper()
        if command in {"SET_VELOCITY_LIMIT", "SET_PRESSURE_ADVANCE"}:
            # Sources primaires : Klipper G-Codes, toolhead.py (v0.12.0 et
            # master), kinematics/extruder.py (master), consultés le 30/10/2026.
            # L'ancien ACCEL_TO_DECEL et le nouveau MINIMUM_CRUISE_RATIO
            # sont deux descriptions du lissage, pas deux accélérations à sommer.
            velocity = command == "SET_VELOCITY_LIMIT"
            allowed = (
                {"VELOCITY", "ACCEL", "ACCEL_TO_DECEL", "MINIMUM_CRUISE_RATIO", "SQUARE_CORNER_VELOCITY"}
                if velocity
                else {"ADVANCE", "SMOOTH_TIME", "EXTRUDER"}
            )
            values = {}
            valid = True
            for token in c.split()[1:]:
                if token.count("=") != 1:
                    valid = False
                    break
                key, raw_value = token.split("=")
                key = key.upper()
                if key not in allowed or key in values:
                    valid = False
                    break
                if key == "EXTRUDER":
                    # Nom déclaré uniquement ; aucune identité de moteur physique
                    # ni correspondance avec Tn n'est déduite de cette chaîne.
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", raw_value):
                        valid = False
                        break
                    values[key] = raw_value
                    continue
                if not re.fullmatch(NUMBER + r"(?:[eE][-+]?\d+)?", raw_value):
                    valid = False
                    break
                try:
                    value = float(raw_value)
                except ValueError:
                    valid = False
                    break
                if (
                    not math.isfinite(value)
                    or value < 0
                    or (key in {"VELOCITY", "ACCEL", "ACCEL_TO_DECEL"} and value == 0)
                    or (key == "MINIMUM_CRUISE_RATIO" and value >= 1)
                    or (key == "SMOOTH_TIME" and value > 0.2)
                ):
                    valid = False
                    break
                values[key] = value
            relation = "non_applicable"
            if valid and {"ACCEL_TO_DECEL", "MINIMUM_CRUISE_RATIO"} <= values.keys():
                # Profil indépendant du firmware : refuser une paire ambiguë sans
                # ACCEL explicite, plutôt que supposer une limite machine initiale.
                if "ACCEL" not in values:
                    valid = False
                    relation = "acceleration_absente"
                else:
                    expected = 1.0 - min(values["ACCEL_TO_DECEL"], values["ACCEL"]) / values["ACCEL"]
                    valid = math.isclose(values["MINIMUM_CRUISE_RATIO"], expected, rel_tol=1e-9, abs_tol=1e-9)
                    relation = "coherente" if valid else "contradictoire"
            kind = "velocity_limit" if velocity else "pressure_advance"
            self.last_event = {
                "kind": kind if valid else "unsupported",
                "command": command,
                "parameters": values if valid else {},
                "raw": c,
                "line": self.line_number,
                "tool": self.tool,
                "object": self.object,
                "type": self.kind,
                "layer": self.layer,
                "source_grammaire": "https://www.klipper3d.org/G-Codes.html#" + command.lower(),
                "application_firmware": "NON VÉRIFIÉ",
                "dynamique_simulee": False,
            }
            if velocity:
                self.last_event["relation_ancien_nouveau"] = relation
                if "ACCEL_TO_DECEL" in values:
                    self.last_event["source_historique"] = (
                        "https://github.com/Klipper3d/klipper/blob/v0.12.0/klippy/toolhead.py"
                    )
            else:
                self.last_event["compensation_extrusion_simulee"] = False
            if not valid:
                self.error("paramètres " + command + " non pris en charge : " + c)
            return None
        m = re.match(r"^([GMT]\d+(?:\.\d+)?)", c, re.I)
        if not m:
            self.error("opcode non pris en charge : " + c.split()[0])
            self.last_event = {
                "kind": "unsupported",
                "command": c.split()[0],
                "raw": c,
                "parameters": {},
                "line": self.line_number,
                "tool": self.tool,
                "object": self.object,
                "type": self.kind,
                "layer": self.layer,
            }
            return None
        op = m.group(1).upper()
        tail = c[m.end() :]
        if op == "M73":
            # Grammaire d'affichage observée dans les sorties natives Anycubic :
            # QuietR/SportR sont des durées affichées, jamais des mouvements.
            values = {}
            valid = True
            for token in tail.split():
                match = re.fullmatch(r"(QuietR|SportR|P|R|Q|S)(" + NUMBER + r")", token, re.I)
                if not match:
                    valid = False
                    break
                key = match[1].upper()
                value = float(match[2])
                if key in values or not math.isfinite(value) or value < 0 or (key in {"P", "Q"} and value > 100):
                    valid = False
                    break
                values[key] = value
            if not values:
                valid = False
            if not valid:
                self.error("paramètres M73 invalides : " + c)
            self.last_event = {
                "kind": "progress" if valid else "unsupported",
                "command": op,
                "parameters": values if valid else {},
                "raw": c,
                "line": self.line_number,
                "tool": self.tool,
                "object": self.object,
                "type": self.kind,
                "layer": self.layer,
            }
            return None
        if op == "M117":
            self.last_event = {
                "kind": "display",
                "command": op,
                "parameters": {},
                "raw": c,
                "line": self.line_number,
                "tool": self.tool,
                "object": self.object,
                "type": self.kind,
                "layer": self.layer,
            }
            return None
        words = WORDS.findall(tail)
        vals = {k.upper(): float(v) for k, v in words}
        # Le texte libre de l'écran et la macro constructeur ont une grammaire distincte.
        if op not in {"G9111", "M117"}:
            if len(vals) != len(words) or WORDS.sub("", tail).strip():
                self.error("paramètres dupliqués ou invalides : " + c)
                self.last_event = {"kind": "unsupported", "command": op, "parameters": {}, "raw": c}
                return None
        if op in {"M82", "M83", "G90", "G91", "G21"} or op.startswith("T"):
            if vals or tail.strip() or (op.startswith("T") and not re.fullmatch(r"T\d+", op)):
                self.error("paramètres modaux non pris en charge : " + c)
                self.last_event = {"kind": "unsupported", "command": op, "parameters": {}, "raw": c}
                return None
        self.last_event = {
            "kind": "command",
            "command": op,
            "parameters": vals,
            "raw": c,
            "line": self.line_number,
            "tool": self.tool,
            "object": self.object,
            "type": self.kind,
            "layer": self.layer,
        }
        if op == "G9111":
            named = {}
            values = {}
            try:
                for token in tail.split():
                    key, value = token.split("=")
                    if key not in ("extruderTemp", "bedTemp") or key in named:
                        raise ValueError()
                    named[key] = value
                    values[key] = float(value)
                    if not math.isfinite(values[key]) or values[key] < 0:
                        raise ValueError()
                if not values:
                    raise ValueError()
            except ValueError:
                self.error("paramètre G9111 non fini/invalide")
                self.last_event.update(kind="unsupported", parameters={}, opaque=True)
                return None
            self.last_event.update(kind="macro", parameters=values, opaque=True)
            return None
        if op in {"M201", "M203", "M204", "M205", "M104", "M109", "M140", "M190", "M106", "M107", "G4"}:
            if WORDS.sub("", c[m.end() :]).strip():
                self.error("paramètres commande non pris en charge : " + c)
                self.last_event["kind"] = "unsupported"
                return None
        if not all(math.isfinite(v) for v in vals.values()):
            self.error("paramètre non fini")
            self.last_event.update(kind="unsupported", parameters={})
            return None
        if op == "M82":
            self.relative_e = False
            return None
        if op == "M83":
            self.relative_e = True
            return None
        if op == "G90":
            self.relative_xyz = False
            return None
        if op == "G91":
            self.relative_xyz = True
            return None
        if op.startswith("T"):
            self.tool = int(op[1:])
            return None
        if op == "G92":
            if not vals:
                self.error("G92 sans axes explicites non pris en charge")
                return None
            if WORDS.sub("", c[m.end() :]).strip() or any(k not in "XYZE" for k in vals):
                self.error("paramètres G92 non pris en charge : " + c)
                return None
            self.xyz = tuple(vals.get(a, p) for a, p in zip("XYZ", self.xyz))
            if "E" in vals:
                self.e[self.tool] = vals["E"]
            return None
        if op not in {"G0", "G1", "G2", "G3"}:
            # G28 is a setup homing operation. Subsequent coordinates remain in the
            # commanded frame; no homing travel is included in the measurement.
            if op.startswith("G") and op not in {"G4", "G21", "G28", "G29", "G80", "G9111"}:
                self.error("commande mouvement non prise en charge : " + op)
            if op in {"G20", "M200", "M206", "M218", "M220", "M221", "G10", "G11"}:
                self.error("commande affectant extrusion/coordonnées non prise en charge : " + op)
            if op.startswith("M") and op not in {
                "M201",
                "M203",
                "M204",
                "M205",
                "M104",
                "M109",
                "M140",
                "M190",
                "M106",
                "M107",
                "M73",
                "M75",
                "M77",
                "M84",
                "M18",
                "M17",
                "M400",
                "M117",
                "M900",
            }:
                self.error("commande M sans modèle explicite : " + op)
            return None
        tail = c[m.end() :]
        if WORDS.sub("", tail).strip() or any(k not in "XYZEF" for k in vals):
            self.error("paramètres mouvement non pris en charge : " + c)
            return None
        if "F" in vals and vals["F"] <= 0:
            self.error("vitesse non positive")
            return None
        if op in {"G2", "G3"}:
            self.error("arc non analysé : " + op)
            return None
        start = self.xyz
        end = tuple(p + vals.get(a, 0.0) if self.relative_xyz else vals.get(a, p) for a, p in zip("XYZ", start))
        self.feed = vals.get("F", self.feed)
        old = self.e.get(self.tool, 0.0)
        de = 0.0
        if "E" in vals:
            if self.relative_e is None:
                self.error("mode extrusion absent (M82/M83 requis)")
            de = vals["E"] if self.relative_e is not False else vals["E"] - old
            self.e[self.tool] = old + de
        debt = self.retracted.get(self.tool, 0.0)
        if de < 0:
            debt -= de
            deposited = 0.0
        else:
            deposited = max(0.0, de - debt)
            debt = max(0.0, debt - de)
        self.retracted[self.tool] = debt
        self.xyz = end
        if op in {"G2", "G3"}:
            self.error("arc non analysé : " + op)
            return None
        length = math.dist(start, end)
        ratio = deposited / de if de > 0 else 0.0
        deposition_start = tuple(b - (b - a) * ratio for a, b in zip(start, end))
        return {
            "start": start,
            "end": end,
            "deposition_start": deposition_start,
            "length": length,
            "deposited_length": length * ratio,
            "extrusion": de,
            "deposited": deposited,
            "feed": self.feed,
            "tool": self.tool,
            "type": self.kind,
            "object": self.object,
            "op": op,
            "line": self.line_number,
            "layer": self.layer,
            "metadata": dict(self.metadata),
        }
