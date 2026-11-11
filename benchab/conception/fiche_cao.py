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
class _Percage:
    """Décrit le passage traversant et son éventuel lamage supérieur, en mm."""

    x: float
    y: float
    rayon: float
    lamage: _Lamage | None


@dataclass(frozen=True, slots=True)
class _Poche:
    """Décrit une poche supérieure alignée sur les axes, en mm."""

    x: float
    y: float
    largeur_x: float
    largeur_y: float
    profondeur: float
    source_cotes: str


def _rayon_entree(trou):
    """Renvoie le rayon XY maximal au bord supérieur, en mm."""
    return trou.lamage.rayon if trou.lamage else trou.rayon


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


def _normaliser_poches(valeurs, bloc, percages):
    """Valide le fond, la marge XY et les intersections au dessus du bloc."""
    if not isinstance(valeurs, list) or len(valeurs) > 4:
        raise ValueError("De zéro à quatre poches rectangulaires sont acceptées.")
    cles = {"x_mm", "y_mm", "largeur_x_mm", "largeur_y_mm", "profondeur_mm", "source_cotes"}
    dx, dy, dz = bloc
    poches = []
    for numero, data in enumerate(valeurs, 1):
        etiquette = f"Poche {numero}"
        if not isinstance(data, dict) or set(data) != cles:
            raise ValueError(f"{etiquette} : centre XY, deux largeurs, profondeur et source des cotes requis.")
        x = _cote(data["x_mm"], f"{etiquette} x", longueur=False)
        y = _cote(data["y_mm"], f"{etiquette} y", longueur=False)
        lx = _cote(data["largeur_x_mm"], f"{etiquette} largeur x", longueur=True)
        ly = _cote(data["largeur_y_mm"], f"{etiquette} largeur y", longueur=True)
        profondeur = _cote(data["profondeur_mm"], f"{etiquette} profondeur", longueur=True)
        source = _source_cotes(data["source_cotes"], etiquette)
        if min((dx - lx) / 2 - abs(x), (dy - ly) / 2 - abs(y)) <= _MARGE_MM:
            raise ValueError(f"{etiquette} : marge insuffisante au bord du bloc.")
        if dz - profondeur <= _MARGE_MM:
            raise ValueError(f"{etiquette} : profondeur inférieure à l’épaisseur du bloc requise.")
        for trou in percages:
            # Le point le plus proche sur le rectangle distingue un coin
            # dégagé d'une vraie intersection avec le cercle du passage.
            hx = max(0.0, abs(x - trou.x) - lx / 2)
            hy = max(0.0, abs(y - trou.y) - ly / 2)
            if math.hypot(hx, hy) - _rayon_entree(trou) <= _MARGE_MM:
                raise ValueError(f"{etiquette} : chevauchement ou tangence avec un perçage ou son lamage.")
        for voisine in poches:
            if (
                abs(x - voisine.x) - (lx + voisine.largeur_x) / 2 <= _MARGE_MM
                and abs(y - voisine.y) - (ly + voisine.largeur_y) / 2 <= _MARGE_MM
            ):
                raise ValueError(f"{etiquette} : chevauchement ou tangence entre deux poches.")
        poches.append(_Poche(x, y, lx, ly, profondeur, source))
    return poches


