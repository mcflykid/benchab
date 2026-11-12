"""Contrôle la géométrie d’une pièce dans son orientation d’impression.

Le lot S du 22/10/2026 vérifie le fichier. La validation physique reste distincte.

usage : python controles_piece.py <stl_orienté> [...] [--epaisseur-min 0.84] [--surplomb-max 45] [--plateau
250x250x250]
        [--hauteur-couche 0.2] [--non-applicable controle=raison ...] [--json FICHIER]
Contrôles : maillage fermé et orienté (ce n'est PAS l'étanchéité physique d'une pièce imprimée), corps distincts,
volume, encombrement
dans le plateau, assise sur le plateau, surplombs au-delà de l'angle donné (hors plateau), épaisseur locale estimée
par lancer de
rayons vers l'intérieur (échantillonnage : un minimum peut échapper), couches courtes (outil couches_courtes.py).
Un contrôle déclaré « non applicable » doit porter sa raison ; il n'est jamais compté PASS. Statuts : PASS, WARN,
FAIL, NON APPLICABLE.
Maillage dense (plus de --faces-max 300000 triangles) : l'épaisseur locale est estimée sur une copie simplifiée
(manifold, tolérance
indiquée dans le détail) ; les autres contrôles restent sur le maillage complet. Constat du 23/10/2026 (support JBL
GO 4, 3,2 M
triangles) : sans embree, le lancer de rayons de trimesh a consommé 51 Go de mémoire.
"""

import argparse
import hashlib
import importlib.metadata
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import trimesh

from benchab import processus
from benchab.chemins import atelier_prive
from benchab.controles import cache_geometrie as O
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.qualite.statuts import code_statut

CC = Path(__file__).resolve().parent / "couches_courtes.py"
DELAI_COUCHES_S = 120


def contexte_cache(options):
    """Décrit les paramètres géométriques et outils du cache, sans créer de rapport.

    Entrée : options d’épaisseur et de couche en mm, angle de surplomb en degrés,
    plateau en mm et limites de faces. Sortie : paramètres déclarés et chemins
    d’outils empreintés. Aucun maillage, réglage ou projet n’est modifié.
    """
    outils = {Path(__file__).resolve(), CC.resolve(), Path(O.__file__).resolve(), Path(sys.executable).resolve()}
    versions = {}
    for nom in ("numpy", "trimesh", "shapely", "manifold3d", "rtree", "scipy", "networkx", "embreex"):
        try:
            distribution = importlib.metadata.distribution(nom)
        except importlib.metadata.PackageNotFoundError:
            versions[nom] = None
            continue
        versions[nom] = distribution.version
        if distribution.files is None:
            raise ValueError("code de dépendance non inventorié : " + nom)
        for fichier in distribution.files:
            if str(fichier).endswith((".py", ".so", ".dylib")):
                chemin = Path(distribution.locate_file(fichier)).resolve()
                if not chemin.is_file():
                    raise ValueError("dépendance absente : " + str(chemin))
                outils.add(chemin)
    parametres = {
        "epaisseur": options["epaisseur_min"],
        "surplomb": options["surplomb_max"],
        "couche": options["hauteur_couche"],
        "plateau": options["plateau"],
        "faces_max": options["faces_max"],
        "non_applicable": options["non_applicable"],
        "python": sys.version,
        "versions": versions,
        "format_cache": 1,
    }
    parametres["code_sha256"] = hashlib.sha256(
        json.dumps({str(chemin): O.sha(chemin) for chemin in sorted(outils)}, sort_keys=True).encode()
    ).hexdigest()
    return parametres, sorted(map(str, outils))


def rapport_reutilisable(rapport):
    """Indique si chaque contrôle peut être mémorisé sans rendre une incertitude PASS.

    Entrée : liste de contrôles numériques. Sortie : booléen ; seuls PASS, WARN et
    NON APPLICABLE avec détails exploitables sont admissibles. Aucune donnée n’est
    modifiée et aucune tenue physique n’est déduite.
    """
    return bool(rapport) and all(
        isinstance(controle, dict)
        and controle.get("statut") in ("PASS", "WARN", "NON APPLICABLE")
        and bool(controle.get("controle"))
        and isinstance(controle.get("detail"), str)
        and bool(controle["detail"].strip())
        and not any(
            motif in controle["detail"].lower()
            for motif in ("illisible", "aucun rayon exploitable", "raison manquante")
        )
        for controle in rapport
    )


