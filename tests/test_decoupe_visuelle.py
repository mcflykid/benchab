"""Même moteur depuis la CLI et l'éditeur local ; aucun fichier de projet réel."""

import json
import threading
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest
import trimesh

from benchab.conception.decoupe import atelier as A
from benchab.visualisation.decoupe import agir, page
from benchab.visualisation.serveur import Serveur


@pytest.fixture
def atelier(tmp_path, monkeypatch):
    prive = tmp_path / "prive"
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    projet = prive / "projets/fixture"
    source = projet / "sources/forme.stl"
    source.parent.mkdir(parents=True)
    trimesh.creation.box((40, 40, 40)).export(source)
    plan = A.nouveau_plan(source, "mm", 2)
    html = projet / "preuves/visualisation/decoupe.html"
    page(plan, html, prive=prive)
    return prive, projet, source, plan, html


def test_editer_recalculer_reassembler_exporter_meme_geometrie(atelier):
    prive, projet, source, plan, html = atelier
    avant = source.read_bytes()
    plan["coupes"][0]["origine"][0] += 2
    plan["coupes"][0]["connecteur"]["type"] = "cheville-d"
    r = agir(prive, {"action": "recalculer", "page": html.relative_to(prive).as_posix(), "plan": plan})
    nouvelle = prive / r["page"].lstrip("/")
    meta = json.loads(nouvelle.with_suffix(".html.decoupe.json").read_text())
    assert meta["plan"]["coupes"][0]["origine"] == plan["coupes"][0]["origine"]
    assert nouvelle.is_file() and html.is_file()
    r = agir(
        prive,
        {"action": "exporter", "page": nouvelle.relative_to(prive).as_posix(), "plan": meta["plan"], "format": "stl"},
    )
    dossier = prive / r["dossier"]
    assert dossier.is_relative_to(projet / "candidats/decoupe")
    rapport = json.loads((dossier / "decoupe.json").read_text())
    assert rapport["reassemblage"]["statut"] == "PASS"
    assert rapport["coupes"][0]["connecteur"]["type"] == "cheville-d"
    assert len(list(dossier.glob("*.stl"))) == 2
    assert source.read_bytes() == avant


def test_modele_source_ne_peut_pas_etre_change_par_une_requete(atelier):
    prive, _, _, plan, html = atelier
    plan["source"]["fichier"] = "/source-interdite-de-fixture"
    with pytest.raises(ValueError, match="remplacé"):
        agir(prive, {"action": "recalculer", "page": html.relative_to(prive).as_posix(), "plan": plan})


def test_page_unicode_encodee_par_le_navigateur(atelier):
    prive, _, _, plan, _ = atelier
    html = prive / "projets/été/preuves/visualisation/découpe.html"
    page(plan, html, prive=prive)
    r = agir(prive, {"action": "recalculer", "page": quote(html.relative_to(prive).as_posix()), "plan": plan})
    assert (prive / r["page"].lstrip("/")).is_file()


@pytest.mark.parametrize("besoin,connecteur", [("mobile", "rotule-double"), ("renfort", "canal-tige")])
def test_reproposer_conserve_le_besoin_fonctionnel(atelier, besoin, connecteur):
    prive, _, source, _, html = atelier
    plan = A.nouveau_plan(source, "mm", 2, **{besoin: True})
    r = agir(prive, {"action": "proposer", "page": html.relative_to(prive).as_posix(), "plan": plan})
    nouvelle = prive / r["page"].lstrip("/")
    suivant = json.loads(nouvelle.with_suffix(".html.decoupe.json").read_text())["plan"]
    assert suivant["besoin_connecteurs"][besoin] is True
    assert suivant["coupes"][0]["connecteur"]["type"] == connecteur


def test_origine_exterieure_refusee_avant_calcul_http(atelier):
    prive, projet, _, plan, html = atelier
    serveur = Serveur(("127.0.0.1", 0), racine=prive)
    t = threading.Thread(target=serveur.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{serveur.server_port}"
        body = json.dumps({"action": "exporter", "page": html.relative_to(prive).as_posix(), "plan": plan}).encode()
        req = urllib.request.Request(
            url + "/__decoupe",
            data=body,
            headers={"Content-Type": "application/json", "Origin": "https://fixture.invalid"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 403
        assert not (projet / "candidats").exists()
        req = urllib.request.Request(
            url + "/__decoupe", data=body, headers={"Content-Type": "application/json", "Origin": url}
        )
        with urllib.request.urlopen(req) as response:
            r = json.loads(response.read())
        assert (prive / r["dossier"]).is_dir()
    finally:
        serveur.shutdown()
        serveur.server_close()
        t.join(timeout=3)
