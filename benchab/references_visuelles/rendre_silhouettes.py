"""Rendus orthographiques reproductibles, uniquement dans Blender en arrière-plan.

blender --background --factory-startup --python rendre_silhouettes.py -- spec.json
Les coordonnées de caméra, cible et ortho_scale sont en millimètres. Aucune
normalisation, aucun cadrage automatique. Sortie nouvelle obligatoire.
"""

import hashlib
import json
import math
import sys
from pathlib import Path

FACTORS = {"mm": 1.0, "cm": 10.0, "m": 1000.0}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def vector(value, name):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(name + " : trois coordonnées attendues")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ValueError(name + " : coordonnées finies attendues")
    return value


def validate_spec(spec, base, check_output=True):
    """Valide sans bpy ni écriture ; check_output=False permet le réaudit."""
    base = Path(base)
    meshes = spec.get("meshes")
    if not isinstance(meshes, list) or not meshes:
        raise ValueError("meshes : liste non vide attendue")
    for mesh in meshes:
        p = Path(mesh["path"])
        p = p if p.is_absolute() else base / p
        if not p.is_file() or p.suffix.lower() not in (".stl", ".obj", ".glb"):
            raise ValueError("source STL/OBJ/GLB absente ou non prise en charge : " + str(p))
        if mesh.get("units") not in FACTORS:
            raise ValueError("unités obligatoires : mm, cm ou m")
        if p.suffix.lower() == ".glb" and mesh["units"] != "m":
            raise ValueError("GLB utilise des mètres : déclarer units=m")
    resolution = spec.get("resolution")
    if (
        not isinstance(resolution, list)
        or len(resolution) != 2
        or any(type(n) is not int or not 16 <= n <= 8192 for n in resolution)
    ):
        raise ValueError("resolution : deux entiers de 16 à 8192 attendus")
    views = spec.get("views")
    if not isinstance(views, dict) or not views:
        raise ValueError("views : dictionnaire non vide attendu")
    for name, camera in views.items():
        if not name or not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError("nom de vue non sûr")
        pos = vector(camera.get("position"), name + ".position")
        target = vector(camera.get("target"), name + ".target")
        if sum((a - b) ** 2 for a, b in zip(pos, target)) < 1e-12:
            raise ValueError("caméra et cible confondues")
        scale = camera.get("ortho_scale")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
            raise ValueError("ortho_scale strictement positive attendue")
    out = Path(spec["output_dir"])
    out = out if out.is_absolute() else base / out
    if check_output and out.exists():
        raise ValueError("sortie existante refusée : " + str(out))
    return out


def render(spec_path):
    """Rend les silhouettes locales et leur description ; aucune action machine."""
    spec_path = Path(spec_path).resolve()
    spec_hash = sha256(spec_path)
    spec = json.loads(spec_path.read_text())
    out = validate_spec(spec, spec_path.parent)
    import bpy
    from mathutils import Matrix, Vector

    if not bpy.app.background:
        raise RuntimeError("rendu refusé dans une session Blender interactive ; utiliser --background")
    # All edits affect only this dedicated background Blender process.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    sources = []
    for mesh in spec["meshes"]:
        path = (spec_path.parent / mesh["path"]).resolve()
        source_hash = sha256(path)
        before = set(bpy.data.objects)
        if path.suffix.lower() == ".stl":
            bpy.ops.wm.stl_import(filepath=str(path))
        elif path.suffix.lower() == ".obj":
            bpy.ops.wm.obj_import(filepath=str(path), forward_axis="Y", up_axis="Z")
        else:
            bpy.ops.import_scene.gltf(filepath=str(path))
        imported = set(bpy.data.objects) - before
        snapshots = []
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj in imported:
            if obj.type != "MESH":
                continue
            evaluated = obj.evaluated_get(depsgraph)
            data = bpy.data.meshes.new_from_object(evaluated)
            data.transform(Matrix.Scale(FACTORS[mesh["units"]], 4) @ obj.matrix_world)
            snapshots.append((obj.name, data))
        for obj in imported:
            bpy.data.objects.remove(obj, do_unlink=True)
        if not snapshots:
            raise ValueError("source sans géométrie : " + str(path))
        for name, data in snapshots:
            obj = bpy.data.objects.new(name, data)
            scene.collection.objects.link(obj)
        if sha256(path) != source_hash:
            raise RuntimeError("source modifiée pendant l’import")
        sources.append({"path": str(path), "sha256": source_hash, "units": mesh["units"]})
    material = bpy.data.materials.new("SilhouetteBlanche")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (1, 1, 1, 1)
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
    for obj in scene.objects:
        if obj.type == "MESH":
            obj.data.materials.clear()
            obj.data.materials.append(material)
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.world = bpy.data.worlds.new("FondNoir")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0, 0, 0, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x, scene.render.resolution_y = spec["resolution"]
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    camera_data = bpy.data.cameras.new("CameraComparaison")
    camera = bpy.data.objects.new("CameraComparaison", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera_data.type = "ORTHO"
    camera_data.clip_start = 0.001
    camera_data.clip_end = 1000000
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema": 1,
        "spec_path": str(spec_path),
        "spec_sha256": spec_hash,
        "sources": sources,
        "resolution": spec["resolution"],
        "views": {},
        "blender_version": bpy.app.version_string,
        "limites": "Silhouettes uniquement ; cadrage explicite ; aucune preuve mécanique ni physique.",
    }
    for name, view in spec["views"].items():
        camera.location = view["position"]
        direction = Vector(view["target"]) - camera.location
        camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        camera_data.ortho_scale = view["ortho_scale"]
        image = out / (name + ".png")
        scene.render.filepath = str(image)
        bpy.ops.render.render(write_still=True)
        manifest["views"][name] = {"image": image.name, "sha256": sha256(image), "camera": view}
    if sha256(spec_path) != spec_hash:
        raise RuntimeError("spécification modifiée pendant le rendu")
    for source in sources:
        if sha256(source["path"]) != source["sha256"]:
            raise RuntimeError("source modifiée pendant le rendu")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RENDU SILHOUETTES :", out)
    return manifest


def main(argv=None):
    args = (
        list(argv) if argv is not None else (sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:])
    )
    if len(args) != 1:
        raise SystemExit("usage : blender -b --factory-startup --python rendre_silhouettes.py -- spec.json")
    try:
        render(args[0])
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print("ÉCHEC :", error, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
