"""Assemblage, contacts et épaisseur : les contrôles existants ne mesurent pas ces interfaces."""

import argparse
import contextlib
import io
import json
import math
from pathlib import Path

from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
from benchab.tiers import modeling as M


def _analyser(fichier, *, jeu=0.3, paroi=1.2, echantillons=2600, mode="assemblage", intentionnels=()):
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in (jeu, paroi)
    ):
        raise ValueError("Jeu et épaisseur : valeurs positives finies exigées")
    if type(echantillons) is not int or not 10 <= echantillons <= 10000:
        raise ValueError("Échantillonnage attendu entre 10 et 10 000")
    if mode not in ("assemblage", "contacts", "parois", "enveloppe"):
        raise ValueError("Mode de contrôle inconnu")
    pieces = M.load_parts(fichier)
    rapport = {
        "statut": "NON VÉRIFIÉ",
        "mode": mode,
        "validation_physique": False,
        "limite": "Contrôle numérique échantillonné ; accès, résistance et tenue physiques non prouvés.",
        "jeu_mm": jeu,
        "paroi_min_mm": paroi,
        "pieces": sorted(pieces),
    }
    if not pieces or any(not p.is_volume for p in pieces.values()):
        return {**rapport, "raison": "Volume fermé exigé pour chaque pièce"}
    if mode == "assemblage":
        echecs, alertes = M.audit(pieces, jeu, set(intentionnels))
        return {
            **rapport,
            "statut": "FAIL" if echecs else "WARN" if alertes else "PASS",
            "echecs": echecs,
            "alertes": alertes,
        }
    if mode == "contacts":
        erreurs = io.StringIO()
        with contextlib.redirect_stderr(erreurs):
            contacts = M.fit_map(pieces, samples=echantillons, designed=intentionnels)
        # Un contact non prévu peut bloquer le montage même sans intersection
        # volumique (docs/explications/fiabilite-fdm.md, jeux et assemblages).
        inattendus = [c for c in contacts if not c.get("designed") and (c.get("press") or c["mm"] <= 0.005)]
        return {
            **rapport,
            "statut": "FAIL" if inattendus else "NON VÉRIFIÉ"
            if erreurs.getvalue()
            else "WARN"
            if any(c.get("press") for c in contacts)
            else "PASS",
            "contacts": contacts,
            "contacts_non_autorises": inattendus,
            "couverture": "PARTIELLE" if erreurs.getvalue() else "ÉCHANTILLONNÉE",
        }
    if len(pieces) != 1:
        raise ValueError("Le mode parois attend un maillage unique ; enveloppe prend la première pièce nommée coque")
    shell = next(iter(pieces.values()))
    if mode == "enveloppe":
        raise ValueError("Utiliser enveloppe(coque, internes) avec des maillages explicitement affectés")
    try:
        percentile, minimum = M.self_thickness(shell, paroi, n=echantillons)
    except SystemExit:
        return {**rapport, "raison": "Aucun rayon valide ; épaisseur non établie"}
    return {
        **rapport,
        "statut": "FAIL" if minimum < paroi else "PASS",
        "minimum_mm": minimum,
        "percentile_1_mm": percentile,
    }


def analyser(fichier, **options):
    """Contrôle une scène/mm ; PASS/WARN/FAIL/NV, empreinte préservée, sans validation physique de l'assemblage."""
    from benchab.qualite.empreintes import sha256_fichier

    source = Path(fichier).resolve(strict=True)
    avant = sha256_fichier(source)
    rapport = _analyser(source, **options)
    if sha256_fichier(source) != avant:
        raise ValueError("Source modifiée pendant le contrôle d'assemblage")
    return {**rapport, "source_sha256": avant, "unites": "mm", "repere": "projet"}


def balayer(fichier, piece, axe, debut_deg, fin_deg, positions, *, pivot=None, jeu=0.3, intentionnels=()):
    """Échantillonne la rotation d'une pièce contre l'assemblage.

    Entrées : chemin de scène, nom de pièce, axe X/Y/Z, angles en degrés,
    nombre de positions (2 à 181), pivot optionnel en mm et jeu en mm. Renvoie
    PASS/WARN/FAIL avec les collisions par angle ; la source reste intacte et
    le contrôle discret ne prouve pas un mouvement continu ou physique.
    """
    if isinstance(jeu, bool) or not isinstance(jeu, (int, float)) or not math.isfinite(jeu) or jeu <= 0:
        raise ValueError("Le jeu doit être positif et fini")
    source = Path(fichier).resolve(strict=True)
    from benchab.qualite.empreintes import sha256_fichier

    avant = sha256_fichier(source)
    pieces = M.load_parts(source)
    if not pieces or any(not p.is_volume for p in pieces.values()):
        raise ValueError("Un volume fermé est exigé pour chaque pièce")
    mesures = M.sweep(pieces, piece, axe.upper(), debut_deg, fin_deg, positions, jeu, intentionnels, pivot)
    # La pose enregistrée est celle du montage, même si l'intervalle demandé
    # exclut 0° (docs/explications/fiabilite-fdm.md, montage et mouvement).
    echecs_initiaux, alertes_initiales = M.audit(pieces, jeu, set(intentionnels))
    if sha256_fichier(source) != avant:
        raise ValueError("Source modifiée pendant le balayage d'assemblage")
    return {
        "statut": "FAIL"
        if echecs_initiaux or any(p["echecs"] for p in mesures)
        else "WARN"
        if alertes_initiales or any(p["alertes"] for p in mesures)
        else "PASS",
        "mode": "balayage discret",
        "validation_physique": False,
        "piece": piece,
        "axe": axe.upper(),
        "debut_deg": debut_deg,
        "fin_deg": fin_deg,
        "positions": mesures,
        "pose_initiale": {"angle_deg": 0, "echecs": echecs_initiaux, "alertes": alertes_initiales},
        "source_sha256": avant,
        "unites": {"angles": "degrés", "coordonnees": "mm"},
        "limite": (
            "Échantillonnage angulaire ; les interférences entre positions et la faisabilité physique "
            "ne sont pas prouvées."
        ),
    }


