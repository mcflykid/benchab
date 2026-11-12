"""Interpréteur modal linéaire partagé. Aucun accès machine.

XYZ absolus au départ ; E exige M82/M83 pour une mesure certifiable.
Position E et dette de rétraction sur l’axe partagé. G92 change le repère de commande
sans déplacer la trajectoire continue ni effacer la dette. Seuls les arcs XY G17 décrits
dans la documentation dédiée ont une longueur analytique ; les autres commandes qui
changent le mouvement invalident la couverture ; chauffe/ventilation sont ignorées.
Les limites de dynamique et la pressure advance sont relevées, sans simuler
accélérations ni compensation du moteur : les mesures restent nominales.
La grammaire reconnue ne prouve jamais son application par le firmware.


"""

import math
import re

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
WORDS = re.compile(r"([A-Z])\s*(" + NUMBER + r")", re.I)


def axes_homing(texte):
    """Lit les axes d'un G28 en repère machine ; aucun argument désigne XYZ, aucune commande exécutée."""
    motif = re.compile(rf"([XYZ])(?:{NUMBER})?", re.I)
    axes = [m.group(1).upper() for m in motif.finditer(texte)]
    if motif.sub("", texte).strip() or len(axes) != len(set(axes)):
        raise ValueError("Axes de G28 invalides ou non pris en charge")
    return axes or ["X", "Y", "Z"]


