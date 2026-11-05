"""Localise les octets historiques d'une préparation sans exécuter les sources du projet."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from benchab.qualite.empreintes import sha256_fichier


def sha(f):
    """Empreinte robuste, en flux, d'un fichier ordinaire."""
    return sha256_fichier(f)


def chemin(base, relatif):
    if not isinstance(relatif, str) or not relatif or Path(relatif).is_absolute() or ".." in Path(relatif).parts:
        raise ValueError("chemin relatif sans remontée requis")
    p = (base / relatif).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError("chemin hors périmètre")
    return p


def verifier(projet, depot):
    projet = Path(projet).resolve()
    depot = Path(depot).resolve()
    erreurs = []
    lacunes = []
    versions = {}
    preparations = {}
    controlees = {}
    requis = set()

    def resultat():
        return {
            "statut": "FAIL" if erreurs else "WARN" if lacunes else "PASS",
            "reproductibilite": "À VÉRIFIER",
            "erreurs": erreurs,
            "lacunes": lacunes,
            "versions": versions,
            "preparations": preparations,
            "empreintes_localisees": len(controlees),
            "note": (
                "rattachements uniquement ; statuts de contrôle conservés, aucune aptitude à imprimer, "
                "reconstruction ou restauration déduite"
            ),
        }

    try:
        index = json.loads(chemin(projet, "reference/RATTACHEMENTS.json").read_text())
        if not isinstance(index, dict) or type(index.get("schema")) is not int or index["schema"] != 1:
            raise ValueError("schéma de rattachement inconnu")
        for champ in ("versions", "preparations", "emplacements", "lacunes"):
            if not isinstance(index.get(champ), dict):
                raise ValueError(champ + " : objet requis")
        if not index["versions"]:
            raise ValueError("aucune version déclarée")
        for ident, v in index["versions"].items():
            if not re.fullmatch(r"V\d+", ident) or not isinstance(v, dict):
                raise ValueError("identifiant de version invalide")
            if v.get("role") not in ("reference_historique", "en_developpement", "amelioree", "remplacee", "retiree"):
                raise ValueError("rôle de version inconnu")
            if v.get("reproductibilite") != "À VÉRIFIER":
                raise ValueError("une localisation ne démontre pas la reproductibilité")
            if not chemin(projet, v.get("dossier")).is_dir():
                raise ValueError("dossier de version absent : " + ident)
            if v["role"] == "reference_historique":
                m = v.get("manifeste", {})
                f = chemin(projet, m.get("chemin"))
                if sha(f) != m.get("sha256"):
                    raise ValueError("manifeste de référence modifié : " + ident)
                for ligne in f.read_text().splitlines():
                    h, nom = ligne.split(maxsplit=1)
                    if sha(chemin(f.parent, nom.lstrip("*"))) != h:
                        raise ValueError("référence historique modifiée : " + nom)
            versions[ident] = {"role": v["role"], "dossier": v["dossier"], "reproductibilite": "À VÉRIFIER"}
        presents = {p.parent.name for p in (projet / "preparations").glob("*/PREPARATION.json")}
        if set(index["preparations"]) != presents:
            raise ValueError("liste des préparations différente du contenu du projet")
        if index.get("preparation_courante") not in presents:
            raise ValueError("préparation courante absente ou inconnue")

        def localiser(h):
            if not isinstance(h, str) or not re.fullmatch("[0-9a-f]{64}", h):
                raise ValueError("SHA-256 de pièce invalide")
            requis.add(h)
            if h in controlees:
                return controlees[h]
            loc = index["emplacements"].get(h)
            manque = index["lacunes"].get(h)
            if loc is not None and manque is not None:
                raise ValueError("empreinte à la fois localisée et manquante")
            if loc is None:
                if not isinstance(manque, str) or not manque.strip():
                    raise ValueError("empreinte sans emplacement ni lacune explicite : " + h)
                return {"statut": "LACUNE", "raison": manque}
            if not isinstance(loc, dict):
                raise ValueError("emplacement non interprétable")
            typ = loc.get("type")
            if typ in ("projet", "depot"):
                f = chemin(projet if typ == "projet" else depot, loc.get("chemin"))
                if sha(f) != h:
                    raise ValueError("octets modifiés pour " + str(f))
            elif typ == "git":
                commit = loc.get("commit", "")
                nom = loc.get("chemin")
                if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
                    raise ValueError("commit Git exact requis")
                chemin(depot, nom)  # même refus des chemins absolus, remontées et liens hors dépôt
                r = subprocess.run(["git", "cat-file", "blob", commit + ":" + nom], cwd=depot, capture_output=True)
                if r.returncode or hashlib.sha256(r.stdout).hexdigest() != h:
                    raise ValueError("objet Git absent ou différent : " + nom)
            else:
                raise ValueError("type de localisation inconnu")
            controlees[h] = {
                "statut": "RETROUVÉ",
                "type": typ,
                "chemin": loc.get("chemin"),
                "commit": loc.get("commit"),
            }
            return controlees[h]

        for ident, lien in index["preparations"].items():
            if not re.fullmatch(r"P\d{2,}", ident) or not isinstance(lien, dict):
                raise ValueError("identifiant de préparation invalide")
            if lien.get("version") not in versions:
                raise ValueError("version de préparation inconnue : " + ident)
            f = chemin(projet, "preparations/" + ident + "/PREPARATION.json")
            if sha(f) != lien.get("sha256"):
                raise ValueError("fiche figée modifiée : " + ident)
            fiche = json.loads(f.read_text())
            if fiche.get("id") != ident:
                raise ValueError("identifiant interne différent : " + ident)
            if "environnement_sha256" in lien:
                if sha(f.parent / "environnement.json") != lien["environnement_sha256"]:
                    raise ValueError("environnement figé modifié : " + ident)
            res = {
                "version": lien["version"],
                "role": "candidate_courante" if ident == index["preparation_courante"] else "preparation_historique",
                "statut_fiche": fiche.get("statut"),
                "envoi_declare": bool(fiche.get("envoi")),
                "retour_present": bool(fiche.get("retour_physique")),
                "statuts_controles": {
                    n: v.get("statuts", {}) for n, v in fiche.get("controles", {}).items() if isinstance(v, dict)
                },
                "rattachements": {},
            }
            for groupe in ("demande", "sources", "modeles", "controles"):
                valeurs = fiche.get(groupe)
                if not isinstance(valeurs, dict):
                    raise ValueError("groupe de pièces absent : " + groupe)
                if groupe == "sources" and not valeurs:
                    lacunes.append(
                        {"preparation": ident, "groupe": groupe, "raison": "aucune source déclarée dans la fiche figée"}
                    )
                rs = []
                for nom, valeur in valeurs.items():
                    h = valeur.get("sha256") if isinstance(valeur, dict) else valeur
                    etat = localiser(h)
                    rs.append({"chemin_historique": nom, "sha256": h, **etat})
                    if etat["statut"] == "LACUNE":
                        lacunes.append(
                            {
                                "preparation": ident,
                                "groupe": groupe,
                                "chemin": nom,
                                "sha256": h,
                                "raison": etat["raison"],
                            }
                        )
                res["rattachements"][groupe] = rs
            for nom in ("plaques_3mf", "tranche_3mf"):
                attendu = fiche.get(nom, {})
                archive = chemin(
                    projet, "preparations/" + ident + "/" + ("plaques.3mf" if nom == "plaques_3mf" else "tranche.3mf")
                )
                if not isinstance(attendu, dict) or sha(archive) != attendu.get("sha256"):
                    raise ValueError("archive figée différente : " + ident + "/" + nom)
            preparations[ident] = res
        if set(index["emplacements"]) | set(index["lacunes"]) != requis:
            raise ValueError("index incomplet ou empreintes sans référence dans les préparations")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
        erreurs.append(str(e))
    return resultat()


def main(argv=None):
    """Vérifie un index de rattachements et renvoie PASS=0, WARN=1 ou FAIL=2."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projet", type=Path)
    parser.add_argument("--depot", type=Path, default=Path(__file__).resolve().parents[2])
    arguments = parser.parse_args(argv)
    if arguments.projet is None:
        rapport = {"statut": "FAIL", "erreurs": ["--projet requis"]}
        print(json.dumps(rapport, ensure_ascii=False))
        return 2
    rapport = verifier(arguments.projet, arguments.depot)
    print(json.dumps(rapport, indent=1, ensure_ascii=False))
    return {"PASS": 0, "WARN": 1, "FAIL": 2}[rapport["statut"]]


if __name__ == "__main__":
    raise SystemExit(main())