def _normaliser(data):
    requis = {"schema", "id", "bloc_mm", "percages"}
    if (
        not isinstance(data, dict)
        or not requis <= data.keys() <= requis | {"hors_perimetre", "poches_rectangulaires"}
        or type(data["schema"]) is not int or data["schema"] != 1
    ):
        raise ValueError("Fiche schema 1 : id, bloc_mm et percages requis ; poches et exclusions facultatives.")
    identifiant = data["id"]
    if not isinstance(identifiant, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", identifiant):
        raise ValueError("Identifiant simple de 1 à 48 caractères requis.")
    if not isinstance(data["bloc_mm"], list) or len(data["bloc_mm"]) != 3:
        raise ValueError("Trois dimensions du bloc en mm sont requises.")
    dx, dy, dz = bloc = [_cote(x, "dimension du bloc", longueur=True) for x in data["bloc_mm"]]
    trous = data["percages"]
    if not isinstance(trous, list) or len(trous) > 8:
        raise ValueError("De zéro à huit perçages sont acceptés.")
    percages = []
    cles_trou = {"x_mm", "y_mm", "diametre_mm"}
    for numero, trou in enumerate(trous, 1):
        if not isinstance(trou, dict) or not cles_trou <= trou.keys() <= cles_trou | {"lamage"}:
            raise ValueError(f"Perçage {numero} : x_mm, y_mm et diametre_mm requis.")
        x = _cote(trou["x_mm"], f"perçage {numero} x", longueur=False)
        y = _cote(trou["y_mm"], f"perçage {numero} y", longueur=False)
        rayon = _cote(trou["diametre_mm"], f"perçage {numero} diamètre", longueur=True) / 2
        lamage = _normaliser_lamage(trou["lamage"], numero, rayon, dz) if "lamage" in trou else None
        operation = _Percage(x, y, rayon, lamage)
        if min(dx / 2 - abs(x), dy / 2 - abs(y)) - _rayon_entree(operation) <= _MARGE_MM:
            raise ValueError(f"Perçage {numero} : marge insuffisante au bord du bloc.")
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
    poches = _normaliser_poches(data.get("poches_rectangulaires", []), bloc, percages)
    return identifiant, bloc, percages, poches, exclus


def _script(bloc, trous, poches):
    # Gabarit fermé : seules les cotes normalisées deviennent des littéraux Python.
    lignes = [
        '"""Bloc CAO en mm issu d’une fiche BENCHAB ; aucune validation physique."""',
        "",
        "from build123d import Align, Box, Cylinder, Pos",
        "from cadgen.engine import step",
        "",
        "",
        '@step(out="modele.step")',
        "def construire():",
        '    """Retourne le solide nominal ; origine XY centrée et base à z=0 mm."""',
        f"    solide = Box({bloc[0]!r}, {bloc[1]!r}, {bloc[2]!r}, align=(Align.CENTER, Align.CENTER, Align.MIN))",
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
    for poche in poches:
        # L'outil dépasse le dessus comme le cylindre du lamage ; le fond
        # reste à l'altitude explicite plutôt qu'au milieu du bloc.
        lignes.extend([
            f"    solide -= Pos({poche.x!r}, {poche.y!r}, {bloc[2] - poche.profondeur!r}) * Box(",
            f"        {poche.largeur_x!r}, {poche.largeur_y!r}, {poche.profondeur + 1!r},",
            "        align=(Align.CENTER, Align.CENTER, Align.MIN)",
            "    )",
        ])
    return "\n".join([*lignes, "    return solide", ""])


def _generer(source, sortie, *, prive):
    identifiant, bloc, trous, poches, exclus = _normaliser(_lire(source))
    destination = destination_neuve(sortie, prive=prive)
    script = _script(bloc, trous, poches)
    dx, dy, dz = bloc
    volume_lamages = math.fsum(
        math.pi * (trou.lamage.rayon**2 - trou.rayon**2) * trou.lamage.profondeur
        for trou in trous if trou.lamage
    )
    volume_poches = math.fsum(poche.largeur_x * poche.largeur_y * poche.profondeur for poche in poches)
    attendu = {
        "dimensions_mm": bloc,
        "solides": 1,
        "volume_mm3": (
            (dx * dy - math.fsum(math.pi * trou.rayon**2 for trou in trous)) * dz - volume_lamages - volume_poches
        ),
    }
    statut = "NON VÉRIFIÉ" if exclus else "PASS"
    plan = {
        "schema": 1, "id": identifiant, "statut": statut,
        "portee": "Gabarit nominal : script généré, aucun STEP construit ni validation physique.",
        "bloc_mm": bloc,
        "percages": [
            {"x_mm": trou.x, "y_mm": trou.y, "diametre_mm": 2 * trou.rayon}
            | ({"lamage": {
                "diametre_mm": 2 * trou.lamage.rayon,
                "profondeur_mm": trou.lamage.profondeur,
                "source_cotes": trou.lamage.source_cotes,
            }} if trou.lamage else {})
            for trou in trous
        ],
        **({"poches_rectangulaires": [
            {"x_mm": p.x, "y_mm": p.y, "largeur_x_mm": p.largeur_x, "largeur_y_mm": p.largeur_y,
             "profondeur_mm": p.profondeur, "source_cotes": p.source_cotes}
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
