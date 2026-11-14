"""Valide la fermeture et le volume d'un STL après conversion en float32."""

import argparse
import json
from pathlib import Path

import numpy as np
import trimesh


def _relu_ferme(candidat, original):
    return (
        candidat.is_watertight
        and candidat.is_volume
        and abs(candidat.volume - original.volume) < 1e-3 * abs(original.volume)
    )


def exportable(maillage):
    """Retourne un maillage fermé et fidèle après aller-retour float32, sinon lève ValueError."""
    import manifold3d

    sommets = np.array(maillage.vertices, dtype=np.float64, order="C", copy=True)
    faces = np.array(maillage.faces, dtype=np.uint64, order="C", copy=True)
    initial = manifold3d.Manifold(manifold3d.Mesh64(vert_properties=sommets, tri_verts=faces))

    for tolerance in (0.005, 0.01, 0.02, 0.04):
        resultat = initial.simplify(tolerance).to_mesh64()
        arrondi = np.asarray(resultat.vert_properties)[:, :3].astype(np.float32).astype(np.float64)
        candidat = trimesh.Trimesh(arrondi, np.asarray(resultat.tri_verts), process=True)
        if _relu_ferme(candidat, maillage):
            return candidat

    candidat = maillage.copy()
    candidat.merge_vertices(digits_vertex=3)
    candidat.update_faces(candidat.nondegenerate_faces())
    candidat.remove_unreferenced_vertices()
    arrondi = np.asarray(candidat.vertices, dtype=np.float32).astype(np.float64)
    candidat = trimesh.Trimesh(arrondi, candidat.faces, process=True)
    if _relu_ferme(candidat, maillage):
        return candidat
    raise ValueError(f"Maillage non exportable proprement (statut manifold {initial.status()})")


def main(argv=None, *, prive):
    """Écrit un STL vérifié dans l'espace privé sans écraser une sortie existante."""
    from benchab.qualite.ecritures import PublicationNeuve

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sortie", type=Path, required=True)
    arguments = parser.parse_args(argv)
    racine = Path(prive).expanduser().resolve(strict=True)

    def fichier_prive(chemin, *, doit_exister):
        from benchab.chemins import sans_lien

        cible = sans_lien(chemin, racine=racine, strict=doit_exister)
        if not cible.is_relative_to(racine):
            raise ValueError("Sources et sorties doivent rester sous base.prive.")
        return cible

    try:
        source = fichier_prive(arguments.source, doit_exister=True)
        sortie = fichier_prive(arguments.sortie, doit_exister=False)
        if not source.is_file() or source.suffix.lower() != ".stl":
            raise ValueError("Un fichier STL privé est requis.")
        if sortie.suffix.lower() != ".stl":
            raise ValueError("La sortie doit avoir l’extension .stl.")
        maillage = trimesh.load_mesh(source, process=True)
        if not isinstance(maillage, trimesh.Trimesh):
            raise ValueError("Le STL doit contenir un maillage unique.")
        resultat = exportable(maillage)
        with PublicationNeuve([source], [sortie]) as publication:
            temporaire = publication.temporaire(sortie)
            resultat.export(temporaire, file_type="stl")
            relu = trimesh.load_mesh(temporaire, process=True)
            if not isinstance(relu, trimesh.Trimesh) or not _relu_ferme(relu, maillage):
                raise ValueError("Le STL exporté n’est pas fermé ou son volume diffère.")
            publication.publier()
        print(
            json.dumps(
                {"sortie": str(sortie.relative_to(racine)), "faces": len(relu.faces), "volume_mm3": relu.volume},
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, TypeError, RuntimeError) as erreur:
        parser.error(str(erreur))
