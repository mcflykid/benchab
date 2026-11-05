"""Comparer des variantes par partie nommée, sans classement automatique.

python variantes.py variantes.json --sortie preuves/comparaison-neuve
Sources intactes ; mesures de fabrication lues dans un 3MF tranché identifié.
"""

import argparse
import json
import math
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

NON_VERIFIE = "NON VÉRIFIÉ"


def importer():
    from benchab.imports3d import importer as importateur

    return importateur


def chemin(base, value):
    p = Path(value)
    return (base / p).resolve() if not p.is_absolute() else p.resolve()


def nombre(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def fabrication(declaration, base, empreinte):
    """Une valeur déclarée seule n’est jamais une mesure vérifiée."""
    if not declaration:
        return {"statut": NON_VERIFIE, "raison": "Aucune preuve de tranchage fournie."}
    if not isinstance(declaration, dict) or not declaration.get("source") or not declaration.get("sha256"):
        return {
            "statut": NON_VERIFIE,
            "raison": "Source et empreinte du 3MF tranché requises ; valeurs seules ignorées.",
        }
    path = chemin(base, declaration["source"])
    if not path.is_file() or empreinte(path) != declaration["sha256"]:
        return {"statut": "FAIL", "raison": "Preuve de fabrication absente ou empreinte périmée."}
    if path.suffix.lower() != ".3mf":
        return {"statut": NON_VERIFIE, "raison": "Seul un résumé 3MF tranché est interprété."}
    try:
        with ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("Metadata/slice_info.config"))
            plates = []
            for plate in root.findall("plate"):
                meta = {item.get("key"): item.get("value") for item in plate.findall("metadata")}
                if "prediction" not in meta or "weight" not in meta:
                    return {"statut": NON_VERIFIE, "raison": "Temps ou masse absent du résumé de tranchage."}
                seconds, grams = float(meta["prediction"]), float(meta["weight"])
                if not nombre(seconds) or not nombre(grams):
                    raise ValueError("mesures non finies ou négatives")
                plates.append({"temps_s": seconds, "masse_g": grams})
            if not plates:
                return {"statut": NON_VERIFIE, "raison": "Aucune plaque dans le résumé de tranchage."}
    except (BadZipFile, KeyError, ValueError, ElementTree.ParseError) as error:
        return {"statut": "FAIL", "raison": "Résumé de tranchage illisible : " + str(error)}
    result = {
        "statut": "PASS",
        "source": str(path),
        "sha256": declaration["sha256"],
        "temps_s": sum(p["temps_s"] for p in plates),
        "masse_g": sum(p["masse_g"] for p in plates),
        "plaques": plates,
        "portee": "Estimations du résumé du slicer ; association géométrie/3MF non démontrée par ce seul résumé.",
    }
    for key in ("temps_s", "masse_g"):
        if key in declaration and (
            not nombre(declaration[key]) or not math.isclose(declaration[key], result[key], rel_tol=1e-6, abs_tol=0.01)
        ):
            return {"statut": "FAIL", "raison": key + " déclaré différent du résumé du slicer."}
    if empreinte(path) != declaration["sha256"]:
        return {"statut": "FAIL", "raison": "La preuve de tranchage a changé pendant sa lecture."}
    return result


def visuel(path, base, modeles, empreinte):
    if not path:
        return {"statut": NON_VERIFIE, "raison": "Aucune évaluation visuelle fournie."}
    path = chemin(base, path)
    old = json.loads(path.read_text(encoding="utf-8"))
    entries = old.get("entrees_sha256", {})
    contracts = [Path(name) for name, value in entries.items() if value == old.get("contrat_sha256")]
    if len(contracts) != 1 or old.get("type") != "evaluation-visuelle-v1":
        return {"statut": "FAIL", "raison": "Rapport visuel non lié à un unique contrat de référence."}
    from benchab.references_visuelles import references as refs

    result = refs.audit_evaluation(contracts[0], path)
    if result.get("statut") == "FAIL":
        return {"statut": "FAIL", "raison": result.get("raison", "Évaluation visuelle en échec ou périmée.")}
    expected = {m["chemin"]: m["sha256"] for m in modeles.values()}
    if any(result.get("entrees_sha256", {}).get(source) != sha for source, sha in expected.items()):
        return {"statut": "FAIL", "raison": "Évaluation visuelle non liée aux modèles de cette variante."}
    if set(result.get("sources_modele", {})) != set(expected):
        return {
            "statut": "FAIL",
            "raison": "Évaluation visuelle ne couvrant pas exactement les parties de cette variante.",
        }
    return {
        "statut": result["statut"],
        "source": str(path),
        "sha256": empreinte(path),
        "portee": "Comparaison de silhouettes selon le contrat ; aucune validation physique.",
    }


