"""Les fiches d'import lient une déclaration à une source synthétique."""

import hashlib
import json

import pytest
import trimesh
from test_lecture_3mf import fabriquer

from benchab.imports3d import importer
from benchab.qualite.publication import controler_modeles


@pytest.fixture
def modele(tmp_path):
    """Crée un cube local sans provenance tierce réelle."""
    p = tmp_path / "cube.stl"
    trimesh.creation.box().export(p)
    return p


def _fiche(modele):
    return {
        "source": "Catalogue synthétique, entrée cube",
        "auteur": "Atelier fictif",
        "licence": "Exemple de licence",
        "droits": "Usage privé déclaré",
        "sha256": hashlib.sha256(modele.read_bytes()).hexdigest(),
    }


def test_attribution_et_reprise(modele, tmp_path):
    """La copie et sa reprise conservent l'attribution de la source originale."""
    fiche = _fiche(modele)
    _, audit = importer.auditer(modele, "mm", fiche_provenance=fiche)
    assert audit["provenance_modele"] == {
        "fiche": fiche, "statut": "RENSEIGNÉE", "manquants": [], "nature": "déclarée"
    }
    rapport = tmp_path / "rapport.json"
    importer.preparer(
        modele, tmp_path / "copie.stl", "mm", rapport_sortie=rapport,
        fiche_provenance=audit["provenance_modele"]["fiche"],
    )
    relu = json.loads(rapport.read_text())
    assert relu["provenance_modele"] == audit["provenance_modele"]
    assert relu["source_sha256"] == fiche["sha256"]


def test_provenance_inconnue_ou_partielle(modele):
    """Un texte ancien ou une déclaration partielle ne prouve pas l'origine."""
    _, ancien = importer.auditer(modele, "mm", "ancienne note")
    assert ancien["provenance"] == "ancienne note"
    assert ancien["provenance_modele"]["statut"] == "NON VÉRIFIÉ"
    _, partiel = importer.auditer(modele, "mm", fiche_provenance={"auteur": "Atelier fictif"})
    assert partiel["provenance_modele"]["fiche"]["auteur"] == "Atelier fictif"
    assert partiel["provenance_modele"]["manquants"] == ["source", "licence", "droits", "sha256"]


def test_reprise_sur_un_modele_different_refusee(modele, tmp_path):
    """Une fiche liée à un autre SHA ne publie aucune copie ou rapport."""
    fiche = _fiche(modele)
    autre = tmp_path / "autre.stl"
    trimesh.creation.box(extents=(2, 2, 2)).export(autre)
    sortie = tmp_path / "copie.stl"
    rapport = tmp_path / "rapport.json"
    with pytest.raises(ValueError, match="empreinte"):
        importer.preparer(autre, sortie, "mm", rapport_sortie=rapport, fiche_provenance=fiche)
    assert not sortie.exists() and not rapport.exists()


def test_declaration_ne_permet_pas_publication(modele, tmp_path):
    """La frontière refuse le fichier même si le rapport d'import est complet."""
    _, audit = importer.auditer(modele, "mm", fiche_provenance=_fiche(modele))
    assert audit["provenance_modele"]["statut"] == "RENSEIGNÉE"
    assert any("cube.stl" in erreur for erreur in controler_modeles(tmp_path, set()))


def test_fiche_cli_et_audit_3mf(tmp_path):
    """La CLI conserve la fiche 3MF sans publier une conversion de la scène."""
    modele = tmp_path / "cube.3mf"
    fabriquer(modele, unite="centimeter")
    rapport = tmp_path / "rapport.json"
    options = [valeur for champ, contenu in _fiche(modele).items()
               for valeur in (f"--provenance-{champ}", contenu)]
    assert importer.main(["audit", str(modele), "--unites", "mm", "--rapport", str(rapport), *options]) == 0
    relu = json.loads(rapport.read_text())
    assert relu["provenance_modele"]["fiche"] == _fiche(modele)
    assert relu["unites_entree"] == "centimeter"
    assert relu["avant"]["dimensions_mm"] == [10, 20, 30]
    with pytest.raises(ValueError, match="parcours dédié"):
        importer.preparer(modele, tmp_path / "conversion.stl", "mm")


def test_audit_3mf_signale_les_modificateurs_exclus(tmp_path):
    """Un maillage valide ne masque pas les parties exclues du calcul."""
    from xml.etree import ElementTree as E
    from zipfile import ZipFile

    from benchab.imports3d.lecture_3mf import NS

    modele = tmp_path / "modifie.3mf"
    fabriquer(modele)
    with ZipFile(modele) as archive:
        principal = E.fromstring(archive.read("3D/3dmodel.model"))
        enfant = E.fromstring(archive.read("3D/Objects/piece.model"))
    parent = principal.find(".//" + NS + "components")
    attributs = dict(parent[0].attrib, objectid="2")
    E.SubElement(parent, NS + "component", attrib=attributs)
    ressources = enfant.find(NS + "resources")
    copie = E.fromstring(E.tostring(ressources[0]))
    copie.set("id", "2")
    ressources.append(copie)
    with ZipFile(modele, "w") as archive:
        archive.writestr("3D/3dmodel.model", E.tostring(principal))
        archive.writestr("3D/Objects/piece.model", E.tostring(enfant))
        archive.writestr("Metadata/model_settings.config", (
            '<config><object id="1"><part id="2" subtype="modifier_part"/></object></config>'
        ))

    _, audit = importer.auditer(modele, "mm")
    assert audit["statut"] == "WARN"
    assert audit["modificateurs_non_geometriques"] == [
        {"objet": "1", "partie": "2", "type": "modifier_part"}
    ]
    assert audit["avant"]["faces"] == 12
