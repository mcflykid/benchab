"""

Vérifie dans le G-code réellement tranché que les réglages par objet demandés ont pris effet
(bordure, lissage, vitesse de paroi extérieure, couture côté arrière du plateau, hauteur de couche, température).
usage : python -I verifier_reglages.py <3mf_tranche> <placement.json> <sortie.json>

"""

import collections
import json
import re
import sys
import zipfile
from pathlib import Path

from benchab import reglages as schema_reglages
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import lire_texte_gcode_3mf


def main(argv):
    """Exécute main avec les entrées déclarées et renvoie le résultat du contrôle. Réglages en unités
    natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé.

    Entrées : archive, placement et rapport ; retour 0 conforme, 1 non conforme ou 2 usage invalide.
    Les consignes thermiques sont informatives et ne changent pas le verdict global.
    """
    if len(argv) != 3:
        print(__doc__)
        return 2
    TMF, PLACE, OUT = Path(argv[0]), json.loads(Path(argv[1]).read_text()), Path(argv[2])
    z = zipfile.ZipFile(TMF)
    voulu = {o["name"]: o.get("reglages", {}) for o in PLACE["objects"]}
    res = {"source": TMF.name, "plaques": []}
    for k, pl in enumerate(PLACE["plates"], 1):
        g = lire_texte_gcode_3mf(TMF, f"Metadata/plate_{k}.gcode", erreurs="ignore").splitlines()
        typ = ""
        zc = 0.0
        x = y = 0.0
        f = 0.0
        hauteurs = set()
        temps = set()
        consignes = []
        premiere = float(
            schema_reglages.decoder(z.read("Metadata/project_settings.config")).get("initial_layer_print_height", "0.2")
        )
        st = collections.defaultdict(lambda: {"types": collections.Counter(), "f_paroi_ext": [], "boucles": []})
        boucle = None
        nouvelle_boucle = False
        it = Interpreter()
        for ligne in g:
            move = it.feed_line(ligne)
            evenement = it.last_event or {}
            commande = evenement.get("command")
            parametres = evenement.get("parameters", {})
            # Une seule grammaire thermique : G9111 utilise déjà les événements
            # du lecteur commun, sans créer un verdict thermique (#99, #13).
            cle_temperature = "extruderTemp" if commande == "G9111" else "S" if "S" in parametres else "R"
            if (
                commande in {"G9111", "M104", "M109"}
                and evenement.get("kind") in {"macro", "command"}
                and cle_temperature in parametres
            ):
                temperature = parametres[cle_temperature]
                cible = parametres.get("T", evenement.get("tool"))
                # M104 Tn vise la buse déclarée sans changer l’outil actif.
                outil = int(cible) if isinstance(cible, (int, float)) and cible >= 0 and int(cible) == cible else None
                temps.add(temperature)
                consignes.append({
                    "commande": commande,
                    "ligne": evenement["line"],
                    "outil": outil,
                    "temperature_c": temperature,
                    "extinction": temperature == 0,
                })
            if ligne.startswith(";Z:"):
                zc = float(ligne[3:])
            elif ligne.startswith(";HEIGHT:"):
                hauteurs.add(ligne[8:])
            elif ligne.startswith(";TYPE:"):
                typ = ligne[6:]
                nouvelle_boucle = True
            elif ligne.startswith("EXCLUDE_OBJECT_START"):
                re.search(r"NAME=(\S+)", ligne).group(1)
            elif ligne.startswith("EXCLUDE_OBJECT_END"):
                pass
            elif move:
                nx, ny = move["end"][:2]
                x, y = move["deposition_start"][:2]
                f = move["feed"]
                if move["deposited_length"] > 0:
                    key = move["object"] or "(hors objet)"
                    st[key]["types"][typ] += 1
                    if typ == "Outer wall":
                        if zc > premiere + 1e-3 and f > 0:
                            st[key]["f_paroi_ext"].append(f)
                        if nouvelle_boucle or boucle is None:
                            boucle = {"debut": (x, y), "ys": [y]}
                            st[key]["boucles"].append(boucle)
                            nouvelle_boucle = False
                        boucle["ys"].append(ny)
                    else:
                        boucle = None
                elif move["length"] > 0:
                    nouvelle_boucle = True
                x, y = nx, ny
        plaque = {
            "plaque": pl["name"],
            "hauteurs_de_couche": sorted(hauteurs, key=float)[:6],
            "temperatures_buse": sorted(temps),
            "consignes_buse": consignes,
            "objets": [],
        }
        centres = {
            o["name"]: (o["xy"][0] + o["size"][0] / 2, o["xy"][1] + o["size"][1] / 2)
            for o in PLACE["objects"]
            if o["plate"] == k
        }
        for name, d in st.items():
            if name == "(hors objet)":
                continue  # purge/setup sans identité : hors critères par objet
            nom = (
                re.sub(r"_id_\d+_copy_\d+$", "", name) if name not in centres else name
            )  # suffixe exact émis par Anycubic, jamais de préfixe flou
            v = voulu.get(nom, {})
            fe = sorted(set(round(q / 60.0) for q in d["f_paroi_ext"]))
            cx, cy = centres.get(nom, (None, None))
            arr = None
            bl = [
                b for b in d["boucles"] if max(b["ys"]) - min(b["ys"]) > 3.0
            ]  # boucles d'au moins 3 mm (couture lisible)
            if bl:  # Départ de boucle à moins de 1 mm du point arrière (+Y plateau) de sa boucle.
                arr = round(100.0 * sum(1 for b in bl if b["debut"][1] >= max(b["ys"]) - 1.0) / len(bl))
            o = {
                "objet": nom,
                "bordure": d["types"].get("Brim", 0),
                "jupe": d["types"].get("Skirt", 0),
                "lissage": d["types"].get("Ironing", 0),
                "vitesses_paroi_ext_mm_s": fe[-4:],
                "coutures_a_l_arriere_de_leur_boucle_pct": arr,
                "voulu": v,
            }
            ok = not it.errors and nom in centres
            if v.get("brim_type") not in (None, "outer_only", "no_brim"):
                ok = False
            if v.get("ironing_type") not in (None, "top", "no ironing"):
                ok = False
            if v.get("seam_position") not in (None, "back"):
                ok = False
            if v.get("brim_type") == "outer_only" and not o["bordure"]:
                ok = False
            if v.get("brim_type") == "no_brim" and o["bordure"]:
                ok = False
            if v.get("ironing_type") == "top" and not o["lissage"]:
                ok = False
            if "outer_wall_speed" in v and (not fe or max(fe) > float(v["outer_wall_speed"]) + 1):
                ok = False
            if v.get("seam_position") == "back" and (arr is None or arr < 90):
                ok = False
            o["conforme"] = ok
            plaque["objets"].append(o)
        presents = {o["objet"] for o in plaque["objets"]}
        for nom in centres.keys() - presents:
            plaque["objets"].append(
                {
                    "objet": nom,
                    "bordure": 0,
                    "jupe": 0,
                    "lissage": 0,
                    "vitesses_paroi_ext_mm_s": [],
                    "coutures_a_l_arriere_de_leur_boucle_pct": None,
                    "voulu": voulu.get(nom, {}),
                    "conforme": False,
                    "erreur": "aucun dépôt identifié pour cet objet",
                }
            )
        plaque["erreurs"] = it.errors
        res["plaques"].append(plaque)
        print(pl["name"], plaque["hauteurs_de_couche"], plaque["temperatures_buse"])
        for o in plaque["objets"]:
            if o["voulu"] or not o["conforme"]:
                print(
                    "   ",
                    o["objet"],
                    "bordure",
                    o["bordure"],
                    "lissage",
                    o["lissage"],
                    "v",
                    o["vitesses_paroi_ext_mm_s"],
                    "arr%",
                    o["coutures_a_l_arriere_de_leur_boucle_pct"],
                    "OK" if o["conforme"] else "NON CONFORME",
                )
    res["tout_conforme"] = (
        bool(res["plaques"])
        and all(p["objets"] and not p["erreurs"] for p in res["plaques"])
        and all(o["conforme"] for p in res["plaques"] for o in p["objets"])
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print("tout conforme", res["tout_conforme"])

    return 0 if res["tout_conforme"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as ex:
        print("Erreur technique :", ex, file=sys.stderr)
        sys.exit(2)
