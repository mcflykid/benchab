"""Génération privée et calibration explicite des trous nominaux, sans commande machine."""

import argparse
import datetime
import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np

from benchab.chemins import atelier_prive, sans_lien
from benchab.conception import trous
from benchab.qualite.ecritures import PublicationNeuve

ICI = Path(__file__).resolve().parent
DIAM = [3, 4, 5, 6, 8, 10, 12]
GOUTTES = [3, 5, 8]


def generer(sortie=None, *, table_path=None):
    """Génère une éprouvette dans une nouvelle sortie; cotes en mm, aucune commande machine."""
    f = (
        Path(sortie)
        if sortie
        else atelier_prive() / "eprouvettes" / (datetime.date.today().isoformat() + "-trous-nominaux.stl")
    )
    if os.path.lexists(f):
        raise FileExistsError(f"{f} existe déjà : choisir une nouvelle sortie")
    import manifold3d as mf
    import trimesh

    def M(m):
        return mf.Manifold(
            mf.Mesh64(vert_properties=np.asarray(m.vertices, np.float64), tri_verts=np.asarray(m.faces, np.uint64))
        )

    plaque = mf.Manifold.cube([90, 24, 5])
    talon = mf.Manifold.cube([90, 8, 16]).translate([0, 24, 0])
    corps = plaque + talon
    corps = corps - mf.Manifold.cube([6, 6, 20]).rotate([0, 0, 45]).translate(
        [0, -4.2, -1]
    )  # coin chanfreiné : côté Ø 3
    x = 6.0
    for d in DIAM:
        corps = corps - mf.Manifold.cylinder(20, d / 2, d / 2, 96).translate([x + d / 2, 12, -5])
        x += d + 4.5
    for i, d in enumerate(GOUTTES):
        corps = corps - M(
            trous.goutte_maillage(
                d,
                20,
                "Y",
                centre=(20 + 25 * i, 28, 7.0),
                ajout=0,
                table_path=table_path,
            )
        )
    o = corps.to_mesh64()
    m = trimesh.Trimesh(np.asarray(o.vert_properties)[:, :3], np.asarray(o.tri_verts))
    with PublicationNeuve([], [f]) as publication:
        m.export(publication.temporaire(f))
        publication.publier()
    print(
        f,
        "fermé",
        m.is_watertight,
        "volume",
        round(m.volume / 1000, 1),
        "cm³ ; trous verticaux",
        DIAM,
        "; gouttes",
        GOUTTES,
    )


def mesure(args, bobine, *, table_path=None):
    """Enregistre des diamètres en mm avec contexte bobine ; ne calibre ni ne commande la machine."""
    mes = {}
    for argument in args:
        if "=" not in argument:
            continue
        if argument.count("=") != 1:
            raise ValueError("mesure attendue : nominal=mesuré")
        d, v = (float(x) for x in argument.split("="))
        if not math.isfinite(d) or d not in DIAM or not math.isfinite(v) or v <= 0 or d in mes:
            raise ValueError("diamètre nominal connu, unique et mesure finie positive requis")
        mes[d] = v
    if not mes:
        raise ValueError("au moins une mesure réelle requise ; table inchangée")
    path = sans_lien(trous.chemin_table(table_path))
    if path.is_symlink():
        raise ValueError("table liée symboliquement : écriture refusée")
    avant = path.read_bytes() if path.exists() else None
    t = json.loads(avant) if avant is not None else trous.table(table_path)
    t["mesures"].append({"date": datetime.date.today().isoformat(), "bobine": bobine, "mesures_mm": mes})
    t["vertical_mm"] = [[d, round(d - v, 3)] for d, v in sorted(mes.items())]
    t["statut"] = (
        f"MESURÉ le {datetime.date.today().strftime('%d/%m/%Y')} ({bobine or 'bobine non précisée'}) "
        "avec une éprouvette nominale ; fichier de preuve à rattacher explicitement"
    )
    payload = json.dumps(t, indent=1, ensure_ascii=False, allow_nan=False)
    # Mise à jour explicite de calibration : préserver l'ancien JSON jusqu'à
    # publication complète, et refuser une modification concurrente détectée.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".calibration-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        json.loads(Path(temporary).read_text())
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != avant:
            raise ValueError("table modifiée pendant la calibration")
        if avant is None:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(t["statut"], t["vertical_mm"])


def main(argv=None, *, prive=None):
    """Commande d'éprouvette sur l'atelier choisi; chemin privé explicite, aucun réglage modifié."""
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    creation = actions.add_parser("generer")
    creation.add_argument("--sortie", type=Path)
    calibration = actions.add_parser("mesure")
    calibration.add_argument("mesures", nargs="+")
    calibration.add_argument("--bobine")
    arguments = parser.parse_args(argv)
    atelier = atelier_prive(prive)
    table_path = atelier / "config/compensation-trous.json"
    if arguments.action == "generer":
        sortie = arguments.sortie or atelier / "eprouvettes" / (datetime.date.today().isoformat() + "-trous.stl")
        generer(sortie, table_path=table_path)
    else:
        mesure(arguments.mesures, arguments.bobine, table_path=table_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
