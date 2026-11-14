"""Worker natif isolé : CGAL Alpha Wrap et meshoptimizer, sans réseau."""

import argparse
import importlib.metadata
import json
import sys

import numpy as np

PINS = {"pymeshlab": "2025.7.post1", "meshoptimizer": "0.2.30a0", "numpy": "2.5.4"}


def diagnostic():
    """Retourne les capacités détectées des moteurs de reconstruction et de simplification."""
    import meshoptimizer
    import pymeshlab

    versions = {n: importlib.metadata.version(n) for n in PINS}
    if versions != PINS:
        raise ValueError("Versions du moteur différentes du verrou déclaré.")
    return {
        "versions": versions,
        "alpha_wrap": "generate_alpha_wrap" in pymeshlab.filter_list(),
        "simplification": callable(meshoptimizer.simplify),
        "python": sys.version.split()[0],
    }


def calculer(v, f, commande, parametres):
    """Traite les sommets en mm et faces triangulées. Retourne les tableaux résultants et les mesures du moteur."""
    if v.ndim != 2 or v.shape[1] != 3 or f.ndim != 2 or f.shape[1] != 3 or not len(f):
        raise ValueError("Tableaux triangulés non vides requis.")
    if not np.isfinite(v).all() or f.min() < 0 or f.max() >= len(v):
        raise ValueError("Coordonnées ou indices invalides.")
    if commande == "alpha-wrap":
        import pymeshlab as ml

        ensemble = ml.MeshSet()
        ensemble.add_mesh(
            ml.Mesh(vertex_matrix=np.asarray(v, dtype=np.float64), face_matrix=np.asarray(f, dtype=np.int32))
        )
        ensemble.generate_alpha_wrap(
            alpha=ml.PureValue(parametres["detail_mm"]), offset=ml.PureValue(parametres["offset_mm"])
        )
        sortie = ensemble.current_mesh()
        vertices = np.asarray(sortie.vertex_matrix(), dtype=np.float64)
        faces = np.asarray(sortie.face_matrix(), dtype=np.int64)
        import trimesh

        controle = trimesh.Trimesh(vertices, faces, process=False)
        if not controle.is_volume:
            raise ValueError("Alpha Wrap n’a pas fourni une enveloppe fermée valide.")
        return (
            vertices,
            faces,
            {"interface_alpha_wrap": "PyMeshLab/CGAL", "unites_parametres": "mm absolus (PureValue)"},
        )
    if commande != "simplifier":
        raise ValueError("Moteur inconnu.")
    import meshoptimizer as mo

    indices = np.ascontiguousarray(f, dtype=np.uint32).ravel()
    # Recentrer évite une perte de précision float32 loin de l'origine.
    centre = v.mean(axis=0)
    positions = np.ascontiguousarray(v - centre, dtype=np.float32)
    destination = np.empty_like(indices)
    erreur = np.zeros(1, dtype=np.float32)
    n = mo.simplify(
        destination,
        indices,
        positions,
        target_index_count=3 * parametres["faces_cibles"],
        target_error=parametres["tolerance_mm"],
        options=mo.SIMPLIFY_ERROR_ABSOLUTE | mo.SIMPLIFY_LOCK_BORDER,
        result_error=erreur,
    )
    if n < 3 or n > len(indices) or n % 3:
        raise ValueError("Résultat de simplification invalide.")
    # Les indices renvoient aux coordonnées float64 d'origine, pas à leur approximation.
    return v, destination[:n].reshape(-1, 3), {"erreur_moteur_mm": float(erreur[0])}


def main():
    """Exécute le diagnostic ou le traitement isolé du maillage. Renvoie 0 ou 2."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("commande", choices=["diagnostic", "alpha-wrap", "simplifier"])
    p.add_argument("entree", nargs="?")
    p.add_argument("sortie", nargs="?")
    p.add_argument("--parametres", default="{}")
    a = p.parse_args()
    try:
        d = diagnostic()
        if a.commande != "diagnostic":
            with np.load(a.entree, allow_pickle=False) as z:
                v, f, details = calculer(z["vertices"], z["faces"], a.commande, json.loads(a.parametres))
            np.savez(a.sortie, vertices=v, faces=f)
            d.update(details)
        print(json.dumps(d, allow_nan=False))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError) as e:
        print(type(e).__name__ + ": " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
