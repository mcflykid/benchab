"""Consultation locale des contrats existants ; aucune fabrication ni migration."""

import hashlib
import html
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import quote, urlencode

from benchab.chemins import atelier_prive
from benchab.visualisation.catalogue_visuel import catalogue, projet_courant, racine_privee
from benchab.visualisation.stockage_commentaires import ouvrir_dossier, sans_liens


def texte(value):
    return html.escape(str(value), quote=True)


def url(projet="", **kw):
    return "/atelier?" + urlencode({"projet": projet, **kw})


def lire_source(root, rel):
    """Petit document déclaré, descripteur régulier épinglé, pas un accès arbitraire."""
    root = atelier_prive(root)
    rel = Path(rel)
    if rel.is_absolute() or ".." in rel.parts or "\\" in str(rel):
        raise ValueError("Chemin interdit")
    path = sans_liens(root / rel)
    path.relative_to(root)
    directory = ouvrir_dossier(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise ValueError("Document absent, non régulier ou trop volumineux")
        body = stream.read(2 * 1024 * 1024 + 1)
        apres = os.fstat(stream.fileno())
        if len(body) > 2 * 1024 * 1024 or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            apres.st_size,
            apres.st_mtime_ns,
            apres.st_ctime_ns,
        ):
            raise ValueError("Document modifié pendant lecture")
    return (body.decode("utf-8"), hashlib.sha256(body).hexdigest())


STYLE = (
    ".marque{display:flex;align-items:center;gap:14px;text-decoration:"
    "none;color:#013f66;margin-bottom:20px}.marque strong{font-size:24"
    "px}.marque img{border-radius:14px}h1,h2{color:#013f66}header{bord"
    "er-top:5px solid #eb9e51}a:hover{text-decoration:underline}body{m"
    "argin:0;background:#efede6;color:#2c3438;font:16px/1.5 -apple-sys"
    "tem,system-ui,sans-serif}main,header{max-width:1100px;margin:auto"
    ";padding:24px}header{border-bottom:1px solid #c7d1df}nav,.actions"
    "{display:flex;gap:16px;flex-wrap:wrap}h1{font-size:32px;margin:20"
    "px 0 6px}h2{margin-top:32px}a{color:#007878}a:focus-visible,input"
    ":focus-visible,select:focus-visible,button:focus-visible{outline:"
    "3px solid #007878;outline-offset:3px}input,button,select{font:inh"
    "erit;padding:8px;max-width:100%}button,.actions a{border:1px soli"
    "d #75869a;border-radius:5px;background:white;color:#263448;text-d"
    "ecoration:none;padding:8px 12px}table{width:100%;border-collapse:"
    "collapse;background:white;text-align:left}td,th{padding:12px;bord"
    "er-bottom:1px solid #d7dfe8;vertical-align:top}.table{overflow:au"
    "to}.carte{background:white;padding:20px;margin:16px 0;border:1px "
    "solid #d7dfe8;border-radius:6px}.note{color:#485b70;max-width:80c"
    "h}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:whit"
    "e;border:1px solid #d7dfe8;padding:16px}code{overflow-wrap:anywhe"
    "re}.badge{font-size:14px;border:1px solid #75869a;border-radius:3"
    "px;padding:2px 6px}summary{cursor:pointer}label{display:block;fon"
    "t-weight:600}form{margin:16px 0}.erreur{border-left:5px solid #9a"
    "3412;padding-left:16px}@media(max-width:600px){header,main{paddin"
    "g:16px}h1{font-size:26px}td,th{padding:8px}}"
)


