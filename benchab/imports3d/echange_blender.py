"""Échange manuel de copies : aucun lancement ni accès à Blender."""

import argparse
import copy
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import trimesh

from benchab.imports3d.importer import UNITES, auditer, charger, charger_objet, empreinte, mesurer, verifier_export
from benchab.machine import profil_maitre as PM
from benchab.machine.configuration import charger as charger_configuration
from benchab.qualite.ecritures import PublicationNeuve, valider_chemins


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _chemin(path):
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("Traversée de chemin refusée.")
    valider_chemins([path], [])
    return path.absolute()


def _sceller(data):
    data = copy.deepcopy(data)
    data.pop("sceau_sha256", None)
    return PM.empreinte_contexte(data)


def _codes():
    racine = Path(__file__).resolve().parents[2]
    fichiers = (
        Path(__file__).resolve(),
        racine / "benchab/imports3d/importer.py",
        racine / "benchab/imports3d/geometrie.py",
        racine / "benchab/qualite/ecritures.py",
        Path(PM.__file__),
    )
    return {path.relative_to(racine).as_posix(): empreinte(path) for path in fichiers}


def _matrice(value, rigide=True):
    a = np.asarray(value)
    if a.shape != (4, 4) or a.dtype.kind not in "ifu" or not np.isfinite(a).all():
        raise ValueError("Matrice4x4 numérique finie requise.")
    a = a.astype(float)
    if not np.allclose(a[3], [0, 0, 0, 1], atol=1e-9):
        raise ValueError("Matrice affine requise.")
    s = np.linalg.svd(a[:3, :3], compute_uv=False)
    if np.linalg.det(a[:3, :3]) <= 0 or min(s) <= 0:
        raise ValueError("Reflet ou matrice singulière refusé.")
    if not np.allclose(s, s[0], atol=1e-8) or (rigide and not np.allclose(s, 1, atol=1e-8)):
        raise ValueError("Échelle incohérente : transformation explicite rigide seulement.")
    return a


def _instances(source, unites, transformations):
    obj = charger_objet(source)
    if isinstance(obj, trimesh.Scene):
        nodes = [(str(n), obj.graph[n][0], obj.geometry[obj.graph[n][1]]) for n in obj.graph.nodes_geometry]
    else:
        nodes = [("source", np.eye(4), obj)]
    if transformations is None:
        transformations = {}
    if not isinstance(transformations, dict) or set(transformations) - {n for n, _, _ in nodes}:
        raise ValueError("Transformations : dictionnaire par nom d’instance existante requis.")
    parts, traces = [], []
    for name, world, mesh in nodes:
        if not isinstance(mesh, trimesh.Trimesh):
            continue
        world = _matrice(world, rigide=False)
        extra = _matrice(transformations.get(name, np.eye(4).tolist()))
        # Matrice source en unités source ; matrice supplémentaire en mm.
        total = extra @ np.diag([UNITES[unites]] * 3 + [1]) @ world
        part = mesh.copy()
        part.apply_transform(total)
        parts.append(part)
        traces.append(
            {
                "instance": name,
                "source_4x4": world.tolist(),
                "ajout_mm_4x4": extra.tolist(),
                "effective_mm_4x4": total.tolist(),
            }
        )
    if not parts:
        raise ValueError("Aucune instance triangulée.")
    return trimesh.util.concatenate(parts), traces


