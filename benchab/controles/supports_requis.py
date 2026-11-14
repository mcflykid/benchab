"""Supports que le slicer ajouterait avec les paramètres demandés.

Usage : benchab controle supports-requis <plaques.3mf> <sortie.json> [--seuil-g 1.0] [--garder <dossier>]
        [--type arbre|normal] [--style organique|fin|fort|hybride|grille|ajuste|defaut] [--manuel] [--partout]
        [--angle 30] [--z-haut 0.18] [--interfaces 2] [--reglage cle=valeur …]
Tranche une copie du 3MF de plaques avec les supports activés (par défaut : arbre organique, plateau seul, seuil
d'angle du profil)
et mesure, par plaque, la masse extrudée en « Support » dans le G-code (corps et interface), par tranche de 5 mm de
hauteur, avec le
temps et la masse totale estimés. Sert à vérifier une exigence « aucun support » : un projet tranché sans support
n'est pas pour autant
imprimable sans support. --manuel : supports seulement sur les forceurs ; --partout : aussi depuis la pièce. Les
réglages passés
remplacent aussi ceux de chaque objet. Le 3MF d'origine n'est jamais modifié. Choix du type :
docs/guides/methodes/supports-anycubic.md.
Code 0 si chaque plaque reste sous le seuil, 1 sinon, 2 si la preuve ne peut pas être publiée.
Estimation du slicer, pas une observation d'impression.
"""

import json
import math
import re
import shutil
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from benchab import reglages as schema_reglages
from benchab.archives_3mf import Archive3MF
from benchab.fabrication import anycubic as A
from benchab.gcode.lecture import lire_texte_gcode_3mf
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve, ecrire_json_neuf

TYPES = {"arbre": "tree", "normal": "normal"}
STYLES = {
    "organique": "organic",
    "fin": "tree_slim",
    "fort": "tree_strong",
    "hybride": "tree_hybrid",
    "grille": "grid",
    "ajuste": "snug",
    "defaut": "default",
}
PAR_DEFAUT = {
    "enable_support": "1",
    "support_type": "tree(auto)",
    "support_style": "organic",
    "support_on_build_plate_only": "1",
}


def reglages(
    type_="arbre", style="organique", manuel=False, partout=False, angle=None, z_haut=None, interfaces=None, autres=()
):
    """Construit les clés Anycubic et Orca du support demandé.

    Lève ValueError sur un nom inconnu ou un style d’arbre associé à un support normal.
    """
    if type_ not in TYPES or style not in STYLES:
        raise ValueError(f"type {type_!r} ou style {style!r} inconnu")
    if (
        type_ == "normal"
        and style not in ("grille", "ajuste", "defaut")
        or type_ == "arbre"
        and style in ("grille", "ajuste")
    ):
        raise ValueError(f"style {style!r} incompatible avec le type {type_!r}")
    r = dict(
        PAR_DEFAUT,
        support_type=f"{TYPES[type_]}({'manual' if manuel else 'auto'})",
        support_style=STYLES[style],
        support_on_build_plate_only="0" if partout else "1",
    )
    if angle is not None:
        r["support_threshold_angle"] = str(angle)
    if z_haut is not None:
        r.update(support_top_z_distance=str(z_haut), support_bottom_z_distance=str(z_haut))
    if interfaces is not None:
        r.update(support_interface_top_layers=str(interfaces), support_interface_bottom_layers=str(interfaces))
    for kv in autres:
        k, v = kv.split("=", 1)
        r[k] = v
    return r


