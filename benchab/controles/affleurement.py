"""Contrôle nominal d'une languette et d'une rainure prismatiques à partir de cotes déclarées."""

import argparse
import json
import math
from pathlib import Path

from benchab.chemins import sans_lien
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve, destination_rapport_optionnel
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.statuts import code_statut

_AXES = ("longueur", "largeur", "profondeur")
_CHAMPS = {"schema", "repere_montage", "languette_mm", "rainure_mm", "tolerance_affleurement_mm"}
_MAX_DECLARATION = 64 * 1024


def _cote(valeur):
    """Vérifie qu'une cote en mm est positive, finie et utilisable dans la coupe."""
    return type(valeur) in (int, float) and 1e-6 <= valeur <= 1e6


def mesurer(declaration):
    """Mesure l'affleurement et les jeux en mm ; PASS/WARN/FAIL/NON VÉRIFIÉ, sans validation physique.

    Les deux enveloppes prismatiques sont données en XYZ. Le repère de montage explicite
    associe chaque dimension utile à un axe distinct. Les jeux sont des écarts totaux.
    """
    base = {"validation_physique": False, "portee": "Cotes nominales déclarées pour deux interfaces prismatiques."}
    if not isinstance(declaration, dict) or declaration.keys() != _CHAMPS or (
        type(declaration.get("schema")) is not int or declaration["schema"] != 1
    ):
        return {**base, "statut": "NON VÉRIFIÉ", "raison": "Déclaration de montage complète, schéma 1 requis."}
    axes = declaration["repere_montage"]
    if not isinstance(axes, dict) or axes.keys() != set(_AXES) or (
        sorted(str(axe) for axe in axes.values()) != ["x", "y", "z"]
    ):
        return {**base, "statut": "NON VÉRIFIÉ", "raison": "Déclarer trois axes de montage distincts parmi x, y, z."}
    languette, rainure = declaration["languette_mm"], declaration["rainure_mm"]
    tolerance = declaration["tolerance_affleurement_mm"]
    if any(not isinstance(cotes, list) or len(cotes) != 3 or not all(map(_cote, cotes))
           for cotes in (languette, rainure)) or not _cote(tolerance):
        return {**base, "statut": "NON VÉRIFIÉ", "raison": "Cotes XYZ et tolérance positives, finies, en mm."}
    x, y, z = ("xyz".index(axes[axe]) for axe in _AXES)
    longitudinal, transversal = rainure[x] - languette[x], rainure[y] - languette[y]
    ecart = languette[z] - rainure[z]
    statut = "FAIL" if min(longitudinal, transversal) < 0 else (
        "PASS" if abs(ecart) <= tolerance or math.isclose(abs(ecart), tolerance, abs_tol=1e-12) else "WARN"
    )
    return {
        **base, "statut": statut, "hauteur_languette_mm": languette[z], "profondeur_rainure_mm": rainure[z],
        "largeur_languette_mm": languette[y], "largeur_rainure_mm": rainure[y],
        "desaffleurement_mm": ecart, "jeu_transversal_mm": transversal, "jeu_longitudinal_mm": longitudinal,
        "tolerance_affleurement_mm": tolerance,
    }


def _coupe(rapport):
    """Produit une coupe SVG à échelle commune des cotes nominales en mm."""
    lr, ll = rapport["largeur_rainure_mm"], rapport["largeur_languette_mm"]
    pr, hl = rapport["profondeur_rainure_mm"], rapport["hauteur_languette_mm"]
    echelle = min(220 / max(lr, ll), 100 / max(pr, hl))
    gauche, droite = 300 - lr * echelle / 2, 300 + lr * echelle / 2
    fond = 146 + pr * echelle
    x, y = 300 - ll * echelle / 2, fond - hl * echelle
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 360" role="img">'
        '<title>Coupe transversale nominale</title><rect width="600" height="360" fill="#f7f8fa"/>'
        '<text x="28" y="40" font-size="22" fill="#172437">Coupe transversale nominale</text>'
        f'<path d="M40 146 H{gauche} V{fond} H{droite} V146 H560 V281 H40 Z" fill="#546980"/>'
        f'<rect x="{x}" y="{y}" width="{ll * echelle}" height="{hl * echelle}" '
        'fill="#b95d10" fill-opacity=".85"><title>Languette</title></rect>'
        f'<text x="32" y="318" fill="#172437">Désaffleurement {rapport["desaffleurement_mm"]:.4g} mm</text>'
        f'<text x="32" y="342" fill="#172437">Jeu transversal {rapport["jeu_transversal_mm"]:.4g} mm</text></svg>\n'
    )


def _paires_uniques(paires):
    """Refuse une clé JSON répétée, qui rendrait une cote ou un repère ambigu."""
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError("Clé de déclaration répétée : " + cle)
        resultat[cle] = valeur
    return resultat


def main(argv=None, *, prive=None):
    """Contrôle les cotes nominales en mm et conserve le verdict numérique.

    Le résumé exige un JSON complet. Le rapport et la coupe SVG éventuelle
    sont publiés ensemble dans des sorties neuves. Aucune cote n’est modifiée.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("declaration", type=Path)
    parser.add_argument("--coupe", type=Path)
    parser.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    parser.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    args = parser.parse_args(argv)
    try:
        cible = destination_rapport_optionnel(args.sortie, resume=args.resume, nom="d’affleurement", prive=prive)
        source = sans_lien(args.declaration, strict=True)
        if not source.is_file():
            raise ValueError("Déclaration de montage régulière requise.")
        with source.open("rb") as fichier:
            octets = fichier.read(_MAX_DECLARATION + 1)
        if len(octets) > _MAX_DECLARATION:
            raise ValueError("Déclaration de montage supérieure à 64 Kio.")
        from hashlib import sha256

        empreinte = sha256(octets).hexdigest()
        rapport = mesurer(json.loads(octets, object_pairs_hook=_paires_uniques))
        if sha256_fichier(source) != empreinte:
            raise ValueError("Déclaration modifiée pendant la lecture.")
        rapport["source_sha256"] = empreinte
        sorties = [cible] if cible is not None else []
        coupe = None
        if args.coupe is not None and rapport["statut"] != "NON VÉRIFIÉ":
            coupe = destination_neuve(args.coupe, prive=prive)
            if coupe.suffix.lower() != ".svg":
                raise ValueError("La coupe exige une destination .svg.")
            rapport["coupe"] = str(coupe)
            sorties.append(coupe)
        if sorties:
            with PublicationNeuve([source], sorties) as publication:
                if sha256_fichier(source) != empreinte:
                    raise ValueError("Déclaration modifiée avant publication.")
                if coupe is not None:
                    publication.temporaire(coupe).write_text(_coupe(rapport), encoding="utf-8")
                if cible is not None:
                    publication.temporaire(cible).write_text(
                        json.dumps(rapport, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8",
                    )
                publication.publier()
        if args.resume:
            rapport = {cle: rapport[cle] for cle in (
                "statut", "raison", "desaffleurement_mm", "jeu_transversal_mm", "jeu_longitudinal_mm",
            ) if cle in rapport} | {"rapport": str(cible)}
    except (OSError, ValueError) as erreur:
        rapport = {"statut": "NON VÉRIFIÉ", "raison": str(erreur), "validation_physique": False}
    print(json.dumps(rapport, ensure_ascii=False, allow_nan=False))
    return code_statut(rapport["statut"])
