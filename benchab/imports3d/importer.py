"""Audit et copie traçable de maillages externes. Aucune correction implicite."""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import trimesh

from benchab.imports3d.lecture_3mf import lire as lire_3mf
from benchab.imports3d.provenance import CHAMPS
from benchab.imports3d.provenance import controler as controler_provenance
from benchab.qualite.ecritures import PublicationNeuve, valider_chemins
from benchab.qualite.empreintes import sha256_fichier as empreinte

UNITES = {"mm": 1.0, "cm": 10.0, "m": 1000.0}
LIMITES = [
    "Aucune mesure d’épaisseur de paroi ni de résistance.",
    "La fermeture topologique ne prouve pas l’étanchéité physique.",
    "Les intersections et détails sous résolution ne sont pas contrôlés.",
    "Aucun composant flottant n’est supprimé automatiquement.",
]


class _SansRessources(dict):
    def __getitem__(self, key):
        # KeyError est absorbée par le lecteur GLB comme une absence d'URI,
        # puis remplacée silencieusement par le buffer BIN. Refuser explicitement.
        raise ValueError("Ressource externe requise : import de géométrie autonome uniquement.")


def charger_objet(source):
    """Géométrie du seul fichier fourni, sans lecture d'annexes implicites."""
    source = Path(source)
    if source.suffix.lower() not in (".stl", ".obj", ".glb"):
        raise ValueError("Format accepté : STL, OBJ ou GLB.")
    try:
        return trimesh.load(source, process=False, skip_materials=True, resolver=_SansRessources(), allow_remote=False)
    except (KeyError, IndexError, NotImplementedError) as exc:
        raise ValueError("Géométrie illisible, format non pris en charge ou ressource externe requise.") from exc


def charger(source, unites):
    """Charge un maillage depuis la source et convertit ses coordonnées des unités déclarées vers les mm."""
    source = Path(source)
    if unites not in UNITES:
        raise ValueError("Unités explicites mm/cm/m requises.")
    if source.suffix.lower() == ".glb" and unites != "m":
        raise ValueError("GLB utilise le mètre : --unites m requis ; aucune réinterprétation implicite.")
    # process=False conserve les défauts pour l’audit. Les transformations de chaque
    # instance de scène sont appliquées avant concaténation, même si elles partagent un mesh.
    obj = charger_objet(source)
    if isinstance(obj, trimesh.Scene):
        parties = []
        for node in obj.graph.nodes_geometry:
            transform, geometry = obj.graph[node]
            mesh = obj.geometry[geometry]
            if not isinstance(mesh, trimesh.Trimesh):
                continue
            mesh = mesh.copy()
            mesh.apply_transform(transform)
            parties.append(mesh)
        if not parties:
            raise ValueError("La scène ne contient aucun maillage triangulé.")
        obj = trimesh.util.concatenate(parties)
    if not isinstance(obj, trimesh.Trimesh) or len(obj.vertices) == 0 or len(obj.faces) == 0:
        raise ValueError("Maillage vide ou non triangulé.")
    obj.apply_scale(UNITES[unites])
    if not np.isfinite(obj.vertices).all():
        raise ValueError("Coordonnées non finies.")
    return obj


def mesurer(mesh):
    """Mesure les dimensions en mm, les volumes en mm³ et les compteurs topologiques sans modifier le maillage."""
    # STL stocke souvent trois sommets par triangle. La copie soudée reste une
    # lecture secondaire : la topologie indexée de la source reste explicite.
    source = mesh.copy()
    topo = mesh.copy()
    topo.merge_vertices()
    faces_valides = mesh.nondegenerate_faces()
    corps_source = source.split(only_watertight=False)
    corps_soudes = topo.split(only_watertight=False)
    signatures = {}
    for index, corps in enumerate(corps_source):
        signature = _signature_corps(corps)
        signatures.setdefault(signature, []).append(index)
    superposes = [indices for indices in signatures.values() if len(indices) > 1]
    topologie_source = _mesures_topologie(source, corps_source)
    topologie_soudee = _mesures_topologie(topo, corps_soudes)
    return {
        "sommets": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "faces_degenerees": int((~faces_valides).sum()),
        "bornes_mm": mesh.bounds.tolist(),
        "dimensions_mm": mesh.extents.tolist(),
        "corps_connectes": int(len(corps_soudes)),
        "ferme_topologiquement": bool(topo.is_watertight),
        "orientation_coherente": bool(topo.is_winding_consistent),
        "volume_mm3": float(topo.volume) if topo.is_volume else None,
        "topologie_source_indexee": topologie_source,
        "topologie_apres_soudure": topologie_soudee,
        "corps_exactement_superposes": superposes,
    }


def _mesures_topologie(mesh, corps=None):
    """Mesure une topologie sans modifier son maillage ; dimensions en mm, volumes en mm³."""
    if corps is None:
        corps = mesh.split(only_watertight=False)
    return {
        "sommets": int(len(mesh.vertices)),
        "corps_connectes": int(len(corps)),
        "ferme_topologiquement": bool(mesh.is_watertight),
        "orientation_coherente": bool(mesh.is_winding_consistent),
        "volume_mm3": float(mesh.volume) if mesh.is_volume else None,
    }


