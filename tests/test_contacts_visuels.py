"""La carte doit appartenir au même modèle ; les points sont des calculs, pas des mesures physiques."""

import json
import re

import pytest
import trimesh

from benchab.controles.assemblage import analyser
from benchab.visualisation import visionneuse as V


def test_carte_liee_et_scene_conservee(tmp_path):
    source = tmp_path / "fixture.glb"
    a = trimesh.creation.box((4, 4, 4))
    b = a.copy()
    b.apply_translation((3, 0, 0))
    scene = trimesh.Scene()
    scene.add_geometry(a, node_name="a")
    scene.add_geometry(b, node_name="b")
    scene.export(source)
    rapport = analyser(source, mode="contacts", echantillons=30)
    r = tmp_path / "rapport.json"
    r.write_text(json.dumps(rapport))
    sortie = tmp_path / "carte.html"
    assert V.main([str(source), "--contacts", str(r), "--sortie", str(sortie), "--unites", "mm"]) == 0
    data = json.loads(re.search(r'id="donnees">(.*?)</script>', sortie.read_text(), re.S)[1])
    assert data["contacts"]["source_sha256"] == rapport["source_sha256"]
    assert data["contacts"]["contacts"][0]["patch"]
    assert {p["nom"] for p in data["pieces"]} == {"a", "b"}
    assert "Carte des ajustements" in sortie.read_text()
    rapport["source_sha256"] = "0" * 64
    r.write_text(json.dumps(rapport))
    with pytest.raises(ValueError, match="autre source"):
        V.main([str(source), "--contacts", str(r), "--sortie", str(tmp_path / "refuse.html"), "--unites", "mm"])
    assert not (tmp_path / "refuse.html").exists()