def enveloppe(coque, internes, *, paroi=1.2, pas=None):
    """Mesure la profondeur/mm par voxels de pas/mm ; PASS/FAIL, sans prouver la tenue physique ni percer la coque."""
    pas = paroi / 4 if pas is None else pas
    if not math.isfinite(pas) or pas <= 0 or not math.isfinite(paroi) or paroi <= 0:
        raise ValueError("Pas et épaisseur positifs finis exigés")
    if math.prod(int(v / pas) + 3 for v in coque.extents) > 16_000_000:
        raise ValueError("Budget voxel dépassé : choisir explicitement un pas plus grand, précision à qualifier")
    mesures = M.breach_check(coque, list(internes.values()), list(internes), paroi, pas)
    return {
        "statut": "FAIL" if any(mm <= pas or mm < paroi for _, mm, _ in mesures) else "PASS",
        "pas_mm": pas,
        "validation_physique": False,
        "mesures": [{"piece": n, "profondeur_mm": mm, "point_mm": pt.tolist()} for n, mm, pt in mesures],
    }


def _afficher_rapport(rapport, source, sortie, resume):
    code = {"FAIL": 1, "NON VÉRIFIÉ": 2}.get(rapport["statut"], 0)
    if sortie is not None:
        ecrire_json_neuf(sortie, rapport, entrees=[source])
    if resume:
        # Les contacts attendus et toutes les poses restent consultables dans le JSON (#35).
        court = {k: rapport[k] for k in ("statut", "mode", "raison", "echecs", "alertes",
                                         "contacts_non_autorises", "couverture", "minimum_mm",
                                         "paroi_min_mm") if k in rapport}
        if "contacts" in rapport:
            court["contacts_serres"] = [c for c in rapport["contacts"] if c.get("press")]
        if "positions" in rapport:
            court["positions_en_ecart"] = [
                p for p in [rapport["pose_initiale"], *rapport["positions"]] if p["alertes"] or p["echecs"]
            ]
        rapport = {**court, "code": code, "rapport": str(sortie)}
    print(json.dumps(rapport, ensure_ascii=False, allow_nan=False))
    return code


def main(argv=None):
    """Contrôle l’assemblage en mm, avec rapport complet neuf en option.

    Renvoie 1 sur FAIL, 2 sur NON VÉRIFIÉ ou refus, 0 sinon. Aucune validation physique.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fichier", type=Path)
    parser.add_argument("--mode", choices=("assemblage", "contacts", "parois"), default="assemblage")
    parser.add_argument("--jeu", type=float, default=0.3)
    parser.add_argument("--paroi", type=float, default=1.2)
    parser.add_argument("--echantillons", type=int, default=2600)
    parser.add_argument("--contact-voulu", action="append", default=[], metavar="A:B")
    parser.add_argument("--sweep", metavar="PIECE=AXE:DEBUT:FIN:POSITIONS")
    parser.add_argument("--pivot", metavar="X,Y,Z", help="Pivot de rotation en mm ; défaut : centroïde de la pièce")
    parser.add_argument("--json", type=Path, help="Rapport complet neuf")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.resume and args.json is None:
            raise ValueError("--resume exige --json pour conserver le rapport complet.")
        sortie = destination_neuve(args.json) if args.json is not None else None
    except (OSError, ValueError) as erreur:
        parser.error(str(erreur))
    voulus = []
    for paire in args.contact_voulu:
        noms = paire.split(":")
        if len(noms) != 2 or not all(noms) or noms[0] == noms[1]:
            parser.error("Contact voulu attendu A:B, deux pièces distinctes")
        voulus.append(frozenset(noms))
    if args.sweep:
        try:
            piece, spec = args.sweep.split("=", 1)
            axe, debut, fin, positions = spec.split(":")
            pivot = tuple(float(v) for v in args.pivot.split(",")) if args.pivot else None
            if pivot is not None and len(pivot) != 3:
                raise ValueError
            rapport = balayer(
                args.fichier,
                piece,
                axe,
                float(debut),
                float(fin),
                int(positions),
                pivot=pivot,
                jeu=args.jeu,
                intentionnels=voulus,
            )
        except (ValueError, TypeError) as erreur:
            parser.error(f"Balayage invalide : {erreur}")
    else:
        if args.pivot:
            parser.error("--pivot nécessite --sweep")
        rapport = analyser(
            args.fichier,
            jeu=args.jeu,
            paroi=args.paroi,
            mode=args.mode,
            echantillons=args.echantillons,
            intentionnels=voulus,
        )
    try:
        return _afficher_rapport(rapport, args.fichier, sortie, args.resume)
    except (OSError, ValueError) as erreur:
        if sortie is None:
            raise
        parser.error(str(erreur))
