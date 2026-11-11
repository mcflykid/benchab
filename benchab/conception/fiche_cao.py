"""Transforme une fiche cotée en plan et script CAO sans exécuter le script."""

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from benchab.chemins import sans_lien
from benchab.qualite.ecritures import destination_neuve, renommer_exclusif

_MAX_FICHE = 16 * 1024
# Écarter les tangences numériques, sans imposer de paroi d'impression.
_MARGE_MM = 1e-6


@dataclass(frozen=True, slots=True)
class _Lamage:
    """Conserve les cotes nominales de l’entrée (mm) et leur source déclarée."""

    rayon: float
    profondeur: float
    source_cotes: str


@dataclass(frozen=True, slots=True)
class _Fraisage:
    """Conserve rayon et profondeur nominale en mm, angle inclus en degrés et source."""

    rayon: float
    angle_inclus: float
    profondeur: float
    source_cotes: str


@dataclass(frozen=True, slots=True)
class _Percage:
    """Décrit le passage traversant et son éventuel élargissement supérieur, en mm."""

    x: float
    y: float
    rayon: float
    lamage: _Lamage | None
    fraisage: _Fraisage | None


@dataclass(frozen=True, slots=True)
class _Poche:
    """Décrit une poche supérieure alignée sur les axes, en mm."""

    x: float
    y: float
    largeur_x: float
    largeur_y: float
    profondeur: float
    source_cotes: str
    rayon_angle: float


def _rayon_entree(trou):
    """Renvoie le rayon XY maximal au bord supérieur, en mm."""
    entree = trou.lamage or trou.fraisage
    return entree.rayon if entree else trou.rayon


def _paires_uniques(paires):
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError(f"Clé JSON dupliquée : {cle[:80]}")
        resultat[cle] = valeur
    return resultat