def masse_support(g, aire, densite):
    """Masse de support extrudée dans le texte G-code g : ({tranche de 5 mm: grammes}, grammes d'interface).

        Seuls les trajets (X ou Y) comptent : un « G1 E… » sans déplacement est un réamorçage après rétraction, pas
    du
        support
        Partagée avec
    orientations_supports.py.
    """
    kind, zc, bandes, interface = None, 0.0, {}, 0.0
    for ligne in g.splitlines():
        if ligne.startswith(";TYPE:"):
            kind = ligne[6:]
            continue
        if ligne.startswith(";Z:"):
            zc = float(ligne[3:])
            continue
        m = re.match(r"G1 [^;]*E(-?[\d.]+)", ligne)
        if (
            m
            and kind
            and kind.startswith("Support")
            and float(m.group(1)) > 0
            and re.search(r"[XY]-?[\d.]", ligne.split(";")[0])
        ):
            gr = float(m.group(1)) * aire * densite / 1000
            k = int(zc // 5) * 5
            bandes[k] = bandes.get(k, 0.0) + gr
            if "interface" in kind.lower():
                interface += gr
    return bandes, interface


def _publier_gcodes(source, dossier, fichiers):
    """Publie les G-code conservés en preuves neuves, sans écraser une plaque antérieure."""
    cibles = [destination_neuve(Path(dossier) / fichier.name) for fichier in fichiers]
    if not cibles:
        return
    with PublicationNeuve([source, *fichiers], cibles) as publication:
        for fichier, cible in zip(fichiers, cibles, strict=True):
            shutil.copyfile(fichier, publication.temporaire(cible))
        publication.publier()


def mesurer(src, regl=None, seuil=1.0, garder=None):
    """Tranche une copie et renvoie le rapport par plaque.

    src n'est jamais
    modifié. Réglages en unités natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage
    inconnu transformé.
    """
    from benchab import integrite_3mf

    regl = dict(PAR_DEFAUT if regl is None else regl)
    avant = integrite_3mf.instantane(src)
    permis = []
    for cle, valeur in regl.items():
        chemin = "projet/" + cle
        if chemin not in avant or avant[chemin] != valeur:
            permis.append(integrite_3mf.autorisation(chemin, avant.get(chemin), valeur, present_avant=chemin in avant))
        for chemin, precedente in avant.items():
            if not chemin.startswith("projet/") and chemin.endswith("/" + cle) and precedente != str(valeur):
                permis.append(integrite_3mf.autorisation(chemin, precedente, str(valeur)))
    with Archive3MF(src) as z:
        c = {n: z.lire(n) for n in z.noms()}
    st = schema_reglages.decoder(c["Metadata/project_settings.config"])
    st.update(regl)
    c["Metadata/project_settings.config"] = schema_reglages.encoder(st).encode()
    ms = c["Metadata/model_settings.config"].decode()
    for k, v in regl.items():
        ms = re.sub(rf'key="{re.escape(k)}" value="[^"]*"', f'key="{k}" value="{v}"', ms)
    c["Metadata/model_settings.config"] = ms.encode()
    densite = float(schema_reglages.valeur_unique(st, "filament_density"))
    aire = math.pi * (float(schema_reglages.valeur_unique(st, "filament_diameter")) / 2) ** 2
    with tempfile.TemporaryDirectory() as d:
        copie = Path(d) / "supports.3mf"
        with ZipFile(copie, "w", ZIP_DEFLATED) as o:
            for n, b in c.items():
                o.writestr(n, b)
        # Variante de support demandée explicitement ; aucun autre réglage
        # ne peut changer en cours de copie (docs/reference/integrite-3mf.md).
        integrite_3mf.verifier(src, copie, permis)
        f, _ = A.trancher(copie, d, "tranche-supports.3mf")
        res = A.resultats(f)
        plaques = []
        gardes = []
        dossier_gcodes = Path(d) / "gcode-a-conserver"
        if garder:
            dossier_gcodes.mkdir()
        for p in res:
            g = lire_texte_gcode_3mf(f, f"Metadata/plate_{p['index']}.gcode")
            bandes, interface = masse_support(g, aire, densite)
            tot = sum(bandes.values())
            if garder:
                temporaire_gcode = dossier_gcodes / f"plaque-{p['index']}.gcode"
                temporaire_gcode.write_text(g, encoding="utf-8")
                gardes.append(temporaire_gcode)
            plaques.append(
                {
                    "plaque": p["index"],
                    "support_g": round(tot, 2),
                    "interface_g": round(interface, 2),
                    "par_5mm_g": {str(k): round(v, 2) for k, v in sorted(bandes.items()) if v >= 0.05},
                    "total_g": round(p["grammes"], 2),
                    "secondes": round(p["secondes"]),
                    "statut": "PASS" if tot < seuil else "WARN",
                }
            )
        if garder:
            _publier_gcodes(src, garder, gardes)
    return {
        "source": str(src),
        "seuil_g": seuil,
        "reglages": regl,
        "plaques": plaques,
        "statut": "PASS" if all(p["statut"] == "PASS" for p in plaques) else "WARN",
        "limite": "supports automatiques du slicer selon les réglages indiqués ; estimation, pas un essai",
    }


def main(argv=None):
    """Estime les supports en g et conserve le rapport complet, même avec --resume.

    Renvoie 0 (PASS), 1 (WARN) ou 2 si refus ; aucune commande imprimante.
    """
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) < 2:
        print(__doc__)
        return 2

    def opt(n, d=None):
        return a[a.index(n) + 1] if n in a else d

    autres = [x for i, x in enumerate(a) if i and a[i - 1] == "--reglage"]
    try:
        source = Path(a[0])
        sortie = destination_neuve(Path(a[1]))
        regl = reglages(
            opt("--type", "arbre"),
            opt("--style", "organique" if opt("--type", "arbre") == "arbre" else "defaut"),
            "--manuel" in a,
            "--partout" in a,
            opt("--angle"),
            opt("--z-haut"),
            opt("--interfaces"),
            autres,
        )
        r = mesurer(source, regl, float(opt("--seuil-g", 1.0)), opt("--garder"))
        ecrire_json_neuf(sortie, r, entrees=[source])
    except (OSError, ValueError, IndexError) as e:
        print(f"refusé : {e}", file=sys.stderr)
        return 2
    if "--resume" in a[2:]:
        # Le détail par hauteur est déjà conservé dans la preuve neuve (#35).
        print(json.dumps({
            "statut": r["statut"], "seuil_g": r["seuil_g"], "plaques": len(r["plaques"]),
            "alertes": [{"plaque": p["plaque"], "support_g": p["support_g"]} for p in r["plaques"]
                        if p["statut"] != "PASS"],
            "rapport": str(sortie),
        }, ensure_ascii=False))
    else:
        for p in r["plaques"]:
            print(f"{p['statut']:5s} plaque {p['plaque']} : {p['support_g']} g de supports demandés {p['par_5mm_g']}")
    return 0 if r["statut"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
