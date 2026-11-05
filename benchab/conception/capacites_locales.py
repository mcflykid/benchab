"""Inventaire local sans réseau, installation, commande machine ou tranchage.

DISPONIBLE décrit la présence du logiciel et ses imports, sans validation physique.
Les dépendances dont le port est prévu plus tard sont explicitement ABSENTES.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from benchab.machine.configuration import charger

R = Path(__file__).resolve().parents[2]
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
ALIASES = {"PIL": "pillow", "yaml": "pyyaml"}


def cad_python():
    explicite = os.environ.get("BENCHAB_CAD_PYTHON")
    if explicite and Path(explicite).expanduser().absolute() != Path(sys.executable).absolute():
        raise ValueError("Python unique : BENCHAB_CAD_PYTHON extérieur refusé")
    return Path(sys.executable)


VENV = CADGEN = cad_python()
SLICER = charger()["slicer"]["executable"]
CAPACITES = {
    "cadgen": (
        "Construction et conversion CAO déterministes",
        VENV,
        ["cadgen", "build123d", "ezdxf"],
        [],
        "benchab/conception/cad_engine.py",
    ),
    "references": (
        "Références visuelles et silhouettes",
        VENV,
        ["PIL", "numpy"],
        [],
        "benchab/references_visuelles/references.py",
    ),
    "analyse_multivue": (
        "Silhouettes et cohérence multivue",
        VENV,
        ["numpy", "scipy"],
        [],
        "benchab/references_visuelles/analyser.py",
    ),
    "silhouette_vers_stl": (
        "Silhouette vers extrusion 2,5D",
        VENV,
        ["PIL", "numpy", "scipy", "trimesh"],
        [],
        "benchab/references_visuelles/contours.py",
    ),
    "rendu_silhouettes": (
        "Silhouettes orthographiques dans Blender",
        None,
        [],
        ["blender"],
        "benchab/references_visuelles/rendre_silhouettes.py",
    ),
    "import_modele": ("Import de modèles externes", VENV, ["numpy", "trimesh"], [], "benchab/imports3d/importer.py"),
    "booleens_maillage": (
        "Booléens de maillages",
        VENV,
        ["trimesh", "manifold3d"],
        [],
        "benchab/controles/zones_locales.py",
    ),
    "cao_parametrique": ("CAO paramétrique build123d", VENV, ["build123d"], [], "benchab/conception/chanfreins.py"),
    "variantes": ("Comparaison de variantes nommées", VENV, [], [], "benchab/conception/variantes.py"),
    "visionneuse_hors_ligne": (
        "Page 3D autonome",
        VENV,
        ["numpy", "trimesh"],
        [],
        "benchab/visualisation/hors_ligne.py",
    ),
    "tranchage": ("Moteur de tranchage externe déclaré", None, [], [SLICER], "benchab/fabrication/trancher.py"),
    "comparaison_remplissages": (
        "Comparaison de remplissages tranchés",
        VENV,
        ["numpy", "trimesh"],
        [SLICER],
        "benchab/fabrication/comparer_remplissages.py",
    ),
    "profils_cibles": ("Filtrage de profils explicites", VENV, [], [], "benchab/machine/profils_cibles.py"),
    "affectations_ace": ("Affectations ACE déclarées", VENV, [], [], "benchab/machine/affectations_ace.py"),
    "rendu_photo": ("Rendu photoréaliste", None, [], ["blender"], "benchab/visualisation/rendu_photo.py"),
    "dfam": (
        "DfAM et limites de couverture",
        VENV,
        ["numpy", "trimesh", "scipy", "rtree"],
        [],
        "benchab/conception/dfam.py",
    ),
    "maillage_avance": (
        "Alpha Wrap et simplification",
        VENV,
        ["numpy", "trimesh", "scipy", "rtree", "pymeshlab", "meshoptimizer"],
        [],
        "benchab/imports3d/maillage_avance.py",
    ),
}
A_PORTER = {}
HISTORIQUES = {
    "rinkhals": "Ancien accès LAN",
    "orca": "Ancien moteur de tranchage",
    "ecoute_imprimante": "Ancien accès LAN",
    "gcode": "Ancien skill Orca",
    "cloud_anycubic": "Hors diagnostic local ; aucune lecture de compte implicite",
}
SEPARES = {"3d-print-modeling": "Méthode de modélisation maillée sur demande"}


def executer_python(commande, **options):
    if Path(commande[0]).absolute() != Path(sys.executable).absolute():
        raise ValueError("Python unique : interpréteur extérieur refusé")
    with tempfile.TemporaryDirectory(prefix="benchab-capacite-", dir="/private/tmp") as dossier:
        environnement = dict(ENV, HOME=dossier, TMPDIR=dossier, XDG_CACHE_HOME=dossier)
        options.pop("env", None)
        commande = list(commande)
        if "-B" not in commande[1:]:
            commande.insert(1, "-B")
        return subprocess.run(commande, env=environnement, **options)


def versions(interp, modules):
    if not modules:
        return {}
    if interp is None or not Path(interp).is_file() or Path(interp).absolute() != Path(sys.executable).absolute():
        return {m: None for m in modules}
    code = (
        "import importlib.metadata as m,json,sys\nr={}\nfor n in sys.argv[1:]:\n"
        " try:r[n]=m.version(n)\n except m.PackageNotFoundError:r[n]=None\nprint(json.dumps(r))"
    )
    try:
        p = executer_python(
            [str(interp), "-I", "-c", code, *[ALIASES.get(m, m) for m in modules]],
            capture_output=True,
            text=True,
            timeout=15,
            env=ENV,
        )
        found = json.loads(p.stdout) if p.returncode == 0 else {}
        return {m: found.get(ALIASES.get(m, m)) for m in modules}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {m: None for m in modules}


def verrous():
    path = R / "uv.lock"
    if not path.is_file():
        return {}
    donnees = tomllib.loads(path.read_text())
    pins = {}
    for paquet in donnees.get("package", []):
        if "version" in paquet:
            pins[paquet["name"].lower().replace("_", "-")] = paquet["version"]
    return pins


def programme(nom):
    p = Path(nom)
    if p.is_absolute():
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    return shutil.which(nom) or next(
        (
            str(q)
            for q in (Path("/Applications/Blender.app/Contents/MacOS/Blender"),)
            if nom == "blender" and q.exists()
        ),
        None,
    )


def importer(interp, modules, delai=60):
    if interp is not None and Path(interp).absolute() != Path(sys.executable).absolute():
        return False, "Python unique : interpréteur extérieur refusé"
    if not modules:
        return True, ""
    code = "import importlib,sys\nfor m in sys.argv[1:]: importlib.import_module(m)"
    try:
        r = executer_python(
            [str(interp), "-I", "-c", code, *modules], capture_output=True, text=True, timeout=delai, env=ENV
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"[:200]
    return r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1][:200]


def verifier_vendor():
    try:
        from benchab.visualisation import hors_ligne

        return True, "three.js " + hors_ligne.verifier_vendor()
    except Exception as erreur:
        return False, f"{type(erreur).__name__}: {erreur}"[:200]


def evaluer(capacites=None):
    capacites = {**CAPACITES, **A_PORTER} if capacites is None else capacites
    res = {}
    for nom, (desc, interp, modules, progs, outil) in capacites.items():
        raisons = []
        if nom in A_PORTER:
            raisons.append("Port incomplet : capacité non disponible, fermeture prévue avant la validation finale")
        if not (R / outil).exists():
            raisons.append(f"outil absent : {outil}")
        if interp is not None and not Path(interp).exists():
            raisons.append(f"interpréteur absent : {interp}")
        elif interp is not None and Path(interp).absolute() != Path(sys.executable).absolute():
            raisons.append("Python unique : interpréteur extérieur refusé")
        elif interp is not None:
            ok, err = importer(interp, modules)
            if not ok:
                raisons.append(f"import impossible ({', '.join(modules)}) : {err}")
        trouves = {}
        for p in progs:
            chemin = programme(p)
            trouves[Path(p).name] = chemin
            if not chemin:
                raisons.append(f"programme absent : {Path(p).name}")
        if nom == "visionneuse_hors_ligne":
            ok, det = verifier_vendor()
            if not ok:
                raisons.append(det)
        moteur_maillage = None
        if nom == "maillage_avance" and not raisons:
            try:
                r = executer_python(
                    [str(interp), "-B", "-m", "benchab.imports3d.maillage_avance", "diagnostic"],
                    capture_output=True,
                    text=True,
                    timeout=25,
                    env=ENV,
                )
                if r.returncode:
                    raisons.append("Moteurs natifs indisponibles : " + r.stderr[-300:])
                else:
                    moteur_maillage = json.loads(r.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                raisons.append("Diagnostic des moteurs incomplet : " + type(e).__name__)
        res[nom] = {
            "description": desc,
            "statut": "DISPONIBLE" if not raisons else "ABSENTE",
            "raisons": raisons,
            "outil": outil,
            "interpreteur": str(interp) if interp else None,
            "modules": modules,
            "programmes": trouves,
        }
        if nom == "maillage_avance":
            res[nom]["moteurs_natifs"] = moteur_maillage
    pins = verrous()
    for nom, row in res.items():
        row["entree"] = "benchab conception capacites-locales -- --capacite " + nom
        row["diagnostic"] = "benchab conception capacites-locales -- --capacite " + nom
        row["versions_observees"] = versions(row["interpreteur"], row["modules"])
        row["versions_attendues"] = {m: pins.get(ALIASES.get(m, m).lower().replace("_", "-")) for m in row["modules"]}
        row["non_verrouillees"] = [m for m, v in row["versions_attendues"].items() if v is None]
        row["installation_executee"] = False
        row["validation_physique"] = False
        if nom == "tranchage":
            row["moteur"] = {**charger()["slicer"], "version_reelle_verifiee": False}
    return res


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacite", choices=sorted(set(CAPACITES) | set(A_PORTER) | set(HISTORIQUES) | set(SEPARES)))
    parser.add_argument("--json", nargs="?", const="-")
    arguments = parser.parse_args(argv)
    if arguments.capacite in HISTORIQUES or arguments.capacite in SEPARES:
        print(
            json.dumps(
                {
                    "capacite": arguments.capacite,
                    "statut": "HISTORIQUE" if arguments.capacite in HISTORIQUES else "SEPARE_SUR_DEMANDE",
                    "raison": HISTORIQUES.get(arguments.capacite, SEPARES.get(arguments.capacite)),
                },
                ensure_ascii=False,
            )
        )
        return 2
    catalogue = {**CAPACITES, **A_PORTER}
    selection = {arguments.capacite: catalogue[arguments.capacite]} if arguments.capacite else catalogue
    resultats = evaluer(selection)
    payload = {
        "capacites": resultats,
        "historiques": HISTORIQUES,
        "separes": SEPARES,
        "reseau": False,
        "installation": False,
    }
    texte = json.dumps(payload, ensure_ascii=False, indent=2)
    if arguments.json and arguments.json != "-":
        with Path(arguments.json).open("x") as flux:
            flux.write(texte + "\n")
    else:
        print(texte)
    return 0 if all(r["statut"] == "DISPONIBLE" for r in resultats.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