def controler_cache(fichier, projet, parametres, outils, options=None):
    """Relit un cache géométrique vérifiable ou recalcule les contrôles de la pièce.

    Entrées : maillage, projet de cache, empreintes de contexte et options en mm.
    Sortie : contrôles numériques conservant PASS/WARN/FAIL/NON APPLICABLE.
    La source est hachée avant et après ; la réutilisation est journalisée dans le
    projet. Aucun réglage d’impression ni maillage source n’est modifié.
    """
    chemin = str(Path(fichier).resolve())
    cle = "geometrie:" + hashlib.sha256(chemin.encode()).hexdigest()
    empreinte_initiale = O.sha(chemin)
    try:
        entree = O.cache_lire(projet, cle, parametres=parametres, outils=outils)
        rapport = entree.get("conclusion", {}).get("rapport")
        if entree["statut"] == "VALIDE" and set(entree["entrees"]) == {chemin} and rapport_reutilisable(rapport):
            empreinte_rapport = hashlib.sha256(json.dumps(rapport, sort_keys=True).encode()).hexdigest()
            if empreinte_rapport == entree["conclusion"].get("rapport_sha256"):
                projet.journal(type="geometrie", resultat="réutilisé", cle=cle)
                return rapport
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    rapport = controler(fichier, **(options or {}))
    if O.sha(chemin) != empreinte_initiale:
        raise RuntimeError("entrée modifiée pendant le contrôle : " + chemin)
    if rapport_reutilisable(rapport):
        try:
            conclusion = {
                "rapport": rapport,
                "rapport_sha256": hashlib.sha256(json.dumps(rapport, sort_keys=True).encode()).hexdigest(),
            }
            O.cache_poser(
                projet,
                cle,
                "contrôle géométrique",
                [chemin],
                conclusion,
                "calculé",
                parametres=parametres,
                outils=outils,
            )
            projet.journal(type="geometrie", resultat="calculé", cle=cle)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return rapport


def pour_rayons(m, faces_max):
    """Prépare un maillage allégé pour le lancer de rayons.

    Renvoie le maillage et une note. La simplification augmente la tolérance en mm
    pour viser faces_max triangles. Le maillage d’entrée reste intact.
    """
    if len(m.faces) <= faces_max:
        return m, ""
    import manifold3d as mf

    M0 = mf.Manifold(
        mf.Mesh64(
            vert_properties=np.asarray(m.vertices, dtype=np.float64), tri_verts=np.asarray(m.faces, dtype=np.uint64)
        )
    )
    for tol in (0.005, 0.01, 0.02, 0.04, 0.08):
        o = M0.simplify(tol).to_mesh64()
        if len(o.tri_verts) <= faces_max:
            break
    q = trimesh.Trimesh(np.asarray(o.vert_properties)[:, :3], np.asarray(o.tri_verts), process=False)
    return q, f" ; sur copie simplifiée {len(m.faces)} → {len(q.faces)} triangles, tolérance {tol} mm"


