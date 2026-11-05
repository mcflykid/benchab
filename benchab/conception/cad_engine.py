"""Adaptateur BENCHAB du moteur MIT cadgen ; aucun daemon persistant.

Les scripts de construction sont du code Python de confiance, sans bac à sable.
Les conversions et rendus sont publiés dans un nouveau dossier exclusif.
Le rendu STEP/XBF réserve temporairement un port HTTP sur 127.0.0.1 dans le moteur.
"""

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

from benchab.chemins import atelier_prive, est_temporaire, sans_lien

REVISION = "5a5dab9503d140b8cf62a28d21ba2573a722d2d6"
FORMATS = ("step", "stl", "glb", "3mf", "xbf", "dxf")


def destination(value):
    """Crée une destination exclusive privée/temporaire; chemin sans unité, FAIL sur collision."""
    brut = Path(value).expanduser().absolute()
    if brut.is_symlink():
        raise FileExistsError("Destination existante symbolique refusée")
    path = sans_lien(brut)
    prive = atelier_prive()
    if not path.is_relative_to(prive) and not est_temporaire(path):
        raise ValueError("Sortie privée ou temporaire exigée")
    # mkdir(exist_ok=False) arbitre aussi les accès concurrents et les liens.
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
    # Garder la graphie déclarée après validation canonique préserve les appels
    # de l'API CAO sans refuser les alias système (docs/reference/chemins-et-atelier.md).
    return brut


def empreinte(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inspect_step(path):
    import build123d as bd

    shape = bd.import_step(str(path))
    box = shape.bounding_box()
    valid = shape.is_valid
    if callable(valid):
        valid = valid()
    return {
        "valid": bool(valid),
        "solids": len(shape.solids()),
        "volume_mm3": float(shape.volume),
        "dimensions_mm": [float(box.size.X), float(box.size.Y), float(box.size.Z)],
    }


def inspect_dxf(path):
    """Contrôles DXF et fermeture des arcs elliptiques omise par cadgen 0.8.4.

    Le chaînage reste celui du moteur épinglé : ce n'est pas une validation CAM.
    Une ellipse partielle peut fermer un profil avec d'autres segments.
    """
    import ezdxf
    from cadgen.engine.export import dxf

    document = ezdxf.readfile(str(path))
    findings = dxf.validate_drawing_document(document)
    drawing, _reason = dxf.document_is_drawing(document)
    partial = 0
    if not drawing:
        intents = dxf.layer_table_intents(document)
        endpoints = {}
        original_endpoints = {}
        for entity in document.modelspace():
            layer = str(entity.dxf.get("layer", "0"))
            if intents.get(layer, "cut") != "cut" or dxf.layer_allows_open_geometry(layer):
                continue
            if entity.dxftype() == "ELLIPSE":
                if math.isclose(entity.construction_tool().param_span, math.tau, rel_tol=0, abs_tol=1e-9):
                    continue
                partial += 1
                pair = (dxf._point(entity.start_point), dxf._point(entity.end_point))
            else:
                pair = dxf._open_endpoints(entity)
                if pair is not None:
                    original_endpoints.setdefault(layer, []).append(pair)
            if pair is not None:
                endpoints.setdefault(layer, []).append(pair)
        if partial:
            # Remplacer seulement les constats du chaînage incomplet : les autres
            # erreurs (polyligne ouverte, doublon, unités…) restent bloquantes.
            previous_chains = dxf._open_chains(original_endpoints)
            findings = [finding for finding in findings if finding not in previous_chains]
            findings.extend(dxf._open_chains(endpoints))
    errors = [finding.render() for finding in findings if finding.severity == "error"]
    if errors:
        raise ValueError("DXF refusé : " + "; ".join(errors))
    return {
        "drawing": drawing,
        "partial_cut_ellipses": partial,
        "validation": "contrôles de dessin et chaînage ; pas une validation CAM ou physique",
    }


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="operation", required=True)
    sub.add_parser("version")
    check = sub.add_parser("inspect-step")
    check.add_argument("source", type=Path)
    for name in ("export", "snapshot", "build"):
        cmd = sub.add_parser(name)
        cmd.add_argument("source", type=Path)
        cmd.add_argument("--output-dir", required=True)
        if name == "export":
            cmd.add_argument("--formats", nargs="+", choices=FORMATS, required=True)
        if name == "build":
            cmd.add_argument("--functions", nargs="+")
    return p


