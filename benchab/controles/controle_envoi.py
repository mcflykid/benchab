"""Contrôle numérique avant envoi : déclaration, G-code, géométrie et revues liées aux empreintes.

Les observations et réglages réellement appliqués sur l’imprimante restent distincts de ce contrôle du fichier.
L’API conserve les contrôles historiques ; la CLI accepte un 3MF et --declaration, puis les références facultatives.
"""

from __future__ import annotations

import json
import math
import re
import sys
import zipfile
from contextlib import ExitStack
from pathlib import Path
from xml.etree import ElementTree as E

from benchab import reglages as schema_reglages
from benchab.controles import zones_locales as ZL
from benchab.controles.gcode_interpreter import Interpreter
from benchab.controles.rapports import RapportCourant
from benchab.gcode.lecture import lire_texte_gcode_3mf
from benchab.qualite.statuts import code_statut


class RapportRefuse(ValueError):
    """Échec de la protection ou de la publication d'un rapport."""

AIRE = math.pi * (1.75 / 2) ** 2
UTILES = re.compile(r"^(G9111|M140|M190|M104|M109|M201|M203|M204|M205|M900|M106 P3|T\d+)\b")


def chemin_local(value):
    p = Path(value).absolute()
    if sys.platform == "darwin" and p.is_relative_to("/var") and Path("/var").resolve() == Path("/private/var"):
        p = Path("/private/var") / p.relative_to("/var")
    return p


def plaques(z):
    return sorted(int(m.group(1)) for n in z.namelist() if (m := re.match(r"Metadata/plate_(\d+)\.gcode$", n)))


def demarrage(g):
    s = g[: g.find(";LAYER_CHANGE")] if ";LAYER_CHANGE" in g else g[:20000]
    out = {}
    for ligne in s.splitlines():
        c = ligne.split(";")[0].strip()
        if UTILES.match(c):
            k = "M106 P3" if c.startswith("M106 P3") else c.split()[0]
            out.setdefault(k, []).append(c)
    return out


def analyse(g, outil_initial=None, erreurs=None):
    it = Interpreter(tool=outil_initial)
    flux = {}
    outils = []
    temperatures = {}
    observees = {}
    couche = -1
    temperature_commune = None
    for line in g.splitlines():
        if line.startswith(";LAYER_CHANGE"):
            couche += 1
        c = line.split(";", 1)[0].strip()
        if re.match(r"^T\d+(?:\s|$)", c):
            tool = int(c.split()[0][1:])
            if tool not in outils:
                outils.append(tool)
        move = it.feed_line(line)
        event = it.last_event or {}
        op, params = event.get("command"), event.get("parameters", {})
        if op == "G9111" and "extruderTemp" in params:
            temperature_commune = params["extruderTemp"]
        elif op in ("M104", "M109") and ("S" in params or "R" in params):
            target = params.get("T", it.tool)
            temperatures[target] = params.get("S", params.get("R"))
            if "T" not in params:
                temperature_commune = temperatures[target]
                temperatures.clear()
        if move and move["deposited"] > 0 and move["deposited_length"] > 0 and move["feed"] > 0:
            tool = move["tool"]
            flux.setdefault(tool, []).append(
                (move["deposited"] * AIRE / (move["deposited_length"] / move["feed"] * 60), move["deposited_length"])
            )
            if tool is not None and tool not in outils:
                outils.append(tool)
            if couche > 0:
                observees.setdefault(tool, set()).add(temperatures.get(tool, temperature_commune))
    if erreurs is not None:
        erreurs.extend(it.errors)
    return outils, flux, observees


def p99(v):
    v = sorted(v)
    tot = sum(longueur for _, longueur in v)
    acc = 0
    for q, longueur in v:
        acc += longueur
        if acc >= 0.99 * tot:
            return q
    return v[-1][0] if v else 0


def ajouter_note(resultats, pl, controle, statut, detail, source, obligatoire=True):
    resultats.append(
        {
            "plaque": pl,
            "controle": controle,
            "statut": statut,
            "detail": detail,
            "source": source,
            "obligatoire": obligatoire,
        }
    )