def document(titre, corps):
    return (
        (
            '<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta '
            'name="viewport" content="width=device-width,initial-scale=1"><tit'
            "le>"
        )
        + texte(titre)
        + ' — BENCHAB 3D</title><link rel="icon" type="image/png" href="/docs/identite/benchab3d-icone.png"><style>'
        + STYLE
        + (
            '</style></head><body><header><a class="marque" href="/atelier"><i'
            'mg src="/docs/identite/benchab3d-icone.png" alt="" width="64" hei'
            'ght="64"><span><strong>BENCHAB 3D</strong><br>Concevoir. Imprimer'
            '. Apprendre.</span></a><nav aria-label="Atelier"><a href="/atelie'
            'r">Projets et pièces</a><a href="/atelier?vue=connaissances">Conn'
            'aissances</a><a href="/atelier?vue=cloud">Cloud Anycubic</a><a hr'
            'ef="/">Visionneuses conservées</a></nav></header><main><h1>'
        )
        + texte(titre)
        + "</h1>"
        + corps
        + (
            '<p class="note">Consultation numérique locale. Un contrôle ou un '
            "aperçu ne valide pas le fonctionnement physique. Aucun lancement "
            "de fabrication depuis cette page.</p></main></body></html>"
        )
    ).encode()


def tableau(entetes, lignes):
    return (
        '<div class="table"><table><thead><tr>'
        + "".join(('<th scope="col">' + texte(x) + "</th>" for x in entetes))
        + "</tr></thead><tbody>"
        + "".join(("<tr>" + "".join(("<td>" + x + "</td>" for x in row)) + "</tr>" for row in lignes))
        + "</tbody></table></div>"
    )


def lien(href, label):
    return '<a href="' + texte(href) + '">' + texte(label) + "</a>"


def pieces_projet(root, nom, q):
    p = Path(root) / "projets" / nom
    rel = q.get("manifeste", "candidats/assemblage.json")
    from benchab.qualite.publication import chemin_projet

    fichier = chemin_projet(root, p, rel)
    if not fichier.is_file():
        return []
    body, _ = lire_source(root, str(fichier.relative_to(root)))
    m = json.loads(body)
    return [str(x.get("name", x.get("nom", ""))) for x in m.get("parts", []) if isinstance(x, dict)] or list(
        m.get("models", m.get("pieces", {}))
    )


def preparations(root, nom):
    out = {}
    p = Path(root) / "projets" / nom
    for f in sorted((p / "preparations").glob("P[0-9]*/PREPARATION.json")):
        if not re.fullmatch("P[0-9]+", f.parent.name):
            continue
        raw, sha = lire_source(root, str(f.relative_to(root)))
        out[f.parent.name] = {"donnees": json.loads(raw), "sha256": sha, "chemin": str(f.relative_to(root))}
    return out


def preuves_declarees(root, nom, q, preps):
    """Seuls les documents explicitement désignés peuvent être consultés."""
    base = "projets/" + nom + "/"
    from benchab.qualite.publication import chemin_projet

    project_path = Path(root) / "projets" / nom
    out = []
    for rel in q.get("suivi", {}).get("preuves_utiles", []):
        path = chemin_projet(root, project_path, rel)
        out.append(str(path.relative_to(root)))
    for ref in q.get("suivi", {}).get("references_imprimees", []) + q.get("suivi", {}).get(
        "references_historiques", []
    ):
        rel = ref.get("preuve")
        if rel:
            if rel.startswith("suivi/journal/"):
                out.append(rel)
            else:
                path = chemin_projet(root, project_path, rel)
                out.append(str(path.relative_to(root)))
    for prep in preps.values():
        out.append(prep["chemin"])
        for rel in prep["donnees"].get("controles", {}):
            out.append(rel if rel.startswith(base) else base + rel)
    return list(
        dict.fromkeys(
            (
                x
                for x in out
                if x.startswith((base, "suivi/journal/"))
                and Path(x).suffix in (".md", ".json")
                and (".." not in Path(x).parts)
            )
        )
    )


