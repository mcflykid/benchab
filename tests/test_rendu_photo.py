"""Les sorties de rendu sont privées, nouvelles et toujours qualifiées de simulations."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchab.visualisation import rendu_photo as photo


def _spec(tmp_path):
    prive = tmp_path / "atelier"
    prive.mkdir()
    (prive / "cube.stl").write_text("fixture non interprétée par les tests du lanceur")
    spec = {"pieces": [{"stl": "cube.stl", "materiau": "pla"}], "sortie": "rendu.png", "taille": [64, 64]}
    return prive, spec


def test_sources_et_images_privees_et_suffixes_uniques(tmp_path):
    prive, spec = _spec(tmp_path)
    normalise, _, sorties, _ = photo.preparer(spec, prive, prive)
    assert "ni photo réelle" in normalise["titre"]
    assert sorties == [prive / "rendu.png"]
    spec["vues"] = [{}, {}]
    with pytest.raises(ValueError, match="même image"):
        photo.preparer(spec, prive, prive)
    spec.pop("vues")
    spec["sortie"] = str(tmp_path / "publique.png")
    with pytest.raises(ValueError, match="privée"):
        photo.preparer(spec, prive, prive)


def test_refus_rendu_ne_publie_ni_image_ni_rapport(tmp_path, monkeypatch):
    prive, spec = _spec(tmp_path)
    fichier = prive / "spec.json"
    fichier.write_text(json.dumps(spec))
    monkeypatch.setattr(
        photo, "executer", lambda *a, **kw: SimpleNamespace(ok=False, stderr="refus simulé", raison="FAIL")
    )
    with pytest.raises(ValueError, match="refus simulé"):
        photo.rendre(fichier, prive=prive, blender=__file__)
    assert not (prive / "rendu.png").exists()
    assert not (prive / "rendu.rapport.json").exists()


def test_publication_simulation_relit_png_preserve_spec_et_interdit_ecrasement(tmp_path, monkeypatch):
    prive, spec = _spec(tmp_path)
    fichier = prive / "spec.json"
    fichier.write_text(json.dumps(spec))
    avant = fichier.read_bytes()

    def moteur(*args, **kw):
        from PIL import Image

        env = kw["env"]
        assert env["HOME"] != str(prive)
        assert env["TMPDIR"] != str(prive)
        Image.new("RGB", (64, 64), "white").save(kw["cwd"] / "rendu.png")
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(photo, "executer", moteur)
    resultat = photo.rendre(fichier, prive=prive, blender=__file__)
    assert resultat["nature"] == "SIMULATION_VISUELLE"
    assert resultat["validation_physique"] is False
    assert fichier.read_bytes() == avant
    assert (prive / "rendu.png").is_file()
    assert json.loads((prive / "rendu.rapport.json").read_text()) == resultat
    with pytest.raises(ValueError):
        photo.rendre(fichier, prive=prive, blender=__file__)


def test_inspection_scene_lit_rapport_et_preserve_source(tmp_path, monkeypatch):
    prive = tmp_path / "atelier"
    prive.mkdir()
    scene = prive / "scene.blend"
    scene.write_bytes(b"blend fixture")
    moteur = prive / "blender"
    moteur.write_bytes(b"fake executable")
    moteur.chmod(0o755)
    avant = scene.read_bytes()

    def blender(args, **kwargs):
        assert "--disable-autoexec" in args
        assert "--background" in args
        assert kwargs["env"]["HOME"] != str(prive)
        rapport = Path(args[-1])
        rapport.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "statut_geometrique": "PASS",
                    "version_blender": "test",
                    "unite_scene": "METRIC",
                    "facteur_mm_par_unite": 1000.0,
                    "objets": [{"nom": "Cube", "dimensions_mm": [20, 20, 20]}],
                }
            )
        )
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(photo, "executer", blender)
    resultat = photo.inspecter_scene(scene, prive=prive, blender=moteur)
    assert resultat["statut"] == "PASS"
    assert resultat["validation_physique"] is False
    assert resultat["objets"][0]["dimensions_mm"] == [20, 20, 20]
    assert scene.read_bytes() == avant
    assert (prive / "scene.inspection.json").is_file()


def test_inspection_scene_refuse_source_exterieure_et_echec_sans_rapport(tmp_path, monkeypatch):
    prive = tmp_path / "atelier"
    prive.mkdir()
    externe = tmp_path / "scene.blend"
    externe.write_bytes("extérieur".encode())
    with pytest.raises(ValueError, match="privé"):
        photo.inspecter_scene(externe, prive=prive, blender=__file__)
    scene = prive / "scene.blend"
    scene.write_bytes("privé".encode())
    monkeypatch.setattr(
        photo,
        "executer",
        lambda *args, **kwargs: SimpleNamespace(ok=False, stderr="crash fixture", raison="FAIL"),
    )
    moteur = prive / "blender"
    moteur.write_bytes(b"fixture")
    moteur.chmod(0o755)
    with pytest.raises(ValueError, match="crash fixture"):
        photo.inspecter_scene(scene, prive=prive, blender=moteur)
    assert not (prive / "scene.inspection.json").exists()


def test_inspection_scene_ne_convertit_pas_une_unite_inconnue_en_mm(tmp_path, monkeypatch):
    prive = tmp_path / "atelier"
    prive.mkdir()
    scene = prive / "unite-inconnue.blend"
    scene.write_bytes(b"blend fixture")
    moteur = prive / "blender"
    moteur.write_bytes(b"fake executable")
    moteur.chmod(0o755)

    def blender(args, **kwargs):
        Path(args[-1]).write_text(
            json.dumps(
                {
                    "schema": 1,
                    "statut_geometrique": "NON VÉRIFIÉ",
                    "unite_scene": "NONE",
                    "objets": [{"nom": "Cube", "dimensions_unites_scene": [2, 2, 2]}],
                }
            )
        )
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(photo, "executer", blender)
    resultat = photo.inspecter_scene(scene, prive=prive, blender=moteur)
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert "dimensions_mm" not in resultat["objets"][0]


def test_cli_inspection_scene_transmet_contexte_prive_et_code_non_verifie(tmp_path, monkeypatch, atelier_initialise):
    """Le routage conserve les chemins et le code 2 ; aucun moteur ni fichier .blend n'est ouvert par ce test."""
    from benchab.cli import main
    from benchab.visualisation import inspection_scene

    appels = []

    def inspection(argv, *, prive):
        appels.append((argv, prive))
        return 2

    monkeypatch.setattr(inspection_scene, "main", inspection)
    prive = tmp_path / "prive"
    assert main(["--prive", str(prive), "visualisation", "inspection-scene", "scene.blend"]) == 2
    assert appels == [(["scene.blend"], prive)]