class Interpreter:
    """Suit les états modaux XYZ et E en mm et le débit nominal, sans simuler le firmware."""

    def __init__(self, tool=None, *, error_limit=None):
        """Initialise les états en mm ; budget de diagnostics facultatif, aucune commande machine."""
        if error_limit is not None and (type(error_limit) is not int or error_limit < 1):
            raise ValueError("Budget de diagnostics : entier strictement positif requis")
        self.error_limit = error_limit
        self.errors_truncated = False
        self.xyz = (0.0, 0.0, 0.0)
        self.spatial_xyz = (0.0, 0.0, 0.0)
        self.relative_xyz = False
        self.arc_center_relative = True
        self.arc_plane = "G17"
        self.relative_e = None
        self.tool = tool
        self.e = {}
        self.retracted = {}
        self._apres_homing = False
        self.feed = 0.0
        self.kind = None
        self.object = None
        self.errors = []
        self.last_event: dict[str, object] | None = None
        self.line_number = 0
        self.layer = -1
        self.metadata = {}
        self.dialect = "Anycubic/Orca nominal, modes XYZ et E indépendants"

    def error(self, message):
        """Enregistre une couverture non vérifiée ; borne facultative des diagnostics, aucune unité."""
        if self.error_limit is not None:
            message = message[:1024]
        if message in self.errors:
            return
        if self.error_limit is None or len(self.errors) < self.error_limit:
            self.errors.append(message)
        elif not self.errors_truncated:
            self.errors.append("Budget de diagnostics dépassé : détails supplémentaires non conservés")
            self.errors_truncated = True

    def _evenement(self, op, vals, c, *, kind="command"):
        """Construit le contrat d’événement en unités natives, sans conserver l’historique des lignes."""
        return {"kind": kind, "command": op, "parameters": vals, "raw": c,
                "line": self.line_number, "tool": self.tool, "object": self.object,
                "type": self.kind, "layer": self.layer}

    def feed_line(self, line):
        """Lit une ligne ; positions et longueurs en mm, vitesse mm/min, ou None sans mouvement.

        Aucune commande exécutée ; seuls les arcs G17 XY décrits dans la documentation
        de continuité sont mesurés analytiquement. Le champ arc_g17 décrit
        leur centre physique (mm), leurs angles (rad) et le début relatif du
        dépôt si le centre est représentable ; aucun point de corde n'est fourni.
        """
        c = self._preparer_ligne(line)
        if not c or self._traiter_objet(c):
            return None
        command = c.split()[0].upper()
        if command in {"SET_VELOCITY_LIMIT", "SET_PRESSURE_ADVANCE"}:
            return self._klipper(c, command)
        opcode = self._opcode(c)
        if opcode is None:
            return None
        op, tail = opcode
        special = {"M73": self._progression, "M117": self._affichage, "G28": self._homing}.get(op)
        if special is not None:
            return special(c, op, tail)
        vals = self._parametres(c, op, tail)
        if vals is None:
            return None
        self.last_event = self._evenement(op, vals, c)
        if op == "G9111":
            return self._macro(tail)
        if self._appliquer_mode(c, op, tail, vals):
            return None
        if op not in {"G0", "G1", "G2", "G3"}:
            return self._autre(op)
        return self._mouvement(c, op, tail, vals)

    def _preparer_ligne(self, line):
        """Prépare métadonnées et texte ; unités mm, sans conserver les lignes précédentes."""
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
        return c

    def _traiter_objet(self, c):
        """Suit les identités d’objet ; booléen de traitement, aucune géométrie inventée."""
        if c.startswith("EXCLUDE_OBJECT_START"):
            m = re.fullmatch(r"EXCLUDE_OBJECT_START\s+NAME=(\S+)", c)
            if not m:
                self.error("début objet invalide : " + c)
                return True
            if self.object is not None:
                self.error("début objet imbriqué : " + c)
                return True
            self.object = m.group(1)
            return True
        if c.startswith("EXCLUDE_OBJECT_END"):
            m = re.fullmatch(r"EXCLUDE_OBJECT_END(?:\s+NAME=(\S+))?", c)
            if not m or self.object is None or (m.group(1) and m.group(1) != self.object):
                self.error("fin objet invalide ou contradictoire : " + c)
                return True
            self.object = None
            return True
        if c.startswith("EXCLUDE_OBJECT_DEFINE"):
            return True
        return False

    def _valeurs_klipper(self, c, allowed):
        """Décode les paramètres déclarés et leur validité ; unités natives, aucune simulation."""
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
        return values, valid

    def _klipper(self, c, command):
        """Relève limites et pressure advance ; unités natives, application firmware NON VÉRIFIÉE."""
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
        values, valid = self._valeurs_klipper(c, allowed)
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

    def _opcode(self, c):
        """Reconnaît la commande et ses alias ; texte sans unité ou None sur refus."""
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
        op = {"G00": "G0", "G01": "G1", "G02": "G2", "G03": "G3"}.get(op, op)
        return op, tail

    def _progression(self, c, op, tail):
        """Relève la progression en unités d’affichage déclarées ; aucun effet physique."""
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

    def _affichage(self, c, op, tail):
        """Relève un texte d’affichage sans interpréter ses mots comme des axes."""
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

    def _homing(self, c, op, tail):
        """Réinitialise les axes déclarés en mm et émet un événement ; référencement non exécuté."""
        try:
            axes = axes_homing(tail)
        except ValueError as erreur:
            self.error(str(erreur))
            return None
        self.last_event = self._evenement("G28", dict.fromkeys(axes, 0.0), c, kind="homing")
        # L'ancien repère ne décrit plus le trajet qui suit le référencement.
        # Voir docs/reference/controles.md, contrôle de débit.
        self._apres_homing = any(self.spatial_xyz)
        self.xyz = (
            0.0 if "X" in axes else self.xyz[0],
            0.0 if "Y" in axes else self.xyz[1],
            0.0 if "Z" in axes else self.xyz[2],
        )
        self.spatial_xyz = (
            0.0 if "X" in axes else self.spatial_xyz[0],
            0.0 if "Y" in axes else self.spatial_xyz[1],
            0.0 if "Z" in axes else self.spatial_xyz[2],
        )
        return None

    def _parametres(self, c, op, tail):
        """Valide les paramètres génériques ; dictionnaire en unités natives ou None sur refus."""
        words = WORDS.findall(tail)
        vals = {k.upper(): float(v) for k, v in words}
        # Le texte libre de l'écran et la macro constructeur ont une grammaire distincte.
        if op not in {"G9111", "M117"}:
            if len(vals) != len(words) or WORDS.sub("", tail).strip():
                self.error("paramètres dupliqués ou invalides : " + c)
                self.last_event = {"kind": "unsupported", "command": op, "parameters": {}, "raw": c}
                return None
        if op in {"M82", "M83", "G90", "G91", "G21", "G17", "G18", "G19", "G90.1", "G91.1"} or op.startswith("T"):
            if vals or tail.strip() or (op.startswith("T") and not re.fullmatch(r"T\d+", op)):
                self.error("paramètres modaux non pris en charge : " + c)
                self.last_event = {"kind": "unsupported", "command": op, "parameters": {}, "raw": c}
                return None
        if op != "G9111" and not all(math.isfinite(v) for v in vals.values()):
            self.error("paramètre non fini")
            self.last_event = self._evenement(op, {}, c, kind="unsupported")
            return None
        return vals

    def _macro(self, tail):
        """Relève les températures G9111 en °C ; macro opaque, aucune chauffe exécutée."""
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
            assert self.last_event is not None  # feed_line initialise l'événement avant la macro.
            self.last_event.update(kind="unsupported", parameters={}, opaque=True)
            return None
        assert self.last_event is not None  # feed_line initialise l'événement avant la macro.
        self.last_event.update(kind="macro", parameters=values, opaque=True)
        return None

    def _appliquer_mode(self, c, op, tail, vals):
        """Suit modes et repères en mm ; booléen de traitement, dette de rétraction conservée."""
        if op == "M82":
            self.relative_e = False
            return True
        if op == "M83":
            self.relative_e = True
            return True
        if op == "G90":
            self.relative_xyz = False
            return True
        if op == "G91":
            self.relative_xyz = True
            return True
        if op in {"G90.1", "G91.1"}:
            self.arc_center_relative = op == "G91.1"
            return True
        if op in {"G17", "G18", "G19"}:
            self.arc_plane = op
            return True
        if op.startswith("T"):
            self.tool = int(op[1:])
            return True
        if op == "G92":
            if not vals:
                self.error("G92 sans axes explicites non pris en charge")
                return True
            if WORDS.sub("", tail).strip() or any(k not in "XYZE" for k in vals):
                self.error("paramètres G92 non pris en charge : " + c)
                return True
            self.xyz = tuple(vals.get(a, p) for a, p in zip("XYZ", self.xyz))
            if "E" in vals:
                self.e[None] = vals["E"]
            return True
        return False

    def _autre(self, op):
        """Signale les commandes sans modèle ; aucune commande inconnue assimilée à une mesure."""
        if op.startswith("G") and op not in {"G4", "G21", "G29", "G80", "G9111"}:
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

    def _mouvement(self, c, op, tail, vals):
        """Calcule trajet et dépôt nominaux en mm et durée en s, sans tessellation physique."""
        if WORDS.sub("", tail).strip() or any(k not in "XYZEF" for k in vals if op in {"G0", "G1"}):
            self.error("paramètres mouvement non pris en charge : " + c)
            return None
        if op in {"G2", "G3"} and (WORDS.sub("", tail).strip() or any(k not in "XYZEFIJR" for k in vals)):
            self.error("syntaxe d'arc non prise en charge : " + c)
            return None
        if "F" in vals and vals["F"] <= 0:
            self.error("vitesse non positive")
            return None
        if op in {"G2", "G3"} and vals.get("F", self.feed) <= 0:
            self.error("arc sans vitesse d'avance connue")
            return None
        start = self.spatial_xyz
        command_start = self.xyz
        command_end = tuple(
            p + vals.get(a, 0.0) if self.relative_xyz else vals.get(a, p)
            for a, p in zip("XYZ", command_start)
        )
        # Appliquer le décalage de repère conserve exactement q lorsque G92 XYZ n'a rien décalé.
        end = tuple(q + (p - r) for p, q, r in zip(start, command_end, command_start))
        if not all(math.isfinite(v) for v in (*command_end, *end)):
            self.error("repère de mouvement hors capacité numérique")
            return None
        arc = None
        if op in {"G2", "G3"}:
            arc = self._geometrie_arc(op, c, command_start, command_end, vals)
            if arc is None:
                return None
        self.feed = vals.get("F", self.feed)
        old = self.e.get(None, 0.0)
        de = 0.0
        if "E" in vals:
            if self.relative_e is None:
                self.error("mode extrusion absent (M82/M83 requis)")
            de = vals["E"] if self.relative_e is not False else vals["E"] - old
            self.e[None] = old + de
        debt = self.retracted.get(None, 0.0)
        if de < 0:
            debt -= de
            deposited = 0.0
        else:
            deposited = max(0.0, de - debt)
            debt = max(0.0, debt - de)
        self.retracted[None] = debt
        self.xyz = command_end
        self.spatial_xyz = end
        length = arc[0] if arc is not None else math.dist(start, end)
        if length > 0 and self._apres_homing:
            self._apres_homing = False
            if deposited > 0:
                self.error("Segment d’extrusion immédiatement après G28 non vérifié")
                assert self.last_event is not None  # L'événement de mouvement est créé avant ce contrôle.
                self.last_event["homing_unverified"] = True
                deposited = 0.0
        ratio = deposited / de if de > 0 else 0.0
        # La première position de dépôt sur un arc reste décrite en fraction de courbe,
        # jamais par un point interpolé sur la corde (#123).
        deposition_start = None if op in {"G2", "G3"} else tuple(
            b - (b - a) * ratio for a, b in zip(start, end)
        )
        mouvement = {
            "start": start,
            "end": end,
            "spatial_start": start,
            "spatial_end": end,
            "deposition_start": deposition_start,
            "length": length,
            "duration_s": length / self.feed * 60 if self.feed > 0 else None,
            "deposited_length": length * ratio,
            "extrusion": de,
            "deposited": deposited,
            "feed": self.feed,
            "tool": self.tool,
            "type": self.kind,
            "object": self.object,
            "op": op,
            "trajectory": "arc_g17_xy" if op in {"G2", "G3"} else "lineaire",
            "line": self.line_number,
            "layer": self.layer,
            "metadata": dict(self.metadata),
        }

        if arc is not None:
            _, cx, cy, rayon, angle, balayage = arc
            centre = (start[0] + (cx - command_start[0]), start[1] + (cy - command_start[1]))
            if all(math.isfinite(v) for v in centre):
                mouvement["arc_g17"] = {
                    "centre_xy_mm": centre,
                    "rayon_mm": rayon,
                    "angle_debut_rad": angle,
                    "balayage_signe_rad": balayage,
                    "debut_depot_fraction": 1 - ratio,
                }
        return mouvement

    def _geometrie_arc(self, op, raw, start, end, vals):
        """Donne longueur/centre/rayon (mm), angle et balayage (rad) ; None si invalide."""
        if self.arc_plane != "G17":
            self.error("plan d'arc non pris en charge : " + self.arc_plane)
            return None
        if not self.arc_center_relative:
            self.error("centres d'arc absolus G90.1 non pris en charge : " + raw)
            return None
        has_ij = "I" in vals or "J" in vals
        if has_ij == ("R" in vals):
            self.error("arc XY exige I/J relatifs ou R signé : " + raw)
            return None
        sx, sy, sz = start
        ex, ey, ez = end
        dx, dy = ex - sx, ey - sy
        if "R" in vals:
            radius = abs(vals["R"])
            chord = math.hypot(dx, dy)
            if radius == 0 or chord == 0 or chord > 2 * radius + 1e-9:
                self.error("rayon R d'arc invalide : " + raw)
                return None
            # Éviter inf-inf sur les carrés : une entrée finie ne justifie pas une fausse géométrie.
            h = radius * math.sqrt(max(0.0, 1 - (chord / radius / 2) ** 2))
            midx, midy = sx + dx / 2, sy + dy / 2
            nx, ny = -dy / chord, dx / chord
            candidats = ((midx + h * nx, midy + h * ny), (midx - h * nx, midy - h * ny))
            centres = []
            for cx, cy in candidats:
                a0, a1 = math.atan2(sy - cy, sx - cx), math.atan2(ey - cy, ex - cx)
                balayage = (a1 - a0) % (2 * math.pi) if op == "G3" else (a0 - a1) % (2 * math.pi)
                centres.append((cx, cy, balayage))
            majeur = vals["R"] < 0
            cx, cy, balayage = next((v for v in centres if (v[2] > math.pi) == majeur), centres[0])
        else:
            cx, cy = sx + vals.get("I", 0.0), sy + vals.get("J", 0.0)
            rayon = math.hypot(sx - cx, sy - cy)
            rayon_fin = math.hypot(ex - cx, ey - cy)
            if rayon <= 0 or not math.isclose(rayon, rayon_fin, rel_tol=1e-7, abs_tol=1e-6):
                self.error("centre I/J et extrémité incohérents : " + raw)
                return None
            a0, a1 = math.atan2(sy - cy, sx - cx), math.atan2(ey - cy, ex - cx)
            balayage = (a1 - a0) % (2 * math.pi) if op == "G3" else (a0 - a1) % (2 * math.pi)
            if math.hypot(dx, dy) <= 1e-9:
                balayage = 2 * math.pi
        rayon = math.hypot(sx - cx, sy - cy)
        if "R" in vals and math.hypot(dx, dy) <= 1e-9:
            self.error("cercle complet exige I/J relatifs : " + raw)
            return None
        if rayon <= 0 or balayage <= 0:
            self.error("géométrie d'arc dégénérée : " + raw)
            return None
        longueur = math.hypot(rayon * balayage, ez - sz)
        if not math.isfinite(longueur):
            self.error("géométrie d'arc hors capacité numérique : " + raw)
            return None
        angle_debut = math.atan2(sy - cy, sx - cx)
        return longueur, cx, cy, rayon, angle_debut, balayage if op == "G3" else -balayage
