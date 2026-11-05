"""Une anomalie de publication reste bloquante, y compris hors frontière privée."""

import json
import shutil
from pathlib import Path

import pytest

from benchab.qualite.edition import compatible, documentation, licences, typographie

R = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("expression", ["AGPL-3.0-only", "GPL-2.0-only", "inconnue", "MIT OR AGPL-3.0-only", "MIT AND"])
def test_licence_non_autorisee_refusee(expression):
    assert not compatible(expression)


def copie_licences(tmp_path):
    for nom in ("uv.lock", "pyproject.toml", "LICENSE", "TIERS.md", "benchab/reference/licences.json"):
        cible = tmp_path / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(R / nom, cible)
    for entree in json.loads((R / "benchab/reference/licences.json").read_text())["integres"]:
        cible = tmp_path / entree["fichier"]
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(R / entree["fichier"], cible)


@pytest.mark.parametrize("anomalie", ["AGPL", "version", "verrou", "notice", "tiers", "dependance"])
def test_inventaire_et_notices_obligatoires(tmp_path, anomalie):
    copie_licences(tmp_path)
    assert licences(tmp_path) == []
    p = tmp_path / "benchab/reference/licences.json"
    manifeste = json.loads(p.read_text())
    if anomalie == "AGPL":
        manifeste["python"][0]["licence"] = "AGPL-3.0-only"
    elif anomalie == "version":
        manifeste["python"][0]["version"] = "999"
    elif anomalie == "verrou":
        with (tmp_path / "uv.lock").open("a") as f:
            f.write("\n# changement non relu\n")
    elif anomalie == "notice":
        (tmp_path / manifeste["integres"][0]["fichier"]).write_text("notice supprimée")
    elif anomalie == "dependance":
        fichier = tmp_path / "pyproject.toml"
        fichier.write_text(fichier.read_text().replace('dependencies = [', 'dependencies = ["inconnu",', 1))
    else:
        (tmp_path / "TIERS.md").write_text("# Inventaire incomplet\n")
    p.write_text(json.dumps(manifeste))
    assert licences(tmp_path)


def test_liens_typographie_et_reseau(tmp_path):
    (tmp_path / "ok.md").write_text("# Une section\n")
    p = tmp_path / "README.md"
    p.write_text("[interne](ok.md#une-section) [externe](https://example.org/source)\n")
    visites = []
    assert documentation(tmp_path, lecteur_url=visites.append) == []
    assert visites == ["https://example.org/source"]
    p.write_text("[absent](absent.md) [ancre](ok.md#absente)\nTexte\ufffd  \n")
    erreurs = documentation(tmp_path, lecteur_url=visites.append)
    assert any("lien mort" in e for e in erreurs)
    assert any("ancre absente" in e for e in erreurs)
    assert any("typographique" in e for e in erreurs)
    assert any("fin de ligne" in e for e in erreurs)


def test_une_panne_reseau_ne_valide_pas_un_lien(tmp_path):
    (tmp_path / "README.md").write_text("[source](https://example.org/source)\n")

    def panne(_url):
        raise OSError("indisponible")

    assert any("HTTP inaccessible" in e for e in documentation(tmp_path, lecteur_url=panne))


def test_un_lien_prive_reste_bloquant(tmp_path):
    (tmp_path / "prive").mkdir()
    (tmp_path / "prive/secret.md").write_text("privé")
    (tmp_path / "README.md").write_text("[privé](prive/secret.md)")
    assert any("hors partie publique" in e for e in documentation(tmp_path))


@pytest.mark.parametrize("cible", ["absent.md", "prive/fait.md"])
def test_liens_de_reference_verifies(tmp_path, cible):
    (tmp_path / "prive").mkdir()
    (tmp_path / "prive/fait.md").write_text("privé")
    (tmp_path / "README.md").write_text(f"[source][fait]\n\n[fait]: {cible}\n")
    assert documentation(tmp_path, lecteur_url=lambda _url: None)


def test_autolien_http_est_effectivement_lu(tmp_path):
    (tmp_path / "README.md").write_text("<https://example.org/source>\n")
    visites = []
    assert documentation(tmp_path, lecteur_url=visites.append) == []
    assert visites == ["https://example.org/source"]


def test_liens_html_et_cibles_avec_espaces(tmp_path):
    (tmp_path / "un fichier.md").write_text("# Référence\n")
    p = tmp_path / "README.md"
    p.write_text('[lien](<un fichier.md>)\n[ref]: <un fichier.md>\n<a href="un fichier.md">page</a>\n')
    assert documentation(tmp_path) == []
    p.write_text('<img src="absent.png">\n')
    assert any("lien mort" in e for e in documentation(tmp_path))


@pytest.mark.parametrize("phrase", ["appui52,52mm", "mailléP01", "Épaisseur : 0,4mm"])
def test_collages_refuses_dans_document_et_rapport_json(tmp_path, phrase):
    p = tmp_path / "rapport.json"
    p.write_text(json.dumps({"resultat": {"detail": phrase}}))
    assert typographie([p])
    p = tmp_path / "README.md"
    p.write_text(phrase + "\n")
    assert documentation(tmp_path)


def test_texte_correct_et_identifiants_techniques_acceptes(tmp_path):
    p = tmp_path / "rapport.md"
    p.write_text("Appui 52,52 mm ; maillé P01 ; build123d, lib3mf ; Kobra S1 ; `objet8/projet5`.\n")
    assert typographie([p]) == []


def test_commande_typographie_bloque_un_rapport_genere(tmp_path):
    from benchab.cli import main

    prive = tmp_path / "atelier"
    from benchab.donnees.base import Base

    Base(prive, creer=True).fermer()
    rapport = tmp_path / "rapport.json"
    rapport.write_text(json.dumps({"detail": "appui52,52mm"}))
    assert main(["--prive", str(prive), "controle", "typographie", str(rapport)]) == 1
    rapport.write_text(json.dumps({"detail": "appui 52,52 mm"}))
    assert main(["--prive", str(prive), "controle", "typographie", str(rapport)]) == 0


def test_typographie_du_dossier_inclut_ses_rapports_json(tmp_path):
    dossier = tmp_path / "rapports"
    dossier.mkdir()
    (dossier / "rapport.json").write_text(json.dumps({"detail": "appui52,52mm"}))
    assert typographie([dossier])


def test_metadonnees_et_elements_html_distincts_ne_sont_pas_des_collages(tmp_path):
    p = tmp_path / "rapport.json"
    p.write_text(json.dumps({"sha256": "ab12" * 16, "espace": " ", "detail": "Appui 52,52 mm"}))
    assert typographie([p]) == []
    p = tmp_path / "vue.html"
    p.write_text('<span>Vue éclatée</span><output>0</output><script>const objet1=1;</script>')
    assert typographie([p]) == []