def aide_ouverture():
    return (
        '<details class="carte"><summary>Quel fichier ouvrir ?</summary>\n<'
        "p><strong>Pour consulter un projet : choisissez son nom dans le c"
        "atalogue ci-dessous.</strong> Inspecter les pièces affiche la géo"
        "métrie déclarée ; Parcourir les couches conservées affiche le G-c"
        "ode disponible. Aucun fichier 3MF à sélectionner dans le Finder.<"
        "/p>\n<table><caption>Dans Anycubic Slicer Next</caption><thead><tr"
        "><th>Fichier</th><th>Usage</th></tr></thead><tbody>\n<tr><td>plaqu"
        "es.3mf</td><td>Disposition et réglages avant tranchage ; ne prouv"
        "e pas que le tranchage a été effectué.</td></tr>\n<tr><td>tranche."
        "3mf, parfois tranches.3mf, ou *.gcode.3mf</td><td>Résultat conser"
        "vé du tranchage si des couches G-code sont embarquées ; le nom se"
        "ul ne suffit pas.</td></tr>\n<tr><td>*.stl ou *.step</td><td>Géomé"
        "trie seule, sans parcours d’impression ni réglages de projet comp"
        "lets.</td></tr>\n</tbody></table><p>Pour une préparation figée, co"
        "nsulter son dossier Pnn et son état avant de choisir un fichier. "
        "Une référence déjà imprimée reste une archive protégée.</p>\n<p>L’"
        "import de fichiers externes par sélection ou glisser-déposer n’es"
        "t pas disponible dans cet atelier. Ouvrir un 3MF externe dans Any"
        "cubic Slicer Next ne lance aucune impression.</p>\n<p><strong>État"
        " de livraison :</strong> application web locale disponible ; appl"
        "ication macOS autonome et Codex intégré reportés après étude. Pou"
        "r demander une analyse à Codex, utiliser la conversation existant"
        "e avec le nom du projet.</p></details>"
    )


def page_catalogue(root, recherche=""):
    query = recherche.casefold().strip()
    rows = []
    for projet in catalogue(root):
        nom = projet["nom"]
        indisponible = "Géométrie non déclarée ou recherche par projet"
        try:
            _, q = projet_courant(root, nom)
            noms = pieces_projet(root, nom, q)
        except (ValueError, OSError, TypeError):
            noms = []
            indisponible = "Pièces non consultables : source non exploitable"
        if query and query not in " ".join([projet["titre"], nom, *noms]).casefold():
            continue
        correspondances = [x for x in noms if not query or query in x.casefold()]
        rows.append(
            [
                lien(url(nom), projet["titre"]),
                texte(projet.get("statut_lisible", projet["etat"])),
                texte(", ".join(correspondances) or indisponible),
                lien(url(nom), "Ouvrir le projet"),
            ]
        )
    form = (
        '<form><label for="recherche">Rechercher un projet ou une pièce</label><input id="recherche" name="q" value="'
        + texte(recherche)
        + '"><button>Rechercher</button></form>'
    )
    return document(
        "Projets et pièces",
        aide_ouverture()
        + form
        + "<p>"
        + str(len(rows))
        + " projet(s) trouvé(s). Les pièces et références imprimées restent distinguées.</p>"
        + tableau(["Projet", "État du projet", "Pièces déclarées", "Détails"], rows),
    )


def page_connaissances(root, recherche=""):
    from benchab.connaissances.memoire import chercher

    resultats = chercher(root, recherche)
    body = (
        (
            '<form><input type="hidden" name="vue" value="connaissances"><labe'
            'l for="recherche">Rechercher dans les connaissances</label><input'
            ' id="recherche" name="q" value="'
        )
        + texte(recherche)
        + '"><button>Rechercher</button></form><p>'
        + str(len(resultats))
        + " résultat(s), au plus100affichés.</p>"
    )
    for k in resultats[:100]:
        body += (
            '<article class="carte"><h2>'
            + texte(k["titre"])
            + '</h2><p><span class="badge">'
            + texte(k["niveau_preuve"])
            + "</span> "
            + texte(k["validite"])
            + "</p><p>"
            + texte(k.get("conditions", ""))
            + "</p><p>Limites : "
            + texte(k.get("exclusions", ""))
            + "</p><details><summary>Identité et provenance</summary><pre>"
            + texte(json.dumps({"id": k["id"], "sources": k["sources"]}, ensure_ascii=False, indent=2))
            + "</pre></details></article>"
        )
    return document("Connaissances", body)


