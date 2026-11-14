"""Scène indicative en millimètres ; worker Blender privé, aucune photographie physique."""

import json
import math
import sys

import bpy
from mathutils import Vector

spec = json.load(open(sys.argv[sys.argv.index("--") + 1]))
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.unit_settings.scale_length = 0.001
cy = sc.cycles
cy.samples = spec.get("echantillons", 160)
cy.use_denoising = True
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = True
    cy.device = "CPU" if spec.get("device") == "CPU" else "GPU"
except Exception:
    cy.device = "CPU"
sc.view_settings.view_transform = (
    "AgX"
    if "AgX" in [i.identifier for i in sc.view_settings.bl_rna.properties["view_transform"].enum_items]
    else "Filmic"
)
sc.view_settings.look = "None"


def noeuds(nom):
    m = bpy.data.materials.new(nom)
    m.use_nodes = True
    return m, m.node_tree.nodes, m.node_tree.links, m.node_tree.nodes["Principled BSDF"]


def entree(b, *noms):
    for n in noms:
        if n in b.inputs:
            return b.inputs[n]


def lignes_couche(nt, lk, b, pas=0.2, force=0.25):
    """Relief des couches d'impression : ondulation selon Z (repère objet, mm)."""
    tc = nt.new("ShaderNodeTexCoord")
    sep = nt.new("ShaderNodeSeparateXYZ")
    lk.new(tc.outputs["Object"], sep.inputs[0])
    m = nt.new("ShaderNodeMath")
    m.operation = "MULTIPLY"
    m.inputs[1].default_value = 2 * math.pi / pas
    lk.new(sep.outputs["Z"], m.inputs[0])
    s = nt.new("ShaderNodeMath")
    s.operation = "SINE"
    lk.new(m.outputs[0], s.inputs[0])
    bump = nt.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = force
    bump.inputs["Distance"].default_value = 0.05
    lk.new(s.outputs[0], bump.inputs["Height"])
    lk.new(bump.outputs["Normal"], b.inputs["Normal"])


