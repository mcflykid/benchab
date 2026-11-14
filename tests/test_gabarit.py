"""Non-régression des pages 3D (27/10/2026) : défauts constatés en navigateur, vérifiés ici dans le
code des pages.
Le contrôle visuel lui-même reste manuel (navigateur intégré, capture) : aucun moteur de navigateur
automatisé n'est installé."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from benchab.visualisation import apercu_gcode as A

ICI = Path(__file__).resolve().parent
VISU = ICI.parent / "benchab/visualisation"
GABARIT = (VISU / "gabarit.html").read_text()
APERCU = A.GABARIT


def fonction(src, nom):
    m = re.search("function " + nom + "\\s*\\([^)]*\\)\\s*\\{", src)
    assert m, nom
    i, n = (m.end(), 1)
    while n:
        n += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.start() : i]


class Gabarit(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node absent : contrôle exécutable des ressources Three non effectué")
    def test_redessin_libere_ressources_privees_une_fois(self):
        script = (
            "\nimport fs from 'node:fs';\nconst THREE=await import('data:text/ja"
            "vascript;base64,'+fs.readFileSync(process.argv[2]).toString('base"
            "64'));\nconst html=fs.readFileSync(process.argv[1],'utf8');\nconst "
            "source=html.slice(html.indexOf('function libererRepere('),html.in"
            "dexOf(\"$('ajouterCom').addEventListener\"));\nconst texture=new THR"
            "EE.DataTexture(new Uint8Array([255,255,255,255]),1,1);\nconst geom"
            "etry=new THREE.SphereGeometry(1),material=new THREE.MeshBasicMate"
            "rial({map:texture});\nlet released={geometry:0,material:0,texture:"
            "0};\ngeometry.addEventListener('dispose',()=>released.geometry++);"
            "material.addEventListener('dispose',()=>released.material++);text"
            "ure.addEventListener('dispose',()=>released.texture++);\nconst mes"
            "h=new THREE.Group(),marker=new THREE.Group();marker.userData.com="
            "true;\nmarker.add(new THREE.Mesh(geometry,material),new THREE.Mesh"
            "(geometry,material));mesh.add(marker);\nconst permanent=new THREE."
            "Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial());"
            "mesh.add(permanent);\nlet permanentReleased=0;permanent.geometry.a"
            "ddEventListener('dispose',()=>permanentReleased++);\nconst pieces="
            "[{mesh}],coms=[],$=()=>({innerHTML:''});\neval(source+'\\ndessinerC"
            "ommentaires();dessinerCommentaires();');\nconsole.log(JSON.stringi"
            "fy({released,permanentReleased,remaining:mesh.children.length,per"
            "manentPresent:mesh.children[0]===permanent}));\n"
        )
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                str(VISU / "gabarit.html"),
                str(VISU / "vendor/three-0.160.0/build/three.module.js"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "released": {"geometry": 1, "material": 1, "texture": 1},
                "permanentReleased": 0,
                "remaining": 1,
                "permanentPresent": True,
            },
        )

    @unittest.skipUnless(shutil.which("node"), "Node absent : contrat export GLB non exécuté")
    def test_export_glb_conserve_octets_originaux_et_libere_url(self):
        self.assertIn('id="exporterGlb" class="sec" disabled', GABARIT)
        self.assertIn("Télécharger les géométries source (.glb · m/Y)", GABARIT)
        self.assertIn("Poses, mécanismes, coupe et sélection ne sont pas exportés.", GABARIT)
        self.assertIn("parfois allégées", GABARIT)
        source = fonction(GABARIT, "exporterApercuGLB")
        script = (
            (
                "\nconst originalBuffer=new Uint8Array([103,108,84,70,2,0,0,0]);\nle"
                "t glbOriginal=new Blob([originalBuffer],{type:'model/gltf-binary'"
                "});\noriginalBuffer.fill(0); // Le parseur 3D peut transformer ses"
                " tableaux : le Blob source reste immuable.\nconst D={titre:'Pièce "
                "/ été'},saved={},timers=[];\nconst document={createElement:()=>({c"
                "lick(){saved.download=this.download;saved.href=this.href;saved.cl"
                "ick=true;}})};\nconst URL={createObjectURL(blob){saved.blob=blob;r"
                "eturn 'blob:fixture';},revokeObjectURL(value){saved.revoked=value"
                ";}};\nconst setTimeout=(fn,delay)=>{timers.push(fn);saved.delay=de"
                "lay;};\n"
            )
            + source
            + (
                "\nexporterApercuGLB();saved.beforeRevoke=!saved.revoked;timers[0]("
                ");\nconsole.log(JSON.stringify({bytes:Array.from(new Uint8Array(aw"
                "ait saved.blob.arrayBuffer())),type:saved.blob.type,download:save"
                "d.download,href:saved.href,click:saved.click,beforeRevoke:saved.b"
                "eforeRevoke,revoked:saved.revoked,delay:saved.delay}));\n"
            )
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["bytes"], [103, 108, 84, 70, 2, 0, 0, 0])
        self.assertEqual(data["type"], "model/gltf-binary")
        self.assertTrue(data["click"])
        self.assertTrue(data["beforeRevoke"])
        self.assertTrue(data["download"].endswith("-geometries-m-y.glb"))
        self.assertEqual(data["href"], data["revoked"])
        self.assertEqual(data["delay"], 1000)

    @unittest.skipUnless(shutil.which("node"), "Node absent : conversion GLB non exécutée")
    def test_conversion_glb_racines_partagees_et_bin_inchange(self):
        source = fonction(GABARIT, "convertirGlbExport")
        script = (
            (
                "\nconst data={asset:{version:'2.0'},nodes:[{name:'A'},{name:'B'}],"
                "scenes:[{nodes:[0,1]},{nodes:[0]},{nodes:[1]}],scene:0};\nconst ra"
                "w=new TextEncoder().encode(JSON.stringify(data)),n=Math.ceil(raw."
                "length/4)*4;\nconst input=new Uint8Array(20+n+12),h=new DataView(i"
                "nput.buffer);h.setUint32(0,0x46546c67,true);h.setUint32(4,2,true)"
                ";h.setUint32(8,input.length,true);h.setUint32(12,n,true);h.setUin"
                "t32(16,0x4e4f534a,true);input.fill(32,20,20+n);input.set(raw,20);"
                "h.setUint32(20+n,4,true);h.setUint32(24+n,0x004e4942,true);input."
                "set([1,2,3,4],28+n);\nconst before=input.slice();\n"
            )
            + source
            + (
                "\nconst output=convertirGlbExport(input.buffer,'Z'),v=new DataView"
                "(output),jsonLength=v.getUint32(12,true),parsed=JSON.parse(new Te"
                "xtDecoder().decode(new Uint8Array(output,20,jsonLength)));\nconsol"
                "e.log(JSON.stringify({nodes:parsed.nodes,scenes:parsed.scenes,bin"
                ":Array.from(new Uint8Array(output,28+jsonLength)),sourceIntact:be"
                "fore.every((x,i)=>x===input[i]),totalOK:v.getUint32(8,true)===out"
                "put.byteLength,padded:jsonLength%4===0}));\n"
            )
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        d = json.loads(result.stdout)
        self.assertEqual(len(d["nodes"]), 4)
        self.assertEqual(d["scenes"], [{"nodes": [2, 3]}, {"nodes": [2]}, {"nodes": [3]}])
        self.assertEqual(d["nodes"][2]["children"], [0])
        self.assertEqual(d["nodes"][3]["children"], [1])
        self.assertEqual(d["bin"], [1, 2, 3, 4])
        self.assertTrue(d["sourceIntact"])
        self.assertTrue(d["totalOK"])
        self.assertTrue(d["padded"])
        self.assertEqual(d["nodes"][2]["matrix"], [0.001, 0, 0, 0, 0, 0, -0.001, 0, 0, 0.001, 0, 0, 0, 0, 0, 1])

    @unittest.skipUnless(shutil.which("node"), "Node absent : réouverture GLB non effectuée")
    def test_conversion_reouverte_par_trimesh_metres_axe_y(self):
        import importlib.util
        import sys

        python = (
            sys.executable
            if importlib.util.find_spec("trimesh")
            else str(ICI.parents[1] / "outils/capacites-3d/.venv-dfam/bin/python")
        )
        if not Path(python).is_file():
            self.skipTest("Interpréteur trimesh absent")
        create = (
            "import trimesh,base64;print(base64.b64encode(trimesh.Scene(trimes"
            "h.creation.box((66,82,36))).export(file_type='glb')).decode())"
        )
        raw = subprocess.run([python, "-c", create], capture_output=True, text=True, timeout=10)
        self.assertEqual(raw.returncode, 0, raw.stderr)
        source = fonction(GABARIT, "convertirGlbExport")
        for axis, expected in [("Z", [0.066, 0.036, 0.082]), ("Y", [0.066, 0.082, 0.036])]:
            script = source + (
                "\nconst b=Buffer.from(process.argv[1],'base64'),a=b.buffer.slice(b"
                ".byteOffset,b.byteOffset+b.byteLength);console.log(Buffer.from(co"
                "nvertirGlbExport(a,process.argv[2])).toString('base64'));"
            )
            converted = subprocess.run(
                ["node", "--input-type=module", "-e", script, raw.stdout.strip(), axis],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(converted.returncode, 0, converted.stderr)
            read = (
                "import trimesh,base64,io,json,sys;m=trimesh.load(io.BytesIO(base6"
                "4.b64decode(sys.stdin.read())),file_type='glb',force='mesh');prin"
                "t(json.dumps(m.extents.tolist()))"
            )
            loaded = subprocess.run(
                [python, "-c", read], input=converted.stdout, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            for actual, wanted in zip(json.loads(loaded.stdout), expected):
                self.assertAlmostEqual(actual, wanted, places=6)

    def test_cadrage_tient_compte_des_proportions(self):
        for src, nom, cam in ((GABARIT, "recadrer", "camera"), (APERCU, "cadrer", "cam")):
            f = fonction(src, nom)
            self.assertIn(cam + ".aspect", f, nom)
            self.assertRegex(f, "Math\\.min\\(v,\\s*h\\)", nom)

    def test_selection_reste_visible(self):
        self.assertRegex(fonction(GABARIT, "materiau"), "emissive\\.setHex\\(p === selection")

    def test_bouton_recadrer_sans_argument(self):
        self.assertNotIn("addEventListener('click', recadrer)", GABARIT)
        self.assertIn("addEventListener('click', () => recadrer())", GABARIT)

    def test_densite_de_pixels_plafonnee(self):
        self.assertIn("setPixelRatio(Math.min(devicePixelRatio, 2))", GABARIT)
        self.assertRegex(APERCU, r"setPixelRatio\s*\(\s*Math\.min\s*\(\s*devicePixelRatio\s*,\s*2\s*\)\s*\)")


if __name__ == "__main__":
    unittest.main()
