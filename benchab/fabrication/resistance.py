"""Estimation statique explicite, pas une certification : traction, console élancée, coque mince.

Unités : N, mm, MPa. Sans effort ou propriété vérifiable : NON VERIFIE, jamais PASS.
Aucun coefficient de motif déduit d'un indice Orca ; aucune modification des modèles.
Les propriétés matière arrivent d'un registre privé explicite et ne sont pas fournies par le paquet public.
"""

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path

LIMITES = [
    "Estimation numérique de sections et charges déclarées ; sections non extraites automatiquement du STL.",
    "Pas de validation physique, fatigue, impact, fluage, flambement, assemblage ou concentration de contraintes.",
    "Les propriétés fabricant sont indicatives ; filament et conditions doivent être représentatifs.",
    "Le coeur non plein ne porte aucune charge dans le calcul prudent sans calibration spécifique.",
    "EF poutre 1D seulement ; aucun calcul volumique 3D de la géométrie réelle annoncé.",
]


def nombre(v, nom, minimum=0, strict=True):
    if (
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(v)
        or (v <= minimum if strict else v < minimum)
    ):
        raise ValueError(nom + " invalide.")
    return float(v)


def section(spec, epaisseur=None):
    forme = spec.get("forme")
    t = nombre(epaisseur if epaisseur is not None else spec.get("epaisseur_mm"), "Épaisseur")
    if forme == "rectangle":
        b = nombre(spec.get("largeur_mm"), "Largeur")
        h = nombre(spec.get("hauteur_mm"), "Hauteur")
        if 2 * t > min(b, h):
            raise ValueError("Épaisseur supérieure à la demi-section.")
        bi, hi = max(0, b - 2 * t), max(0, h - 2 * t)
        ac = bi * hi
        ic = bi * hi**3 / 12
        return {
            "A_parois": b * h - ac,
            "I_parois": b * h**3 / 12 - ic,
            "A_coeur": ac,
            "I_coeur": ic,
            "c": h / 2,
            "hauteur": h,
        }
    if forme == "tube":
        d = nombre(spec.get("diametre_mm"), "Diamètre")
        if 2 * t > d:
            raise ValueError("Épaisseur supérieure au rayon.")
        di = max(0, d - 2 * t)
        ac = math.pi * di**2 / 4
        ic = math.pi * di**4 / 64
        return {
            "A_parois": math.pi * d**2 / 4 - ac,
            "I_parois": math.pi * d**4 / 64 - ic,
            "A_coeur": ac,
            "I_coeur": ic,
            "c": d / 2,
            "hauteur": d,
        }
    raise ValueError("Section rectangle ou tube requise.")


def densite(v):
    if isinstance(v, str):
        v = float(v.rstrip("%"))
    v = nombre(v, "Densité", strict=False)
    if v > 100:
        raise ValueError("Densité supérieure à 100 %.")
    return v / 100


def fleche_ef(force, longueur, rigidite, elements=8):
    """EF Euler-Bernoulli 1D encastré-libre, charge ponctuelle en bout, section constante."""
    import numpy as np
    from scipy.sparse import lil_matrix
    from scipy.sparse.linalg import spsolve

    if isinstance(elements, bool) or not isinstance(elements, int) or not 1 <= elements <= 100:
        raise ValueError("Nombre d’éléments invalide.")
    h = longueur / elements
    k = (
        rigidite
        / h**3
        * np.array(
            [
                [12, 6 * h, -12, 6 * h],
                [6 * h, 4 * h * h, -6 * h, 2 * h * h],
                [-12, -6 * h, 12, -6 * h],
                [6 * h, 2 * h * h, -6 * h, 4 * h * h],
            ]
        )
    )
    K = lil_matrix((2 * (elements + 1), 2 * (elements + 1)))
    f = np.zeros(2 * (elements + 1))
    f[-2] = force
    for e in range(elements):
        ix = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        for i, a in enumerate(ix):
            for j, b in enumerate(ix):
                K[a, b] += k[i, j]
    u = spsolve(K.tocsr()[2:, 2:], f[2:])
    if not np.isfinite(u).all():
        raise ValueError("Système EF non fini.")
    return float(u[-2])


