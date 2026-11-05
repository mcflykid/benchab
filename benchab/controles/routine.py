"""Remplace la chaîne shell par les mêmes cinq contrôles, sans masquer un échec par un ancien rapport."""

import contextlib
import json
from pathlib import Path

from benchab.controles import debit_gcode, disposition_reelle, plaques_interface, verifier_reglages
from benchab.qualite.ecritures import _sans_lien


def controler(plaques, tranche, placement, sortie):
    entrees = [_sans_lien(Path(p)) for p in (plaques, tranche, placement)]
    dossier = _sans_lien(Path(sortie))
    dossier.mkdir(parents=True, exist_ok=True)
    fichier_plaques, fichier_tranche, fichier_placement = map(str, entrees)
    etapes = [
        ("plaques-interface-plaques", plaques_interface.main, [fichier_plaques], "conforme"),
        ("plaques-interface-tranche", plaques_interface.main, [fichier_tranche], "conforme"),
        ("reglages-gcode", verifier_reglages.main, [fichier_tranche, fichier_placement], "tout_conforme"),
        ("disposition-reelle", disposition_reelle.main, [fichier_tranche, fichier_placement], "conforme"),
        ("debit-gcode", debit_gcode.main, [fichier_tranche, "--sortie"], "conforme"),
    ]
    for nom, _fonction, _arguments, _champ in etapes:
        for extension in ("json", "log"):
            cible = _sans_lien(dossier / f"{nom}.{extension}")
            if cible.resolve() in {f.resolve() for f in entrees}:
                raise ValueError("Collision entrée/rapport : aucun fichier source n’est modifié")
    resultats = []
    for nom, fonction, arguments, champ in etapes:
        rapport = dossier / f"{nom}.json"
        with (dossier / f"{nom}.log").open("w") as journal:
            with contextlib.redirect_stdout(journal), contextlib.redirect_stderr(journal):
                try:
                    code = fonction([*arguments, str(rapport)])
                except SystemExit as erreur:
                    code = int(erreur.code or 0)
                except (OSError, ValueError, KeyError, TypeError) as erreur:
                    print(f"Contrôle interrompu : {erreur}")
                    code = 2
        conforme = False
        if code == 0:
            try:
                conforme = json.loads(rapport.read_text()).get(champ) is True
            except (OSError, ValueError, AttributeError):
                pass
        resultats.append(
            {
                "controle": nom,
                "code": code,
                "statut": "PASS" if code == 0 and conforme else "FAIL",
                "rapport": str(rapport),
            }
        )
    conforme = all(r["statut"] == "PASS" for r in resultats)
    return {
        "statut": "PASS" if conforme else "FAIL",
        "conforme": conforme,
        "resultats": resultats,
        "validation_physique": False,
    }