def _signature(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _lire(source):
    chemin = sans_lien(source, strict=True)
    if chemin.suffix.lower() != ".json":
        raise ValueError("Une fiche .json est requise.")
    fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > _MAX_FICHE:
            raise ValueError("Une fiche régulière de 16 Kio au plus est requise.")
        brut = flux.read(_MAX_FICHE + 1)
        apres = os.fstat(flux.fileno())
        courant = chemin.lstat()
        if _signature(avant) != _signature(apres) or _signature(apres) != _signature(courant):
            raise ValueError("La fiche a changé pendant la lecture.")
    if len(brut) > _MAX_FICHE:
        raise ValueError("Une fiche de 16 Kio au plus est requise.")
    try:
        return json.loads(brut.decode("utf-8"), object_pairs_hook=_paires_uniques)
    except RecursionError as exc:
        raise ValueError("Fiche JSON trop imbriquée.") from exc


def _cote(valeur, nom, *, longueur):
    minimum = 0.1 if longueur else -1000
    if type(valeur) not in (int, float) or not minimum <= valeur <= 1000:
        raise ValueError(f"{nom} : cote finie entre {minimum} et 1000 mm requise.")
    return float(valeur) if valeur else 0.0


def _source_cotes(source, etiquette):
    """Conserve une source courte dans le plan, sans l’injecter dans le script."""
    if not isinstance(source, str) or not source.strip() or len(source) > 240 or not source.isprintable():
        raise ValueError(f"{etiquette} : source des cotes sur une ligne de 240 caractères au plus.")
    return source


def _normaliser_lamage(lamage, numero, rayon_passage, epaisseur):
    """Valide un épaulement annulaire avec fond restant, à cotes et source déclarées."""
    if not isinstance(lamage, dict) or set(lamage) != {"diametre_mm", "profondeur_mm", "source_cotes"}:
        raise ValueError(f"Lamage {numero} : diamètre, profondeur et source des cotes requis.")
    rayon = _cote(lamage["diametre_mm"], f"lamage {numero} diamètre", longueur=True) / 2
    profondeur = _cote(lamage["profondeur_mm"], f"lamage {numero} profondeur", longueur=True)
    source = _source_cotes(lamage["source_cotes"], f"Lamage {numero}")
    if rayon - rayon_passage <= _MARGE_MM:
        raise ValueError(f"Lamage {numero} : diamètre supérieur à celui du passage requis.")
    if epaisseur - profondeur <= _MARGE_MM:
        raise ValueError(f"Lamage {numero} : profondeur inférieure à l’épaisseur du bloc requise.")
    return _Lamage(rayon, profondeur, source)


def _normaliser_fraisage(fraisage, numero, rayon_passage, epaisseur):
    """Déduit la profondeur du tronc de cône à partir du diamètre et de l’angle inclus."""
    etiquette = f"Fraisage {numero}"
    if not isinstance(fraisage, dict) or set(fraisage) != {"diametre_mm", "angle_inclus_deg", "source_cotes"}:
        raise ValueError(f"{etiquette} : diamètre, angle inclus et source des cotes requis.")
    rayon = _cote(fraisage["diametre_mm"], f"{etiquette} diamètre", longueur=True) / 2
    angle = fraisage["angle_inclus_deg"]
    if type(angle) not in (int, float) or not 30 <= angle <= 150:
        raise ValueError(f"{etiquette} : angle inclus fini entre 30 et 150 degrés requis.")
    source = _source_cotes(fraisage["source_cotes"], etiquette)
    if rayon - rayon_passage <= _MARGE_MM:
        raise ValueError(f"{etiquette} : diamètre supérieur à celui du passage requis.")
    profondeur = (rayon - rayon_passage) / math.tan(math.radians(angle) / 2)
    if not _MARGE_MM < profondeur < epaisseur - _MARGE_MM:
        raise ValueError(f"{etiquette} : profondeur positive et inférieure à l’épaisseur de la base requise.")
    return _Fraisage(rayon, float(angle), profondeur, source)



def _ecart_poche(x, y, demi_x, demi_y, rayon):
    """Renvoie l’écart en mm au rectangle arrondi centré, négatif à l’intérieur."""
    # Le contour est celui du noyau rectangulaire élargi par un disque ;
    # mesurer la distance au noyau évite une fausse collision au coin (#32).
    qx = abs(x) - (demi_x - rayon)
    qy = abs(y) - (demi_y - rayon)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - rayon

def _normaliser_poches(valeurs, bloc, percages, rayon_base=None):
    """Valide le fond, la marge XY et les intersections au dessus du bloc."""
    if not isinstance(valeurs, list) or len(valeurs) > 4:
        raise ValueError("De zéro à quatre poches rectangulaires sont acceptées.")
    cles = {"x_mm", "y_mm", "largeur_x_mm", "largeur_y_mm", "profondeur_mm", "source_cotes"}
    dx, dy, dz = bloc
    poches = []
    for numero, data in enumerate(valeurs, 1):
        etiquette = f"Poche {numero}"
        if not isinstance(data, dict) or not cles <= set(data) <= cles | {"rayon_angle_mm"}:
            raise ValueError(f"{etiquette} : centre XY, deux largeurs, profondeur et source des cotes requis.")
        x = _cote(data["x_mm"], f"{etiquette} x", longueur=False)
        y = _cote(data["y_mm"], f"{etiquette} y", longueur=False)
        lx = _cote(data["largeur_x_mm"], f"{etiquette} largeur x", longueur=True)
        ly = _cote(data["largeur_y_mm"], f"{etiquette} largeur y", longueur=True)
        profondeur = _cote(data["profondeur_mm"], f"{etiquette} profondeur", longueur=True)
        source = _source_cotes(data["source_cotes"], etiquette)
        rayon = 0.0
        if "rayon_angle_mm" in data:
            rayon = _cote(data["rayon_angle_mm"], f"{etiquette} rayon d’angle", longueur=True)
            if min(lx, ly) / 2 - rayon <= _MARGE_MM:
                raise ValueError(f"{etiquette} : rayon d’angle strictement inférieur à chaque demi-largeur.")
        if rayon_base is None:
            marge = min((dx - lx) / 2 - abs(x), (dy - ly) / 2 - abs(y))
        else:
            # Le point le plus loin du centre de base appartient au coin
            # du noyau, puis au rayon de l’arrondi dans la même direction.
            marge = rayon_base - (math.hypot(abs(x) + lx / 2 - rayon, abs(y) + ly / 2 - rayon) + rayon)
        if marge <= _MARGE_MM:
            raise ValueError(f"{etiquette} : marge insuffisante au bord de la base.")
        if dz - profondeur <= _MARGE_MM:
            raise ValueError(f"{etiquette} : profondeur inférieure à l’épaisseur du bloc requise.")
        for trou in percages:
            if _ecart_poche(x - trou.x, y - trou.y, lx / 2, ly / 2, rayon) - _rayon_entree(trou) <= _MARGE_MM:
                raise ValueError(
                    f"{etiquette} : chevauchement ou tangence avec un perçage, son lamage ou son fraisage."
                )
        for voisine in poches:
            demi_x = (lx + voisine.largeur_x) / 2
            demi_y = (ly + voisine.largeur_y) / 2
            rayon_total = rayon + voisine.rayon_angle
            if rayon_total:
                touche = _ecart_poche(x - voisine.x, y - voisine.y, demi_x, demi_y, rayon_total) <= _MARGE_MM
            else:
                touche = abs(x - voisine.x) - demi_x <= _MARGE_MM and abs(y - voisine.y) - demi_y <= _MARGE_MM
            if touche:
                raise ValueError(f"{etiquette} : chevauchement ou tangence entre deux poches.")
        poches.append(_Poche(x, y, lx, ly, profondeur, source, rayon))
    return poches


def _normaliser(data):
    """Lit une base unique et des opérations disjointes, avec toutes les cotes en mm."""
    requis = {"schema", "id", "percages"}
    bases = {"bloc_mm", "base_cylindrique"}
    if (
        not isinstance(data, dict)
        or not requis <= data.keys() <= requis | bases | {"hors_perimetre", "poches_rectangulaires"}
        or len(bases & data.keys()) != 1
        or type(data["schema"]) is not int or data["schema"] != 1
    ):
        raise ValueError("Fiche schema 1 : id, une base bloc_mm ou base_cylindrique et percages requis.")
    identifiant = data["id"]
    if not isinstance(identifiant, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", identifiant):
        raise ValueError("Identifiant simple de 1 à 48 caractères requis.")
    rayon_base = None
    if "bloc_mm" in data:
        if not isinstance(data["bloc_mm"], list) or len(data["bloc_mm"]) != 3:
            raise ValueError("Trois dimensions du bloc en mm sont requises.")
        bloc = [_cote(x, "dimension du bloc", longueur=True) for x in data["bloc_mm"]]
    else:
        base = data["base_cylindrique"]
        if not isinstance(base, dict) or set(base) != {"diametre_mm", "epaisseur_mm"}:
            raise ValueError("Base cylindrique : diamètre et épaisseur en mm requis.")
        diametre = _cote(base["diametre_mm"], "diamètre de base", longueur=True)
        rayon_base = diametre / 2
        bloc = [diametre, diametre, _cote(base["epaisseur_mm"], "épaisseur de base", longueur=True)]
    dx, dy, dz = bloc
    trous = data["percages"]
    if not isinstance(trous, list) or len(trous) > 8:
        raise ValueError("De zéro à huit perçages sont acceptés.")
    percages = []
    cles_trou = {"x_mm", "y_mm", "diametre_mm"}
    for numero, trou in enumerate(trous, 1):
        if not isinstance(trou, dict) or not cles_trou <= trou.keys() <= cles_trou | {"lamage", "fraisage"}:
            raise ValueError(f"Perçage {numero} : x_mm, y_mm et diametre_mm requis.")
        if {"lamage", "fraisage"} <= trou.keys():
            raise ValueError(f"Perçage {numero} : lamage et fraisage sont exclusifs.")
        x = _cote(trou["x_mm"], f"perçage {numero} x", longueur=False)
        y = _cote(trou["y_mm"], f"perçage {numero} y", longueur=False)
        rayon = _cote(trou["diametre_mm"], f"perçage {numero} diamètre", longueur=True) / 2
        lamage = _normaliser_lamage(trou["lamage"], numero, rayon, dz) if "lamage" in trou else None
        fraisage = _normaliser_fraisage(trou["fraisage"], numero, rayon, dz) if "fraisage" in trou else None
        operation = _Percage(x, y, rayon, lamage, fraisage)
        marge = min(dx / 2 - abs(x), dy / 2 - abs(y)) if rayon_base is None else rayon_base - math.hypot(x, y)
        if marge - _rayon_entree(operation) <= _MARGE_MM:
            raise ValueError(f"Perçage {numero} : marge insuffisante au bord de la base.")
        percages.append(operation)
    for premier, second in itertools.combinations(percages, 2):
        distance = math.hypot(premier.x - second.x, premier.y - second.y)
        if distance - _rayon_entree(premier) - _rayon_entree(second) <= _MARGE_MM:
            raise ValueError("Perçages : chevauchement ou tangence entre deux trous.")
    exclus = data.get("hors_perimetre", [])
    if not isinstance(exclus, list) or len(exclus) > 8 or any(
        not isinstance(exigence, str) or not exigence.strip() or len(exigence) > 240
        or not exigence.isprintable() for exigence in exclus
    ):
        raise ValueError("Au plus huit exigences hors périmètre, une ligne de 240 caractères chacune.")
    poches = _normaliser_poches(data.get("poches_rectangulaires", []), bloc, percages, rayon_base)
    return identifiant, bloc, rayon_base, percages, poches, exclus


def _script(bloc, trous, poches, rayon_base=None):
    """Émet les seules opérations couvertes, sous la forme d’un script fermé en mm."""
    # Gabarit fermé : seules les cotes normalisées deviennent des littéraux Python.
    construction = (
        f"Box({bloc[0]!r}, {bloc[1]!r}, {bloc[2]!r}, " if rayon_base is None
        else f"Cylinder({rayon_base!r}, {bloc[2]!r}, "
    )
    imports = (
        "from build123d import Align, Box, Cone, Cylinder, Pos"
        if any(trou.fraisage for trou in trous) else "from build123d import Align, Box, Cylinder, Pos"
    )
    if any(poche.rayon_angle for poche in poches):
        imports += ", RectangleRounded, extrude"
    lignes = [
        '"""Bloc CAO en mm issu d’une fiche BENCHAB ; aucune validation physique."""',
        "",
        imports,
        "from cadgen.engine import step",
        "",
        "",
        '@step(out="modele.step")',
        "def construire():",
        '    """Retourne le solide nominal ; origine XY centrée et base à z=0 mm."""',
        f"    solide = {construction}align=(Align.CENTER, Align.CENTER, Align.MIN))",
    ]
    for trou in trous:
        x, y, rayon = trou.x, trou.y, trou.rayon
        # Le cylindre dépasse les deux faces pour éviter un booléen coplanaire.
        lignes.extend([
            f"    solide -= Pos({x!r}, {y!r}, -1.0) * Cylinder(",
            f"        {rayon!r}, {bloc[2] + 2!r}, align=(Align.CENTER, Align.CENTER, Align.MIN)",
            "    )",
        ])
        if trou.lamage:
            # Le cylindre d’entrée dépasse le dessus mais s’arrête à la cote
            # du fond. Le passage déjà soustrait ne compte qu'une fois.
            lamage = trou.lamage
            lignes.extend([
                f"    solide -= Pos({x!r}, {y!r}, {bloc[2] - lamage.profondeur!r}) * Cylinder(",
                f"        {lamage.rayon!r}, {lamage.profondeur + 1!r}, align=(Align.CENTER, Align.CENTER, Align.MIN)",
                "    )",
            ])
        if trou.fraisage:
            # Dépasser le dessus d’un mm sans changer la pente, afin
            # d’éviter une coupe coplanaire au diamètre d’entrée.
            fraisage = trou.fraisage
            rayon_haut = fraisage.rayon + (fraisage.rayon - rayon) / fraisage.profondeur
            lignes.extend([
                f"    solide -= Pos({x!r}, {y!r}, {bloc[2] - fraisage.profondeur!r}) * Cone(",
                f"        {rayon!r}, {rayon_haut!r}, {fraisage.profondeur + 1!r},",
                "        align=(Align.CENTER, Align.CENTER, Align.MIN)",
                "    )",
            ])
    for poche in poches:
        # L'outil dépasse le dessus comme le cylindre du lamage ; le fond
        # reste à l'altitude explicite plutôt qu'au milieu du bloc.
        if poche.rayon_angle:
            lignes.extend([
                f"    solide -= Pos({poche.x!r}, {poche.y!r}, {bloc[2] - poche.profondeur!r}) * extrude(",
                f"        RectangleRounded({poche.largeur_x!r}, {poche.largeur_y!r}, {poche.rayon_angle!r}),",
                f"        amount={poche.profondeur + 1!r},",
                "    )",
            ])
        else:
            lignes.extend([
                f"    solide -= Pos({poche.x!r}, {poche.y!r}, {bloc[2] - poche.profondeur!r}) * Box(",
                f"        {poche.largeur_x!r}, {poche.largeur_y!r}, {poche.profondeur + 1!r},",
                "        align=(Align.CENTER, Align.CENTER, Align.MIN)",
                "    )",
            ])
    return "\n".join([*lignes, "    return solide", ""])


def _generer(source, sortie, *, prive):
    identifiant, bloc, rayon_base, trous, poches, exclus = _normaliser(_lire(source))
    destination = destination_neuve(sortie, prive=prive)
    script = _script(bloc, trous, poches, rayon_base)
    dx, dy, dz = bloc
    aire_base = dx * dy if rayon_base is None else math.pi * rayon_base**2
    volume_lamages = math.fsum(
        math.pi * (trou.lamage.rayon**2 - trou.rayon**2) * trou.lamage.profondeur
        for trou in trous if trou.lamage
    )
    # Le passage cylindrique est déjà retiré dans aire_base moins les trous.
    volume_fraisages = math.fsum(
        math.pi * f.profondeur * ((f.rayon - trou.rayon) * (f.rayon + 2 * trou.rayon) / 3)
        for trou in trous if (f := trou.fraisage)
    )
    # Quatre carrés de côté r laissent quatre quarts de disque de rayon r.
    volume_poches = math.fsum(
        (poche.largeur_x * poche.largeur_y - (4 - math.pi) * poche.rayon_angle**2) * poche.profondeur
        for poche in poches
    )
    attendu = {
        "dimensions_mm": bloc,
        "solides": 1,
        "volume_mm3": (
            (aire_base - math.fsum(math.pi * trou.rayon**2 for trou in trous)) * dz
            - volume_lamages - volume_poches - volume_fraisages
        ),
    }
    statut = "NON VÉRIFIÉ" if exclus else "PASS"
    plan = {
        "schema": 1, "id": identifiant, "statut": statut,
        "portee": "Gabarit nominal : script généré, aucun STEP construit ni validation physique.",
        **({"bloc_mm": bloc} if rayon_base is None else {"base_cylindrique": {"diametre_mm": dx, "epaisseur_mm": dz}}),
        "percages": [
            {"x_mm": trou.x, "y_mm": trou.y, "diametre_mm": 2 * trou.rayon}
            | ({"lamage": {
                "diametre_mm": 2 * trou.lamage.rayon,
                "profondeur_mm": trou.lamage.profondeur,
                "source_cotes": trou.lamage.source_cotes,
            }} if trou.lamage else {})
            | ({"fraisage": {
                "diametre_mm": 2 * trou.fraisage.rayon,
                "angle_inclus_deg": trou.fraisage.angle_inclus,
                "profondeur_calculee_mm": trou.fraisage.profondeur,
                "source_cotes": trou.fraisage.source_cotes,
            }} if trou.fraisage else {})
            for trou in trous
        ],
        **({"poches_rectangulaires": [
            {"x_mm": p.x, "y_mm": p.y, "largeur_x_mm": p.largeur_x, "largeur_y_mm": p.largeur_y,
             "profondeur_mm": p.profondeur, "source_cotes": p.source_cotes}
            | ({"rayon_angle_mm": p.rayon_angle} if p.rayon_angle else {})
            for p in poches
        ]} if poches else {}),
        "attendu": attendu, "exigences_non_verifiees": exclus,
        "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
    }
    # Deux fichiers solidaires sont publiés ensemble, sans écraser une version.
    with TemporaryDirectory(prefix=".fiche-cao-", dir=destination.parent) as temporaire:
        dossier = Path(temporaire)
        (dossier / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (dossier / "modele.py").write_text(script, encoding="utf-8")
        renommer_exclusif(dossier, destination)
    resume = {"statut": statut, "rapport": str(destination / "plan.json"), "script": str(destination / "modele.py")}
    return resume, plan


def generer(source, sortie, *, prive=None):
    """Publie un plan nominal (mm, mm³) et un script dans un dossier neuf.

    Lit la fiche JSON sans exécuter son contenu ni écrire à la source. Le plan
    donne PASS pour le gabarit ou NON VÉRIFIÉ si une exigence est hors portée ;
    une entrée invalide lève ValueError et une collision FileExistsError.
    La construction du solide et toute validation physique restent distinctes.
    """
    resume, _ = _generer(source, sortie, prive=prive)
    return resume


def main(argv=None, *, prive=None):
    """Affiche un résumé JSON ; codes 0 PASS, 2 NON VÉRIFIÉ, 1 refus, sans construction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fiche", type=Path)
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--detail", action="store_true", help="afficher aussi le plan complet")
    args = parser.parse_args(argv)
    try:
        resume, plan = _generer(args.fiche, args.sortie, prive=prive)
        if args.detail:
            resume["plan"] = plan
    except (ValueError, OSError) as exc:
        print(f"Fiche CAO : {exc}", file=sys.stderr)
        return 1
    print(json.dumps(resume, ensure_ascii=False, indent=2))
    return 0 if resume["statut"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
