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
Un .gcode isolé est comparé par commandes puis par commentaires.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.qualite.ecritures import PublicationNeuve, sortie_privee, valider_chemins
from benchab.qualite.empreintes import sha256_fichier

GEO = {".stl", ".step", ".stp", ".glb", ".obj"}
GEO_NOMS = {"assemblage.json", "cao-nominale.json"}
FAB_NOMS = {"placement.json", "plaques-noms.json"}
MAX_DETAILS_MEMBRE_OCTETS = 64 * 1024 * 1024
DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?"
    r"|\d{2}/\d{2}/\d{4}( \d{2}:\d{2})?"
    r'|"(date|heure|debut|fin|tranchage_secondes|duree_s|secondes)": ("[^"]*"|[\d.]+)'
)  # pas de nombre générique


def h(b):
    """Calcule le SHA-256 hexadécimal des octets comparés."""
    return hashlib.sha256(b).hexdigest()


def categorie(rel):
    """Classe un chemin en géométrie, fabrication ou documentation d’après son nom et son suffixe."""
    p = Path(rel)
    if p.suffix.lower() in GEO or p.name in GEO_NOMS:
        return "geometrie"
    if p.suffix.lower() in (".3mf", ".gcode") or p.name in FAB_NOMS:
        return "fabrication"
    return "documentaire"


def commandes(b):
    """Extrait les lignes de G-code non vides et non commentées sans interpréter leurs commandes."""
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


def _empreinte_membre(archive, nom):
    """Calcule le SHA d’un membre lu en flux jusqu’au CRC, sans le charger en mémoire."""
    with archive.ouvrir_flux(nom) as flux:
        return hashlib.file_digest(flux, "sha256").hexdigest()


def _verifier_archive(path):
    """Relit une archive non comparée par paire : index, CRC et source stable."""
    with Archive3MF(path) as archive:
        if not archive.noms():
            raise ValueError("3MF vide : " + str(path))
        archive.verifier()
        archive.verifier_source(path)


def comparer_3mf(a, b):
    """Compare deux 3MF : écarts de fabrication/documentaires, budgets en octets et CRC complets.

    Au-delà du budget de détail, des empreintes différentes restent un écart
    même si le contenu n’est pas interprété. Aucune archive ni aucun réglage
    n’est modifié ; la comparaison n’est pas une validation d’impression.
    """
    with Archive3MF(a) as za, Archive3MF(b) as zb:
        na, nb = set(za.noms()), set(zb.noms())
        if not na or not nb:
            raise ValueError("3MF vide : aucune comparaison de fabrication.")
        # Les membres présents d’un seul côté restent soumis au CRC.
        za.verifier(membres=sorted(na - nb))
        zb.verifier(membres=sorted(nb - na))
        ecarts = [{"membre": m, "ecart": "absent de la reconstruction"} for m in sorted(na - nb)] + [
            {"membre": m, "ecart": "en trop dans la reconstruction"} for m in sorted(nb - na)
        ]
        doc = []
        for m in sorted(na & nb):
            tailles = [za.taille_membre(m), zb.taille_membre(m)]
            if max(tailles) > MAX_DETAILS_MEMBRE_OCTETS:
                sha_a, sha_b = _empreinte_membre(za, m), _empreinte_membre(zb, m)
                if sha_a == sha_b:
                    continue
                if m.lower().endswith(".png"):
                    doc.append({"membre": m, "ecart": "vignette différente"})
                else:
                    ecarts.append({
                        "membre": m, "ecart": "contenu différent (à expliquer)", "detail": "NON VÉRIFIÉ",
                        "tailles": tailles, "sha256_attendu": sha_a, "sha256_obtenu": sha_b,
                    })
                continue
            x, y = za.lire(m, limite_octets=MAX_DETAILS_MEMBRE_OCTETS), zb.lire(
                m, limite_octets=MAX_DETAILS_MEMBRE_OCTETS
            )
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
        za.verifier_source(a)
        zb.verifier_source(b)
    return ecarts, doc


def normaliser(t):
    """Masque les dates reconnues dans un texte de comparaison, sans modifier le fichier."""
    return DATE.sub("<N>", t)


def comparer(attendu, obtenu, sortie, *, prive=None):
    """Compare deux dossiers et publie un rapport privé neuf sans modifier les entrées.

    Les verdicts PASS/FAIL ou À ANALYSER distinguent géométrie, fabrication,
    identité binaire et documentation. Les détails d’archives sont bornés en
    octets ; aucune différence n’autorise de validation physique.
    """
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
    initial = {p: sha256_fichier(p) for p in [*fa.values(), *fo.values()]}
    communs = set(fa) & set(fo)
    paires_3mf = {
        p for r in communs if fa[r].suffix.lower() == ".3mf" and initial[fa[r]] != initial[fo[r]]
        for p in (fa[r], fo[r])
    }
    for p in initial:
        if p.suffix.lower() == ".3mf" and p not in paires_3mf:
            try:
                _verifier_archive(p)
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
    for r in sorted(communs):
        c = categorie(r)
        suffixe = fa[r].suffix.lower()
        sha_a, sha_b = initial[fa[r]], initial[fo[r]]
        if sha_a == sha_b:
            res[c]["identiques"] += 1
            continue
        if c != "geometrie" and suffixe != ".3mf":
            x, y = fa[r].read_bytes(), fo[r].read_bytes()
        if c == "geometrie":
            res[c]["ecarts"].append({"fichier": r, "attendu": sha_a, "obtenu": sha_b})
        elif suffixe == ".3mf":
            try:
                e, d = comparer_3mf(fa[r], fo[r])
            except zipfile.BadZipFile as exc:
                raise ValueError("3MF vide ou corrompu : " + r) from exc
            if e:
                res["fabrication"]["ecarts"].append({"fichier": r, "membres": e})
            else:
                res["fabrication"]["identiques"] += 1
                res["identite_binaire"]["differents_contenu_identique"].append(
                    {
                        "fichier": r,
                        "attendu": sha_a,
                        "obtenu": sha_b,
                        "nature": "membres identiques, conteneur ZIP différent",
                    }
                )
            if d:
                res["documentaire"]["ecarts"].append({"fichier": r, "membres": d, "nature": "vignettes du 3MF"})
        elif suffixe == ".gcode":
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
        if any(sha256_fichier(p) != empreinte for p, empreinte in initial.items()):
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
