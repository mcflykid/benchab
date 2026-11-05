"""Adaptation locale du DfAM installé : conserver les mesures partielles par corps.

Source MIT earthtojake/text-to-cad, révision 5a5dab9503d140b8cf62a28d21ba2573a722d2d6.
L’instantané livré est contrôlé avant import ; aucun skill global n’est consulté.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "tiers/dfam_tool.py"
SOURCE_SHA256 = "0a0f4032da8ae11a1ba2959c62b16d7462d7e3fbb27fe35f2fbf3c170b3ff954"


def valider_arguments(arguments):
    """Refuser des valeurs qui produiraient des mesures absentes ou non finies."""
    # Même résolution des abréviations que le parseur amont.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--samples", type=int)
    parser.add_argument("--angle-limit", type=float)
    values, _ = parser.parse_known_args(arguments)
    if values.samples is not None and values.samples <= 0:
        raise ValueError("--samples doit etre strictement positif.")
    angle = values.angle_limit
    if angle is not None and (not math.isfinite(angle) or not 0 <= angle <= 90):
        raise ValueError("--angle-limit doit etre fini entre 0 et 90 degres.")


def couverture_epaisseur(result):
    """Garder les statistiques disponibles tout en signalant les corps non mesurés."""
    if "error" in result:
        return result
    bodies = result.get("per_body")
    if bodies is not None:
        missing = [body["body"] for body in bodies if not body.get("samples_valid", 0)]
        if missing:
            return {**result, "error": "Epaisseur non mesuree pour certains corps.", "unmeasured_bodies": missing}
    elif not result.get("samples_valid", 0):
        return {**result, "error": "Epaisseur sans echantillon valide."}
    return result


def charger(source=SOURCE):
    source_bytes = source.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != SOURCE_SHA256:
        raise ValueError("Skill DfAM modifie : revoir son contrat avant de renouveler son empreinte.")
    spec = importlib.util.spec_from_file_location("benchab_upstream_dfam", source)
    module = importlib.util.module_from_spec(spec)
    # Exécuter exactement l'instantané du skill de confiance dont le SHA a été vérifié,
    # sans seconde lecture ni utilisation d'un bytecode antérieur.
    exec(compile(source_bytes, str(source), "exec"), module.__dict__)
    original = module._wall_facts

    def wall_facts(*args, **kwargs):
        return couverture_epaisseur(original(*args, **kwargs))

    module._wall_facts = wall_facts
    return module


def main(argv=None):
    try:
        arguments = sys.argv[1:] if argv is None else argv
        valider_arguments(arguments)
        module = charger()
    except (OSError, ImportError, ValueError) as exc:
        print(json.dumps({"error": f"DfAM local : {type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    return module.main(arguments)


if __name__ == "__main__":
    sys.exit(main())