def _signature_corps(mesh):
    """Produit une signature exacte des triangles géométriques d’un corps indexé."""
    sommets = [tuple(float(valeur) for valeur in sommet) for sommet in mesh.vertices]
    triangles = [tuple(sorted(sommets[indice] for indice in face)) for face in mesh.faces]
    return tuple(sorted(triangles))


def alertes(mesure):
    """Retourne les messages d’alerte applicables aux mesures de géométrie et de topologie."""
    problemes = []
    sources = mesure.get("topologie_source_indexee")
    superposes = mesure.get("corps_exactement_superposes", [])
    if not mesure["ferme_topologiquement"]:
        if sources and sources["ferme_topologiquement"] and superposes:
            problemes.append(
                "Topologie après soudure non manifold, contrairement à la topologie source indexée ; "
                "des corps exactement superposés sont présents."
            )
        elif sources and sources["ferme_topologiquement"]:
            problemes.append(
                "Maillage ouvert ou non manifold après soudure ; la topologie source indexée est fermée."
            )
        else:
            problemes.append(
                "Maillage ouvert ou non manifold après soudure, également présent dans la topologie source indexée."
            )
    if not mesure["orientation_coherente"]:
        problemes.append("Orientation des faces incohérente.")
    if mesure["faces_degenerees"]:
        problemes.append("Faces dégénérées présentes.")
    if mesure["corps_connectes"] > 1:
        problemes.append("Plusieurs composants : vérifier leur rôle avant fabrication.")
    return problemes


def auditer(source, unites, provenance="", *, fiche_provenance=None):
    """Audite une source STL/OBJ/GLB/3MF et retourne maillage en mm et rapport, sans écrire.

    La géométrie renvoie PASS/WARN ; la fiche déclarée a son statut distinct
    RENSEIGNÉE/NON VÉRIFIÉ. Une empreinte déclarée divergente interrompt l'audit.
    """
    valider_chemins([source], [])
    sha = empreinte(source)
    attribution = controler_provenance(fiche_provenance, sha)
    est_3mf = Path(source).suffix.lower() == ".3mf"
    if est_3mf and unites != "mm":
        raise ValueError("Le 3MF déclare ses unités ; --unites mm demande les mesures normalisées.")
    # Le lecteur 3MF garde les unités et rôles d'origine ; l'audit n'étudie que les volumes imprimables.
    scene = lire_3mf(source) if est_3mf else None
    mesh = scene.to_mesh() if scene is not None else charger(source, unites)
    mesure = mesurer(mesh)
    if empreinte(source) != sha:
        raise ValueError("La source a changé pendant l’audit.")
    problemes = alertes(mesure)
    details_3mf = {}
    if scene is not None:
        ignores = scene.metadata["modificateurs_non_geometriques"]
        details_3mf["modificateurs_non_geometriques"] = ignores
        if ignores:
            problemes.append("Modificateurs 3MF exclus du maillage ; leur effet reste NON VÉRIFIÉ.")
    return mesh, {
        **details_3mf,
        "schema": 1,
        "source": Path(source).name,
        "source_sha256": sha,
        "unites_entree": scene.metadata["unites_fichier"] if scene is not None else unites,
        "provenance": provenance,
        "provenance_modele": attribution,
        "avant": mesure,
        "statut": "WARN" if problemes else "PASS",
        "alertes": problemes,
        "statut_avant": "WARN" if problemes else "PASS",
        "alertes_avant": problemes,
        "materiaux": (
            "Non pris en compte : audit de géométrie uniquement ; MTL/textures non contrôlés et non inclus dans les "
            "empreintes."
        ),
        "portee": "Audit numérique partiel du maillage importé ; pas un GO impression.",
        "limites": LIMITES,
    }


