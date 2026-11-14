"""Parcours Safari natif minimal via le WebDriver livré par macOS.

L'activation de l'automatisation appartient au propriétaire. Ce script ne change
aucun réglage et ne relance pas Safari. Une tentative, délais bornés, nettoyage
de sa seule session. Budgets : sélection ≤25 clics, autres boutons un clic.
"""

import argparse
import base64
import hashlib
import http.client
import json
import subprocess
import time
from pathlib import Path

from benchab.processus import _terminer
from benchab.qualite.reception import dossier as dossier_reception
from benchab.qualite.reception import url_locale


def parcours(page, sortie, port, v1=False, v2=False, inspection=False, atelier=False):
    """Teste les interactions d’une fixture locale.

    Renvoie les mesures en pixels et secondes ou lève sur FAIL. Aucune commande machine n’est émise.
    """
    if str(page).startswith(("http:", "https:")):
        page = url_locale(str(page))
    sortie = dossier_reception(sortie)
    record = {"page": str(page), "etapes": []}
    sid = None
    with (sortie / "driver.log").open("w") as log:
        proc = subprocess.Popen(
            ["/usr/bin/safaridriver", "--port", str(port)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )

        def req(method, path, data=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=25)
            try:
                connection.request(
                    method, path, json.dumps(data) if data is not None else None, {"Content-Type": "application/json"}
                )
                response = connection.getresponse()
                value = json.loads(response.read())
                if response.status >= 400:
                    raise RuntimeError(str(value))
                return value.get("value")
            finally:
                connection.close()

        def session(method, path, data=None):
            return req(method, "/session/" + sid + path, data)

        def js(code):
            return session("POST", "/execute/sync", {"script": code, "args": []})

        def click(css):
            value = session("POST", "/element", {"using": "css selector", "value": css})
            ident = value["element-6066-11e4-a52e-4f735466cecf"]
            session("POST", "/element/" + ident + "/click", {})
            time.sleep(0.1)  # laisser le tour d'événements natif se terminer

        def choisir(css, texte):
            value = session("POST", "/element", {"using": "css selector", "value": css})
            ident = value["element-6066-11e4-a52e-4f735466cecf"]
            index = js(
                "return [...document.querySelector("
                + json.dumps(css)
                + ").options].findIndex(o=>o.textContent==="
                + json.dumps(texte)
                + ")"
            )
            assert index >= 0, (css, texte)
            valeur = js("return document.querySelector(" + json.dumps(css) + ").options[" + str(index) + "].value")
            session("POST", "/element/" + ident + "/value", {"text": valeur})
            assert js("return document.querySelector(" + json.dumps(css) + ").value") == valeur
            record.setdefault("choix_observes", []).append(
                js('return [...document.querySelectorAll("select")].map(e=>({id:e.id,valeur:e.value}))')
            )

        def pointer(x, y):
            session(
                "POST",
                "/actions",
                {
                    "actions": [
                        {
                            "type": "pointer",
                            "id": "souris",
                            "parameters": {"pointerType": "mouse"},
                            "actions": [
                                {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y), "origin": "viewport"},
                                {"type": "pointerDown", "button": 0},
                                {"type": "pointerUp", "button": 0},
                            ],
                        }
                    ]
                },
            )

        try:
            for _ in range(30):
                if proc.poll() is not None:
                    raise RuntimeError("Le pilote Safari a quitté ; consulter driver.log")
                try:
                    record["driver_status"] = req("GET", "/status")
                    break
                except ConnectionRefusedError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Pilote Safari indisponible")
            value = req("POST", "/session", {"capabilities": {"alwaysMatch": {"browserName": "safari"}}})
            sid = value["sessionId"]
            record["capabilities"] = value["capabilities"]
            record["etapes"].append("session créée")
            session("POST", "/window/rect", {"width": 1440, "height": 1000})
            t = time.monotonic()
            session(
                "POST",
                "/url",
                {"url": str(page) if str(page).startswith(("http://", "https://")) else Path(page).resolve().as_uri()},
            )
            if atelier:

                def saisir(css, texte):
                    value = session("POST", "/element", {"using": "css selector", "value": css})
                    session(
                        "POST", "/element/" + value["element-6066-11e4-a52e-4f735466cecf"] + "/value", {"text": texte}
                    )

                assert js('return document.querySelector("h1").textContent') == "Projets et pièces"
                saisir("#recherche", "BaseB")
                click("main form button")
                click('main table a[href*="inspection-ete"]')
                choisir("#a", "P01")
                choisir("#b", "P02")
                click("main form button")
                assert "P01 → P02" in js('return document.getElementById("comparaisonPreparations").textContent')
                click('a[href*="controle=contrat"]')
                assert "PASS — contrat" in js('return document.querySelector("[role=status]").textContent')
                click('header a[href="/atelier?vue=connaissances"]')
                saisir("#recherche", "base")
                click("main form button")
                assert "Ne valide pas le contact réel" in js('return document.querySelector("main").textContent')
                click('header a[href="/atelier"]')
                click('main table a[href*="ancienne-lune"]')
                assert "aucune géométrie déclarée" in js('return document.querySelector("main").textContent')
                record.update(
                    statut="PASS",
                    catalogue_recherche_piece=True,
                    preparations_comparees=True,
                    contrat_lu=True,
                    connaissances=True,
                    historique=True,
                    limites=[
                        (
                            "Parcours HTML natif Safari ; observations pointées et écriture reçues séparéme"
                            "nt sur fixture Chromium, aucun projet réel modifié."
                        )
                    ],
                )
                (sortie / "ecran.png").write_bytes(base64.b64decode(session("GET", "/screenshot")))
            if not atelier:
                for _ in range(100):
                    if js("return !!window.visionneuse && visionneuse.pieces.every(p=>p.mesh)"):
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("Géométrie non chargée")
                record["chargement_s"] = time.monotonic() - t
                record["etapes"].append("géométrie affichée")
                total = js("return visionneuse.pieces.filter(p=>p.mesh.visible).length")
                assert total >= (1 if v2 else 2), "Nombre de pièces insuffisant pour le parcours choisi"
                rect = js(
                    'let r=document.querySelector("#vue canvas").getBoundingClientRect();'
                    "return {x:r.x,y:r.y,w:r.width,h:r.height}"
                )
                selected = False
                if v1 or v2 or inspection:
                    click("#listePieces button")
                    selected = bool(js("return visionneuse.etat().selection"))
                for fy in (0.5, 0.4, 0.6, 0.3, 0.7):
                    if selected:
                        break
                    for fx in (0.5, 0.4, 0.6, 0.3, 0.7):
                        pointer(rect["x"] + rect["w"] * fx, rect["y"] + rect["h"] * fy)
                        if js('return !document.querySelector("#isoler").disabled'):
                            selected = True
                            break
                    if selected:
                        break
                assert selected, "Sélection canvas absente"
                record["etapes"].append("sélection liste" if (v1 or v2 or inspection) else "sélection canvas")
                click("#isoler")
                assert js("return visionneuse.pieces.filter(p=>p.mesh.visible).length") == 1
                record["etapes"].append("isolement")
                click("#tout")
                assert js("return visionneuse.pieces.filter(p=>p.mesh.visible).length") == total
                record["etapes"].append("tout afficher")
                click("#recadrer")
                record["etapes"].append("recadrage")
                if v2:
                    click("#anomalies button")
                    d = js('return JSON.parse(document.getElementById("donnees").textContent)')
                    a = next(a for diag in d["diagnostics"] for a in diag["anomalies"])
                    piece = next(p for p in d["pieces"] if p["nom"] == a["piece"])
                    point = a["point_modele"] + [1]
                    m = piece["matrice"]
                    w = [sum(m[i * 4 + j] * point[j] for j in range(4)) for i in range(3)]
                    expected = [w[0], w[2], -w[1]] if d["axe_haut"] == "Z" else w
                    target = js("return visionneuse.etat().cible")
                    assert max(abs(x - y) for x, y in zip(expected, target)) < 1e-6, (expected, target)
                    click("#blocAnomalies summary")
                    assert not js('return document.getElementById("blocAnomalies").open')
                    pointer(rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2)
                    assert js('return document.getElementById("blocAnomalies").open')
                    assert js('return document.getElementById("diagnostic").dataset.anomalie') == a["id"]
                    assert js("return window.__erreursVisionneuse") == []
                    record.update(
                        cible_calculee=expected,
                        cible_observee=target,
                        source=a["source"],
                        sha256=a["sha256"],
                        erreurs_javascript=[],
                    )
                    record["etapes"].extend(
                        ["anomalie depuis liste", "centrage indépendant vérifié", "diagnostic retrouvé depuis marqueur"]
                    )
                if v1:
                    before = js("return visionneuse.etat().camera")
                    rect = js(
                        (
                            'let r=document.querySelector("#vue canvas").getBoundingClientRect();return {x:'
                            "r.x,y:r.y,w:r.width,h:r.height}"
                        )
                    )
                    x, y = int(rect["x"] + rect["w"] / 2), int(rect["y"] + rect["h"] / 2)
                    session(
                        "POST",
                        "/actions",
                        {
                            "actions": [
                                {
                                    "type": "pointer",
                                    "id": "souris",
                                    "parameters": {"pointerType": "mouse"},
                                    "actions": [
                                        {"type": "pointerMove", "duration": 0, "x": x, "y": y, "origin": "viewport"},
                                        {"type": "pointerDown", "button": 0},
                                        {
                                            "type": "pointerMove",
                                            "duration": 250,
                                            "x": x + 70,
                                            "y": y + 30,
                                            "origin": "viewport",
                                        },
                                        {"type": "pointerUp", "button": 0},
                                    ],
                                }
                            ]
                        },
                    )
                    time.sleep(0.3)
                    assert js("return visionneuse.etat().camera") != before, "Rotation Safari sans effet"
                    record["etapes"].append("rotation souris")
                    click("#initiale")
                    click("#listePieces button")
                    click("#masquer")
                    assert js("return visionneuse.etat().selection") is None
                    click("#tout")
                    click('#coupe option[value="2"]')
                    assert js("return visionneuse.etat().coupes") == 1
                    time.sleep(0.15)
                    image_before = js('return document.querySelector("#vue canvas").toDataURL()')
                    click("#inverse")
                    time.sleep(0.15)
                    assert js('return document.querySelector("#vue canvas").toDataURL()') != image_before, (
                        "Coupe Safari sans effet sur le rendu"
                    )
                    record["etapes"].append("coupe avec rendu")
                    click("#initiale")
                    assert js("return visionneuse.etat().coupes") == 0
                    # Contrôle du PNG proposé par la vraie commande, sans changer la préférence de
                    # téléchargement Safari.
                    js(
                        (
                            "window.__exportSafari=null;window.__clicOriginal=HTMLAnchorElement.prototype.c"
                            "lick;HTMLAnchorElement.prototype.click=function(){window.__exportSafari={nom:t"
                            "his.download,image:this.href};window.__clicOriginal.call(this)}"
                        )
                    )
                    dossier = Path.home() / "Downloads"
                    avant = {f: (f.stat().st_size, f.stat().st_mtime_ns) for f in dossier.glob("*.png")}
                    click("#image")
                    export = js("return window.__exportSafari")
                    assert export and export["image"].startswith("data:image/png;base64,")
                    (sortie / "export-propose.png").write_bytes(base64.b64decode(export["image"].split(",", 1)[1]))
                    record["etapes"].append("export PNG proposé et déclenché")
                    assert js("return window.__erreursVisionneuse") == []
                    record["erreurs_javascript"] = []
                    attendu = hashlib.sha256(base64.b64decode(export["image"].split(",", 1)[1])).hexdigest()
                    trouves = []
                    for _ in range(50):
                        for f in dossier.glob(Path(export["nom"]).stem + "*.png"):
                            if avant.get(f) == (f.stat().st_size, f.stat().st_mtime_ns):
                                continue
                            if hashlib.sha256(f.read_bytes()).hexdigest() == attendu:
                                trouves.append(str(f))
                        if trouves:
                            break
                        time.sleep(0.1)
                    assert trouves, "Téléchargement PNG Safari absent ou différent du PNG proposé"
                    record["export_telecharge"] = {"fichiers": trouves, "sha256": attendu}
                    record["etapes"].append("PNG téléchargé et identité vérifiée")
                if inspection:
                    choisir("#filtreRisques", "Bord ouvert")
                    click('#anomalies button[data-type="bord_ouvert"]')
                    assert "Bord ouvert" in js('return document.getElementById("diagnostic").textContent'), (
                        "diagnostic bord ouvert absent"
                    )
                    record["etapes"].append("bord ouvert localisé")
                    click("#blocMesures summary")
                    texte = js('return document.getElementById("mesuresSource").textContent')
                    assert "Volume du maillage : non vérifié" in texte and "3 bords ouverts" in texte, texte
                    record["etapes"].append("mesures source")
                    click("#blocComparaison summary")
                    choisir("#compareA", "BaseA")
                    choisir("#compareB", "BaseB")
                    click("#comparerGeometries")
                    texte = js('return document.getElementById("comparaison").textContent')
                    assert "Écart de volume : 2 mm³" in texte, texte
                    click("#ouvrirCouches")
                    frame = session("POST", "/element", {"using": "css selector", "value": "#couchesFrame"})
                    session("POST", "/frame", {"id": frame})
                    for _ in range(100):
                        if js("return !!window.apercu && !!window.apercu3d"):
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Couches Safari non chargées")
                    js(
                        (
                            'window.__erreursCouches=[];addEventListener("error",e=>__erreursCouches.push(e'
                            '.message));addEventListener("unhandledrejection",e=>__erreursCouches.push(Stri'
                            "ng(e.reason)))"
                        )
                    )
                    click("#plus")
                    assert js("return apercu.couche") == 1
                    click("#o3d")
                    assert js('return !!document.querySelector("#vue3d canvas")')
                    assert js("return window.__erreursCouches") == []
                    session("POST", "/frame", {"id": None})
                    click("#fermerCouches")
                    assert js(
                        (
                            'return document.getElementById("panneauCouches").hidden && !document.getElemen'
                            'tById("couchesFrame").hasAttribute("srcdoc")'
                        )
                    )
                    assert js("return window.__erreursVisionneuse") == []
                    record.update(
                        inspection_source=True,
                        comparaison_geometries=True,
                        couches_2d_3d=True,
                        fermeture_couches=True,
                        erreurs_javascript=[],
                        erreurs_couches_apres_chargement=[],
                    )
                    record["etapes"].extend(
                        [
                            "bord ouvert localisé",
                            "mesures source",
                            "comparaison de volume",
                            "couches 2D/3D",
                            "retour et libération du document",
                        ]
                    )
                png = base64.b64decode(session("GET", "/screenshot"))
                assert png.startswith(b"\x89PNG\r\n\x1a\n")
                (sortie / "ecran.png").write_bytes(png)
                record.update(
                    statut="PASS",
                    limites=[
                        "Inspection numérique synthétique, aucune validation physique"
                        if inspection
                        else "Localisation V2 calculée, aucune validation physique"
                        if v2
                        else "Parcours étendu V1"
                        if v1
                        else "Parcours minimal ; rotation, coupe, export et réception V1 restent séparés",
                        "Collecte des erreurs/rejets via le gabarit ; iframe après chargement si inspection"
                        if (v1 or v2 or inspection)
                        else "Le protocole classique ne fournit pas ici la collecte des erreurs JavaScript",
                    ],
                )
        except Exception as exc:
            statut = "FAIL" if sid or isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else "NON_VERIFIE"
            record.update(statut=statut, erreur=type(exc).__name__ + ": " + str(exc))
        finally:
            if sid:
                try:
                    session("DELETE", "")
                except (OSError, ValueError, RuntimeError) as exc:
                    record["nettoyage_erreur"] = str(exc)
                    record["statut"] = "FAIL"
            # Le pilote et les enfants qu’il a lancés appartiennent à ce
            # groupe, distinct du Safari déjà ouvert (contrôles.md).
            _terminer(proc, 0.5)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                record.update(statut="FAIL", nettoyage_erreur="Délai d’arrêt du groupe Safari dépassé")
    (sortie / "resultat.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return int(record["statut"] != "PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True)
    parser.add_argument("--v1", action="store_true")
    parser.add_argument("--v2", action="store_true")
    parser.add_argument("--inspection", action="store_true")
    parser.add_argument("--atelier", action="store_true")
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--port", type=int, default=51251)
    args = parser.parse_args()
    raise SystemExit(parcours(args.page, args.sortie, args.port, args.v1, args.v2, args.inspection, args.atelier))