def comparer(manifeste):
    manifeste = Path(manifeste).resolve()
    helper = importer()
    manifest_hash = helper.empreinte(manifeste)
    data = json.loads(manifeste.read_text(encoding="utf-8"))
    variations = data.get("variantes")
    if not isinstance(variations, list) or len(variations) < 2:
        raise ValueError("Deux variantes au moins sont requises.")
    names = [v.get("nom") for v in variations]
    if any(not isinstance(n, str) or not n.strip() for n in names) or len(names) != len(set(names)):
        raise ValueError("Noms de variantes non vides et uniques requis.")
    baseline = data.get("base")
    if baseline not in names:
        raise ValueError("base doit désigner une variante existante.")
    protected = data.get("protegees", [])
    if not isinstance(protected, list):
        raise ValueError("protegees doit être une liste de {piece, sha256}.")
    protection = {}
    for item in protected:
        if (
            not isinstance(item, dict)
            or not item.get("piece")
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
        ):
            raise ValueError("Protection : piece et SHA-256 explicites requis.")
        if item["piece"] in protection:
            raise ValueError("Protection dupliquée : " + item["piece"])
        protection[item["piece"]] = item["sha256"]
    report = {
        "schema": 1,
        "type": "comparaison-variantes-v1",
        "base": baseline,
        "manifest": str(manifeste),
        "manifest_sha256": manifest_hash,
        "variantes": {},
        "classement": "NON EFFECTUÉ",
        "validation_physique": NON_VERIFIE,
        "limites": [
            "Aucune mesure de résistance ou d’étanchéité physique.",
            "Aucun classement par temps, masse ou volume : les critères d’usage doivent être examinés.",
            "Les métriques du slicer ne prouvent pas à elles seules leur association aux géométries comparées.",
        ],
    }
    for variation in variations:
        parts = variation.get("modeles")
        if not isinstance(parts, list) or not parts:
            raise ValueError("modeles non vide requis pour " + variation["nom"])
        modeles = {}
        for part in parts:
            name = part.get("piece")
            if not isinstance(name, str) or not name.strip() or name in modeles:
                raise ValueError("Noms de parties non vides et uniques requis.")
            if part.get("unites") not in helper.UNITES:
                raise ValueError("Unités obligatoires mm/cm/m pour " + name)
            source = chemin(manifeste.parent, part["chemin"])
            before = helper.empreinte(source)
            _, audit = helper.auditer(source, part["unites"], "comparaison de variantes")
            if helper.empreinte(source) != before:
                raise ValueError("Source modifiée pendant la mesure : " + str(source))
            modeles[name] = {
                "chemin": str(source),
                "sha256": before,
                "unites": part["unites"],
                "mesures": audit["avant"],
                "statut": audit["statut"],
                "alertes": audit["alertes"],
            }
        metrics = fabrication(variation.get("mesures_fabrication"), manifeste.parent, helper.empreinte)
        visual = visuel(variation.get("evaluation_visuelle"), manifeste.parent, modeles, helper.empreinte)
        report["variantes"][variation["nom"]] = {"modeles": modeles, "fabrication": metrics, "visuel": visual}
    base_parts = report["variantes"][baseline]["modeles"]
    all_states = []
    for name, variation in report["variantes"].items():
        states = [variation["fabrication"]["statut"], variation["visuel"]["statut"]]
        states.extend(m["statut"] for m in variation["modeles"].values())
        changes = {}
        for part in sorted(set(base_parts) | set(variation["modeles"])):
            a, b = base_parts.get(part), variation["modeles"].get(part)
            if a is None:
                change = "ajouté"
            elif b is None:
                change = "retiré"
            elif a["sha256"] == b["sha256"] and a["unites"] == b["unites"]:
                change = "inchangé"
            else:
                change = "modifié"
            changes[part] = {"etat": change}
            if a and b:
                changes[part]["ecart_dimensions_mm"] = [
                    new - old for old, new in zip(a["mesures"]["dimensions_mm"], b["mesures"]["dimensions_mm"])
                ]
                va, vb = a["mesures"]["volume_mm3"], b["mesures"]["volume_mm3"]
                changes[part]["ecart_volume_mm3"] = vb - va if va is not None and vb is not None else None
        errors = []
        for part, expected in protection.items():
            a, b = base_parts.get(part), variation["modeles"].get(part)
            if not a or not b or a["sha256"] != expected or b["sha256"] != expected or a["unites"] != b["unites"]:
                errors.append(part + " : partie protégée absente, modifiée ou unités différentes.")
        variation["protections"] = {"statut": "FAIL" if errors else "PASS", "erreurs": errors}
        variation["changements"] = changes
        states.append(variation["protections"]["statut"])
        variation["statut"] = "FAIL" if "FAIL" in states else "WARN" if any(s != "PASS" for s in states) else "PASS"
        all_states.append(variation["statut"])
        other = variation["fabrication"]
        ref = report["variantes"][baseline]["fabrication"]
        if other["statut"] == "PASS" and ref["statut"] == "PASS":
            variation["ecarts_fabrication"] = {key: other[key] - ref[key] for key in ("temps_s", "masse_g")}
    # Recheck inputs after all variants have been read.
    for variation in report["variantes"].values():
        for model in variation["modeles"].values():
            if helper.empreinte(model["chemin"]) != model["sha256"]:
                raise ValueError("Source modifiée pendant la comparaison : " + model["chemin"])
    if helper.empreinte(manifeste) != manifest_hash:
        raise ValueError("Manifeste modifié pendant la comparaison.")
    report["statut"] = "FAIL" if "FAIL" in all_states else "WARN" if "WARN" in all_states else "PASS"
    report["portee"] = "Comparaison numérique ; PASS éventuel ne constitue pas un GO impression."
    return report