def calculer(cas, reglages=None, registre=None):
    reglages = reglages or {}
    try:
        if not isinstance(registre, dict):
            raise ValueError("Registre matière privé explicite requis ; aucune propriété n'est supposée.")
        if not isinstance(cas, dict):
            raise ValueError("Cas d’effort objet JSON requis.")
        for cle in ("source_effort", "justification_section"):
            if not isinstance(cas.get(cle), str) or not cas[cle].strip():
                raise ValueError(cle + " requis.")
        materiau = registre["materiaux"].get(cas.get("materiau"))
        if not materiau:
            raise ValueError("Matériau non documenté ; aucune substitution PLA implicite.")
        direction = cas.get("direction_couches")
        if direction not in ("XY", "Z"):
            raise ValueError("Direction des contraintes par rapport aux couches XY/Z requise.")
        resistance = nombre(materiau["traction_MPa"].get(direction), "Résistance du matériau")
        young = nombre(materiau["young_MPa"].get(direction), "Module")
        sf_cible = nombre(cas.get("securite_cible", 2), "Sécurité cible")
        if sf_cible < 1:
            raise ValueError("Sécurité cible inférieure à 1.")
        rho = densite(reglages.get("sparse_infill_density", cas.get("densite_pct", 0)))
        motif = str(reglages.get("sparse_infill_pattern", cas.get("motif", "non_declared")))
        ep = None
        if "wall_loops" in reglages:
            parois = nombre(float(reglages["wall_loops"]), "Parois")
            if not parois.is_integer():
                raise ValueError("Nombre entier de parois requis.")
            largeur = nombre(cas.get("largeur_ligne_mm"), "Largeur réelle de ligne requise pour comparer les parois")
            ep = parois * largeur
        sec = section(cas.get("section", {}), ep)
        coef = 1.0 if rho == 1 else 0.0
        A = sec["A_parois"] + coef * sec["A_coeur"]
        inertie = sec["I_parois"] + coef * sec["I_coeur"]
        EIA = young * inertie
        EIB = young * (sec["I_parois"] + rho * sec["I_coeur"])
        modele = cas.get("modele")
        force = nombre(cas.get("force_N"), "Force") if modele != "pression_cylindre" else None
        result = {}
        prudence = []
        if modele == "traction":
            longueur = nombre(cas.get("longueur_mm"), "Longueur")
            sigma = force / A
            result.update(allongement_prudent_mm=force * longueur / (young * A))
        elif modele == "flexion_console":
            longueur = nombre(cas.get("longueur_mm"), "Longueur")
            if longueur / sec["hauteur"] < 10:
                raise ValueError("Console non élancée (L/h < 10) : modèle poutre non applicable.")
            sigma = force * longueur * sec["c"] / inertie
            fleche = force * longueur**3 / (3 * EIA)
            fleche_num = fleche_ef(force, longueur, EIA)
            result.update(
                fleche_prudente_mm=fleche,
                fleche_optimiste_mm=force * longueur**3 / (3 * EIB),
                fleche_ef_1d_mm=fleche_num,
            )
            prudence.append(
                "Flexion : résistance en traction utilisée comme indicateur ; compression, cisaillement et "
                "encastrement non vérifiés."
            )
        elif modele == "pression_cylindre":
            if cas["section"].get("forme") != "tube":
                raise ValueError("Coque cylindrique : section tube requise.")
            t = ep if ep is not None else cas["section"]["epaisseur_mm"]
            rayon = cas["section"]["diametre_mm"] / 2 - t / 2
            if rayon / t < 10:
                raise ValueError("Coque non mince (r/t < 10).")
            pression = nombre(cas.get("pression_MPa"), "Pression")
            sigma = pression * rayon / t
            result.update(contrainte_circonferentielle_MPa=sigma, contrainte_longitudinale_MPa=sigma / 2)
            prudence.append("Pression : fonds, raccords et direction secondaire non vérifiés.")
        else:
            raise ValueError("Modèle traction, flexion_console ou pression_cylindre requis.")
        sf = resistance / sigma
        statut = "FAIL" if sf < sf_cible else "PASS"
        if cas.get("conditions_representatives") is not True:
            prudence.append("Correspondance filament/procédé non établie ; coefficient indicatif seulement.")
        if rho < 1 and motif not in registre["remplissages"]["motifs"]:
            prudence.append("Motif inconnu ; coeur ignoré, aucune propriété de motif inventée.")
        if prudence and statut == "PASS":
            statut = "WARN"
        r = {
            "statut": statut,
            "modele": modele,
            "contrainte_MPa": sigma,
            "coefficient_securite": sf,
            "securite_cible": sf_cible,
            "direction_couches": direction,
            "section": sec,
            "aire_prudente_mm2": A,
            "inertie_prudente_mm4": inertie,
            "remplissage": {
                "motif": motif,
                "densite_pct": rho * 100,
                "fraction_coeur_creditee_resistance": coef,
                "module_coeur_encadrement_MPa": [0.0, rho * young],
            },
            "materiau": cas["materiau"],
            "source_materiau": materiau["source"],
            "source_effort": cas["source_effort"],
            "justification_section": cas["justification_section"],
            "avertissements": prudence,
            "limites": LIMITES,
            **result,
        }
        # Interdit tout rapport JSON NaN ou infini, même en cas de dimensions extrêmes.
        json.dumps(r, allow_nan=False)
        return r
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as e:
        return {"statut": "NON VERIFIE", "detail": str(e), "coefficient_securite": None, "limites": LIMITES}