def page_automatisation(root, nom, params):
    from benchab.organisation.automatisation import cache_projet, planifier
    from benchab.organisation.synthese_atelier import bilan, comparer_preparations, connaissances

    ident = params.get("preparation")
    plan = planifier(root, nom, ident)
    ident = plan["preparation"]
    if ident is not None and (not re.fullmatch("P[0-9]+", ident)):
        raise ValueError("Préparation invalide")
    body = lien(url(nom), "Retour au projet") + (
        "<p>Contrôles locaux, résumé, supports, vues et reprise. Les sorti"
        "es de calcul restent hors du dépôt. Aucun envoi à l’imprimante.</"
        "p>"
    )
    body += "<h2>Préparation guidée</h2><pre>" + texte(json.dumps(plan, ensure_ascii=False, indent=2)) + "</pre>"
    preps = preparations(root, nom)
    body += (
        '<form><input type="hidden" name="projet" value="'
        + texte(nom)
        + (
            '"><input type="hidden" name="controle" value="automatisation"><la'
            'bel for="prep">Préparation à contrôler</label><select name="prepa'
            'ration" id="prep">'
        )
        + "".join(("<option" + (" selected" if k == ident else "") + ">" + texte(k) + "</option>" for k in preps))
        + "</select><button>Choisir cette préparation</button></form>"
    )
    if not plan["blocages"]:
        body += (
            '<button type="button" data-auto="executer">Calculer le dossier de'
            ' contrôle et les vues</button> <button type="button" data-auto="o'
            'uvrir-slicer">Ouvrir une copie dans le slicer</button>'
        )
    else:
        body += (
            (
                "<p>Compléter les prérequis affichés avant le calcul. Le terminal "
                "permet de planifier puis créer une nouvelle préparation depuis le"
                "s candidats : <code>make preparer PROJET="
            )
            + texte(nom)
            + "</code>.</p>"
        )
    body += '<p id="resultat-auto" role="status" aria-live="polite"></p>'
    body += (
        (
            '<script>for(const b of document.querySelectorAll("[data-auto]")){'
            'b.addEventListener("click",async()=>{const out=document.getElemen'
            'tById("resultat-auto");b.disabled=true;out.textContent="Calcul en'
            ' cours…";try{const r=await fetch("/__automatisation",{method:"POS'
            'T",headers:{"Content-Type":"application/json"},body:JSON.stringif'
            "y({action:b.dataset.auto,projet:"
        )
        + json.dumps(nom)
        + ",preparation:"
        + json.dumps(ident)
        + (
            "})});out.textContent=JSON.stringify(await r.json(),null,2);}catch"
            '(e){out.textContent="Exécution indisponible : consulter le diagno'
            'stic local.";}finally{b.disabled=false;}});}</script>'
        )
    )
    if ident and ident in preps:
        for title, data in (
            ("Résumé, réglages et supports", bilan(root, nom, ident)),
            ("Connaissances et limites de contexte", connaissances(root, nom)),
            ("Cache et motifs de réutilisation", cache_projet(root, nom)),
        ):
            body += (
                "<details><summary>"
                + title
                + "</summary><pre>"
                + texte(json.dumps(data, ensure_ascii=False, indent=2))
                + "</pre></details>"
            )
        a = params.get("a")
        b = params.get("b")
        if a in preps and b in preps:
            body += (
                "<h2>Écarts de réglages expliqués</h2><pre>"
                + texte(json.dumps(comparer_preparations(root, nom, a, b), ensure_ascii=False, indent=2))
                + "</pre>"
            )
    return document("Préparation et diagnostic — " + nom, body)