def controler(
    f, *, epaisseur_min=0.84, surplomb_max=45.0, hauteur_couche=0.2, plateau=None, faces_max=300000, non_applicable=None
):
    """Contrôle un maillage en mm ; PASS/WARN/FAIL/NON VÉRIFIÉ, sans preuve physique ni réglage modifié."""
    from benchab.machine.configuration import charger as charger_configuration

    plateau = plateau or charger_configuration()["placement"]["plateau_xyz_mm"]
    raisons_non_applicables = non_applicable or {}
    m = trimesh.load_mesh(f)
    out = []

    def n(c, s, d):
        if c in raisons_non_applicables:
            s, d = "NON APPLICABLE", raisons_non_applicables[c] or "raison manquante"
        out.append({"controle": c, "statut": s, "detail": d})

    n(
        "maillage fermé",
        "PASS" if m.is_watertight and m.is_winding_consistent else "FAIL",
        f"fermé={m.is_watertight}, orientation cohérente={m.is_winding_consistent} "
        "(fichier ; l'étanchéité physique n'est pas évaluée)",
    )
    corps = m.split(only_watertight=False)
    n("corps distincts", "PASS" if len(corps) == 1 else "WARN", f"{len(corps)} corps")
    n("volume", "PASS" if m.volume > 0 else "FAIL", f"{m.volume:.1f} mm³")
    e = m.bounds[1] - m.bounds[0]
    n(
        "encombrement plateau",
        "PASS" if all(e[i] <= plateau[i] - (10 if i < 2 else 0) for i in range(3)) else "FAIL",
        f"{e[0]:.1f} × {e[1]:.1f} × {e[2]:.1f} mm (plateau {plateau}, marge 10 mm en XY)",
    )
    z0 = m.bounds[0][2]
    fn = m.face_normals
    c = m.triangles_center
    a = m.area_faces
    bas = (fn[:, 2] < -0.999) & (c[:, 2] < z0 + 1e-3)
    assise = a[bas].sum()
    n("assise sur le plateau", "PASS" if assise > 0 else "FAIL", f"{assise:.1f} mm² au contact du plateau")
    ang = np.degrees(np.arccos(np.clip(-fn[:, 2], -1, 1)))  # 0° = face tournée vers le bas
    sur = (~bas) & (fn[:, 2] < 0) & (ang < 90 - surplomb_max) & (c[:, 2] > z0 + hauteur_couche)
    n(
        "surplombs",
        "PASS" if a[sur].sum() < 1 else "WARN",
        f"{a[sur].sum():.1f} mm² inclinés à plus de {surplomb_max:.0f}° de la verticale (hors plateau)",
    )
    mr, note = pour_rayons(m, faces_max)
    pts, fi = (
        trimesh.sample.sample_surface_even(mr, 3000, seed=1)
        if hasattr(trimesh.sample, "sample_surface_even")
        else trimesh.sample.sample_surface(mr, 3000)
    )
    dirs = -mr.face_normals[fi]
    org = pts + dirs * 1e-3
    loc, ir, _ = mr.ray.intersects_location(org, dirs, multiple_hits=False)
    d = np.linalg.norm(loc - org[ir], axis=1) if len(ir) else np.array([])
    d = d[np.isfinite(d) & (d > 0.05)]  # Touches voisines/arêtes et résultats non finis non exploitables.
    if len(d):
        p1 = float(np.percentile(d, 1))
        mn = float(d.min())
        n(
            "épaisseur locale (échantillonnée)",
            "PASS" if p1 >= epaisseur_min else "WARN",
            f"minimum {mn:.2f} mm, 1er centile {p1:.2f} mm (seuil {epaisseur_min} ; {len(d)} rayons, estimation ; "
            f"crêtes de filets et arêtes vives abaissent ces valeurs{note})",
        )
    else:
        n("épaisseur locale (échantillonnée)", "WARN", "aucun rayon exploitable")
    if "couches courtes" in raisons_non_applicables:
        n("couches courtes", "", "")
    else:
        with tempfile.TemporaryDirectory(prefix="benchab-couches-") as tampon:
            r = processus.executer(
                [sys.executable, str(CC), str(f), str(hauteur_couche)],
                cwd=Path.cwd(), diagnostics=tampon, delai=DELAI_COUCHES_S, conserver_capture=True,
            )
            if r.resultat == "NON_VERIFIE":
                n("couches courtes", "NON VÉRIFIÉ", f"Analyse incomplète ({r.raison}, délai {DELAI_COUCHES_S} s)")
                return out
            try:
                contenu = Path(r.diagnostic).with_name("stdout.log").read_text(encoding="utf-8")
                il = json.loads(contenu).get("ilots_isoles", [])
            except ValueError:
                il = None
        if il is None:
            n("couches courtes", "WARN", "sortie illisible : " + (r.stderr or r.stdout)[-160:])
        else:
            n(
                "couches courtes",
                "PASS" if not il else "WARN",
                f"{len(il)} îlot(s) isolé(s) en couche courte"
                + (
                    f" (de Z {il[0]['de_z']} à {il[0]['a_z']} mm…) : "
                    "temps de couche à vérifier dans le G-code (valeur calculée, pas mesurée)"
                    if il
                    else ""
                ),
            )
    return out