def _run(args):
    os.environ.update(CADGEN_TELEMETRY="0", DO_NOT_TRACK="1")
    from importlib.metadata import version

    if version("cadgen") != "0.8.4" or version("build123d") != "0.13.0":
        raise ValueError("Runtime incompatible ; synchroniser uv.lock avec uv sync.")
    if args.operation == "version":
        return {
            "cadgen": version("cadgen"),
            "build123d": version("build123d"),
            "revision": REVISION,
            "daemon": False,
            "telemetry": False,
        }
    source = args.source.expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("Source régulière requise.")
    if args.operation == "inspect-step":
        if source.suffix.lower() not in (".step", ".stp"):
            raise ValueError("Un fichier STEP est requis.")
        result = inspect_step(source)
        if not result["valid"] or result["solids"] == 0:
            raise ValueError("STEP invalide ou sans solide.")
        return result
    if args.operation == "build":
        source = sans_lien(args.source.expanduser(), strict=True)
        prive = atelier_prive()
        if not source.is_relative_to(prive) and not est_temporaire(source):
            raise ValueError("Un programme source ne peut être exécuté que depuis une copie privée ou temporaire")
    if args.operation == "build" and source.suffix.lower() != ".py":
        raise ValueError("Un script Python de confiance est requis.")
    if args.operation == "export":
        if source.suffix.lower() not in (".step", ".stp", ".xbf", ".stl", ".glb", ".3mf"):
            raise ValueError("Source CAO ou maillage requis ; construire les scripts avant export.")
        if len(set(args.formats)) != len(args.formats):
            raise ValueError("Formats dupliqués.")
    output = destination(args.output_dir)
    source_sha256 = empreinte(source)
    try:
        from cadgen.engine import api

        if args.operation == "export":
            paths = [output / ("modele." + fmt) for fmt in args.formats]
            api.export(source, *paths, daemon=False, progress=False)
            controls = {}
            for path in paths:
                if not path.is_file() or path.stat().st_size == 0:
                    raise ValueError(f"Export absent ou vide : {path.name}")
                if path.suffix == ".step":
                    controls[path.name] = inspect_step(path)
                    if not controls[path.name]["valid"] or controls[path.name]["solids"] == 0:
                        raise ValueError("STEP exporté invalide ou sans solide.")
                elif path.suffix == ".dxf":
                    controls[path.name] = inspect_dxf(path)
        elif args.operation == "snapshot":
            api.snapshot(source, output / "apercu.png", daemon=False, progress=False)
            if not (output / "apercu.png").is_file() or (output / "apercu.png").stat().st_size == 0:
                raise ValueError("Rendu absent ou vide.")
            controls = {"rendu": "visualisation, pas une validation géométrique"}
        else:
            # Les chemins relatifs des décorateurs sont isolés des originaux.
            script = output / source.name
            shutil.copyfile(source, script)
            ancien_path = list(sys.path)
            try:
                sys.path.insert(0, str(source.parent))
                report = api.build_script(script, args.functions, verify=True, daemon=False, progress=False)
            finally:
                sys.path[:] = ancien_path
            controls = {"construction": asdict(report)}
            if not len(report) or not report.ok:
                raise ValueError("Construction refusée : aucun modèle ou modèle en erreur.")
            exports = [p for p in output.rglob("*") if p.is_file() and p.suffix.lower().lstrip(".") in FORMATS]
            if not exports:
                raise ValueError("Construction sans export ; utiliser @step(out=...) ou @xbf(...).")
            for path in exports:
                if not path.stat().st_size:
                    raise ValueError("Construction avec export vide.")
                if path.suffix.lower() in (".step", ".stp"):
                    check = inspect_step(path)
                    controls[str(path.relative_to(output))] = check
                    if not check["valid"] or not check["solids"]:
                        raise ValueError("STEP construit invalide ou sans solide.")
                elif path.suffix.lower() == ".dxf":
                    controls[str(path.relative_to(output))] = inspect_dxf(path)
        files = sorted(p for p in output.rglob("*") if p.is_file())
        if args.operation != "build" and not files:
            raise ValueError("Aucun fichier produit.")
        if empreinte(source) != source_sha256:
            raise ValueError("Source modifiée pendant le traitement ; publication refusée.")
        result = {
            "operation": args.operation,
            "source": str(source),
            "source_sha256": source_sha256,
            "revision": REVISION,
            "controls": controls,
            "files": [
                {"path": str(p.relative_to(output)), "bytes": p.stat().st_size, "sha256": empreinte(p)} for p in files
            ],
            "fabrication": "non tranché ; aucun profil Anycubic ; non validé physiquement",
        }
        (output / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        return result
    except Exception:
        # Conserver les preuves de l'échec, sans les présenter comme livrables.
        (output / "ECHEC.txt").write_text(
            "Traitement incomplet : ne pas utiliser pour fabrication.\n",
            encoding="utf-8",
        )
        raise


def run(args, *, prive=None):
    """État et caches du moteur strictement privés ; aucun daemon, télémétrie désactivée."""
    root = atelier_prive(prive)
    env = {
        "CADGEN_STATE_DIR": str(root / "cache/cadgen/etat"),
        "CADGEN_CACHE_DIR": str(root / "cache/cadgen/cache"),
        "MPLCONFIGDIR": str(root / "cache/matplotlib"),
        "CADGEN_TELEMETRY": "0",
        "DO_NOT_TRACK": "1",
        "BENCHAB_PRIVE": str(root),
    }
    anciens = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        for k in ("CADGEN_STATE_DIR", "CADGEN_CACHE_DIR", "MPLCONFIGDIR"):
            sans_lien(Path(env[k]), racine=root).mkdir(parents=True, exist_ok=True)
        return _run(args)
    finally:
        for k, v in anciens.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main(argv=None, *, prive=None):
    args = parser().parse_args(argv)
    try:
        # Le moteur (et les scripts de confiance) peuvent journaliser sur stdout.
        # La sortie du CLI reste un unique document JSON exploitable.
        with redirect_stdout(sys.stderr):
            result = run(args, prive=prive)
    except Exception as exc:
        print(f"CAD : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