def page_cloud(root):
    """Compose le HTML de lecture Cloud, sans unité ni statut matériel ; aucun accès compte, réseau ou mot de passe."""
    from benchab.machine.cloud_lecture import etat

    state = etat()
    configured = state.get("compte_autonome", {}).get("statut") == "COMPTE_CONFIGURE_NON_VALIDE"
    status = (
        "Compte configuré sur ce Mac ; connexion à vérifier sur demande."
        if configured
        else "Première configuration du compte requise sur ce Mac."
    )
    body = (
        "<p>Lecture seule, sur demande. Ouvrir cette page ne contacte pas "
        "Anycubic et ne lit aucun mot de passe ni session du slicer.</p>"
    )
    body += (
        '<section class="carte"><h2>Compte Anycubic autonome</h2><p id="cloud-account-status">' + texte(status) + "</p>"
    )
    body += (
        '<button id="cloud-account-button" type="button">Lire avec mon com'
        "pte configuré</button><details><summary>Configurer mon compte une"
        " première fois</summary><p>Dans le terminal de ce Mac, depuis BEN"
        "CHAB 3D :</p><pre>uv run benchab machine session-compte -- --conf"
        "igurer</pre><p>L’e-mail puis le mot de passe sont saisis dans le "
        "terminal, le mot de passe sans écho. Ils restent dans un fichier "
        "privé sur ce Mac. Ne les saisir ni dans cette page ni dans un cha"
        "t. Les renouvellements sont ensuite gérés lors des lectures.</p><"
        '/details></section><label for="cloud-machine">Imprimante à lire</'
        'label><select id="cloud-machine"><option value="">Lister mes impr'
        "imantes</option></select><details><summary>Autres modes de connex"
        'ion existants</summary><form id="cloud-form"><label for="cloud-to'
        'ken">Jeton Anycubic fourni manuellement</label><input id="cloud-t'
        'oken" type="password" autocomplete="off" maxlength="4096" require'
        'd><button id="cloud-button">Lire avec mon jeton</button> <button '
        'id="cloud-session-button" type="button">Lire ma session locale</b'
        "utton></form><p>La session locale est un autre mode, lié à la ver"
        "sion du slicer et seulement lu à votre demande explicite.</p></de"
        'tails><pre id="cloud-resultat" role="status" aria-live="polite"><'
        "/pre><p>Après la liste, choisir une imprimante puis utiliser le m"
        "ême bouton pour la lire. Le parcours compte ne transmet aucun mot"
        " de passe ni jeton au navigateur. Ses jetons sont conservés uniqu"
        "ement dans le cache privé du Mac. En cas de refus, attendre quelq"
        "ues secondes avant une nouvelle lecture. Les quantités ACE sont r"
        "apportées par le Cloud, sans pesée physique.</p>"
    )
    body += (
        '<script>\nconst form=document.getElementById("cloud-form"),token=d'
        'ocument.getElementById("cloud-token"),machine=document.getElement'
        'ById("cloud-machine"),out=document.getElementById("cloud-resultat'
        '"),direct=document.getElementById("cloud-button"),local=document.'
        'getElementById("cloud-session-button"),account=document.getElemen'
        'tById("cloud-account-button");\nasync function lireCloud(mode){\n  '
        'const sessionLocale=mode==="session",compte=mode==="compte";\n  di'
        "rect.disabled=true;local.disabled=true;account.disabled=true;out."
        'textContent="Connexion en lecture seule…";let garder=false;\n  con'
        "st demande={machine:machine.value||null};\n  if(compte){demande.co"
        'mpte=true;token.value="";}else if(sessionLocale){demande.session_'
        'slicer=true;token.value="";}else{demande.jeton=token.value;}\n  tr'
        'y{\n    const response=await fetch("/__cloud_lecture",{method:"POS'
        'T",headers:{"Content-Type":"application/json"},body:JSON.stringif'
        "y(demande)});\n    const data=await response.json();out.textConten"
        "t=JSON.stringify(data,null,2);\n    if(response.ok&&data.statut==="
        '"CHOISIR_MACHINE"){\n      machine.replaceChildren();\n      const '
        'choisir=document.createElement("option");choisir.value="";choisir'
        '.textContent="Choisir une imprimante";machine.append(choisir);\n  '
        "    for(const imprimante of data.machines){const option=document."
        'createElement("option");option.value=String(imprimante.id);option'
        '.textContent=(imprimante.modele||"Modèle non identifié")+" — "+St'
        "ring(imprimante.id);machine.append(option);}\n      garder=!sessio"
        'nLocale&&!compte;direct.textContent="Lire cette imprimante avec m'
        'on jeton";local.textContent="Lire cette imprimante via ma session'
        ' locale";account.textContent="Lire cette imprimante avec mon comp'
        'te";machine.focus();\n    }\n  }catch(error){out.textContent="Cloud'
        " indisponible : attendre quelques secondes avant une nouvelle lec"
        'ture. Aucune commande machine envoyée.";}\n  finally{if(!garder)to'
        'ken.value="";direct.disabled=false;local.disabled=false;account.d'
        'isabled=false;}\n}\nform.addEventListener("submit",event=>{event.pr'
        'eventDefault();lireCloud("jeton");});\nlocal.addEventListener("cli'
        'ck",()=>lireCloud("session"));\naccount.addEventListener("click",('
        ')=>lireCloud("compte"));\n</script>'
    )
    return document("Cloud Anycubic — lecture seule", body)


