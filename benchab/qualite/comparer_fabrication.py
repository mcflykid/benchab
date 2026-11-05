"""Comparaison stricte d'une reconstruction à ses références. Lecture seule.
Lancé séparément, hors du bac à sable, après la reconstruction : seul ce comparateur lit attendu/.
Trois catégories, sans aucune tolérance automatique :
  géométrie   : STL, STEP, GLB, manifestes (assemblage.json, cao-nominale.json) -> égalité d'empreinte ;
  fabrication : 3MF membre par membre (G-code comparé ligne à ligne hors commentaires,
                réglages comparés clé par clé), placements et noms de plaques ; toute
                différence est bloquante (« à expliquer »), sauf vignettes PNG documentaires ;
  documentaire : journaux, rapports, images -> chaque différence est listée ; une différence
                 réduite à des dates, heures ou durées est signalée, toute autre reste « non expliquée ».
Un écart documentaire ne compense jamais un écart de géométrie ou de fabrication.
Identité binaire : un 3MF dont les membres sont identiques mais dont le conteneur ZIP
diffère (par exemple horodatages) est compté « contenu identique » et relevé séparément.
Un .gcode isolé est comparé par commandes puis par commentaires."""

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from benchab import reglages
from benchab.qualite.ecritures import PublicationNeuve, sortie_privee, valider_chemins

GEO = {".stl", ".step", ".stp", ".glb", ".obj"}
GEO_NOMS = {"assemblage.json", "cao-nominale.json"}
FAB_NOMS = {"placement.json", "plaques-noms.json"}
DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?"
    r"|\d{2}/\d{2}/\d{4}( \d{2}:\d{2})?"
    r'|"(date|heure|debut|fin|tranchage_secondes|duree_s|secondes)": ("[^"]*"|[\d.]+)'
)  # pas de nombre générique


def h(b):
    return hashlib.sha256(b).hexdigest()


def categorie(rel):
    p = Path(rel)
    if p.suffix.lower() in GEO or p.name in GEO_NOMS:
        return "geometrie"
    if p.suffix.lower() in (".3mf", ".gcode") or p.name in FAB_NOMS:
        return "fabrication"
    return "documentaire"


def commandes(b):
    return [
        ligne
        for ligne in b.decode("utf8", "ignore").splitlines()
        if ligne.strip() and not ligne.lstrip().startswith(";")
    ]


def cles(b, *, reglages_slicer=False):
    """Décode un objet sans unité ; None si illisible, sans normaliser ni réécrire les réglages."""
    try:
        # Le lecteur typé conserve les clés inconnues et refuse les clés JSON
        # dupliquées sans les écraser (docs/reference/reglages-et-integrite.md).
        return reglages.decoder(b) if reglages_slicer else json.loads(b)
    except Exception:
        return None


def comparer_3mf(a, b):
    """Compare deux ZIP 3MF sans unité ; renvoie écarts/doc, sans transformer les réglages ni valider l'impression."""
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        na, nb = set(za.namelist()), set(zb.namelist())
        ecarts = [{"membre": m, "ecart": "absent de la reconstruction"} for m in sorted(na - nb)] + [
            {"membre": m, "ecart": "en trop dans la reconstruction"} for m in sorted(nb - na)
        ]
        doc = []
        for m in sorted(na & nb):
            x, y = za.read(m), zb.read(m)
            if x == y:
                continue
            if m.lower().endswith(".png"):
                doc.append({"membre": m, "ecart": "vignette différente"})
                continue
            if m.endswith(".gcode"):
                ca, cb = commandes(x), commandes(y)
                if ca == cb:
                    da = [ligne for ligne in x.decode("utf8", "ignore").splitlines() if ligne.lstrip().startswith(";")]
                    db = [ligne for ligne in y.decode("utf8", "ignore").splitlines() if ligne.lstrip().startswith(";")]
                    diff = [(u, v) for u, v in zip(da, db) if u != v][:20]
                    ecarts.append(
                        {
                            "membre": m,
                            "ecart": "commandes identiques ; commentaires différents (à expliquer)",
                            "commentaires": diff,
                            "nb": sum(u != v for u, v in zip(da, db)) + abs(len(da) - len(db)),
                        }
                    )
                else:
                    k = next((i for i, (u, v) in enumerate(zip(ca, cb)) if u != v), min(len(ca), len(cb)))
                    ecarts.append(
                        {
                            "membre": m,
                            "ecart": "COMMANDES G-CODE DIFFÉRENTES",
                            "lignes": [len(ca), len(cb)],
                            "premiere_difference": k,
                            "attendu": ca[k : k + 3],
                            "obtenu": cb[k : k + 3],
                        }
                    )
                continue
            est_reglage = m == "Metadata/project_settings.config"
            ja, jb = cles(x, reglages_slicer=est_reglage), cles(y, reglages_slicer=est_reglage)
            if isinstance(ja, dict) and isinstance(jb, dict):
                d = {c: [ja.get(c), jb.get(c)] for c in sorted(set(ja) | set(jb)) if ja.get(c) != jb.get(c)}
                ecarts.append(
                    {
                        "membre": m,
                        "ecart": "réglages différents (à expliquer)",
                        "cles": {c: [str(v[0])[:200], str(v[1])[:200]] for c, v in d.items()},
                    }
                )
            else:
                la, lb = x.decode("utf8", "ignore").splitlines(), y.decode("utf8", "ignore").splitlines()
                diff = [(i, u[:200], v[:200]) for i, (u, v) in enumerate(zip(la, lb)) if u != v][:20]
                ecarts.append(
                    {
                        "membre": m,
                        "ecart": "contenu différent (à expliquer)",
                        "lignes_differentes": diff,
                        "tailles": [len(x), len(y)],
                    }
                )
    return ecarts, doc