def controler(fichier, declaration, *, reference=None, geometrie=None, revue_locale=None, sortie_json=None):
    """Contrôle un 3MF tranché et renvoie son rapport sans état global."""
    with ExitStack() as ressources:
        return _controler(
            ressources,
            fichier,
            declaration,
            reference=reference,
            geometrie=geometrie,
            revue_locale=revue_locale,
            sortie_json=sortie_json,
        )


def _controler(ressources, fichier, declaration, *, reference, geometrie, revue_locale, sortie_json):
    T3MF = chemin_local(fichier)
    DECLARATION = chemin_local(declaration)
    REFF = chemin_local(reference) if reference else None
    RAPPORT = chemin_local(sortie_json) if sortie_json else None
    base_declaration = DECLARATION.resolve().parent
    if geometrie is None:
        candidate = base_declaration / "preuves/controles-pieces.json"
        geometrie = candidate if candidate.is_file() else None
    else:
        geometrie = chemin_local(geometrie)
    revue = chemin_local(revue_locale or base_declaration / "REVUE-ZONES-LOCALES.json")
    ENTREES = [T3MF, DECLARATION] + ([REFF] if REFF else []) + ([geometrie] if geometrie else []) + [revue]
    try:
        rapport = RapportCourant(RAPPORT, ENTREES, "controle_envoi") if RAPPORT else None
    except (ValueError, OSError) as error:
        raise RapportRefuse(str(error)) from error
    DEC = json.loads(Path(DECLARATION).read_text())
    risques_locaux, erreurs_locales = [], []
    z = ressources.enter_context(zipfile.ZipFile(T3MF))
    ps = schema_reglages.decoder(z.read("Metadata/project_settings.config"))
    ms = E.fromstring(z.read("Metadata/model_settings.config"))
    obj_ext = {
        o.get("id"): next((m.get("value") for m in o.findall("metadata") if m.get("key") == "extruder"), "1")
        for o in ms.findall("object")
    }
    plate_obj = {
        int(next(m.get("value") for m in p.findall("metadata") if m.get("key") == "plater_id")): [
            next(m.get("value") for m in mi.findall("metadata") if m.get("key") == "object_id")
            for mi in p.findall("model_instance")
        ]
        for p in ms.findall("plate")
    }
    ref = ressources.enter_context(zipfile.ZipFile(REFF)) if REFF else None
    res = []
    bed = ps.get("curr_bed_type")
    ajouter_note(
        res,
        0,
        "type de plateau",
        "PASS" if bed == DEC.get("plateau") else "FAIL",
        f"{bed} (déclaré : {DEC.get('plateau')})",
        "3MF : project_settings.curr_bed_type",
    )
    slots = {k: ps.get(k) for k in ("ace_t_slot_vector", "ace_t_box_vector", "ace_t_nozzle_vector")}
    liste_plaques = plaques(z)
    if not liste_plaques:
        ajouter_note(res, 0, "G-code présent", "FAIL", "aucune plaque tranchée : contrôle impossible", "3MF")
    for pl in liste_plaques:
        g = lire_texte_gcode_3mf(T3MF, f"Metadata/plate_{pl}.gcode", erreurs="ignore")
        local = ZL.analyser_gcode(g, pl)
        risques_locaux.extend(local["zones"])
        erreurs_locales.extend(local["erreurs"])
        dem = demarrage(g)
        exts = sorted({int(obj_ext.get(o, "1")) for o in plate_obj.get(pl, [])})
        attendus = [e - 1 for e in exts]
        # S1 natif mono-filament : G9111 prépare l'unique buse sans émettre T0.
        # Ne rien déduire d'une déclaration seule, d'un projet multi-filament ou d'un autre dialecte.
        mono_natif = (
            ps.get("printer_model") == "Anycubic Kobra S1"
            and all(
                isinstance(ps.get(k), list) and len(ps[k]) == 1
                for k in (
                    "filament_type",
                    "nozzle_temperature",
                    "nozzle_temperature_initial_layer",
                    "filament_max_volumetric_speed",
                )
            )
            and attendus == [0]
            and len(dem.get("G9111", [])) == 1
            and not re.search(r"^\s*T\d+(?:\s|;|$)", g, re.M)
        )
        erreurs_interpretation = []
        outils, flux, temperatures = analyse(g, outil_initial=0 if mono_natif else None, erreurs=erreurs_interpretation)
        if erreurs_interpretation:
            ajouter_note(
                res,
                pl,
                "couverture interprétation G-code",
                "FAIL",
                "; ".join(erreurs_interpretation),
                "interpréteur linéaire commun",
            )
        if flux.get(None):
            ajouter_note(
                res,
                pl,
                "extrusions sans outil identifié",
                "FAIL",
                "extrusion avant toute sélection d’outil ; débit non attribuable",
                "G-code et model_settings",
            )
        ajouter_note(
            res,
            pl,
            "outils utilisés",
            "PASS" if sorted(outils) == attendus else "FAIL",
            f"T{outils} dans le G-code ; extrudeurs des objets {exts} → T{attendus}"
            + (
                " ; T0 implicite : Kobra S1, un seul filament, G9111 et extrusion effective, aucune commande T"
                if mono_natif
                else ""
            ),
            "G-code et model_settings",
        )
        for e in exts:
            fd = DEC["filaments"].get(str(e))
            if not fd:
                ajouter_note(res, pl, f"filament {e}", "FAIL", "extrudeur utilisé non déclaré", "déclaration")
                continue
            g91 = dem.get("G9111", [""])[0]
            vals = dict(re.findall(r"(\w+)=([\d.]+)", g91))
            if e - 1 == (outils[0] if outils else None):
                ok = (
                    float(vals.get("extruderTemp", -1)) == fd["temp_1re"]
                    and float(vals.get("bedTemp", -1)) == fd["plateau_c"]
                )
                ajouter_note(
                    res,
                    pl,
                    f"chauffe de démarrage (filament {e})",
                    "PASS" if ok else "FAIL",
                    f"G9111 : buse {vals.get('extruderTemp')} °C, plateau {vals.get('bedTemp')} °C ; "
                    f"déclaré {fd['temp_1re']} / {fd['plateau_c']} (fiche {fd.get('fiche')})",
                    "G-code : G9111",
                )
            valeurs = temperatures.get(e - 1, set())
            tv = sorted(v for v in valeurs if v is not None)
            etat = (
                "NON VÉRIFIÉ"
                if not valeurs or None in valeurs
                else "PASS"
                if all(v == fd["temp"] for v in tv)
                else "WARN"
            )
            ajouter_note(
                res,
                pl,
                f"température après la 1re couche (filament {e})",
                etat,
                f"Cibles de chauffe pendant extrusion après couche 1 : {tv} ; déclaré {fd['temp']} ; "
                f"réglage du 3MF {ps['nozzle_temperature'][e - 1]}",
                "G-code : G9111/M104/M109, état modal par outil ; pas de température physique mesurée",
            )
            q = p99(flux.get(e - 1, []))
            mx = max((a for a, _ in flux.get(e - 1, [])), default=0)
            plafond = fd.get("debit_max")
            plafond_valide = (
                isinstance(plafond, (int, float))
                and not isinstance(plafond, bool)
                and math.isfinite(plafond)
                and plafond > 0
            )
            ajouter_note(
                res,
                pl,
                f"débit commandé (filament {e})",
                "PASS" if plafond_valide and flux.get(e - 1) and q <= plafond * 1.05 else "FAIL",
                f"99 % de la longueur sous {q:.2f} mm³/s, maximum ponctuel {mx:.2f} ; plafond déclaré {plafond} ; "
                f"réglage du 3MF {ps['filament_max_volumetric_speed'][e - 1]}",
                "G-code (calcul ; ce n'est pas une mesure sur la machine)",
            )
            vide = not any(slots.values())
            ajouter_note(
                res,
                pl,
                f"correspondance ACE (filament {e})",
                "NON VÉRIFIÉ",
                (
                    f"déclaré : emplacement {fd['emplacement_ace']} ({fd['matiere']}) → T{e - 1} ; "
                    "vecteurs ace_t_* vides dans ce 3MF : l'affectation se fait à l'envoi dans l'interface"
                    if vide
                    else f"déclaré : emplacement {fd['emplacement_ace']} ; 3MF : {slots}"
                )
                + " ; routage physique non vérifié sans essai",
                "déclaration ; 3MF : ace_t_*",
                obligatoire=False,
            )
        if ref:
            rp = plaques(ref)
            if pl not in rp:
                ajouter_note(
                    res,
                    pl,
                    "commandes utiles du démarrage",
                    "FAIL",
                    "plaque absente de la référence ; aucune substitution",
                    "3MF de référence",
                )
                continue
            rg = lire_texte_gcode_3mf(REFF, f"Metadata/plate_{pl}.gcode", erreurs="ignore")
            rd = demarrage(rg)
            ecarts = {k: (rd.get(k), dem.get(k)) for k in sorted(set(rd) | set(dem)) if rd.get(k) != dem.get(k)}
            ajouter_note(
                res,
                pl,
                "commandes utiles du démarrage",
                "PASS" if not ecarts else "WARN",
                "identiques à la référence"
                if not ecarts
                else f"à expliquer : {json.dumps(ecarts, ensure_ascii=False)[:600]}",
                f"G-code, comparé à {Path(REFF).name}",
            )
        else:
            ajouter_note(
                res,
                pl,
                "commandes utiles du démarrage",
                "NON VÉRIFIÉ",
                f"aucune référence fournie ; relevé : {json.dumps(dem, ensure_ascii=False)[:400]}",
                "G-code",
                obligatoire=False,
            )
    local = ZL.evaluer(risques_locaux, erreurs_locales, T3MF, geometrie, revue)
    ajouter_note(
        res,
        0,
        "revue locale anti-boudinage",
        local["statut"],
        f"{len(local['zones'])} zones, {len(local['non_revues'])} sans revue valide ; erreurs : {local['erreurs']}",
        "G-code, contrôle géométrique et REVUE-ZONES-LOCALES.json liés par empreintes ; aucune validation physique",
    )
    pire = max(
        2
        if r["statut"] == "NON VÉRIFIÉ" and r["obligatoire"]
        else {"FAIL": 2, "WARN": 1, "NON VÉRIFIÉ": 0, "PASS": 0}[r["statut"]]
        for r in res
    )
    statut_global = "FAIL" if pire == 2 else "WARN" if any(r["statut"] != "PASS" for r in res) else "PASS"
    if RAPPORT:
        try:
            rapport.publier(
                {
                    "fichier": str(T3MF),
                    "statut": statut_global,
                    "code": code_statut(statut_global),
                    "conforme_numerique": pire == 0,
                    "resultats": res,
                    "zones_locales": local,
                }
            )
        except (ValueError, OSError) as error:
            raise RapportRefuse(str(error)) from error
    return {
        "code": code_statut(statut_global),
        "fichier": str(T3MF),
        "statut": statut_global,
        "conforme_numerique": pire == 0,
        "resultats": res,
        "zones_locales": local,
    }


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    options = {}
    positional = []
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--"):
            if token not in ("--declaration", "--reference", "--geometrie", "--revue-locale", "--json"):
                print(f"Option inconnue : {token}", file=sys.stderr)
                return 2
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                print(f"Option sans valeur : {token}", file=sys.stderr)
                return 2
            options[token] = args[i + 1]
            i += 2
        else:
            positional.append(token)
            i += 1
    if len(positional) != 1 or not options.get("--declaration"):
        print(
            "Usage : controle_envoi.py <3mf> --declaration DECLARATION.json "
            "[--reference 3mf] [--geometrie RAPPORT] [--revue-locale REVUE] [--json SORTIE]",
            file=sys.stderr,
        )
        return 2
    try:
        rapport = controler(
            positional[0],
            options["--declaration"],
            reference=options.get("--reference"),
            geometrie=options.get("--geometrie"),
            revue_locale=options.get("--revue-locale"),
            sortie_json=options.get("--json"),
        )
    except RapportRefuse as error:
        print("Rapport refusé : " + str(error), file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        print("Contrôle refusé : " + str(error), file=sys.stderr)
        return 2
    for r in rapport["resultats"]:
        print(f"P{r['plaque']} {r['statut']:12s} {r['controle']} — {r['detail']}  [{r['source']}]")
    return rapport["code"]


if __name__ == "__main__":
    raise SystemExit(main())