def preparer_echange(source, destination, unites, transformations=None):
    """Prépare la copie d’échange et son manifeste, en mm, sans ouvrir Blender."""
    source, destination = _chemin(source), _chemin(destination)
    if unites not in UNITES:
        raise ValueError("Unités explicites mm/cm/m requises.")
    if destination.suffix.lower() != ".stl":
        raise ValueError("Destination STL neuve requise, coordonnées mm.")
    _, audit = auditer(source, unites)
    mesh, instances = _instances(source, unites, transformations)
    manifeste = destination.with_suffix(".echange.json")
    copie = destination.with_name(destination.stem + ".source" + source.suffix.lower())
    configuration = charger_configuration()
    data = {
        "schema": 1,
        "type": "echange-blender-manuel",
        "date": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "source_sha256": audit["source_sha256"],
        "unites_source": unites,
        "copie_source": str(copie),
        "copie_travail": str(destination),
        "unites_travail": "mm",
        "instances": instances,
        "avant": audit["avant"],
        "travail": mesurer(mesh),
        "configuration_machine_sha256": PM.empreinte_contexte(configuration),
        "codes_sha256": _codes(),
        "limites": [
            "Copie STL : matière, couleur et attribution outils non conservées.",
            "Contrôle numérique partiel, aucune validation physique.",
            "Sceau de cohérence local, pas une signature authentifiée.",
        ],
        "manifeste": str(manifeste),
        "ouverture": (
            "Manuelle dans une nouvelle instance Blender choisie par l’utilisateur ; aucune scène existante touchée."
        ),
    }
    with PublicationNeuve([source], [destination, copie, manifeste]) as pub:
        temp = pub.temporaire(destination)
        mesh.export(temp, file_type="stl")
        verifier_export(mesh, charger(temp, "mm"))
        pub.temporaire(copie).write_bytes(source.read_bytes())
        if empreinte(source) != data["source_sha256"] or empreinte(pub.temporaire(copie)) != data["source_sha256"]:
            raise ValueError("Source modifiée pendant la préparation.")
        data["travail_sha256"] = empreinte(temp)
        data["sceau_sha256"] = _sceller(data)
        pub.temporaire(manifeste).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        if _json(pub.temporaire(manifeste)) != data:
            raise ValueError("Manifeste non relu.")
        pub.publier()
    return data


def _verifier(manifeste):
    path = _chemin(manifeste)
    data = _json(path)
    if (
        data.get("schema") != 1
        or data.get("type") != "echange-blender-manuel"
        or data.get("sceau_sha256") != _sceller(data)
    ):
        raise ValueError("Manifeste altéré ou inconnu.")
    if Path(data["manifeste"]) != path:
        raise ValueError("Manifeste déplacé : contexte inconnu.")
    for key, sha in [
        ("source", "source_sha256"),
        ("copie_source", "source_sha256"),
        ("copie_travail", "travail_sha256"),
    ]:
        p = _chemin(data[key])
        if not p.is_file() or empreinte(p) != data[sha]:
            raise ValueError("Entrée scellée modifiée : " + key)
    if data["codes_sha256"] != _codes() or data["configuration_machine_sha256"] != PM.empreinte_contexte(
        charger_configuration()
    ):
        raise ValueError("Configuration machine ou code modifié : nouvelle préparation requise.")
    return data


def controler_retour(manifeste, candidat, unites_candidat):
    if unites_candidat not in UNITES:
        raise ValueError("Unités explicites mm/cm/m requises pour le retour.")
    data = _verifier(manifeste)
    candidat = _chemin(candidat)
    if str(candidat) in (data["source"], data["copie_source"], data["copie_travail"]):
        raise ValueError("Le retour doit être un nouveau fichier distinct.")
    mesh, audit = auditer(candidat, unites_candidat)
    mesure = audit["avant"]
    if not mesh.is_winding_consistent or (mesh.copy().volume < 0):
        raise ValueError("Orientation/reflet incohérent du retour.")
    ratios = np.asarray(mesure["dimensions_mm"]) / np.maximum(data["travail"]["dimensions_mm"], 1e-12)
    if np.allclose(ratios, ratios[0], rtol=1e-4) and not math.isclose(float(ratios[0]), 1, rel_tol=1e-4):
        raise ValueError("Changement d’échelle uniforme : unités ou transformation à clarifier.")
    from benchab.imports3d.geometrie import comparer

    comparaison = comparer(charger(data["copie_travail"], "mm"), mesh)
    return {
        "schema": 1,
        "type": "controle-retour-blender",
        "manifeste": str(Path(manifeste).absolute()),
        "sceau_preparation": data["sceau_sha256"],
        "candidat": str(candidat),
        "candidat_sha256": audit["source_sha256"],
        "unites_candidat": unites_candidat,
        "avant": data["travail"],
        "apres": mesure,
        "comparaison": comparaison,
        "changements": {
            k: {"avant": data["travail"][k], "apres": v} for k, v in mesure.items() if data["travail"][k] != v
        },
        "statut": "A_CONFIRMER",
        "validation_physique": "NON VERIFIE",
        "limites": [
            "Distance échantillonnée sans recalage ; pas de preuve exhaustive.",
            "Un reflet d’une forme symétrique ou exporté avec normales corrigées ne peut être déduit "
            "de la géométrie seule.",
            "Les changements attendus doivent être examinés avant acceptation explicite.",
        ],
    }


