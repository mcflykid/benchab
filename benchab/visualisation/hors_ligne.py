"""Intègre les modules three.js vérifiés dans les pages de visualisation."""

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

from benchab.chemins import atelier_prive, est_temporaire, sans_lien
from benchab.qualite.ecritures import PublicationNeuve

VENDOR = Path(__file__).resolve().parent / "vendor" / "three-0.160.0"
MODULES = {
    "three": "build/three.module.js",
    "three/addons/loaders/GLTFLoader.js": "examples/jsm/loaders/GLTFLoader.js",
    "three/addons/controls/OrbitControls.js": "examples/jsm/controls/OrbitControls.js",
    "three/addons/environments/RoomEnvironment.js": "examples/jsm/environments/RoomEnvironment.js",
    "three/addons/utils/BufferGeometryUtils.js": "examples/jsm/utils/BufferGeometryUtils.js",
}
DISTANTS = ("cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com", "fonts.gstatic.com")


def verifier_vendor(vendor=VENDOR):
    """Vérifie les empreintes et la présence des modules utilisés par le gabarit."""
    provenance = json.loads((vendor / "PROVENANCE.json").read_text())
    for relatif, attendu in provenance["fichiers"].items():
        empreinte = hashlib.sha256((vendor / relatif).read_bytes()).hexdigest()
        if empreinte != attendu:
            raise ValueError(f"vendor altéré : {relatif}")
    manquants = [relatif for relatif in MODULES.values() if relatif not in provenance["fichiers"]]
    if manquants:
        raise ValueError(f"vendor incomplet : {manquants}")
    return provenance["version"]


def _source(relatif, vendor):
    code = (vendor / relatif).read_text()
    if relatif.startswith("examples/jsm/"):
        base = Path(relatif).parent

        def specificateur_nu(match):
            cible = (Path("/") / base / match.group(2)).resolve().as_posix().lstrip("/")
            chemin = cible.removeprefix("examples/jsm/")
            return f"{match.group(1)}three/addons/{chemin}{match.group(3)}"

        code = re.sub(r"(from\s+')((?:\.\.?/)[^']+)(')", specificateur_nu, code)
    return code


def carte_import(vendor=VENDOR):
    """Renvoie la carte d'import avec des modules intégrés en data URL."""
    verifier_vendor(vendor)
    imports = {
        spec: "data:text/javascript;base64," + base64.b64encode(_source(rel, vendor).encode()).decode("ascii")
        for spec, rel in MODULES.items()
    }
    return {"imports": imports}


def autonome(html, vendor=VENDOR):
    """Remplace les ressources distantes par les modules locaux vérifiés."""
    html = re.sub(r"<link[^>]+(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>\n?", "", html)
    nouvelle_carte = '<script type="importmap">' + json.dumps(carte_import(vendor), separators=(",", ":")) + "</script>"
    html, nombre = re.subn(
        r"<script type=\"importmap\">.*?</script>",
        lambda _: nouvelle_carte,
        html,
        count=1,
        flags=re.S,
    )
    if nombre != 1:
        raise ValueError("carte d'import absente du gabarit")
    restants = [distant for distant in DISTANTS if distant in html]
    if restants:
        raise ValueError(f"ressources distantes restantes : {restants}")
    return html


def partagee(html, vendor=VENDOR):
    """Référence les ressources installées sous le serveur local BENCHAB."""
    verifier_vendor(vendor)
    html = re.sub(r"<link[^>]+(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>\n?", "", html)
    base = "/benchab/visualisation/vendor/three-0.160.0/"
    carte = {"imports": {spec: base + relatif for spec, relatif in MODULES.items()}}
    nouvelle_carte = '<script type="importmap">' + json.dumps(carte, separators=(",", ":")) + "</script>"
    html, nombre = re.subn(
        r"<script type=\"importmap\">.*?</script>",
        lambda _: nouvelle_carte,
        html,
        count=1,
        flags=re.S,
    )
    if nombre != 1 or any(distant in html for distant in DISTANTS):
        raise ValueError("ressource graphique non locale ou carte d'import absente")
    return html


def main(argv=None):
    """Convertit une page HTML vers une sortie neuve ; code0, erreur sur FAIL, sans écraser ni publier la source."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sortie", type=Path)
    args = parser.parse_args(argv)
    prive = atelier_prive()
    source = sans_lien(args.source.expanduser(), strict=True)
    sortie = sans_lien((args.sortie or args.source).expanduser())
    if not sortie.is_relative_to(prive) and not est_temporaire(sortie):
        parser.error("la sortie doit rester dans prive/ ou /private/tmp")
    if sortie.exists() or sortie.is_symlink():
        parser.error("la sortie existe déjà; jamais d'écrasement")
    # Une conversion interrompue ne doit livrer ni page partielle ni changement
    # de l'original (docs/reference/controles.md, reconstruction).
    with PublicationNeuve([source], [sortie]) as publication:
        publication.temporaire(sortie).write_text(autonome(source.read_text()), encoding="utf-8")
        publication.publier()
    print(f"{sortie.name} : page autonome ({sortie.stat().st_size} octets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