def materiau(type_, couleur=None):
    m, nt, lk, b = noeuds(type_)
    if (
        type_ == "pla_glow"
    ):  # PLA phosphorescent : blanc légèrement vert, satiné, un peu translucide (diffusion sous la surface)
        b.inputs["Base Color"].default_value = (*(couleur or (0.86, 0.9, 0.8)), 1)
        b.inputs["Roughness"].default_value = 0.42
        w = entree(b, "Subsurface Weight", "Subsurface")
        if w:
            w.default_value = 0.18
        r = entree(b, "Subsurface Radius")
        if r:
            r.default_value = (1.5, 2.0, 1.2)
        lignes_couche(nt, lk, b)
    elif type_ == "pla":
        b.inputs["Base Color"].default_value = (*(couleur or (0.8, 0.8, 0.8)), 1)
        b.inputs["Roughness"].default_value = 0.45
        lignes_couche(nt, lk, b)
    elif type_ == "plastique_noir":
        b.inputs["Base Color"].default_value = (*(couleur or (0.018, 0.018, 0.02)), 1)
        b.inputs["Roughness"].default_value = 0.32
    elif type_ == "verre":
        b.inputs["Base Color"].default_value = (1, 1, 1, 1)
        b.inputs["Roughness"].default_value = 0.02
        entree(b, "Transmission Weight", "Transmission").default_value = 1.0
        b.inputs["IOR"].default_value = 1.5
    elif type_ == "aluminium_brosse":  # cadre d'horloge, électroménager : métal satiné à reflets étirés
        b.inputs["Base Color"].default_value = (*(couleur or (0.8, 0.8, 0.82)), 1)
        b.inputs["Metallic"].default_value = 1.0
        b.inputs["Roughness"].default_value = 0.28
        an = entree(b, "Anisotropic")
        if an:
            an.default_value = 0.6
    elif type_ == "plastique_mat":
        b.inputs["Base Color"].default_value = (*(couleur or (0.02, 0.02, 0.022)), 1)
        b.inputs["Roughness"].default_value = 0.7
    elif type_ == "rouge":
        b.inputs["Base Color"].default_value = (*(couleur or (0.75, 0.05, 0.04)), 1)
        b.inputs["Roughness"].default_value = 0.4
    elif type_ == "blanc_casse":
        b.inputs["Base Color"].default_value = (*(couleur or (0.92, 0.91, 0.88)), 1)
        b.inputs["Roughness"].default_value = 0.35
    elif type_ == "melamine":
        b.inputs["Base Color"].default_value = (0.9, 0.9, 0.88, 1)
        b.inputs["Roughness"].default_value = 0.38
    elif type_ == "parquet":
        tc = nt.new("ShaderNodeTexCoord")
        mp = nt.new("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (0.004, 0.03, 0.004)
        lk.new(tc.outputs["Object"], mp.inputs[0])
        wv = nt.new("ShaderNodeTexWave")
        wv.wave_type = "BANDS"
        wv.inputs["Distortion"].default_value = 6
        wv.inputs["Detail"].default_value = 4
        lk.new(mp.outputs[0], wv.inputs[0])
        cr = nt.new("ShaderNodeValToRGB")
        cr.color_ramp.elements[0].color = (0.32, 0.2, 0.11, 1)
        cr.color_ramp.elements[1].color = (0.55, 0.38, 0.22, 1)
        lk.new(wv.outputs["Fac"], cr.inputs[0])
        lk.new(cr.outputs[0], b.inputs["Base Color"])
        b.inputs["Roughness"].default_value = 0.5
    elif type_ == "mur":
        b.inputs["Base Color"].default_value = (0.82, 0.8, 0.76, 1)
        b.inputs["Roughness"].default_value = 0.9
    elif type_ == "tissu":
        b.inputs["Base Color"].default_value = (*(couleur or (0.35, 0.42, 0.5)), 1)
        b.inputs["Roughness"].default_value = 0.95
    elif type_ == "feuille":
        b.inputs["Base Color"].default_value = (0.12, 0.32, 0.12, 1)
        b.inputs["Roughness"].default_value = 0.6
    return m


def bloc(nom, taille, centre, mat, biseau=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=centre)
    o = bpy.context.object
    o.name = nom
    o.scale = taille
    bpy.ops.object.transform_apply(scale=True)
    if biseau:
        md = o.modifiers.new("biseau", "BEVEL")
        md.width = biseau
        md.segments = 3
    o.data.materials.append(mat)
    return o


def scene_kallax():
    """Séjour indicatif : fenêtre à gauche, meuble 77 × 39 × 77 cm, panneaux de 39 mm."""
    H, L, P, e = 770.0, 770.0, 390.0, 39.0
    mel = materiau("melamine")
    bloc("Dessus", (L, P, e), (0, 0, H - e / 2), mel, 1.5)
    bloc("Dessous", (L, P, e), (0, 0, e / 2), mel, 1.5)
    bloc("Côté G", (e, P, H - 2 * e), (-L / 2 + e / 2, 0, H / 2), mel, 1.5)
    bloc("Côté D", (e, P, H - 2 * e), (L / 2 - e / 2, 0, H / 2), mel, 1.5)
    bloc("Montant", (16, P, H - 2 * e), (0, 0, H / 2), mel, 0.8)
    bloc("Tablette", (L - 2 * e, P, 16), (0, 0, H / 2), mel, 0.8)
    bloc("Fond", (L, 4, H), (0, P / 2 - 2, H / 2), mel)
    bloc("Sol", (6000, 6000, 10), (0, 1000, -5), materiau("parquet"))
    bloc("Mur", (6000, 10, 3000), (0, P / 2 + 30, 1500), materiau("mur"))
    bloc("Plinthe", (6000, 12, 80), (0, P / 2 + 20, 40), materiau("melamine"))
    # Livres dans un casier et panier en tissu dans un autre : échelle et réalisme.
    for i, (ep, h, c) in enumerate(
        [
            (28, 240, (0.55, 0.12, 0.1)),
            (34, 260, (0.1, 0.2, 0.35)),
            (22, 225, (0.75, 0.68, 0.5)),
            (30, 250, (0.2, 0.3, 0.2)),
        ]
    ):
        bloc(
            f"Livre {i}",
            (ep, 230, h),
            (-330 + sum(x[0] + 2 for x in [(28,), (34,), (22,), (30,)][:i]) + ep / 2, -10, e + h / 2),
            materiau("tissu", c),
            1.0,
        )
    bloc("Panier", (300, 330, 290), (190, -10, H / 2 + 8 + 145), materiau("tissu", (0.62, 0.55, 0.45)), 6.0)
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=55, depth=110, location=(300, 40, H + 55))
    pot = bpy.context.object
    pot.data.materials.append(materiau("blanc_casse"))
    bpy.ops.object.shade_smooth()
    for k in range(9):
        a = k * 2 * math.pi / 9
        bpy.ops.mesh.primitive_uv_sphere_add(
            radius=32, location=(300 + 28 * math.cos(a), 40 + 28 * math.sin(a), H + 130 + 18 * (k % 3))
        )
        f = bpy.context.object
        f.scale = (1, 0.45, 1.6)
        f.rotation_euler = (0, 0.4 * math.cos(a), a)
        f.data.materials.append(materiau("feuille"))
        bpy.ops.object.shade_smooth()
    # Lumière : grande fenêtre à gauche (soleil doux), ciel diffus, rebond chaud.
    bpy.ops.object.light_add(type="AREA", location=(-1800, -900, 1500))
    w = bpy.context.object
    w.data.shape = "RECTANGLE"
    w.data.size = 1400
    w.data.size_y = 1800
    w.data.energy = 2.2e7
    w.data.color = (1.0, 0.97, 0.92)
    w.rotation_euler = (0, math.radians(-70), math.radians(-25))
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 3000))
    s = bpy.context.object
    s.data.energy = 1.4
    s.data.angle = math.radians(6)
    s.rotation_euler = (math.radians(50), math.radians(-35), math.radians(-30))
    s.data.color = (1.0, 0.95, 0.86)
    sc.world = bpy.data.worlds.new("Ciel")
    sc.world.use_nodes = True
    bg = sc.world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.75, 0.8, 0.88, 1)
    bg.inputs["Strength"].default_value = 0.35
    return H


