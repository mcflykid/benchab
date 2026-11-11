"""Aperçu schématique des points de couture contrôlés, sans nouvelle mesure."""

import hashlib
import json
import math
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

from benchab.chemins import atelier_prive, sans_lien
from benchab.qualite.ecritures import destination_neuve, renommer_exclusif

_SVG = "http://www.w3.org/2000/svg"
_COULEURS = {
    "alignee": "#b45309",
    "visible": "#c2410c",
    "cachee": "#0f766e",
    "inconnue": "#475569",
}


def _texte(parent, x, y, texte, *, taille="13", couleur="#243448"):
    noeud = ET.SubElement(parent, "text", x=str(x), y=str(y), fill=couleur, attrib={"font-size": taille})
    noeud.text = texte
    return noeud


def _coord(point, dimensions):
    if not isinstance(point, (list, tuple)) or len(point) != dimensions or any(
        type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e6 for v in point
    ):
        raise ValueError("Coordonnées d’aperçu finies en mm requises.")
    return tuple(point)


def _projection(points, cadre):
    """Conserve l’échelle des deux axes de chaque projection, en pixels par mm."""
    x0, y0, largeur, hauteur = cadre
    xs, ys = zip(*points, strict=True) if points else ((0, 1), (0, 1))
    centre_x = (min(xs) + max(xs)) / 2
    centre_y = (min(ys) + max(ys)) / 2
    echelle = min((largeur - 34) / max(max(xs) - min(xs), 1), (hauteur - 34) / max(max(ys) - min(ys), 1))

    def placer(x, y):
        return x0 + largeur / 2 + (x - centre_x) * echelle, y0 + hauteur / 2 - (y - centre_y) * echelle

    return placer


def _couleur(boucle):
    if boucle.get("statut") == "WARN":
        return _COULEURS["alignee"]
    if boucle.get("statut") == "NON VÉRIFIÉ":
        return _COULEURS["inconnue"]
    return _COULEURS.get(boucle.get("visibilite"), _COULEURS["inconnue"])