def markdown(report):
    lines = [
        "# Comparaison de variantes",
        "",
        "Base : " + report["base"],
        "Statut : " + report["statut"] + " ; aucun classement automatique.",
        "",
        "| Variante | Partie | État | Dimensions mm | Volume mm³ | Topologie fermée |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    for name, variation in report["variantes"].items():
        for part, change in variation["changements"].items():
            model = variation["modeles"].get(part)
            measure = model["mesures"] if model else {}
            lines.append(
                "| "
                + " | ".join(
                    map(
                        cell,
                        [
                            name,
                            part,
                            change["etat"],
                            measure.get("dimensions_mm", "—"),
                            measure.get("volume_mm3", "—"),
                            measure.get("ferme_topologiquement", "—"),
                        ],
                    )
                )
                + " |"
            )
    lines += [
        "",
        "| Variante | Temps s | Masse g | Preuve slicer | Visuel | Protection |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, variation in report["variantes"].items():
        metric = variation["fabrication"]
        lines.append(
            "| "
            + " | ".join(
                map(
                    cell,
                    [
                        name,
                        metric.get("temps_s", "—"),
                        metric.get("masse_g", "—"),
                        metric["statut"],
                        variation["visuel"]["statut"],
                        variation["protections"]["statut"],
                    ],
                )
            )
            + " |"
        )
    lines += ["", *["- " + limit for limit in report["limites"]], ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifeste", type=Path)
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.sortie.exists():
            raise ValueError("Dossier de sortie existant : écrasement refusé.")
        report = comparer(args.manifeste)
        args.sortie.mkdir(parents=True, exist_ok=False)
        with (args.sortie / "rapport.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
        with (args.sortie / "comparaison.md").open("x", encoding="utf-8") as output:
            output.write(markdown(report))
        print(
            json.dumps({"statut": report["statut"], "rapport": str(args.sortie / "rapport.json")}, ensure_ascii=False)
        )
        return {"PASS": 0, "WARN": 1, "FAIL": 2}[report["statut"]]
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.exit(2, "ERREUR : " + str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
