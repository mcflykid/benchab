import numpy as np
import pytest
import trimesh

from benchab.conception.vissage import jeu_radial_minimal, main, profil_fichier, profil_radial


def test_profils_radiaux_sont_parametres_et_mesurent_deux_rayons():
    interieur = trimesh.creation.cylinder(radius=5.0, height=2.0, sections=128)
    exterieur = trimesh.creation.cylinder(radius=4.7, height=2.0, sections=128)
    z = [0.0]
    femelle = profil_radial(interieur.triangles, z, cote="interieur", angles=128)
    male = profil_radial(exterieur.triangles, z, cote="exterieur", angles=128)
    assert femelle.shape == male.shape == (1, 128)
    assert np.allclose(femelle, 5.0, atol=0.13)
    assert np.allclose(male, 4.7, atol=0.13)
    assert jeu_radial_minimal(femelle, male, index_axial=np.array([0])) == pytest.approx(0.3, abs=0.03)


def test_jeu_radial_applique_phase_et_refuse_indices_invalides():
    femelle = np.array([[2.0, 4.0, 6.0, 8.0]])
    male = np.array([[3.0, 3.0, 3.0, 3.0]])
    assert jeu_radial_minimal(femelle, male, index_axial=np.array([0]), phase=1) == -1.0
    with pytest.raises(ValueError, match="hors du profil"):
        jeu_radial_minimal(femelle, male, index_axial=np.array([1]))


def test_profils_refusent_parametres_non_valides():
    triangles = np.zeros((1, 3, 3))
    with pytest.raises(ValueError, match="cote"):
        profil_radial(triangles, [0], cote="mauvais")
    with pytest.raises(ValueError, match="angulaire"):
        profil_radial(triangles, [0], angles=2)


def test_cli_profil_maillage_synthetique_respecte_parametres_et_prive(
    tmp_path, monkeypatch, capsys, atelier_initialise
):
    """Vérifie cli profil maillage synthetique respecte parametres et prive.

    Atelier initialisé, aucune machine touchée."""
    mesh_path = tmp_path / "cylindre.stl"
    trimesh.creation.cylinder(radius=5.0, height=2.0, sections=64).export(mesh_path)
    private = tmp_path / "prive"
    private.mkdir(exist_ok=True)
    monkeypatch.setenv("BENCHAB_PRIVE", str(private))
    report = profil_fichier(mesh_path, cote="interieur", pas_z_mm=0.25, angles=64)
    assert report["statut"] == "PASS"
    assert report["parametres"] == {"pas_z_mm": 0.25, "angles": 64, "phase_indice": 0.0}
    assert report["rayon_min_mm"] == pytest.approx(5.0, abs=0.2)

    output = private / "profil.json"
    assert (
        main([str(mesh_path), "--cote", "interieur", "--pas-z-mm", "0.25", "--angles", "64", "--json", str(output)])
        == 0
    )
    assert output.is_file()
    assert '"statut": "PASS"' in capsys.readouterr().out
    with pytest.raises(ValueError, match="doit rester"):
        main([str(mesh_path), "--json", str(tmp_path / "public.json")])
