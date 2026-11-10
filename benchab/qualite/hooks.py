"""Hooks projet réversibles ; filtre shell heuristique, sans garantie de confinement.

Les contrôles explicites des livrables restent nécessaires ; voir docs/guides/developpement.md.
"""

import json
import re
import shlex
import sys
from pathlib import Path

from benchab.chemins import sans_lien
from benchab.qualite.validation import exiger, protege

PY = sys.executable


def projet(p):
    """Résout le projet contenant un chemin ; renvoie Path ou None, sans unité ni écriture."""
    p = Path(p).resolve()
    for d in [p if p.is_dir() else p.parent, *p.parents]:
        if (d / "qualite.json").exists():
            return d
    return None


def pre(data):
    """Contrôle une charge de hook ; renvoie allow ou lève un refus, sans écrire de fichier.

    Chemins sans unité ; le filtre shell est indicatif, pas une autorisation de modifier un livrable.
    """
    ti = data.get("tool_input", {})
    paths = [ti[k] for k in ("file_path", "path") if ti.get(k)]
    # Le repli doit suivre chaque appel, pas le dossier présent à l’import (#21).
    cwd = sans_lien(data.get("cwd") or Path.cwd())
    for p in paths:
        f = Path(p) if Path(p).is_absolute() else cwd / p
        root = protege(f)
        if root:
            raise RuntimeError("Écriture refusée : fichier figé dans " + root)
        text = str(ti.get("content", ti.get("new_string", "")))
        if f.suffix == ".md" and re.search(r"(?im)^\s*(?:\*\*)?(?:GO\b|PR[ÊE]T(?:E)?\s+[ÀA]\s+IMPRIMER)", text):
            d = projet(f)
            if not d:
                raise RuntimeError("Décision refusée : projet/qualite.json absent")
            exiger(d)
    cmd = ti.get("command", "")
    if cmd:
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = []
        if "cd" in tokens:
            i = tokens.index("cd")
            if i + 1 < len(tokens):
                cwd = (cwd / tokens[i + 1]).resolve()
        mutating = bool(
            re.search(r"\b(rm|mv|cp|tee|touch|truncate|sed)\b|write_text|write_bytes|open\([^\n]+[\"\']w|[>]", cmd)
        )
        if mutating:
            for token in tokens:
                if "/" not in token and not token.endswith((".py", ".json", ".md", ".stl", ".3mf")):
                    continue
                token = token.strip(";&()")
                f = Path(token) if token.startswith("/") else cwd / token
                if protege(f):
                    raise RuntimeError("Commande refusée : écriture visant un fichier figé")
        if "livrables.py" in cmd and re.search(r"\bfiger\b", cmd):
            try:
                i = tokens.index("figer")
                d = (cwd / tokens[i + 1]).resolve()
            except (ValueError, IndexError):
                raise RuntimeError("Gel refusé : cible non déterminée")
            if (d / "qualite.json").exists():
                exiger(d)
    return {"decision": "allow"}


def post(data):
    """Contrôle une source depuis le cwd de la charge ; rend un code et écrit seulement son état.

    Chemins sans unité ; ne modifie aucun livrable figé ni réglage d’impression.
    """
    ti = data.get("tool_input", {})
    p = ti.get("file_path", ti.get("path"))
    if not p or "src" not in Path(p).parts:
        return {"controle": "sans source modifiee"}
    cwd = sans_lien(data.get("cwd") or Path.cwd())
    p = Path(p) if Path(p).is_absolute() else cwd / p
    d = projet(p)
    if not d:
        return {"controle": "projet sans qualite.json"}
    prive = next((parent for parent in (d, *d.parents) if (parent / "benchab.sqlite").is_file()), None)
    if prive is None:
        return {"controle": "atelier privé indéterminé ; contrôle non exécuté", "code": 2}
    from benchab.qualite.qualite import executer_borne

    r = executer_borne(
        [
            str(PY),
            "-B",
            "-m",
            "benchab.cli",
            "--prive",
            str(prive),
            "qualite",
            "niveau1",
            str(d / "qualite.json"),
        ]
    )
    note = (
        "# État — NO GO\n\nSource modifiée : "
        + str(p)
        + "\nContrôle niveau1 exécuté ; les preuves mécaniques doivent être rejouées avec benchab qualite tout.\n"
        "Voir preuves/qualite/niveau1.md.\n"
    )
    if not protege(d / "ETAT.md"):
        (d / "ETAT.md").write_text(note, encoding="utf-8")
    return {"controle": "niveau1 apres src", "code": r.returncode, "sortie": r.stdout[-1800:]}


def main(argv=None):
    """Lit une charge JSON et rend 0 ou 2 ; aucun secret ni commande machine, chemins sans unité."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in (["pre"], ["post"]):
        print("Usage : benchab qualite hooks pre|post", file=sys.stderr)
        return 2
    try:
        data = json.load(sys.stdin)
        res = pre(data) if args[0] == "pre" else post(data)
        print(json.dumps(res, ensure_ascii=False))
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
