"""Réparation EXPLICITE d'un maillage importé (chaîne C ; Claude, 27/10/2026).

Complète importer.py, qui audite sans corriger.

Usage : reparer.py SOURCE.stl|.obj|.glb SORTIE.stl --unites mm|cm|m
        [--rapport rapport.json] [--trous-max N] [--tolerance-volume 0.5]

Corrections sûres, appliquées et tracées dans le rapport (avant / après) :
1. Soudure des sommets confondus.
2. Retrait des faces dégénérées puis dupliquées.
3. Retrait des sommets orphelins.
4. Orientation cohérente des faces, normales vers l'extérieur.
5. Bouchage des bords simples triangulaires ou quadrilatères autorisés par N
   (défaut 30, 0 = jamais).

Jamais : suppression de corps flottants, remaillage, simplification, changement
d'échelle (hors --unites) ou écrasement de la source.

Validation : manifold3d (même moteur que les opérations booléennes) doit accepter
le résultat comme solide. Un écart de volume supérieur à --tolerance-volume %
(si le volume d'origine est mesurable) refuse le STL.

Code 0 : réparé et valide. Code 1 : écrit mais validation incomplète ou non
solide (rapport à lire). Code 2 : refusé (aucun STL publié ; rapport possible).
Un maillage fermé n'est pas pour autant imprimable : épaisseurs, surplombs et
échelle restent à contrôler (controles_piece.py, chaîne de projet).
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import trimesh

from benchab.imports3d.importer import PublicationNeuve, charger, empreinte, mesurer, valider_chemins, verifier_export


def composantes_bord(m):
    """Composantes d'arêtes présentes une fois ; un bord ramifié n'est pas un trou."""
    t = m.copy()
    t.merge_vertices()
    indices = trimesh.grouping.group_rows(t.edges_sorted, require_count=1)
    g = trimesh.graph.nx.from_edgelist(t.edges_sorted[indices])
    return [
        (set(c), g.subgraph(c).number_of_edges(), all(g.degree(v) == 2 for v in c))
        for c in trimesh.graph.nx.connected_components(g)
    ]


def bords_ouverts(m):
    """Nombre de composantes de bord et nombre d'arêtes de la plus grande."""
    composants = composantes_bord(m)
    return len(composants), max((n for _, n, _ in composants), default=0)


def remplir_compatibles(m, maximum):
    admissibles = [sommets for sommets, n, simple in composantes_bord(m) if simple and 3 <= n <= min(4, maximum)]
    if not admissibles:
        return
    candidat = m.copy()
    trimesh.repair.fill_holes(candidat)
    nouvelles = candidat.faces[len(m.faces) :]
    selection = [face for face in nouvelles if any(set(face) <= sommets for sommets in admissibles)]
    if selection:
        m.extend_faces(np.asarray(selection))


def valide_manifold(m):
    try:
        import manifold3d

        m = m.copy()
        m.merge_vertices()
        r = manifold3d.Manifold(
            manifold3d.Mesh(
                vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)
            )
        )
        return {
            "statut": str(r.status()).split(".")[-1],
            "solide": str(r.status()).endswith("NoError") and not r.is_empty(),
            "volume_mm3": float(r.volume()),
        }
    except ImportError:
        return {"statut": "manifold3d absent", "solide": None}
    except Exception as e:
        return {"statut": "erreur moteur", "solide": None, "erreur": str(e)}