hauteur = {"kallax": scene_kallax}[spec.get("scene", "kallax")]()
for p in spec["pieces"]:
    bpy.ops.wm.stl_import(filepath=p["stl"])
    o = bpy.context.selected_objects[0]
    o.location = (0, 0, hauteur)
    o.data.materials.append(materiau(p["materiau"], p.get("couleur")))
    # Conserver l'angle des arêtes du modèle ; un lissage intégral masquerait
    # des facettes dans la simulation (docs/explications/preuves.md).
    bpy.ops.object.shade_auto_smooth(angle=math.radians(35))
# Accessoires privés explicitement fournis, exécutés dans cette scène isolée.
for chemin in spec.get("scripts", []):
    exec(
        open(chemin).read(),
        {
            "bpy": bpy,
            "math": math,
            "Vector": Vector,
            "materiau": materiau,
            "bloc": bloc,
            "hauteur": hauteur,
            "spec": spec,
        },
    )
r = sc.render
r.resolution_x, r.resolution_y = spec.get("taille", [1600, 1200])
r.image_settings.file_format = "PNG"
if spec.get("titre"):  # étiquette incrustée (consigne du 22/10)
    r.use_stamp = True
    r.stamp_note_text = spec["titre"]
    for att in dir(r):
        if att.startswith("use_stamp_") and isinstance(getattr(r, att), bool):
            setattr(r, att, False)
    r.use_stamp_note = True
    r.stamp_font_size = max(18, r.resolution_x // 60)
    r.stamp_foreground = (1, 1, 1, 1)
    r.stamp_background = (0, 0, 0, 0.6)
base = spec["sortie"][:-4] if spec["sortie"].endswith(".png") else spec["sortie"]
for v in spec.get("vues", [{}]):
    cible = Vector(v.get("cible", [0, 0, hauteur + 150]))
    bpy.ops.object.camera_add(location=v.get("camera", (-700, -1900, 1250)))
    cam = bpy.context.object
    sc.camera = cam
    cam.data.lens = v.get("focale", 50)
    cam.data.clip_end = 30000
    cam.rotation_euler = (cible - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.dof.use_dof = True
    cam.data.dof.focus_distance = (cible - cam.location).length
    cam.data.dof.aperture_fstop = v.get("ouverture", 4.0)
    if spec.get("titre"):
        r.stamp_note_text = spec["titre"] + v.get("titre_suffixe", "")
    r.filepath = base + v.get("suffixe", "") + ".png"
    bpy.ops.render.render(write_still=True)
    print("RENDU", r.filepath, cy.device)
