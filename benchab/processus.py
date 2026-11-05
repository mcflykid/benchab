"""Processus POSIX appartenant à un groupe créé ici, diagnostics conservés à l'échec.

Aucune découverte par nom ni intervention sur un service existant. Les sessions
volontairement détachées par un programme ne sont pas capturées par son groupe.
La RSS échantillonnée est une somme approximative, pas une allocation ni un PSS.
"""

import json
import math
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Resultat:
    args: object
    returncode: object
    code_controle: int
    raison: str
    stdout: str
    stderr: str
    diagnostic: object
    pid: object
    duree_secondes: float
    sortie_tronquee: bool

    @property
    def ok(self):
        return self.code_controle == 0

    @property
    def execution(self):
        return (
            "PASS"
            if self.raison == "TERMINE" and self.returncode is not None and self.returncode >= 0
            else "ERREUR_EXECUTION"
        )

    @property
    def resultat(self):
        return "PASS" if self.ok else "FAIL" if self.execution == "PASS" else "NON_VERIFIE"


def _membres(groupe):
    try:
        r = subprocess.run(["ps", "-axo", "pid=,pgid=,stat=,rss="], capture_output=True, text=True, timeout=1)
        if r.returncode:
            return None
        rows = []
        for ligne in r.stdout.splitlines():
            champs = ligne.split()
            if len(champs) == 4 and champs[1].isdigit() and int(champs[1]) == groupe:
                rows.append({"pid": int(champs[0]), "etat": champs[2], "rss_octets": int(champs[3]) * 1024})
        return rows
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _actif(groupe):
    rows = _membres(groupe)
    if rows is not None:
        return any(not r["etat"].startswith("Z") for r in rows)
    try:
        os.killpg(groupe, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # état inconnu, jamais présenté comme terminé
    return True


def _terminer(p, grace):
    signaux = []
    if not _actif(p.pid):
        return signaux
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
            signaux.append(sig.name)
        except ProcessLookupError:
            break
        except PermissionError:
            if _actif(p.pid):
                signaux.append("REFUS_PERMISSION")
            break
        fin = time.monotonic() + grace
        while time.monotonic() < fin:
            p.poll()
            if not _actif(p.pid):
                return signaux
            time.sleep(0.02)
    return signaux


def _queue(path, limite):
    with path.open("rb") as f:
        taille = f.seek(0, 2)
        f.seek(max(0, taille - limite))
        data = f.read(limite)
    return data.decode("utf-8", errors="replace"), taille > limite


def executer(
    args,
    *,
    cwd,
    diagnostics,
    delai=None,
    shell=False,
    annulation=None,
    env=None,
    grace=0.3,
    limite_queue=65536,
    conserver_capture=False,
    fusion_stderr=False,
):
    """Exécuter une commande ; aucun délai implicite pour un calcul long.

    code_controle conserve 124 pour le délai et 130 pour l'annulation ; returncode
    est le vrai retour du processus (ou None s'il n'a pas été créé). Les fichiers
    de capture réussie sont des tampons temporaires ; les échecs restent complets.
    """
    if os.name != "posix":
        raise ValueError("gestion des groupes POSIX requise")
    if delai is not None and (
        isinstance(delai, bool) or not isinstance(delai, (int, float)) or not math.isfinite(delai) or delai <= 0
    ):
        raise ValueError("délai positif et fini requis")
    if not 0 < grace < 5 or type(limite_queue) is not int or limite_queue < 1:
        raise ValueError("bornes de terminaison/capture invalides")
    if (shell and not isinstance(args, str)) or (
        not shell and (not isinstance(args, (list, tuple)) or not args or not all(isinstance(x, str) for x in args))
    ):
        raise ValueError("argv requis, ou chaîne avec shell explicite")
    cwd = Path(cwd).resolve()
    if not cwd.is_dir():
        raise ValueError("répertoire de travail absent")
    diagnostics = Path(diagnostics).resolve()
    diagnostics.mkdir(parents=True, exist_ok=True)
    dossier = Path(tempfile.mkdtemp(prefix="processus-", dir=diagnostics))
    out = dossier / "stdout.log"
    err = dossier / "stderr.log"
    t0 = time.monotonic()
    p = None
    raison = "TERMINE"
    interruption = None
    signaux = []
    pic = None
    echantillons = 0
    prochaine = 0
    with out.open("xb") as stdout, err.open("xb") as stderr:
        os.chmod(out, 0o600)
        os.chmod(err, 0o600)
        try:
            p = subprocess.Popen(
                args,
                cwd=cwd,
                env=env,
                shell=shell,
                stdout=stdout,
                stderr=subprocess.STDOUT if fusion_stderr else stderr,
                start_new_session=True,
            )
            while p.poll() is None:
                maintenant = time.monotonic()
                if annulation is not None and annulation.is_set():
                    raison = "ANNULATION"
                    break
                if delai is not None and maintenant - t0 >= delai:
                    raison = "DELAI"
                    break
                if maintenant >= prochaine:
                    rows = _membres(p.pid)
                    vivants = [r for r in rows or [] if not r["etat"].startswith("Z")]
                    if vivants:
                        mesure = sum(r["rss_octets"] for r in vivants)
                        pic = max(pic or 0, mesure)
                        echantillons += 1
                    prochaine = maintenant + 0.2
                time.sleep(0.02)
        except KeyboardInterrupt as exc:
            raison = "ANNULATION"
            interruption = exc
        except OSError as exc:
            raison = "NON_EXECUTABLE"
            stderr.write(str(exc).encode())
        finally:
            if p is not None:
                if raison == "TERMINE" and _actif(p.pid):
                    raison = "DESCENDANTS_ACTIFS"
                if raison != "TERMINE":
                    signaux = _terminer(p, grace)
                try:
                    p.wait(timeout=max(1, grace))
                except subprocess.TimeoutExpired:
                    raison = "TERMINAISON_NON_CONFIRMEE"
    retour = p.returncode if p else None
    code = {"DELAI": 124, "ANNULATION": 130, "NON_EXECUTABLE": 127}.get(raison, retour if raison == "TERMINE" else 125)
    if code is None:
        code = 125
    queue_out, tronque_out = _queue(out, limite_queue)
    queue_err, tronque_err = _queue(err, limite_queue)
    if raison == "DELAI":
        queue_err += "\ndélai de la cible dépassé"
    diagnostic = None
    if code or tronque_out or tronque_err or conserver_capture:
        execution = "PASS" if raison == "TERMINE" and retour is not None and retour >= 0 else "ERREUR_EXECUTION"
        rapport = {
            "execution": execution,
            "resultat": "FAIL" if code and execution == "PASS" else "NON_VERIFIE" if code else "PASS",
            "raison": raison,
            "args": args,
            "shell": shell,
            "cwd": str(cwd),
            "pid": p.pid if p else None,
            "pgid_cree": p.pid if p else None,
            "code_processus": retour,
            "code_controle": code,
            "signaux": signaux,
            "duree_secondes": time.monotonic() - t0,
            "rss_groupe_pic_observe_octets": pic,
            "echantillons_rss": echantillons,
            "limites_rss": "somme RSS échantillonnée ; pages partagées et pics entre lectures non distingués",
            "stdout": "stdout.log",
            "stderr": "stderr.log",
            "captures_tronquees_en_memoire": tronque_out or tronque_err,
            "groupe_actif_apres": _actif(p.pid) if p else False,
        }
        diagnostic = dossier / "diagnostic.json"
        with diagnostic.open("x") as f:
            json.dump(rapport, f, ensure_ascii=False, indent=2)
        os.chmod(diagnostic, 0o600)
    else:
        # Seulement les deux tampons créés dans notre répertoire privé unique.
        out.unlink()
        err.unlink()
        dossier.rmdir()
    result = Resultat(
        args,
        retour,
        int(code),
        raison,
        queue_out,
        queue_err,
        str(diagnostic) if diagnostic else None,
        p.pid if p else None,
        time.monotonic() - t0,
        tronque_out or tronque_err,
    )
    if interruption is not None:
        interruption.diagnostic = result.diagnostic
        raise interruption
    return result