def _plateau(value):
    try:
        valeurs = [float(part) for part in value.split("x")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("format attendu : XxYxZ") from error
    if len(valeurs) != 3 or not all(np.isfinite(valeurs)) or any(v <= 0 for v in valeurs):
        raise argparse.ArgumentTypeError("trois dimensions finies et positives requises")
    return valeurs


def main(argv=None):
    """Contrôle les maillages en mm avec cache privé ; statuts numériques conservés, aucune tenue physique présumée."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fichiers", nargs="+", type=Path)
    parser.add_argument("--epaisseur-min", type=float, default=0.84)
    parser.add_argument("--surplomb-max", type=float, default=45.0)
    parser.add_argument("--hauteur-couche", type=float, default=0.2)
    parser.add_argument("--plateau", type=_plateau)
    parser.add_argument("--non-applicable", nargs="*", default=[])
    parser.add_argument("--faces-max", type=int, default=300000)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--sans-cache", action="store_true")
    args = parser.parse_args(argv)
    sortie = None
    if args.json is not None:
        try:
            # Refuser avant contrôle et cache pour protéger les maillages (#152, chemins-et-atelier.md).
            sortie = destination_neuve(args.json)
        except (OSError, ValueError) as erreur:
            print(f"Sortie JSON refusée : {erreur}", file=sys.stderr)
            return 2
    from benchab.machine.configuration import charger as charger_configuration

    options = {
        "epaisseur_min": args.epaisseur_min,
        "surplomb_max": args.surplomb_max,
        "hauteur_couche": args.hauteur_couche,
        "plateau": args.plateau or charger_configuration()["placement"]["plateau_xyz_mm"],
        "faces_max": args.faces_max,
        "non_applicable": {},
    }
    for valeur in args.non_applicable:
        nom, sep, raison = valeur.partition("=")
        if not sep or not nom:
            print("Format --non-applicable attendu : controle=raison", file=sys.stderr)
            return 2
        options["non_applicable"][nom] = raison
    if any(not np.isfinite(options[k]) or options[k] <= 0 for k in ("epaisseur_min", "hauteur_couche")):
        print("Épaisseur et hauteur de couche doivent être finies et positives", file=sys.stderr)
        return 2
    if not np.isfinite(options["surplomb_max"]) or not 0 < options["surplomb_max"] < 90 or args.faces_max <= 0:
        print("Surplomb et nombre de faces hors limites", file=sys.stderr)
        return 2
    cache = None
    parametres = outils = None
    if not args.sans_cache:
        try:
            parametres, outils = contexte_cache(options)
            prive = atelier_prive()
            repertoire = hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()[:20]
            cache = O.Projet(prive / "cache" / "geometrie" / repertoire)
        except (OSError, ValueError, TypeError):
            cache = None
    resultats = {
        path.name: (
            controler_cache(path, cache, parametres, outils, options)
            if cache is not None
            else controler(path, **options)
        )
        for path in args.fichiers
    }
    if sortie is not None:
        try:
            ecrire_json_neuf(
                sortie,
                {
                    "fichiers": resultats,
                    "parametres": {
                        "epaisseur_min": options["epaisseur_min"],
                        "surplomb_max": options["surplomb_max"],
                        "plateau": options["plateau"],
                        "non_applicable": options["non_applicable"],
                    },
                },
                entrees=args.fichiers,
            )
        except (OSError, ValueError) as erreur:
            print(f"Sortie JSON refusée : {erreur}", file=sys.stderr)
            return 2
    pire = 0
    for fichier, controles in resultats.items():
        print("#", fichier)
        for resultat in controles:
            print(f"  {resultat['statut']:15s} {resultat['controle']} — {resultat['detail']}")
            if resultat["statut"] != "NON APPLICABLE":
                pire = max(pire, code_statut(resultat["statut"]))
    return pire


def cli(argv=None):
    """Exécute le contrôle CLI de pièces ; retourne le code agrégé ou 2 sur erreur.

    Entrée : arguments du programme, dont cotes en mm et angle en degrés. Sortie :
    code numérique de statut, sans commande machine ni validation physique.
    """
    try:
        return main(argv)
    except Exception as exc:
        print("ERREUR de contrôle : " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