@pytest.mark.parametrize("echelle", [0, -1, float("nan"), float("inf"), True, "1"])
def test_inspection_refuse_echelle_invalide_avant_lancement(tmp_path, echelle):
    """Les mm par unité doivent être positifs finis ; aucun fichier ni moteur ouvert sur erreur."""
    with pytest.raises(ValueError, match="Échelle mm par unité"):
        photo.inspecter_scene(tmp_path / "absent.blend", prive=tmp_path, mm_par_unite=echelle)


@pytest.mark.parametrize("materiau", ["melamine", "parquet", "mur", "tissu", "feuille"])
def test_materiaux_du_moteur_restent_accessibles_a_la_specification(tmp_path, materiau):
    """Les matériaux existants du moteur restent configurables ; aucun rendu ni résultat physique ici."""
    prive, spec = _spec(tmp_path)
    spec["pieces"][0]["materiau"] = materiau
    prepare, *_ = photo.preparer(spec, prive, prive)
    assert prepare["pieces"][0]["materiau"] == materiau


def test_inspection_blender_topologie_unites_et_objet_vide(tmp_path):
    """Cube/plan mesurés en mm/mm³, vide NON VÉRIFIÉ ; aucune scène source sauvegardée par l'inspection."""
    import hashlib
    import os

    from benchab.processus import executer

    blender = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    if os.environ.get("TEST_BLENDER") != "1" or not blender.is_file():
        pytest.skip("NON EXÉCUTÉ : qualification Blender explicite indisponible")
    prive = tmp_path / "prive"
    prive.mkdir()
    scene = prive / "fixture.blend"
    script = tmp_path / "fixture.py"
    script.write_text("""import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.context.scene.unit_settings.system='NONE'
bpy.ops.mesh.primitive_cube_add(size=20)
bpy.context.object.name='Cube'
bpy.ops.mesh.primitive_plane_add(size=10, location=(40,0,0))
bpy.context.object.name='Plan'
bpy.context.collection.objects.link(bpy.data.objects.new('Vide', bpy.data.meshes.new('Vide')))
bpy.ops.wm.save_as_mainfile(filepath=""" + repr(str(scene)) + ")\n")
    environnement = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for cle in ("HOME", "TMPDIR", "XDG_CACHE_HOME"):
        dossier = tmp_path / cle
        dossier.mkdir()
        environnement[cle] = str(dossier)
    resultat = executer([str(blender), "--background", "--factory-startup", "--disable-autoexec",
                         "--python", str(script)], cwd=tmp_path, env=environnement,
                        diagnostics=tmp_path / "diagnostics", delai=90)
    assert resultat.ok, resultat.stderr
    avant = hashlib.sha256(scene.read_bytes()).hexdigest()
    inconnu = photo.inspecter_scene(scene, prive=prive, blender=blender)
    assert inconnu["statut"] == "NON VÉRIFIÉ"
    assert all("dimensions_mm" not in o for o in inconnu["objets"])
    explicite = photo.inspecter_scene(scene, prive=prive, blender=blender,
                                      mm_par_unite=1, sortie=prive / "explicite.json")
    objets = {o["nom"]: o for o in explicite["objets"]}
    assert explicite["statut"] == "NON VÉRIFIÉ"  # Le vide ne devient jamais PASS.
    assert objets["Cube"]["dimensions_mm"] == [20, 20, 20]
    assert abs(objets["Cube"]["signed_volume_mm3"] - 8000) < .001
    assert objets["Cube"]["boundary_edges"] == objets["Cube"]["non_manifold_edges"] == 0
    assert objets["Plan"]["boundary_edges"] == objets["Plan"]["non_manifold_edges"] == 4
    assert objets["Vide"]["statut"] == "NON VÉRIFIÉ"
    assert "sans sommets" in objets["Vide"]["erreur"]
    assert hashlib.sha256(scene.read_bytes()).hexdigest() == avant