def normaliser(t):
    return DATE.sub("<N>", t)


def comparer(attendu, obtenu, sortie, *, prive=None):
    attendu, obtenu, sortie = Path(attendu), Path(obtenu), Path(sortie)
    sortie = sortie_privee(sortie, prive=prive)
    valider_chemins([attendu, obtenu], [sortie])
    if not attendu.is_dir() or not obtenu.is_dir():
        raise ValueError("Dossiers attendu et obtenu existants requis.")
    attendu, obtenu = attendu.resolve(), obtenu.resolve()
    if attendu == obtenu:
        raise ValueError("Attendu et obtenu doivent être distincts.")
    if sortie.resolve().is_relative_to(attendu) or sortie.resolve().is_relative_to(obtenu):
        raise ValueError("Le rapport doit rester hors des dossiers comparés.")
    for dossier in (attendu, obtenu):
        for here, dirs, files in os.walk(dossier, followlinks=False):
            for nom in dirs + files:
                if (Path(here) / nom).is_symlink():
                    raise ValueError("Lien symbolique dans les entrées comparées.")
    fa = {str(p.relative_to(attendu)): p for p in attendu.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    fo = {str(p.relative_to(obtenu)): p for p in obtenu.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if not fa or not fo:
        raise ValueError("Dossiers comparés vides : aucune preuve de reconstruction.")
    initial = {p: h(p.read_bytes()) for p in [*fa.values(), *fo.values()]}
    for p in initial:
        if p.suffix.lower() == ".3mf":
            try:
                with zipfile.ZipFile(p) as z:
                    if not z.namelist() or len(set(z.namelist())) != len(z.namelist()) or z.testzip() is not None:
                        raise ValueError("3MF vide, corrompu ou membres dupliqués : " + str(p))
            except zipfile.BadZipFile as exc:
                raise ValueError("3MF vide ou corrompu : " + str(p)) from exc
    res = {
        c: {"identiques": 0, "ecarts": [], "absents": [], "en_trop": []}
        for c in ("geometrie", "fabrication", "documentaire")
    }
    res["identite_binaire"] = {"differents_contenu_identique": []}
    for r in sorted(set(fa) - set(fo)):
        res[categorie(r)]["absents"].append(r)
    for r in sorted(set(fo) - set(fa)):
        res[categorie(r)]["en_trop"].append(r)
    for r in sorted(set(fa) & set(fo)):
        c = categorie(r)
        x, y = fa[r].read_bytes(), fo[r].read_bytes()
        if x == y:
            res[c]["identiques"] += 1
            continue
        if c == "geometrie":
            res[c]["ecarts"].append({"fichier": r, "attendu": h(x), "obtenu": h(y)})
        elif r.endswith(".3mf"):
            e, d = comparer_3mf(fa[r], fo[r])
            if e:
                res["fabrication"]["ecarts"].append({"fichier": r, "membres": e})
            else:
                res["fabrication"]["identiques"] += 1
                res["identite_binaire"]["differents_contenu_identique"].append(
                    {
                        "fichier": r,
                        "attendu": h(x),
                        "obtenu": h(y),
                        "nature": "membres identiques, conteneur ZIP différent",
                    }
                )
            if d:
                res["documentaire"]["ecarts"].append({"fichier": r, "membres": d, "nature": "vignettes du 3MF"})
        elif r.endswith(".gcode"):
            ca, cb = commandes(x), commandes(y)
            if ca == cb:
                res[c]["ecarts"].append(
                    {
                        "fichier": r,
                        "ecart": "commandes identiques ; commentaires différents (à expliquer)",
                        "attendu": h(x),
                        "obtenu": h(y),
                    }
                )
            else:
                k = next((i for i, (u, v) in enumerate(zip(ca, cb)) if u != v), min(len(ca), len(cb)))
                res[c]["ecarts"].append(
                    {
                        "fichier": r,
                        "ecart": "COMMANDES G-CODE DIFFÉRENTES",
                        "lignes": [len(ca), len(cb)],
                        "premiere_difference": k,
                        "attendu": ca[k : k + 3],
                        "obtenu": cb[k : k + 3],
                    }
                )
        elif c == "fabrication":
            res[c]["ecarts"].append(
                {
                    "fichier": r,
                    "ecart": "contenu différent (à expliquer)",
                    "attendu": x.decode("utf8", "ignore")[:400],
                    "obtenu": y.decode("utf8", "ignore")[:400],
                }
            )
        else:
            try:
                ta, tb = x.decode("utf8"), y.decode("utf8")
            except UnicodeDecodeError:
                res[c]["ecarts"].append({"fichier": r, "nature": "binaire différent (non expliqué)"})
                continue
            nat = "dates, heures ou durées seulement" if normaliser(ta) == normaliser(tb) else "non expliqué"
            la, lb = ta.splitlines(), tb.splitlines()
            res[c]["ecarts"].append(
                {"fichier": r, "nature": nat, "exemples": [(u[:160], v[:160]) for u, v in zip(la, lb) if u != v][:5]}
            )

    def verdict(categorie):
        return (
            "PASS"
            if not (res[categorie]["ecarts"] or res[categorie]["absents"] or res[categorie]["en_trop"])
            else "FAIL"
        )

    res["verdicts"] = {
        "geometrie": verdict("geometrie"),
        "fabrication": verdict("fabrication"),
        "fabrication_identite_binaire": "FAIL"
        if (
            res["fabrication"]["ecarts"]
            or res["fabrication"]["absents"]
            or res["fabrication"]["en_trop"]
            or res["identite_binaire"]["differents_contenu_identique"]
        )
        else "PASS",
        "documentaire": "PASS"
        if not (
            res["documentaire"]["absents"]
            or res["documentaire"]["en_trop"]
            or any(
                e.get("nature") == "non expliqué" or "binaire" in e.get("nature", "")
                for e in res["documentaire"]["ecarts"]
            )
        )
        else "À ANALYSER",
    }
    with PublicationNeuve(list(initial), [sortie]) as publication:
        temp = publication.temporaire(sortie)
        temp.write_text(json.dumps(res, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        json.loads(temp.read_text(encoding="utf-8"))
        if any(h(p.read_bytes()) != empreinte for p, empreinte in initial.items()):
            raise ValueError("Une entrée a changé pendant la comparaison.")
        publication.publier()
    print(
        f"identité binaire des 3MF : {len(res['identite_binaire']['differents_contenu_identique'])} conteneur(s) "
        f"à contenu identique ; verdict fabrication {res['verdicts']['fabrication_identite_binaire']}"
    )
    for c in ("geometrie", "fabrication", "documentaire"):
        print(
            f"{c:13s} {res['verdicts'][c]:10s} identiques {res[c]['identiques']}, "
            f"écarts {len(res[c]['ecarts'])}, absents {len(res[c]['absents'])}, "
            f"en trop {len(res[c]['en_trop'])}"
        )

    return res


def main(argv=None, *, prive=None):
    """Compare deux dossiers privés ; codes0/PASS,1/écart,2/FAIL d'entrée, sans modifier les préparations."""
    parser = argparse.ArgumentParser(description="Compare une reconstruction sans tolérance de fabrication implicite")
    parser.add_argument("--attendu", type=Path, required=True)
    parser.add_argument("--obtenu", type=Path, required=True)
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        resultat = comparer(args.attendu, args.obtenu, args.sortie, prive=prive)
    except (OSError, ValueError, zipfile.BadZipFile) as erreur:
        # Une entrée non exploitable n'est ni un écart mesuré ni une comparaison
        # réussie ; conserver l'erreur exacte (docs/reference/controles.md).
        print(f"FAIL comparaison de fabrication : {type(erreur).__name__}: {erreur}", file=sys.stderr)
        return 2
    return 0 if all(v == "PASS" for v in resultat["verdicts"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
