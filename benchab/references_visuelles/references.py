"""Contrats visuels et comparaison de silhouettes, sans réseau ni modification des images."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

VERSION = 1
NON_VERIFIE = "NON VÉRIFIÉ"


def empreinte(path):
    """Retourne le SHA-256 hexadécimal des octets du fichier."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ecrire_json(path, data):
    """Publie un JSON UTF-8 sur un nouveau chemin, sans écraser un fichier existant."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)


def seuils_valides(seuils):
    """Vérifie les seuils de centre et boîte en pixels ainsi que le recouvrement entre 0 et 1."""
    if not isinstance(seuils, dict):
        raise ValueError("seuils doit être un objet")
    cles = {"iou_min", "centre_max_px", "bbox_max_px"}
    if set(seuils) != cles:
        raise ValueError("seuils explicites requis : iou_min, centre_max_px, bbox_max_px")
    for cle, valeur in seuils.items():
        if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur):
            raise ValueError("seuil numérique fini requis : " + cle)
        if valeur < 0 or (cle == "iou_min" and valeur > 1):
            raise ValueError("seuil hors limites : " + cle)


def masque(path, mode, seuil):
    """Retourne le masque booléen en pixels selon luminosité ou alpha et le seuil entier de 0 à 255."""
    if mode not in ("clair", "sombre", "alpha"):
        raise ValueError("mode de masque : clair, sombre ou alpha")
    if isinstance(seuil, bool) or not isinstance(seuil, int) or not 0 <= seuil <= 255:
        raise ValueError("seuil_masque doit être un entier entre 0 et 255")
    with Image.open(path) as image:
        if mode == "alpha":
            if "A" not in image.getbands():
                raise ValueError("image sans canal alpha")
            return np.asarray(image.getchannel("A")) > seuil
        valeurs = np.asarray(image.convert("L"))
        return valeurs > seuil if mode == "clair" else valeurs < seuil


def valider_contrat(data):
    """Vérifie version, vues, projections et seuils déclarés du contrat. Lève une erreur si invalides."""
    if data.get("version") != VERSION:
        raise ValueError("version de contrat non prise en charge")
    seuils_valides(data.get("seuils"))
    vues = data.get("vues")
    obligatoires = data.get("vues_obligatoires")
    if not isinstance(vues, dict) or not vues:
        raise ValueError("contrat sans vue")
    if (
        not isinstance(obligatoires, list)
        or not obligatoires
        or any(not isinstance(v, str) or not v for v in obligatoires)
    ):
        raise ValueError("contrat sans vues obligatoires explicites")
    if not isinstance(data.get("sources_modele", {}), dict):
        raise ValueError("sources_modele doit contenir les chemins et empreintes")
    for nom, vue in vues.items():
        for cle in ("image", "sha256", "taille", "projection", "mode", "seuil_masque"):
            if cle not in vue:
                raise ValueError("champ manquant dans la vue " + nom + " : " + cle)
        if vue["projection"] not in ("orthographique", "concept"):
            raise ValueError("projection non reconnue : " + nom)


def figer(specification, destination):
    """Fige le besoin, les vues et leurs empreintes dans un nouveau contrat JSON."""
    specification, destination = Path(specification).resolve(), Path(destination).resolve()
    data = json.loads(specification.read_text(encoding="utf-8"))
    if not isinstance(data.get("besoin"), str) or not data["besoin"].strip():
        raise ValueError("besoin explicite requis")
    vues = data.get("vues")
    if not isinstance(vues, dict) or not vues:
        raise ValueError("au moins une vue est requise")
    obligatoires = data.get("vues_obligatoires")
    if (
        not isinstance(obligatoires, list)
        or not obligatoires
        or any(not isinstance(v, str) or not v for v in obligatoires)
        or len(set(obligatoires)) != len(obligatoires)
    ):
        raise ValueError("vues_obligatoires doit être une liste non vide de noms uniques")
    seuils_valides(data.get("seuils"))
    result = {
        "version": VERSION,
        "besoin": data["besoin"],
        "vues_obligatoires": obligatoires,
        "seuils": data["seuils"],
        "hypotheses": data.get("hypotheses", []),
        "vues": {},
    }
    import os

    for nom, vue in vues.items():
        if not isinstance(nom, str) or not nom or not isinstance(vue, dict):
            raise ValueError("vue invalide")
        origine = vue.get("origine")
        provenance = vue.get("provenance")
        if origine not in ("fournie", "generee") or not isinstance(provenance, str) or not provenance.strip():
            raise ValueError("origine fournie/generee et provenance explicites requises : " + nom)
        # Une image IA ne devient pas une preuve de dimension ou une vue orthographique par défaut.
        if vue.get("projection") not in ("orthographique", "concept"):
            raise ValueError("projection explicite orthographique ou concept requise : " + nom)
        image = (specification.parent / vue["image"]).resolve()
        m = masque(image, vue.get("mode"), vue.get("seuil_masque"))
        if not m.any():
            raise ValueError("silhouette de référence vide : " + nom)
        result["vues"][nom] = {k: vue[k] for k in ("origine", "provenance", "projection", "mode", "seuil_masque")}
        result["vues"][nom].update(
            image=os.path.relpath(image, destination.parent), sha256=empreinte(image), taille=[m.shape[1], m.shape[0]]
        )
        if vue.get("rendu"):
            rendu = (specification.parent / vue["rendu"]).resolve()
            result["vues"][nom].update(rendu=os.path.relpath(rendu, destination.parent), rendu_sha256=empreinte(rendu))
    result["sources_modele"] = {}
    for chemin in data.get("sources_modele", []):
        source = (specification.parent / chemin).resolve()
        result["sources_modele"][os.path.relpath(source, destination.parent)] = empreinte(source)
    result["limites"] = (
        "Comparaison visuelle 2D ; aucune preuve de cote réelle, profondeur, résistance ou imprimabilité."
    )
    ecrire_json(destination, result)
    return result


def caracteristiques(m):
    """Retourne boîte et centre du masque non vide, en pixels d’image."""
    y, x = np.where(m)
    return np.array([x.min(), y.min(), x.max() + 1, y.max() + 1]), np.array([x.mean(), y.mean()])


def comparer(contrat, rendus, sortie, _data=None):
    """Compare contrat et rendus sans recalage, avec verdicts PASS, FAIL ou NON VÉRIFIÉ."""
    contrat = Path(contrat).resolve()
    sortie = Path(sortie) if sortie is not None else None
    data = _data if _data is not None else json.loads(contrat.read_text(encoding="utf-8"))
    valider_contrat(data)
    if not isinstance(rendus, dict):
        raise ValueError("rendus doit être un dictionnaire vue → chemin")
    if sortie is not None:
        if sortie.exists():
            raise FileExistsError("sortie existante : choisir un nouveau dossier")
        sortie.mkdir(parents=True)
    rapport = {
        "version": VERSION,
        "contrat_sha256": empreinte(contrat),
        "statut": "PASS",
        "vues": {},
        "sources_modele": {},
        "limites": data.get("limites"),
        "alignement_automatique": False,
    }
    for fichier, attendu in data.get("sources_modele", {}).items():
        source = contrat.parent / fichier
        rapport["sources_modele"][fichier] = "PASS" if source.is_file() and empreinte(source) == attendu else "FAIL"
    noms = list(dict.fromkeys(data["vues_obligatoires"] + list(data["vues"])))
    for index, nom in enumerate(noms):
        resultat = {"statut": NON_VERIFIE}
        rapport["vues"][nom] = resultat
        if nom not in data["vues"]:
            resultat["raison"] = "référence obligatoire absente"
            continue
        v = data["vues"][nom]
        source = contrat.parent / v["image"]
        if not source.is_file() or empreinte(source) != v["sha256"]:
            resultat.update(statut="FAIL", raison="référence absente ou empreinte modifiée ; contrat invalidé")
            continue
        if nom not in rendus or not Path(rendus[nom]).is_file():
            resultat["raison"] = "rendu absent"
            continue
        rendu = Path(rendus[nom])
        if v.get("rendu_sha256") and empreinte(rendu) != v["rendu_sha256"]:
            resultat.update(statut="FAIL", raison="rendu modifié depuis le gel")
            continue
        if v.get("projection") != "orthographique":
            # Un concept peut être revu seulement si un rendu réel est présent et traçable.
            try:
                with Image.open(rendu) as image:
                    image.verify()
            except (OSError, ValueError) as erreur:
                resultat.update(statut="FAIL", raison="rendu illisible : " + str(erreur))
                continue
            resultat.update(
                raison="concept visuel : comparaison métrique orthographique non applicable",
                projection="concept",
                metrique_orthographique=False,
                rendu_sha256=empreinte(rendu),
                reference_sha256=v["sha256"],
            )
            continue
        try:
            a = masque(source, v["mode"], v["seuil_masque"])
            b = masque(rendu, v["mode"], v["seuil_masque"])
            if a.shape != b.shape:
                raise ValueError("tailles différentes ; aucun redimensionnement automatique")
            if [a.shape[1], a.shape[0]] != v["taille"]:
                raise ValueError("taille de référence incohérente avec le contrat")
            if not a.any() or not b.any():
                raise ValueError("silhouette vide")
            box_a, centre_a = caracteristiques(a)
            box_b, centre_b = caracteristiques(b)
            iou = float(np.count_nonzero(a & b) / np.count_nonzero(a | b))
            centre = float(np.linalg.norm(centre_a - centre_b))
            bbox = int(np.max(np.abs(box_a - box_b)))
            s = data["seuils"]
            ok = iou >= s["iou_min"] and centre <= s["centre_max_px"] and bbox <= s["bbox_max_px"]
            overlay = np.zeros((*a.shape, 3), dtype=np.uint8)
            overlay[a] = (0, 180, 255)
            overlay[b] = (255, 100, 0)
            overlay[a & b] = (255, 255, 255)
            fichier = f"vue-{index:02d}-superposition.png"
            if sortie is not None:
                Image.fromarray(overlay).save(sortie / fichier)
            resultat.update(
                statut="PASS" if ok else "FAIL",
                iou=iou,
                centre_ecart_px=centre,
                bbox_ecart_max_px=bbox,
                seuils=s,
                superposition=fichier,
                rendu_sha256=empreinte(rendu),
                reference_sha256=v["sha256"],
            )
        except (ValueError, OSError) as erreur:
            resultat.update(statut="FAIL", raison=str(erreur))
    statuts = [v["statut"] for v in rapport["vues"].values()] + list(rapport["sources_modele"].values())
    if not data.get("sources_modele"):
        rapport["liaison_modele"] = NON_VERIFIE
        statuts.append(NON_VERIFIE)
    rapport["statut"] = "FAIL" if "FAIL" in statuts else NON_VERIFIE if NON_VERIFIE in statuts else "PASS"
    if sortie is not None:
        ecrire_json(sortie / "rapport.json", rapport)
    return rapport


def audit_contrat(path, rapport=None):
    """Audit sans écriture des vues et rendus figés ; toute entrée manquante reste explicite."""
    if rapport is not None:
        return audit_evaluation(path, rapport)
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    valider_contrat(data)
    rapport = {"statut": "PASS", "contrat_sha256": empreinte(path), "vues": {}, "sources_modele": {}}
    for fichier, attendu in data.get("sources_modele", {}).items():
        source = path.parent / fichier
        rapport["sources_modele"][fichier] = "PASS" if source.is_file() and empreinte(source) == attendu else "FAIL"
    for nom in dict.fromkeys(data["vues_obligatoires"] + list(data["vues"])):
        v = data["vues"].get(nom)
        r = {"statut": NON_VERIFIE}
        rapport["vues"][nom] = r
        if not v:
            r["raison"] = "référence obligatoire absente"
            continue
        image = path.parent / v["image"]
        if not image.is_file() or empreinte(image) != v["sha256"]:
            r.update(statut="FAIL", raison="référence absente ou modifiée")
            continue
        if v.get("projection") != "orthographique":
            r["raison"] = "concept visuel : appréciation esthétique humaine nécessaire"
            continue
        if not v.get("rendu") or not v.get("rendu_sha256"):
            r["raison"] = "rendu non figé"
            continue
        rendu = path.parent / v["rendu"]
        if not rendu.is_file() or empreinte(rendu) != v["rendu_sha256"]:
            r.update(statut="FAIL", raison="rendu absent ou modifié")
            continue
        try:
            a = masque(image, v["mode"], v["seuil_masque"])
            b = masque(rendu, v["mode"], v["seuil_masque"])
            if a.shape != b.shape or [a.shape[1], a.shape[0]] != v["taille"]:
                raise ValueError("dimensions des images différentes ou contrat incohérent")
            if not a.any() or not b.any():
                raise ValueError("silhouette vide")
            ba, ca = caracteristiques(a)
            bb, cb = caracteristiques(b)
            iou = float(np.count_nonzero(a & b) / np.count_nonzero(a | b))
            centre = float(np.linalg.norm(ca - cb))
            bbox = int(np.max(np.abs(ba - bb)))
            seuils = data["seuils"]
            ok = iou >= seuils["iou_min"] and centre <= seuils["centre_max_px"] and bbox <= seuils["bbox_max_px"]
            r.update(
                statut="PASS" if ok else "FAIL",
                iou=iou,
                centre_ecart_px=centre,
                bbox_ecart_max_px=bbox,
                reference_sha256=v["sha256"],
                rendu_sha256=v["rendu_sha256"],
            )
        except (ValueError, OSError) as erreur:
            r.update(statut="FAIL", raison=str(erreur))
    statuts = [r["statut"] for r in rapport["vues"].values()] + list(rapport["sources_modele"].values())
    if not data.get("sources_modele"):
        rapport["liaison_modele"] = NON_VERIFIE
        statuts.append(NON_VERIFIE)
    rapport["statut"] = "FAIL" if "FAIL" in statuts else NON_VERIFIE if NON_VERIFIE in statuts else "PASS"
    rapport["limites"] = "Silhouettes 2D seulement ; cotes réelles, profondeur et fabrication non validées."
    return rapport


def evaluer(contrat, manifest, sortie=None):
    """Évalue une nouvelle génération sans modifier la référence choisie."""
    import copy

    contrat, manifest = Path(contrat).resolve(), Path(manifest).resolve()
    data = copy.deepcopy(json.loads(contrat.read_text(encoding="utf-8")))
    valider_contrat(data)
    rendu = json.loads(manifest.read_text(encoding="utf-8"))
    if rendu.get("schema") != 1 or not isinstance(rendu.get("sources"), list) or not rendu["sources"]:
        raise ValueError("manifest renderer schema 1 et sources non vides requis")
    entrees = {str(contrat): empreinte(contrat), str(manifest): empreinte(manifest)}
    sources = {}
    for source in rendu["sources"]:
        chemin = (manifest.parent / source["path"]).resolve()
        if not chemin.is_file() or empreinte(chemin) != source["sha256"]:
            raise ValueError("source modèle modifiée depuis le rendu : " + str(chemin))
        entrees[str(chemin)] = source["sha256"]
        sources[str(chemin)] = source["sha256"]
    spec = rendu.get("spec_path")
    if not spec:
        raise ValueError("spec_path requis dans le manifest renderer")
    spec = (manifest.parent / spec).resolve()
    if not spec.is_file() or empreinte(spec) != rendu.get("spec_sha256"):
        raise ValueError("spécification caméra modifiée ou absente")
    from benchab.references_visuelles import rendre_silhouettes as renderer

    camera_spec = json.loads(spec.read_text(encoding="utf-8"))
    output_dir = renderer.validate_spec(camera_spec, spec.parent, check_output=False).resolve()
    if output_dir != manifest.parent:
        raise ValueError("dossier du manifest différent de la sortie de la spécification")
    attendus = sorted((str((spec.parent / m["path"]).resolve()), m["units"]) for m in camera_spec["meshes"])
    observes = sorted((str((manifest.parent / m["path"]).resolve()), m["units"]) for m in rendu["sources"])
    if attendus != observes:
        raise ValueError("sources ou unités du manifest différentes de la spécification caméra")
    if rendu.get("resolution") != camera_spec["resolution"]:
        raise ValueError("résolution du manifest différente de la spécification caméra")
    if set(rendu.get("views", {})) != set(camera_spec["views"]):
        raise ValueError("vues du manifest différentes de la spécification caméra")
    for nom, camera in camera_spec["views"].items():
        if rendu["views"][nom].get("camera") != camera:
            raise ValueError("caméra du manifest différente de la spécification : " + nom)
    entrees[str(spec)] = rendu["spec_sha256"]
    chemins = {}
    for nom, vue in rendu.get("views", {}).items():
        image = (manifest.parent / vue["image"]).resolve()
        if not image.is_file() or empreinte(image) != vue["sha256"]:
            raise ValueError("rendu modifié ou absent : " + nom)
        with Image.open(image) as image_obj:
            if list(image_obj.size) != camera_spec["resolution"]:
                raise ValueError("taille du PNG différente de la résolution déclarée : " + nom)
        chemins[nom] = image
        entrees[str(image)] = vue["sha256"]
    data["sources_modele"] = sources
    for vue in data["vues"].values():
        vue.pop("rendu", None)
        vue.pop("rendu_sha256", None)
    resultat = comparer(contrat, chemins, sortie, _data=data)
    for nom, vue in data["vues"].items():
        entrees[str((contrat.parent / vue["image"]).resolve())] = vue["sha256"]
    resultat.update(
        type="evaluation-visuelle-v1",
        manifest=str(manifest),
        liaison_modele="PASS",
        entrees_sha256=entrees,
        validation_physique=NON_VERIFIE,
    )
    if sortie is not None:
        # Le rapport vient d’être créé dans un nouveau dossier réservé à cette évaluation.
        (Path(sortie) / "rapport.json").write_text(
            json.dumps(resultat, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
    return resultat


def audit_evaluation(contrat, rapport):
    """Rejoue une évaluation liée au contrat et aux SHA des entrées, ou renvoie FAIL si périmée."""
    rapport = Path(rapport).resolve()
    ancien = json.loads(rapport.read_text(encoding="utf-8"))
    if ancien.get("type") != "evaluation-visuelle-v1" or ancien.get("contrat_sha256") != empreinte(contrat):
        return {"statut": "FAIL", "raison": "rapport non lié au contrat courant"}
    if not ancien.get("entrees_sha256"):
        return {"statut": "FAIL", "raison": "rapport sans empreintes des entrées"}
    for nom, attendu in ancien["entrees_sha256"].items():
        path = Path(nom)
        if not path.is_file() or empreinte(path) != attendu:
            return {"statut": "FAIL", "raison": "rapport périmé : " + nom}
    try:
        actuel = evaluer(contrat, ancien["manifest"])
        actuel["rapport_sha256"] = empreinte(rapport)
        return actuel
    except (ValueError, OSError, KeyError, TypeError) as erreur:
        return {"statut": "FAIL", "raison": str(erreur)}


def main(argv=None):
    """Exécute l’action de référence visuelle et renvoie les codes 0, 1 ou 2 selon le résultat."""
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="commande", required=True)
    f = sub.add_parser("figer")
    f.add_argument("specification")
    f.add_argument("contrat")
    c = sub.add_parser("comparer")
    c.add_argument("contrat")
    c.add_argument("--rendus", required=True, help="JSON vue → chemin relatif à ce JSON")
    c.add_argument("--sortie", required=True)
    e = sub.add_parser("evaluer")
    e.add_argument("contrat")
    e.add_argument("--rendus", required=True)
    e.add_argument("--sortie", required=True)
    args = p.parse_args(argv)
    try:
        if args.commande == "figer":
            figer(args.specification, args.contrat)
            print("Contrat figé :", args.contrat)
            return 0
        if args.commande == "evaluer":
            rapport = evaluer(args.contrat, args.rendus, args.sortie)
            print(rapport["statut"])
            return {"PASS": 0, NON_VERIFIE: 1, "FAIL": 2}[rapport["statut"]]
        fichier = Path(args.rendus).resolve()
        chemins = {
            nom: str((fichier.parent / valeur).resolve()) for nom, valeur in json.loads(fichier.read_text()).items()
        }
        rapport = comparer(args.contrat, chemins, args.sortie)
        print(rapport["statut"])
        return {"PASS": 0, NON_VERIFIE: 1, "FAIL": 2}[rapport["statut"]]
    except (ValueError, OSError, KeyError, TypeError) as erreur:
        print("FAIL :", erreur)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