def importer_retour(manifeste, candidat, unites_candidat, destination_neuve, acceptation_explicite=False):
    """Importe le retour Blender et son rapport, sans modifier l’original."""
    if acceptation_explicite is not True:
        raise ValueError("Acceptation explicite du retour requise.")
    rapport = controler_retour(manifeste, candidat, unites_candidat)
    dest = _chemin(destination_neuve)
    if dest.suffix.lower() != ".stl":
        raise ValueError("Nouvelle version STL requise.")
    rp = dest.with_suffix(".retour.json")
    data = _verifier(manifeste)
    mesh = charger(candidat, unites_candidat)
    with PublicationNeuve(
        [manifeste, candidat, data["source"], data["copie_source"], data["copie_travail"]], [dest, rp]
    ) as pub:
        mesh.export(pub.temporaire(dest), file_type="stl")
        verifier_export(mesh, charger(pub.temporaire(dest), "mm"))
        _verifier(manifeste)
        if empreinte(candidat) != rapport["candidat_sha256"]:
            raise ValueError("Candidat modifié pendant l’import.")
        rapport.update(
            destination=str(dest),
            destination_sha256=empreinte(pub.temporaire(dest)),
            acceptation_explicite=True,
            statut="VERSION_NEUVE_CONTROLEE",
        )
        pub.temporaire(rp).write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        _json(pub.temporaire(rp))
        pub.publier()
    return rapport


def main(argv=None):
    """Distribue les échanges Blender locaux ; retourne le code de contrôle."""
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    a = sub.add_parser("preparer")
    a.add_argument("source")
    a.add_argument("--destination", required=True)
    a.add_argument("--unites", required=True, choices=UNITES)
    a.add_argument("--transformations", help="JSON dictionnaire instance→matrice4x4 rigide en mm")
    for name in ("controler", "importer"):
        a = sub.add_parser(name)
        a.add_argument("manifeste")
        a.add_argument("candidat")
        a.add_argument("--unites", required=True, choices=UNITES)
        if name == "importer":
            a.add_argument("--destination", required=True)
            a.add_argument("--accepter", action="store_true")
        else:
            a.add_argument("--json")
    a = p.parse_args(argv)
    try:
        if a.action == "preparer":
            r = preparer_echange(
                a.source, a.destination, a.unites, _json(_chemin(a.transformations)) if a.transformations else None
            )
        elif a.action == "importer":
            r = importer_retour(a.manifeste, a.candidat, a.unites, a.destination, a.accepter)
        else:
            r = controler_retour(a.manifeste, a.candidat, a.unites)
            if a.json:
                out = _chemin(a.json)
                with PublicationNeuve([a.manifeste, a.candidat], [out]) as pub:
                    pub.temporaire(out).write_text(
                        json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False),
                        encoding="utf-8",
                    )
                    _json(pub.temporaire(out))
                    pub.publier()
        print(json.dumps(r, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