def preparer(
    source, sortie, unites, provenance="", hauteur_mm=None, nettoyer=False, rapport_sortie=None,
    *, fiche_provenance=None,
):
    """Crée une copie STL en mm et renvoie son rapport PASS/WARN sans modifier la source.

    Les transformations explicites gardent l'attribution liée au SHA d'origine.
    Une fiche incohérente ou une sortie existante interrompt la préparation.
    """
    source, sortie = Path(source), Path(sortie)
    destinations = [sortie] + ([Path(rapport_sortie)] if rapport_sortie else [])
    valider_chemins([source], destinations)
    if source.suffix.lower() == ".3mf":
        raise ValueError("Le 3MF est accepté en audit ; utiliser le parcours dédié pour conserver les rôles.")
    if sortie.suffix.lower() != ".stl":
        raise ValueError("La copie préparée doit être un STL (coordonnées en mm).")
    mesh, rapport = auditer(source, unites, provenance, fiche_provenance=fiche_provenance)
    operations = []
    if hauteur_mm is not None:
        if not math.isfinite(hauteur_mm) or hauteur_mm <= 0 or mesh.extents[2] <= 0:
            raise ValueError("Hauteur cible et hauteur Z initiale doivent être positives et finies.")
        ratio = hauteur_mm / mesh.extents[2]
        mesh.apply_scale(ratio)
        operations.append({"operation": "echelle_uniforme", "facteur": float(ratio), "hauteur_z_cible_mm": hauteur_mm})
    if nettoyer:
        count = int((~mesh.nondegenerate_faces()).sum())
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
        if len(mesh.faces) == 0:
            raise ValueError("Le nettoyage ne laisse aucune face.")
        operations.append({"operation": "retirer_faces_degenerees_et_sommets_non_references", "faces_retirees": count})
    with PublicationNeuve([source], destinations) as publication:
        temp = publication.temporaire(sortie)
        temp.write_bytes(mesh.export(file_type="stl"))
        rapport["sortie"] = sortie.name
        rapport["sortie_sha256"] = empreinte(temp)
        rapport["operations"] = operations
        relu = charger(temp, "mm")
        verifier_export(mesh, relu)
        rapport["apres"] = mesurer(relu)
        rapport["source_preservee"] = empreinte(source) == rapport["source_sha256"]
        if not rapport["source_preservee"]:
            raise ValueError("La source a changé pendant la préparation.")
        rapport["alertes_apres"] = alertes(rapport["apres"])
        rapport["statut_apres"] = "WARN" if rapport["alertes_apres"] else "PASS"
        rapport["statut"] = rapport["statut_apres"]
        rapport["alertes"] = rapport["alertes_apres"]
        if rapport_sortie:
            publication.temporaire(rapport_sortie).write_text(
                json.dumps(rapport, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
            )
        if rapport_sortie:
            json.loads(publication.temporaire(rapport_sortie).read_text(encoding="utf-8"))
        if empreinte(source) != rapport["source_sha256"]:
            raise ValueError("La source a changé avant publication.")
        publication.publier()
    return rapport


def verifier_export(attendu, relu):
    """Refuse un export relu dont la géométrie en mm ou la topologie divergent des mesures attendues."""
    if not np.isfinite(relu.vertices).all() or not np.allclose(relu.bounds, attendu.bounds, atol=1e-4, rtol=1e-6):
        raise ValueError("Bornes exportées divergentes.")
    av, ap = mesurer(attendu), mesurer(relu)
    for cle in ("faces", "faces_degenerees", "corps_connectes", "ferme_topologiquement", "orientation_coherente"):
        if av[cle] != ap[cle]:
            raise ValueError("Topologie exportée divergente : " + cle)
    ta, tr = attendu.copy(), relu.copy()
    ta.merge_vertices()
    tr.merge_vertices()
    if ta.is_volume != tr.is_volume or ta.euler_number != tr.euler_number:
        raise ValueError("Orientation ou topologie exportée divergente.")


def main(argv=None):
    """Exécute audit ou préparation en mm ; codes 0 PASS, 1 WARN, 2 erreur."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("commande", choices=["audit", "preparer"])
    p.add_argument("source", type=Path)
    p.add_argument("--unites", choices=UNITES, required=True)
    p.add_argument("--provenance", default="", help="note libre historique, sans preuve d'attribution")
    for champ in CHAMPS:
        p.add_argument(f"--provenance-{champ}", help=f"fiche d'attribution déclarée : {champ}")
    p.add_argument("--sortie", type=Path)
    p.add_argument("--hauteur-mm", type=float)
    p.add_argument("--nettoyer", action="store_true")
    p.add_argument("--rapport", type=Path, required=True)
    a = p.parse_args(argv)
    fiche = {champ: getattr(a, f"provenance_{champ}") for champ in CHAMPS}
    try:
        valider_chemins([a.source], [a.rapport] + ([a.sortie] if a.sortie else []))
        if a.commande == "audit":
            if a.sortie or a.hauteur_mm is not None or a.nettoyer:
                raise ValueError("Options de transformation réservées à preparer.")
            _, rapport = auditer(a.source, a.unites, a.provenance, fiche_provenance=fiche)
        else:
            if not a.sortie:
                raise ValueError("--sortie est requis pour preparer.")
            rapport = preparer(
                a.source, a.sortie, a.unites, a.provenance, a.hauteur_mm, a.nettoyer, a.rapport,
                fiche_provenance=fiche,
            )
        if a.commande == "audit":
            with PublicationNeuve([a.source], [a.rapport]) as publication:
                publication.temporaire(a.rapport).write_text(
                    json.dumps(rapport, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
                )
                json.loads(publication.temporaire(a.rapport).read_text(encoding="utf-8"))
                if empreinte(a.source) != rapport["source_sha256"]:
                    raise ValueError("La source a changé avant publication.")
                publication.publier()
        print(json.dumps({"statut": rapport["statut"], "rapport": str(a.rapport)}, ensure_ascii=False))
        return {"PASS": 0, "WARN": 1, "FAIL": 2}[rapport["statut"]]
    except (ValueError, OSError, TypeError) as e:
        p.exit(2, f"ERREUR : {e}\n")


if __name__ == "__main__":
    raise SystemExit(main())