def page_projet(root, nom, params):
    """Construit une vue de projet privé; chemin relatif, sans écriture ni validation physique."""
    root = atelier_prive(root)
    p, q = projet_courant(root, nom)
    s = q.get("suivi", {})
    preps = preparations(root, nom)
    noms = pieces_projet(root, nom, q)
    refs = s.get("references_imprimees", [])
    preuves = preuves_declarees(root, nom, q, preps)
    if params.get("controle") == "automatisation":
        return page_automatisation(root, nom, params)
    if "preuve" in params:
        index = int(params["preuve"])
        if index < 0 or index >= len(preuves):
            raise ValueError("Preuve non déclarée")
        raw, sha = lire_source(root, preuves[index])
        return document(
            "Preuve déclarée",
            lien(url(nom), "Retour au projet")
            + "<p>"
            + texte(preuves[index])
            + "</p><p>SHA-256 du document lu : <code>"
            + sha
            + "</code></p><pre>"
            + texte(raw)
            + "</pre>",
        )
    body = (
        '<p><span class="badge">'
        + texte(s.get("statut", "NON_VERIFIE"))
        + "</span> — "
        + texte(s.get("reproductibilite", "Non vérifiée"))
        + '</p><p class="note">État du projet, pas une validation de toutes ses pièces. Identifiant : '
        + texte(s.get("id", "non renseigné"))
        + '.</p><div class="actions">'
    )
    item = next((x for x in catalogue(root) if x["nom"] == nom))
    if item["disponible"]:
        body += lien("/inspecter?" + urlencode({"projet": nom}), "Inspecter les pièces") + lien(
            "/paquet-inspection?" + urlencode({"projet": nom}), "Exporter le paquet hors ligne"
        )
    else:
        body += "<span>Scène indisponible : aucune géométrie déclarée.</span>"
    if item.get("couches"):
        body += lien("/couches?" + urlencode({"projet": nom}), "Parcourir les couches conservées")
    body += (
        lien(url(nom, controle="contrat"), "Vérifier le contrat du projet")
        + lien(url(nom, controle="automatisation"), "Préparation et diagnostic")
        + "</div>"
    )
    if params.get("controle") == "contrat":
        from benchab.qualite.publication import charger

        try:
            charger(p)
            verdict = "PASS — contrat de métadonnées du projet valide, aucune impression ni validité mécanique déduite."
        except (ValueError, OSError, TypeError, KeyError) as exc:
            verdict = "FAIL — " + str(exc)
        body += '<p role="status" class="carte">' + texte(verdict) + "</p>"
    body += "<h2>Pièces et références</h2>" + tableau(
        ["Pièce déclarée", "Références physiques associées"],
        [
            [
                texte(n),
                texte(", ".join((str(x["id"]) for x in refs if x.get("id") == n)) or "Non détaillées pour cette pièce"),
            ]
            for n in noms
        ],
    )
    if not noms:
        body += "<p>Pièces non individualisées dans les sources conservées.</p>"
    body += (
        "<details><summary>Historique et références déclarés</summary><pre>"
        + texte(
            json.dumps(
                {
                    "references_imprimees": refs,
                    "evenements": s.get("evenements", []),
                    "physique": s.get("physique", {}),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        + "</pre></details><h2>Préparations conservées</h2>"
    )
    body += tableau(
        ["Préparation", "Statut déclaré", "Date", "Provenance"],
        [
            [
                texte(k),
                texte(v["donnees"].get("statut", "NON_VERIFIE")),
                texte(v["donnees"].get("date", "non renseignée")),
                "<code>" + v["sha256"] + "</code>",
            ]
            for k, v in preps.items()
        ],
    )
    body += (
        "<p>Préparation courante : "
        + texte(s.get("preparation_courante", "Non renseignée"))
        + ". Une préparation candidate ne devient pas une référence imprimée.</p>"
    )
    if len(preps) >= 2:
        keys = list(preps)
        a = params.get("a", keys[0])
        b = params.get("b", keys[-1])
        if a not in preps or b not in preps:
            raise ValueError("Préparation inconnue")
        body += '<form><input type="hidden" name="projet" value="' + texte(nom) + '">'
        for name, label, val in (("a", "Préparation A", a), ("b", "Préparation B", b)):
            body += (
                '<label for="'
                + name
                + '">'
                + label
                + '</label><select id="'
                + name
                + '" name="'
                + name
                + '">'
                + "".join(("<option" + (" selected" if k == val else "") + ">" + texte(k) + "</option>" for k in keys))
                + "</select>"
            )
        body += lien(url(nom, controle="automatisation", a=a, b=b), "Voir les écarts détaillés de réglages")
        da, db = (preps[a]["donnees"], preps[b]["donnees"])
        champs = sorted(set(da) | set(db))
        diff = [k for k in champs if da.get(k) != db.get(k)]
        body += (
            '<button>Comparer les préparations</button></form><p id="comparaisonPreparations">'
            + texte(a + " → " + b)
            + " : champs différents : "
            + texte(", ".join(diff) or "aucun")
            + ". Comparaison des déclarations conservées, pas de géométrie ou de fabrication déduite.</p>"
        )
    body += (
        "<h2>Contrôles, réglages et preuves</h2><ul>"
        + "".join(("<li>" + lien(url(nom, preuve=i), rel) + "</li>" for i, rel in enumerate(preuves)))
        + (
            "</ul><p>Document déclaré ≠ contrôle fraîchement recalculé. Les ré"
            "glages conservés sont consultables dans ces preuves ; leur embarq"
            "uement actuel reste à vérifier.</p>"
        )
    )
    pages = [f for f in sorted((p / "preuves/visualisation").glob("*.html")) if f.name != "apercu-gcode.html"]
    body += (
        "<h2>Observations</h2><p>Les observations pointées utilisent les c"
        "ommentaires existants de la visionneuse, avec leur pièce et leurs"
        " coordonnées. Elles ne modifient pas le statut physique du projet"
        ".</p>"
    )
    if pages:
        body += (
            "<ul>"
            + "".join(
                (
                    "<li>"
                    + lien("/" + quote(str(f.relative_to(root))), "Ajouter une observation pointée — " + f.stem)
                    + "</li>"
                    for f in pages
                )
            )
            + "</ul>"
        )
    else:
        body += (
            "<p>Observation pointée indisponible : aucune visionneuse conservé"
            "e compatible. La création d’un journal reste disponible par l’out"
            "il existant, sans faux point3D.</p>"
        )
    body += "<p>" + lien("/atelier?vue=connaissances", "Consulter les connaissances") + "</p>"
    return document(s.get("titre", nom), body)


def page(root=None, params=None):
    """Construit une page locale depuis l'atelier choisi; aucune écriture ou lecture Cloud."""
    root = atelier_prive(root or racine_privee())
    params = params or {}
    if params.get("vue") == "cloud":
        return page_cloud(root)
    if len(params.get("q", "")) > 200:
        raise ValueError("Recherche trop longue")
    if params.get("vue") == "connaissances":
        return page_connaissances(root, params.get("q", ""))
    if params.get("projet"):
        return page_projet(root, params["projet"], params)
    return page_catalogue(root, params.get("q", ""))
