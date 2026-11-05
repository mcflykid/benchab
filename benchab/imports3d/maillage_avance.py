"""Transformations explicites sur une copie STL en mm, avec contrôle après export.

Alpha Wrap reconstruit une enveloppe ; simplifier réduit les triangles sans
autoriser le retrait de corps. Aucun traitement implicite, aucun GO impression.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import trimesh

from benchab.chemins import atelier_prive
from benchab.imports3d.geometrie import comparer, positif
from benchab.imports3d.importer import UNITES, PublicationNeuve, charger, empreinte, valider_chemins, verifier_export
from benchab.imports3d.reparer import composantes_bord
from benchab.processus import executer

NATIF = Path(sys.executable)
WORKER = "benchab.imports3d.moteur_maillage"


def moteur(commande, mesh=None, *, delai=180, prive=None, **parametres):
    """Cadre le moteur local et ses diagnostics privés ; unités natives conservées, aucune installation ni
    source modifiée."""
    if not NATIF.is_file():
        raise ValueError("Python courant indisponible.")
    positif(delai, "Délai")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    if commande == "diagnostic":
        # Diagnostic réellement en lecture seule : aucun dossier de capture créé.
        try:
            with tempfile.TemporaryDirectory(prefix="benchab-maillage-diagnostic-") as espace:
                for nom in ("home", "cache", "tmp"):
                    (Path(espace) / nom).mkdir()
                env.update(HOME=espace + "/home", XDG_CACHE_HOME=espace + "/cache", TMPDIR=espace + "/tmp")
                r = subprocess.run(
                    [str(NATIF), "-I", "-B", "-m", WORKER, "diagnostic"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=delai,
                )
        except subprocess.TimeoutExpired as e:
            raise ValueError("Délai du diagnostic natif dépassé.") from e
        if r.returncode:
            raise ValueError("Diagnostic natif refusé : " + r.stderr[-500:])
        return json.loads(r.stdout)
    with tempfile.TemporaryDirectory(prefix="benchab-maillage-natif-") as tmp:
        d = Path(tmp).resolve()
        for nom in ("home", "cache", "tmp"):
            (d / nom).mkdir()
        env.update(HOME=str(d / "home"), XDG_CACHE_HOME=str(d / "cache"), TMPDIR=str(d / "tmp"))
        args = [str(NATIF), "-I", "-B", "-m", WORKER, commande]
        if mesh is not None:
            np.savez(d / "entree.npz", vertices=mesh.vertices, faces=mesh.faces)
            args += [
                str(d / "entree.npz"),
                str(d / "sortie.npz"),
                "--parametres",
                json.dumps(parametres, allow_nan=False),
            ]
        res = executer(
            args,
            cwd=d,
            diagnostics=atelier_prive(prive) / "diagnostics/maillages",
            delai=delai,
            env=env,
        )
        if res.execution != "PASS" or res.returncode != 0:
            raise ValueError("Moteur interrompu ou refusé : " + (res.stderr[-500:] or res.raison))
        infos = json.loads(res.stdout)
        infos["secondes_processus"] = res.duree_secondes
        if mesh is None:
            return infos
        with np.load(d / "sortie.npz", allow_pickle=False) as z:
            v, f = z["vertices"], z["faces"]
            if v.ndim != 2 or v.shape[1] != 3 or f.ndim != 2 or f.shape[1] != 3 or not len(f):
                raise ValueError("Sortie native invalide.")
            if not np.isfinite(v).all() or f.min() < 0 or f.max() >= len(v) or not np.issubdtype(f.dtype, np.integer):
                raise ValueError("Sortie native non finie ou indices invalides.")
            resultat = trimesh.Trimesh(v, f, process=False)
            resultat.remove_unreferenced_vertices()
        return resultat, infos


def transformer(
    source,
    sortie,
    rapport,
    unites,
    commande,
    *,
    tolerance_mm,
    faces_cibles=None,
    detail_mm=None,
    offset_mm=None,
    echantillons=2000,
    delai=180,
    max_faces=2000000,
    fermer_ouvertures=False,
    prive=None,
):
    source, sortie, rapport = map(Path, (source, sortie, rapport))
    valider_chemins([source], [sortie, rapport])
    if sortie.suffix.lower() != ".stl" or rapport.suffix.lower() != ".json":
        raise ValueError("Une nouvelle sortie STL et un nouveau rapport JSON sont requis.")
    positif(tolerance_mm, "Tolérance en mm")
    positif(delai, "Délai")
    if type(echantillons) is not int or not 100 <= echantillons <= 50000:
        raise ValueError("Échantillons : entier de 100 à 50000.")
    if type(max_faces) is not int or not 1 <= max_faces <= 2000000:
        raise ValueError("Budget d’entrée : entier de 1 à 2000000 faces.")
    sha = empreinte(source)
    mesh = charger(source, unites)
    if len(mesh.faces) > max_faces:
        raise ValueError("Budget de faces dépassé : découper explicitement le modèle.")
    mesh.merge_vertices()
    if not mesh.nondegenerate_faces().all():
        raise ValueError("Faces dégénérées : exécuter d’abord la réparation simple explicite.")
    params = {}
    travail = mesh
    bouchage = {"demande": bool(fermer_ouvertures), "ouvertures": 0, "faces_ajoutees": 0}
    if commande == "alpha-wrap":
        positif(detail_mm, "Détail en mm")
        positif(offset_mm, "Offset en mm")
        if np.linalg.norm(mesh.extents) / detail_mm > 200:
            raise ValueError("Détail trop fin pour ce budget local : diagonale/détail > 200.")
        params.update(detail_mm=detail_mm, offset_mm=offset_mm)
        if fermer_ouvertures:
            # Opt-in : fermer les ouvertures voulues serait une modification fonctionnelle.
            groupes = composantes_bord(mesh)
            if any(not simple for _, _, simple in groupes):
                raise ValueError("Bord ramifié : bouchage automatique refusé, analyser la géométrie.")
            verts = mesh.vertices.tolist()
            faces = mesh.faces.tolist()
            bords = mesh.edges_sorted[trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)]
            groupes_par_sommet = {}
            for sommets, n, _ in groupes:
                if n > 10000:
                    raise ValueError("Ouverture de plus de 10000 arêtes : budget de bouchage dépassé.")
                index = len(verts)
                verts.append(mesh.vertices[sorted(sommets)].mean(axis=0).tolist())
                groupes_par_sommet.update({v: index for v in sommets})
            for a, b in bords:
                faces.append([int(a), int(b), groupes_par_sommet[a]])
            travail = trimesh.Trimesh(verts, faces, process=False)
            bouchage.update(ouvertures=len(groupes), faces_ajoutees=len(bords))
    elif commande == "simplifier":
        if fermer_ouvertures:
            raise ValueError("La simplification ne bouche aucune ouverture.")
        if type(faces_cibles) is not int or not 4 <= faces_cibles < len(mesh.faces):
            raise ValueError("Cible entière entre 4 et le nombre de faces source exclu.")
        params.update(faces_cibles=faces_cibles, tolerance_mm=tolerance_mm)
    else:
        raise ValueError("Transformation inconnue.")
    debut = time.perf_counter()
    # Aucun flag Prune/KeepLargest : les petits composants doivent rester.
    resultat, infos = moteur(commande, travail, delai=delai, prive=prive, **params)
    if len(resultat.faces) > 4000000:
        raise ValueError("Budget de sortie dépassé (4000000 faces).")
    with PublicationNeuve([source], [sortie, rapport]) as publication:
        temp = publication.temporaire(sortie)
        temp.write_bytes(resultat.export(file_type="stl"))
        relu = charger(temp, "mm")
        verifier_export(resultat, relu)
        debut_comparaison = time.perf_counter()
        comparaison = comparer(mesh, relu, tolerance_mm, echantillons, 0)
        secondes_comparaison = time.perf_counter() - debut_comparaison
        av, ap = comparaison["reference"], comparaison["candidat"]
        refus = []
        if av["corps_connectes"] != ap["corps_connectes"]:
            refus.append("Nombre de corps modifié : fusion ou perte de composant refusée.")
        if commande == "simplifier":
            refus += comparaison["raisons"]
            if len(relu.faces) >= len(mesh.faces):
                refus.append("Aucune réduction obtenue sous cette tolérance.")
        else:
            if not ap["solide_oriente_exterieur"] or ap["faces_degenerees"]:
                refus.append("Enveloppe reconstruite non solide ou dégénérée.")
            if (
                max(
                    comparaison[s]["maximum_observe_mm"] for s in ("reference_vers_candidat", "candidat_vers_reference")
                )
                > tolerance_mm
            ):
                refus.append("Écart de surface observé au-delà de la tolérance.")
            if (
                np.max(comparaison["delta_bornes_mm"]) > tolerance_mm
                or np.max(comparaison["delta_dimensions_mm"]) > tolerance_mm
            ):
                refus.append("Bornes ou dimensions au-delà de la tolérance.")
        statut = (
            "REFUSE" if refus else ("RECONSTRUIT_A_REVOIR" if commande == "alpha-wrap" else "CONFORME_ECHANTILLONS")
        )
        r = {
            "schema": 1,
            "commande": commande,
            "statut": statut,
            "raisons_refus": refus,
            "source": str(source.resolve()),
            "source_sha256": sha,
            "unites_entree": unites,
            "parametres": dict(
                params, tolerance_mm=tolerance_mm, echantillons=echantillons, delai=delai, max_faces=max_faces
            ),
            "moteur": infos,
            "avant": av,
            "apres": ap,
            "comparaison": comparaison,
            "bouchage_avant_wrap": bouchage,
            "risque_coque_autour_surface_ouverte": commande == "alpha-wrap"
            and not av["ferme_topologiquement"]
            and not fermer_ouvertures,
            "reduction_faces_pct": 100 * (1 - len(relu.faces) / len(mesh.faces)),
            "variation_aire_pct": 100 * (relu.area / mesh.area - 1),
            "secondes_comparaison": secondes_comparaison,
            "faces_cibles_atteintes": len(relu.faces) <= faces_cibles if faces_cibles else None,
            "secondes": time.perf_counter() - debut,
            "limites": [
                "Contrôle de distance échantillonné, pas une borne de Hausdorff.",
                "Alpha Wrap peut fermer des ouvertures voulues et modifier la topologie.",
                "Épaisseurs, interfaces, intersections entre corps et imprimabilité non validées.",
                "Textures et couleurs non conservées dans le STL.",
            ],
        }
        if not refus:
            r.update(sortie=str(sortie.resolve()), sortie_sha256=empreinte(temp))
        r["source_preservee"] = empreinte(source) == sha
        if not r["source_preservee"]:
            raise ValueError("Source modifiée pendant le traitement.")
        contenu = json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False)
        if refus:
            # Conserver le diagnostic de refus, sans publier un STL non conforme.
            with PublicationNeuve([source], [rapport]) as diagnostic:
                diagnostic.temporaire(rapport).write_text(contenu + "\n")
                if empreinte(source) != sha:
                    raise ValueError("Source modifiée avant publication du refus.")
                diagnostic.publier()
        else:
            publication.temporaire(rapport).write_text(contenu + "\n")
            if empreinte(source) != sha:
                raise ValueError("Source modifiée avant publication.")
            publication.publier()
    return r


def main(argv=None, *, prive=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("commande", choices=["diagnostic", "alpha-wrap", "simplifier"])
    p.add_argument("source", type=Path, nargs="?")
    p.add_argument("--sortie", type=Path)
    p.add_argument("--rapport", type=Path)
    p.add_argument("--unites", choices=UNITES)
    p.add_argument("--tolerance-mm", type=float)
    p.add_argument("--detail-mm", type=float)
    p.add_argument("--offset-mm", type=float)
    p.add_argument("--faces-cibles", type=int)
    p.add_argument(
        "--fermer-ouvertures",
        action="store_true",
        help=("Alpha Wrap seulement : fermer explicitement les boucles de bord avant reconstruction ; "
              "peut supprimer des ouvertures fonctionnelles."),
    )
    p.add_argument("--echantillons", type=int, default=2000)
    p.add_argument("--delai", type=float, default=180)
    p.add_argument("--max-faces", type=int, default=2000000)
    a = p.parse_args(argv)
    try:
        if a.commande == "diagnostic":
            print(json.dumps(moteur("diagnostic", prive=prive), ensure_ascii=False))
            return 0
        if any(x is None for x in (a.source, a.sortie, a.rapport, a.unites, a.tolerance_mm)):
            raise ValueError("Source, --sortie, --rapport, --unites et --tolerance-mm requis.")
        if a.commande == "alpha-wrap" and (a.detail_mm is None or a.offset_mm is None or a.faces_cibles is not None):
            raise ValueError("Alpha Wrap exige --detail-mm et --offset-mm ; pas de cible de faces.")
        if a.commande == "simplifier" and (
            a.faces_cibles is None or a.detail_mm is not None or a.offset_mm is not None or a.fermer_ouvertures
        ):
            raise ValueError("Simplifier exige --faces-cibles ; pas de paramètres Alpha Wrap.")
        r = transformer(
            a.source,
            a.sortie,
            a.rapport,
            a.unites,
            a.commande,
            tolerance_mm=a.tolerance_mm,
            faces_cibles=a.faces_cibles,
            detail_mm=a.detail_mm,
            offset_mm=a.offset_mm,
            echantillons=a.echantillons,
            delai=a.delai,
            max_faces=a.max_faces,
            fermer_ouvertures=a.fermer_ouvertures,
            prive=prive,
        )
        print(json.dumps({"statut": r["statut"], "rapport": str(a.rapport)}, ensure_ascii=False))
        return {"REFUSE": 2, "RECONSTRUIT_A_REVOIR": 1, "CONFORME_ECHANTILLONS": 0}[r["statut"]]
    except (ValueError, OSError, TypeError, ImportError) as e:
        p.exit(2, "ERREUR : " + str(e) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
