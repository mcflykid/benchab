"""Témoin dense généré par l'atelier : deux simplifications identiques, aucune géométrie tierce."""

import hashlib

import trimesh

from benchab.imports3d.maillage_avance import transformer


def test_sphere_dense_deux_passes_gardent_tolerance_corps_et_sha(tmp_path):
    """327680→25000 faces, tolérance0,1mm échantillonnée, SHA identiques ; aucune preuve physique ni gain annoncé."""
    source = tmp_path / "sphere-dense.stl"
    trimesh.creation.icosphere(subdivisions=7, radius=20).export(source)
    avant = hashlib.sha256(source.read_bytes()).hexdigest()
    resultats = []
    for phase in ("premiere", "repetition"):
        rapport = transformer(
            source, tmp_path / (phase + ".stl"), tmp_path / (phase + ".json"), "mm", "simplifier",
            faces_cibles=25000, tolerance_mm=.1, echantillons=2000, prive=tmp_path / "prive",
        )
        assert rapport["statut"] == "CONFORME_ECHANTILLONS", rapport.get("raisons_refus")
        assert rapport["avant"]["faces"] == 327680
        assert rapport["apres"]["faces"] <= 25000
        assert max(rapport["comparaison"][sens]["maximum_observe_mm"] for sens in
                   ("reference_vers_candidat", "candidat_vers_reference")) <= .1
        resultats.append(rapport)
    assert resultats[0]["sortie_sha256"] == resultats[1]["sortie_sha256"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == avant
