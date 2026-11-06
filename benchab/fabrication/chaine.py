"""Chaîne fixe des gabarits : l'automatisation de séance ne fabriquait pas la pièce.

Réutilise les contrôles communs jusqu'à l'envoi ; aucun accès à l'imprimante.
"""

import argparse
import contextlib
import json
import os
import sys
import traceback
from pathlib import Path

from benchab import processus
from benchab.controles import (
    colonnes_isolees,
    controle_envoi,
    controles_piece,
    decollement,
    envoi,
    ponts_internes,
    premiere_couche,
    revue_locale,
    routine,
)
from benchab.mesure import environnement
from benchab.qualite import coherence
from benchab.qualite import qualite as qualite_projet
from benchab.qualite.ecritures import _sans_lien, ecrire_json_neuf
from benchab.qualite.statuts import code_statut

DELAI_SCRIPT_S = 600
DELAI_TRANCHAGE_S = 1800


class _Arret(Exception):
    pass


class _DureeDepassee(Exception):
    pass


def executer(projet, prive=None, *, optimiser=False, objectif=None):
    """Fabrique dans un projet privé configuré, dimensions mm et durées s.

    Renvoie un rapport PASS/WARN/FAIL/NON VÉRIFIÉ ; s'arrête au premier FAIL.
    Ne modifie aucun réglage, profil installé ou préparation figée ; n'imprime jamais.
    """
    projet = _sans_lien(Path(projet).absolute())
    prive = _sans_lien(Path(prive).absolute() if prive else projet.parent.parent)
    if not projet.is_relative_to(prive / "projets") or not projet.is_dir():
        raise ValueError("Projet existant sous prive/projets requis")
    dossier = projet / "preuves/chaine"
    _sans_lien(dossier)
    dossier.mkdir(parents=True, exist_ok=False)
    etapes = []
    rapport = {
        "statut": "PASS",
        "etapes": etapes,
        "validation_physique": False,
        "rapport": str(dossier / "rapport.json"),
    }

    def etape(nom, fonction, acceptes=(0, 1)):
        journal = dossier / (nom + ".log")
        with journal.open("x") as flux, contextlib.redirect_stdout(flux), contextlib.redirect_stderr(flux):
            try:
                code = fonction()
            except _DureeDepassee:
                code = _DureeDepassee
            except SystemExit as erreur:
                code = erreur.code
            except Exception:
                traceback.print_exc()
                code = 2
        if code is _DureeDepassee:
            etapes.append({"etape": nom, "code": 2, "statut": "NON VÉRIFIÉ", "journal": str(journal)})
            raise _DureeDepassee(nom)
        statut = "FAIL" if code not in acceptes else "PASS" if code == 0 else "NON VÉRIFIÉ" if code == 3 else "WARN"
        etapes.append({"etape": nom, "code": code, "statut": statut, "journal": str(journal)})
        if statut == "FAIL":
            raise _Arret(nom)
        return code

    def script(nom, arguments=(), avertissement=False):
        # Les seuls scripts exécutés sont les points d'entrée des gabarits,
        # jamais une commande libre du modèle (docs/guides/cycle-projet.md).
        def lancer():
            delai = DELAI_TRANCHAGE_S if nom == "trancher" else DELAI_SCRIPT_S
            resultat = processus.executer(
                [sys.executable, "-B", "-m", "src." + nom, *arguments],
                cwd=projet, diagnostics=dossier, delai=delai,
                env=dict(os.environ, BENCHAB_PRIVE=str(prive)),
            )
            print(resultat.stdout, end="")
            print(resultat.stderr, end="", file=sys.stderr)
            if resultat.diagnostic:
                print(f"\nDiagnostic : {resultat.diagnostic}", file=sys.stderr)
            if resultat.raison == "DELAI":
                print(f"Délai du gabarit dépassé ({delai} s).", file=sys.stderr)
                raise _DureeDepassee(nom)
            if resultat.resultat == "NON_VERIFIE":
                return 2
            if resultat.returncode == 1 and avertissement and resultat.stdout.strip().endswith("WARN"):
                return 1
            return 2 if resultat.returncode == 1 else resultat.returncode

        return lancer

    candidats = projet / "candidats"
    plaques = candidats / "plaques.3mf"
    tranche = candidats / "tranche.3mf"
    placement = projet / "preuves/placement.json"
    geometrie = dossier / "controles-pieces.json"
    declaration = projet / "DECLARATION-ENVOI.json"
    revue = dossier / "REVUE-ZONES-LOCALES.json"
    faits = dossier / "revue-faits.json"
    try:
        erreurs = coherence.verifier(prive)
        if erreurs:
            raise ValueError("Cohérence native : " + "; ".join(erreurs))
        etape("environnement", lambda: environnement.collecter(prive)["code"])
        etape("build", script("construire"), (0,))
        fichiers = sorted(candidats.glob("*.stl"))
        if not fichiers:
            raise ValueError("Aucun STL produit : arrêt avant préparation ou slicer")
        etape("geometrie", lambda: controles_piece.main([*[str(p) for p in fichiers], "--json", str(geometrie)]))
        etape("interfaces", script("controler_interfaces", avertissement=True))
        etape("references", script("references"), (0,))
        etape("plaques", script("preparer"), (0,))
        if optimiser:
            etape("optimiser", script("optimiser", ["--objectif", objectif] if objectif else []), (0,))
        etape(
            "decollement",
            lambda: decollement.main(
                [
                    str(plaques),
                    "--placement",
                    str(placement),
                    "--candidats",
                    str(candidats),
                    "--json",
                    str(dossier / "decollement.json"),
                ]
            ),
        )
        # U09 : src.trancher appelle exclusivement le lanceur commun ; aucune
        # seconde commande slicer ici (docs/reference/slicer-anycubic.md).
        etape("trancher", script("trancher"), (0,))
        if (projet / "src/verifier.py").is_file():
            etape("controles-projet", script("verifier", avertissement=True), (0, 1, 3))
        if (projet / "qualite.json").is_file():
            for niveau in ("niveau1", "niveau3"):
                # La CLI historique distingue FAIL et NON VÉRIFIÉ avec d'autres
                # codes ; adapter le statut explicite, pas une erreur d'exécution
                # (docs/reference/controles.md).
                def qualite(n=niveau):
                    statut = getattr(qualite_projet, n)(projet / "qualite.json")
                    return {"PASS": 0, "WARNING": 1, "NON VÉRIFIÉ": 3, "FAIL": 2}[statut]

                etape(niveau, qualite, (0, 1, 3))
        etape("premiere-couche", lambda: premiere_couche.main([str(tranche), "--json", str(dossier / "premiere.json")]))
        etape("colonnes", lambda: colonnes_isolees.main([str(tranche), "--json", str(dossier / "colonnes.json")]))
        etape("ponts", lambda: ponts_internes.main([str(tranche), "--json", str(dossier / "ponts.json")]))
        bilan = routine.controler(plaques, tranche, placement, dossier / "routine")
        etape("routine", lambda: 0 if bilan["statut"] == "PASS" else 2)
        initial = dossier / "envoi-avant-revue.json"
        controle_envoi.controler(tranche, declaration, geometrie=geometrie, sortie_json=initial)
        # La revue automatique ne vaut pas vote : elle justifie uniquement les
        # critères déjà établis et garde les autres NON VÉRIFIÉS (docs/reference/vote.md).
        resultat = revue_locale.revoir(tranche, initial, revue, faits)
        revue_locale.ecrire(resultat, revue, faits)
        final = envoi.controler(tranche, declaration, geometrie=geometrie, revue_locale=revue)
        ecrire_json_neuf(dossier / "envoi.json", final, entrees=[tranche, declaration, geometrie, revue])
        etapes.append({"etape": "envoi", "statut": final["statut"], "rapport": str(dossier / "envoi.json")})
        rapport["statut"] = final["statut"]
        if rapport["statut"] == "PASS" and any(e["statut"] == "WARN" for e in etapes):
            rapport["statut"] = "WARN"
        if rapport["statut"] != "FAIL" and any(e["statut"] == "NON VÉRIFIÉ" for e in etapes):
            rapport["statut"] = "NON VÉRIFIÉ"
    except _DureeDepassee as erreur:
        rapport.update(statut="NON VÉRIFIÉ", etape_bloquante=str(erreur))
    except _Arret as erreur:
        rapport.update(statut="FAIL", etape_bloquante=str(erreur))
    except Exception as erreur:
        rapport.update(statut="FAIL", erreur=str(erreur))
        (dossier / "erreur.log").write_text(traceback.format_exc(), encoding="utf-8")
    rapport["code"] = code_statut(rapport["statut"])
    ecrire_json_neuf(dossier / "rapport.json", rapport)
    return rapport


def main(argv=None):
    """Lit les chemins CLI, exécute la chaîne ; codes 0/PASS, 1/WARN, 2/FAIL ou NON VÉRIFIÉ, jamais d'impression."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projet", type=Path)
    parser.add_argument("--prive", type=Path)
    parser.add_argument("--optimiser", action="store_true")
    parser.add_argument("--objectif")
    args = parser.parse_args(argv)
    rapport = executer(args.projet, args.prive, optimiser=args.optimiser, objectif=args.objectif)
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return rapport["code"]


if __name__ == "__main__":
    raise SystemExit(main())