def lire_parametres(projet):
    p = Path(projet) / "src/parametres.py"
    if not p.exists():
        return {}, None
    # Aucune exécution du code de conception pour lire la déclaration mécanique.
    tree = ast.parse(p.read_text())
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id in ("EFFORTS", "REMPLISSAGE"):
                    try:
                        result[t.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        result[t.id] = None
    return result, hashlib.sha256(p.read_bytes()).hexdigest()


def evaluer_piece(cas, usage="non_declare", reglages=None, registre=None):
    if usage == "decoratif" and not cas:
        return {
            "statut": "NON APPLICABLE",
            "detail": "Usage décoratif explicitement déclaré, sans effort.",
            "coefficient_securite": None,
        }
    if not cas:
        return {
            "statut": "NON VERIFIE",
            "detail": "EFFORTS absent pour cette pièce ; aucune résistance suffisante annoncée.",
            "coefficient_securite": None,
        }
    if not isinstance(cas, list):
        cas = [cas]
    lignes = [calculer(c, reglages, registre) for c in cas]
    priorite = {"FAIL": 3, "NON VERIFIE": 2, "WARN": 1, "PASS": 0}
    pire = max(lignes, key=lambda r: priorite[r["statut"]])
    sfs = [x["coefficient_securite"] for x in lignes if x.get("coefficient_securite") is not None]
    return {"statut": pire["statut"], "coefficient_securite": min(sfs) if sfs else None, "cas": lignes}


def reglages_3mf(path):
    """Lit les réglages typés globaux/objets en unités natives ; aucun automatique inféré, aucune modification."""
    import xml.etree.ElementTree as ET
    import zipfile

    from benchab import reglages as schema_reglages

    if not path:
        return {}, {}, "Aucun 3MF fourni : paramètres mécaniques déclaratifs seulement."
    with zipfile.ZipFile(path) as z:
        key = "Metadata/project_settings.config"
        if key not in z.namelist():
            return {}, {}, "Réglages du 3MF absents : correspondance non vérifiée."
        globaux = schema_reglages.decoder(z.read(key))
        if not isinstance(globaux, dict):
            raise ValueError("Réglages 3MF invalides.")
        objets = {}
        if "Metadata/model_settings.config" in z.namelist():
            xml = ET.fromstring(z.read("Metadata/model_settings.config"))
            for obj in xml.findall(".//object"):
                valeurs = {m.get("key"): m.get("value") for m in obj.findall("./metadata")}
                nom = valeurs.pop("name", None)
                if nom:
                    nom = Path(nom).stem
                    if nom in objets:
                        raise ValueError("Nom d’objet 3MF ambigu : " + nom)
                    objets[nom] = schema_reglages.Reglages(valeurs, contexte={"portee": "objet"})
        return globaux, objets, None


def auditer_projet(projet, tranche=None, registre=None):
    projet = Path(projet)
    declarations, h = lire_parametres(projet)
    efforts = declarations.get("EFFORTS") or {}
    usage = declarations.get("REMPLISSAGE") or {}
    if not isinstance(efforts, dict) or not isinstance(usage, dict):
        return {
            "statut": "WARN",
            "detail": "Déclarations littérales EFFORTS/REMPLISSAGE par pièce requises.",
            "pieces": {},
            "limites": LIMITES,
        }
    noms = set(efforts) | set(usage)
    noms.discard("*")
    manifeste = projet / "candidats/assemblage.json"
    if manifeste.exists():
        data = json.loads(manifeste.read_text())
        if not isinstance(data, dict):
            raise ValueError("Manifeste assemblage invalide.")
        if isinstance(data.get("pieces"), dict):
            noms.update(data["pieces"])
        for plaque in data.get("plaques", []):
            for it in plaque.get("items", []):
                if it.get("piece"):
                    noms.add(it["piece"])
    if not noms:
        noms = {"*"}
    globaux, objets, incertitude = reglages_3mf(tranche)
    pieces = {}
    for nom in sorted(noms):
        u = usage.get(nom, usage.get("*", "non_declare"))
        u = u.get("usage", "non_declare") if isinstance(u, dict) else u
        reg = dict(globaux, **objets.get(nom, {}))
        # Si des surcharges existent sans correspondance, ne pas supposer les réglages globaux applicables.
        correspondance = incertitude or (
            "Surcharges 3MF non rattachées à la pièce." if objets and nom not in objets else None
        )
        pieces[nom] = evaluer_piece(efforts.get(nom, efforts.get("*")), u, reg, registre)
        if correspondance and pieces[nom]["statut"] not in ("FAIL", "NON APPLICABLE", "NON VERIFIE"):
            pieces[nom]["statut"] = "WARN"
            pieces[nom]["reglages_non_verifies"] = correspondance
    statut = (
        "FAIL"
        if any(x["statut"] == "FAIL" for x in pieces.values())
        else "WARN"
        if any(x["statut"] in ("NON VERIFIE", "WARN") for x in pieces.values())
        else "PASS"
    )
    return {
        "schema": 1,
        "statut": statut,
        "pieces": pieces,
        "parametres_sha256": h,
        "tranche_sha256": hashlib.sha256(Path(tranche).read_bytes()).hexdigest() if tranche else None,
        "materiaux_sha256": hashlib.sha256(
            json.dumps(registre, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
        ).hexdigest()
        if isinstance(registre, dict)
        else None,
        "limites": LIMITES,
    }


def cle_classement(variante):
    st = variante.get("resistance_calculee", {}).get("statut", "NON VERIFIE")
    rang = {"PASS": 0, "NON APPLICABLE": 0, "WARN": 1, "NON VERIFIE": 2, "FAIL": 3}.get(st, 3)
    return (bool(variante.get("alerte_imprimabilite")), rang, variante["heures"], variante["grammes"])


def main(argv=None, *, prive=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--projet", type=Path, required=True)
    p.add_argument("--json", type=Path, required=True)
    p.add_argument("--tranche", type=Path)
    p.add_argument("--materiaux", type=Path, required=True)
    a = p.parse_args(argv)
    try:
        resultat = json.loads(a.materiaux.read_text(encoding="utf-8"))
        if not isinstance(resultat, dict) or not isinstance(resultat.get("materiaux"), dict):
            raise ValueError("Registre matière invalide.")
        brut = a.json.expanduser()
        if any(element.is_symlink() for element in (brut, *brut.parents)):
            raise ValueError("Lien symbolique dans le chemin du rapport refusé.")
        cible = brut.resolve()
        racine_privee = (
            Path(prive).expanduser().resolve(strict=True)
            if prive is not None
            else Path(__file__).resolve().parents[2] / "prive"
        )
        if not cible.is_relative_to(racine_privee) and not cible.is_relative_to(Path("/private/tmp")):
            raise ValueError("Le rapport doit rester dans prive/ ou /private/tmp.")
        if cible.exists() or cible.is_symlink():
            raise FileExistsError("Le rapport existe déjà : aucun écrasement.")
        cible.parent.mkdir(parents=True, exist_ok=True)
        r = auditer_projet(a.projet, a.tranche, resultat)
        with cible.open("x", encoding="utf-8") as flux:
            json.dump(r, flux, indent=2, ensure_ascii=False, allow_nan=False)
        print("résistance :", r["statut"], "—", [(n, x["statut"]) for n, x in r["pieces"].items()])
        return 2 if r["statut"] == "FAIL" else 1 if r["statut"] == "WARN" else 0
    except (OSError, ValueError, SyntaxError, TypeError) as e:
        p.exit(2, "ERREUR : " + str(e) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
