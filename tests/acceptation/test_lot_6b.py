"""Découpe 1.0 : géométrie, choix, édition locale et exports, fixtures originales."""

import json

import numpy as np
import trimesh
from fixtures_decoupe import figurine, trois_branches

from benchab.conception.decoupe import atelier as A
from benchab.conception.decoupe import connecteurs as C
from benchab.conception.decoupe import geometrie as G
from benchab.imports3d.lecture_3mf import lire
from benchab.visualisation.decoupe import agir, page


def test_detection_du_cou_et_quatre_pieces_sans_placer_les_coupes(tmp_path):
    p = G.proposer(figurine())
    assert 29 < p["coupes"][0]["origine"][2] < 44
    source = tmp_path / "branches.stl"
    trois_branches().export(source)
    plan = A.nouveau_plan(source, "mm", 4)
    resultat = A.calculer(plan)
    assert len(resultat["pieces"]) == 4 and len(resultat["accessoires"]) == 3
    assert resultat["rapport"]["reassemblage"]["statut"] == "PASS"


def test_catalogue_exact_et_ajustements_geometriques():
    assert set(C.CATALOGUE) == {
        "cheville-rond",
        "cheville-rectangle",
        "cheville-d",
        "cheville-queue-aronde",
        "rotule-simple",
        "rotule-double",
        "douille",
        "canal-tige",
        "aucun",
    }
    mesh = trimesh.creation.box((40, 40, 40))
    coupe = {"origine": [0, 0, 0], "normale": [0, 0, 1]}
    parties, _ = G.couper(mesh, coupe)
    for type_ in C.CATALOGUE:
        p, extras, rapport = C.appliquer(mesh, parties, coupe, {"type": type_})
        assert all(m.is_volume for m in (p | extras).values())
        assert rapport["reassemblage"]["statut"] == "PASS"
        assert rapport["validation_physique"] is False


def test_surface_courbe_et_export_3mf_reouvert(tmp_path):
    source = tmp_path / "courbe.stl"
    trimesh.creation.box((40, 40, 40)).export(source)
    plan = A.nouveau_plan(source, "mm", 2)
    angle = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    xy = np.c_[np.cos(angle), np.sin(angle)]
    xy *= 20 / np.max(np.abs(xy), axis=1)[:, None]
    plan["coupes"] = [
        {
            "type": "courbe",
            "points": np.c_[xy, 3 * np.sin(2 * angle)].tolist(),
            "normale": [0, 0, 1],
            "fermee": True,
            "connecteur": {"type": "aucun"},
        }
    ]
    resultat = A.calculer(plan)
    sortie = A.exporter(resultat, tmp_path / "export", "3mf")
    rapport = json.loads((sortie / "decoupe.json").read_text())
    assert rapport["reassemblage"]["statut"] == "PASS"
    assert len(list(sortie.glob("*.3mf"))) == 2
    assert all(lire(p).to_mesh().is_volume for p in sortie.glob("*.3mf"))


def test_editeur_enregistre_point_modifie_puis_exporte_dans_le_projet(tmp_path, monkeypatch):
    prive = tmp_path / "prive"
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    projet = prive / "projets/fixture"
    source = projet / "sources/forme.stl"
    source.parent.mkdir(parents=True)
    trimesh.creation.box((40, 40, 40)).export(source)
    plan = A.nouveau_plan(source, "mm", 2)
    origine = projet / "preuves/visualisation/decoupe.html"
    page(plan, origine, prive=prive)
    plan["coupes"][0].update(type="courbe", connecteur={"type": "aucun"})
    plan["coupes"][0]["points"][0][0] += 1
    recalcul = agir(prive, {"action": "recalculer", "page": origine.relative_to(prive).as_posix(), "plan": plan})
    html = prive / recalcul["page"].lstrip("/")
    enregistre = json.loads(html.with_suffix(".html.decoupe.json").read_text())["plan"]
    assert enregistre["coupes"][0]["points"] == plan["coupes"][0]["points"]
    export = agir(prive, {"action": "exporter", "page": html.relative_to(prive).as_posix(), "plan": enregistre})
    assert json.loads((prive / export["dossier"] / "decoupe.json").read_text())["reassemblage"]["statut"] == "PASS"