def rendre(rapport):
    """Dessine le rapport de couture en SVG, avec axes XY et XZ exprimés en mm.

    Reçoit le rapport structuré de l’analyse ; reprend les points, statuts et
    régions déclarées. L’aperçu ne modifie aucun réglage et ne qualifie ni la
    visibilité physique ni la géométrie entière de la pièce.
    """
    sha = rapport.get("source_sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise ValueError("Empreinte du 3MF source requise pour l’aperçu.")
    regions = [
        (region, [_coord(p, 2) for p in region["polygone_xy_mm"]])
        for region in rapport["regions"]
    ]
    positions = [
        (boucle, _coord(boucle["point_depart_xyz_mm"], 3))
        for boucle in rapport["boucles"] if boucle.get("point_depart_xyz_mm") is not None
    ]
    sans_point = len(rapport["boucles"]) - len(positions)
    svg = ET.Element("svg", {
        "xmlns": _SVG, "viewBox": "0 0 900 480", "data-source-sha256": sha, "role": "img",
        "aria-labelledby": "titre description", "font-family": "system-ui, sans-serif",
    })
    ET.SubElement(svg, "title", id="titre").text = "Coutures — aperçu du contrôle"
    ET.SubElement(svg, "desc", id="description").text = (
        "Deux projections du rapport : régions de visibilité déclarées en XY et points en XY et XZ, en mm."
    )
    ET.SubElement(svg, "rect", width="900", height="480", fill="#f1f5f9")
    _texte(svg, 26, 34, "Coutures — aperçu du contrôle", taille="23", couleur="#172c40")
    objet = rapport["objet_id"]
    libelle = objet if len(objet) <= 66 else objet[:65] + "…"
    _texte(svg, 26, 58, f"Plaque {rapport['plaque']} · {libelle} · {rapport['statut']} · SHA {sha[:12]}")
    for plan, cadre, points, sous_titre in (
        ("XY", (26, 100, 488, 300), [(x, y) for _, (x, y, _) in positions] + [p for _, poly in regions for p in poly],
         "XY — régions déclarées et points (mm)"),
        ("XZ", (538, 100, 336, 300), [(x, z) for _, (x, _, z) in positions], "XZ — positions et hauteurs (mm)"),
    ):
        x0, y0, largeur, hauteur = cadre
        _texte(svg, x0, 89, sous_titre)
        ET.SubElement(svg, "rect", x=str(x0), y=str(y0), width=str(largeur), height=str(hauteur), rx="10",
                      fill="white", stroke="#cbd5e1")
        placer = _projection(points, cadre)
        if plan == "XY":
            for region, poly in regions:
                couleur = _COULEURS.get(region["visibilite"], _COULEURS["inconnue"])
                jeton = " ".join(f"{x:.3f},{y:.3f}" for x, y in (placer(*p) for p in poly))
                zone = ET.SubElement(svg, "polygon", points=jeton, fill=couleur, stroke=couleur,
                                     attrib={"fill-opacity": "0.14", "stroke-dasharray": "4 3"})
                ET.SubElement(zone, "title").text = (
                    f"Région {region['id']} ({region['visibilite']}) ; "
                    f"Z {region['z_min_mm']} à {region['z_max_mm']} mm"
                )
        for boucle, (x, y, z) in positions:
            px, py = placer(x, y if plan == "XY" else z)
            marqueur = ET.SubElement(svg, "circle", {
                "cx": f"{px:.3f}", "cy": f"{py:.3f}", "r": "7" if boucle["statut"] == "WARN" else "5",
                "fill": _couleur(boucle), "data-statut": boucle["statut"],
                "stroke": "white", "data-plan": plan, "data-couche": str(boucle["couche"]),
                "data-ligne": str(boucle["ligne_debut"]),
            })
            region = boucle.get("region_id") or "indéterminée"
            ET.SubElement(marqueur, "title").text = (
                f"Couche {boucle['couche']}, ligne {boucle['ligne_debut']} ; "
                f"X {x:g}, Y {y:g}, Z {z:g} mm ; région {region} ; {boucle['statut']}"
            )
        if not positions:
            _texte(svg, x0 + 16, y0 + 26, "Aucun point tracé")
    for i, (cle, libelle) in enumerate((
        ("alignee", "Visible alignée"), ("visible", "Visible isolée"),
        ("cachee", "Cachée déclarée"), ("inconnue", "Indéterminée"),
    )):
        _texte(svg, 26 + 186 * i, 428, libelle, couleur=_COULEURS[cle])
    _texte(svg, 26, 455, f"{sans_point} boucle sans coordonnées" if sans_point == 1
           else f"{sans_point} boucles sans coordonnées")
    _texte(svg, 538, 455, "Schéma des points ; aucune validation physique")
    return ET.tostring(svg, encoding="unicode") + "\n"


def publier(rapport, sortie, *, prive=None):
    """Publie ensemble le rapport JSON et son SVG dans un dossier neuf.

    Les chemins n’ont pas d’unité. La destination est privée ou temporaire,
    et une collision lève FileExistsError sans réécrire un fichier existant.
    Le statut renvoyé est celui du rapport ; aucun contrôle physique ajouté.
    """
    destination = destination_neuve(sortie, prive=prive)
    svg = rendre(rapport)
    with TemporaryDirectory(prefix=".coutures-", dir=destination.parent) as temporaire:
        dossier = Path(temporaire)
        (dossier / "rapport.json").write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
        (dossier / "apercu.svg").write_text(svg, encoding="utf-8")
        renommer_exclusif(dossier, destination)
    return {"statut": rapport["statut"], "rapport": str(destination / "rapport.json"),
            "apercu": str(destination / "apercu.svg")}


def _cles_uniques(paires):
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError("Clé dupliquée dans la déclaration de couture.")
        resultat[cle] = valeur
    return resultat


def controler_declaration(source, declaration, *, prive=None):
    """Contrôle un 3MF et une déclaration privée liés par SHA, sans écriture.

    Les régions et les points sont en mm ; le rapport distingue PASS, WARN
    et NON VÉRIFIÉ. Les pistes de réglage restent au statut CANDIDAT, sans
    valeur imposée, modification de profil ou validation physique.
    """
    from benchab.controles.coutures import analyser
    from benchab.qualite.plan_reparation import _empreinte_fichier

    racine = atelier_prive(prive)
    chemin = sans_lien(declaration, racine=racine, strict=True)
    preuve = _empreinte_fichier(racine, chemin.relative_to(racine).as_posix(), lecture=True)
    if preuve["statut"] != "PRESENT":
        raise ValueError("Déclaration privée régulière et lisible dans le budget requis.")
    donnees = preuve["contenu"]
    sha = hashlib.sha256(donnees).hexdigest()
    if sha != preuve["sha256"]:
        raise ValueError("La déclaration a changé pendant la lecture.")
    try:
        valeurs = json.loads(donnees, object_pairs_hook=_cles_uniques)
    except RecursionError as exc:
        raise ValueError("Déclaration de couture trop imbriquée.") from exc
    rapport = analyser(source, valeurs)
    rapport["declaration_sha256"] = sha
    rapport["propositions"] = []
    if rapport["groupes_alignes"]:
        if any(region["visibilite"] == "cachee" for region in rapport["regions"]):
            rapport["propositions"].append({
                "statut": "CANDIDAT", "reglage": "seam_position",
                "raison": "Comparer un placement vers une région cachée déclarée ; aucun côté automatique.",
            })
        rapport["propositions"].append({
            "statut": "CANDIDAT", "reglages": ["wipe_on_loops", "wipe_before_external_loop"],
            "raison": "Comparer un essuyage au voisinage de la couture ; il ne déplace pas la couture.",
        })
    return rapport