def reparer(m, trous_max=30):
    if isinstance(trous_max, bool) or not isinstance(trous_max, int) or trous_max < 0:
        raise ValueError("trous-max doit être un entier positif ou nul.")
    m = m.copy()
    journal = []

    def etape(nom, f):
        av = (len(m.vertices), len(m.faces))
        f()
        ap = (len(m.vertices), len(m.faces))
        journal.append({"etape": nom, "sommets": [av[0], ap[0]], "faces": [av[1], ap[1]]})

    etape("soudure des sommets confondus", m.merge_vertices)
    etape("faces dégénérées retirées", lambda: m.update_faces(m.nondegenerate_faces()))
    etape("faces dupliquées retirées", lambda: m.update_faces(m.unique_faces()))
    etape("sommets orphelins retirés", m.remove_unreferenced_vertices)
    etape("orientation et normales", lambda: trimesh.repair.fix_normals(m, multibody=True))
    n, plus_grand = bords_ouverts(m)
    # fill_holes ne ferme que des triangles et quadrilatères. Le seuil est une
    # autorisation, pas une promesse de bouchage de polygones arbitraires.
    if n and any(simple and 3 <= nombre <= min(4, trous_max) for _, nombre, simple in composantes_bord(m)):
        etape(
            "tentative de bouchage des ouvertures compatibles (triangles/quadrilatères)",
            lambda: remplir_compatibles(m, trous_max),
        )
        restants, taille = bords_ouverts(m)
        journal[-1].update(
            ouvertures_avant=n,
            ouvertures_restantes=restants,
            ouvertures_fermees=max(0, n - restants),
            plus_grande_restante=taille,
        )
        if restants:
            journal.append(
                {
                    "etape": f"{restants} ouverture(s) NON bouchée(s) : limite de fill_holes ou bord non simple",
                    "sommets": None,
                    "faces": None,
                }
            )
    elif n:
        raison = (
            "bouchage désactivé"
            if not trous_max
            else (
                f"aucun bord simple compatible avec fill_holes et le seuil {trous_max} "
                f"(plus grand : {plus_grand} arêtes)"
            )
        )
        journal.append(
            {
                "etape": f"{n} trou(s) NON bouché(s) : {raison}",
                "sommets": None,
                "faces": None,
                "ouvertures_avant": n,
                "ouvertures_restantes": n,
                "ouvertures_fermees": 0,
            }
        )
    return m, journal


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("source")
    a.add_argument("sortie")
    a.add_argument("--unites", required=True, choices=["mm", "cm", "m"])
    a.add_argument("--rapport")
    a.add_argument("--trous-max", type=int, default=30)
    a.add_argument("--tolerance-volume", type=float, default=0.5)
    o = a.parse_args(argv)
    src, out = Path(o.source), Path(o.sortie)
    try:
        destinations = [out] + ([Path(o.rapport)] if o.rapport else [])
        valider_chemins([src], destinations)
        if out.suffix.lower() != ".stl":
            raise ValueError("La sortie doit être un STL en mm.")
        if o.trous_max < 0 or not math.isfinite(o.tolerance_volume) or o.tolerance_volume < 0:
            raise ValueError("trous-max et tolerance-volume doivent être positifs ou nuls et finis.")
        sha = empreinte(src)
        m0 = charger(src, o.unites)
        avant = mesurer(m0)
        m, journal = reparer(m0, o.trous_max)
        apres = mesurer(m)
        ecart = (
            None
            if not (avant["volume_mm3"] and apres["volume_mm3"])
            else 100 * abs(apres["volume_mm3"] - avant["volume_mm3"]) / abs(avant["volume_mm3"])
        )
        rapport = {
            "outil": "outils/imports-3d/reparer.py",
            "source": str(src),
            "source_sha256": sha,
            "unites": o.unites,
            "avant": avant,
            "corrections": journal,
            "apres": apres,
            "manifold3d": {"statut": "non effectué : STL non publié", "solide": None},
            "ecart_volume_pct": ecart,
            "limites": [
                "Fermé ≠ imprimable : épaisseurs, surplombs, échelle et assise restent à contrôler.",
                "Corps flottants conservés : à juger par l’utilisateur.",
                "Intersections entre composants et auto-intersections non démontrées absentes par ce contrôle.",
                "Les composantes de bord ne sont pas toujours des trous simples ; "
                "fill_holes traite seulement triangles et quadrilatères.",
                "Publication de chaque fichier atomique ; ensemble STL/rapport non atomique face à un arrêt brutal.",
            ],
        }
        refus = ecart is not None and ecart > o.tolerance_volume
        if refus:
            rapport["decision"] = f"REFUSÉ : volume modifié de {ecart:.2f} % > {o.tolerance_volume} %"
        with PublicationNeuve(
            [src], ([] if refus else [out]) + ([Path(o.rapport)] if o.rapport else [])
        ) as publication:
            if not refus:
                temp = publication.temporaire(out)
                temp.write_bytes(m.export(file_type="stl"))
                relu = charger(temp, "mm")
                verifier_export(m, relu)
                rapport["apres"] = mesurer(relu)
                rapport["manifold3d"] = valide_manifold(relu)
                rapport["sortie"] = str(out)
                rapport["sortie_sha256"] = empreinte(temp)
                valide = rapport["apres"]["ferme_topologiquement"] and rapport["manifold3d"].get("solide") is True
                rapport["decision"] = (
                    "RÉPARÉ ET VALIDE" if valide else "ÉCRIT, VALIDATION INCOMPLÈTE OU NON SOLIDE : lire le rapport"
                )
            if empreinte(src) != sha:
                raise ValueError("La source a changé pendant la réparation.")
            rapport["source_preservee"] = True
            texte = json.dumps(rapport, indent=1, ensure_ascii=False, allow_nan=False)
            if o.rapport:
                publication.temporaire(o.rapport).write_text(texte + "\n", encoding="utf-8")
                json.loads(publication.temporaire(o.rapport).read_text(encoding="utf-8"))
            if empreinte(src) != sha:
                raise ValueError("La source a changé avant publication.")
            publication.publier()
        print(texte)
        return 2 if refus else (0 if rapport["decision"] == "RÉPARÉ ET VALIDE" else 1)
    except (ValueError, OSError, TypeError, ImportError) as e:
        print("REFUSÉ : " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
