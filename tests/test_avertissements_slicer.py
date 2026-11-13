"""Contrats d’extraction des avertissements natifs du slicer."""

import zipfile

from benchab.controles.avertissements_slicer import controler


def _archive(path, xml):
    """Crée une fixture 3MF compacte pour l’extraction du membre qualifié."""
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        if xml is not None:
            archive.writestr("Metadata/slice_info.config", xml)


def test_envoi_conserve_avertissement_et_fait_dominer_refus(tmp_path, monkeypatch):
    """Une déclaration historique favorable ne neutralise pas un contrôle commun bloquant."""
    from benchab.controles import controle_envoi, envoi

    path = tmp_path / "sortie.3mf"
    _archive(path, '''<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
    <plate><metadata key="index" value="1"/>
    <warning msg="Message conservé" level="1" error_code="code_futur"/></plate></config>''')
    # Le contrôle historique dépend de la déclaration complète ; isoler son verdict
    # pour exercer l'archive réelle et le refus indépendant des identifiants absents.
    monkeypatch.setattr(controle_envoi, "controler", lambda *a, **k: {
        "statut": "PASS", "conforme_numerique": True, "resultats": [],
    })
    rapport = envoi.controler(path, declaration=tmp_path / "declaration.json")
    assert rapport["statut_historique"] == "PASS"
    assert rapport["statut"] == "FAIL"
    assert rapport["conforme_numerique"] is False
    natif = next(r for r in rapport["controles_communs"] if r["controle"] == "avertissements natifs du slicer")
    assert natif["statut"] == "WARN"
    assert natif["avertissements"][0]["code"] == "code_futur"
    assert rapport["validation_physique"] is False


def test_extrait_avertissements_par_plaque_et_conserve_code_inconnu(tmp_path):
    """Les deux codes cités et un code futur restent rattachés à leur plaque."""
    path = tmp_path / "slice.3mf"
    _archive(path, '''<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
    <plate><metadata key="index" value="1"/>
      <warning msg="Buse inconnue" level="1" error_code="vendor_new_code"/>
      <warning msg="Buse différente" level="1" error_code="the_actual_nozzle_hrc_smaller_than_the_required_nozzle_hrc"/>
      <warning msg="Lit trop chaud" level="1" error_code="bed_temperature_too_high_than_filament"/>
    </plate><plate><metadata key="index" value="2"/>
      <warning msg="Avertissement de plaque 2" level="1" error_code="second_plate_code"/>
    </plate></config>''')

    rapport = controler(path)

    assert rapport["statut"] == "WARN"
    assert rapport["avertissements"] == [{
        "plaque": 1, "code": "vendor_new_code", "message": "Buse inconnue",
        "origine": "slicer (Metadata/slice_info.config)",
    }, {
        "plaque": 1,
        "code": "the_actual_nozzle_hrc_smaller_than_the_required_nozzle_hrc",
        "message": "Buse différente",
        "origine": "slicer (Metadata/slice_info.config)",
    }, {
        "plaque": 1, "code": "bed_temperature_too_high_than_filament",
        "message": "Lit trop chaud",
        "origine": "slicer (Metadata/slice_info.config)",
    }, {
        "plaque": 2, "code": "second_plate_code",
        "message": "Avertissement de plaque 2",
        "origine": "slicer (Metadata/slice_info.config)",
    }]


def test_archive_sans_slice_info_est_non_verifiee(tmp_path):
    """L’absence du membre requis ne devient jamais un résultat vide PASS."""
    path = tmp_path / "sans.3mf"
    _archive(path, None)

    rapport = controler(path)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["avertissements"] == []


def test_archive_qualifiee_sans_avertissement_est_pass(tmp_path):
    """Une archive qualifiée sans warning renvoie PASS et aucun avertissement."""
    path = tmp_path / "propre.3mf"
    _archive(path, '''<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
    <plate><metadata key="index" value="1"/></plate></config>''')

    rapport = controler(path)

    assert rapport["statut"] == "PASS"
    assert rapport["code"] == 0
    assert rapport["avertissements"] == []


def test_structure_inconnue_est_non_verifiee(tmp_path):
    """Un warning hors d’une plaque n’est pas réinterprété silencieusement."""
    path = tmp_path / "inconnu.3mf"
    _archive(path, b"<config><warning msg='x' error_code='x'/></config>")

    assert controler(path)["statut"] == "NON VÉRIFIÉ"


def test_avertissement_incomplet_est_non_verifie(tmp_path):
    """Un code ou un niveau absent ne se transforme pas en warning incomplet."""
    path = tmp_path / "incomplet.3mf"
    _archive(path, '''<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
    <plate><metadata key="index" value="1"/><warning msg="x" level="1"/></plate></config>''')

    assert controler(path)["statut"] == "NON VÉRIFIÉ"


def test_origine_absente_garde_les_messages_trouves(tmp_path):
    """Un en-tête absent bloque le verdict mais n’efface pas les warnings lisibles."""
    path = tmp_path / "origine-absente.3mf"
    _archive(path, '''<config><plate><metadata key="index" value="1"/>
    <warning msg="Message conservé" level="1" error_code="new_code"/></plate></config>''')

    rapport = controler(path)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["avertissements"][0]["message"] == "Message conservé"
    assert rapport["avertissements"][0]["code"] == "new_code"
    assert rapport["avertissements"][0]["origine"] == "NON VÉRIFIÉ (Metadata/slice_info.config)"


def test_plaque_malformee_nefface_pas_les_messages_lisibles(tmp_path):
    """Une plaque sans index invalide la couverture sans effacer les autres alertes."""
    path = tmp_path / "partiel.3mf"
    _archive(path, '''<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
    <plate><metadata key="index" value="1"/><warning msg="Précédent" level="1" error_code="avant"/></plate>
    <plate><warning msg="Sans plaque" level="1" error_code="sans_index"/></plate>
    <plate><metadata key="index" value="3"/><warning msg="Suivant" level="1" error_code="apres"/></plate>
    </config>''')
    rapport = controler(path)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert [(r["plaque"], r["code"]) for r in rapport["avertissements"]] == [
        (1, "avant"), (None, "sans_index"), (3, "apres"),
    ]


def test_index_duplique_ne_qualifie_pas_le_rattachement(tmp_path):
    """Le code natif reste visible sans lui attribuer une plaque ambiguë."""
    path = tmp_path / "ambigu.3mf"
    _archive(path, '''<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
    <plate><metadata key="index" value="1"/><warning msg="Ambigu" level="1" error_code="x"/></plate>
    <plate><metadata key="index" value="1"/></plate></config>''')
    rapport = controler(path)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["avertissements"][0]["code"] == "x"
    assert rapport["avertissements"][0]["plaque"] is None


def test_type_client_inconnu_ne_qualifie_pas_origine(tmp_path):
    """Une chaîne arbitraire dans le XML n'atteste pas le type de producteur."""
    path = tmp_path / "client.3mf"
    _archive(path, '''<config><header><header_item key="X-BBL-Client-Type" value="futur"/></header>
    <plate><metadata key="index" value="1"/><warning msg="Conservé" level="1" error_code="x"/></plate></config>''')
    rapport = controler(path)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["avertissements"][0]["origine"].startswith("NON VÉRIFIÉ")


def test_reconnait_entete_client_anycubic_slicer_next(tmp_path):
    """L’en-tête ASN qualifie l’origine sans confondre plusieurs déclarations."""
    path = tmp_path / "asn.3mf"
    _archive(path, '''<config><header><header_item key="X-ACNext-Client-Type" value="slicer"/>
    <header_item key="X-ACNext-Client-Version" value="2.0.0.3 20260904033713"/></header>
    <plate><metadata key="index" value="1"/><warning msg="Alerte ASN" level="1" error_code="asn_code"/></plate>
    </config>''')

    rapport = controler(path)

    assert rapport["statut"] == "WARN"
    assert rapport["producteur_declare"] == {
        "famille": "Anycubic Slicer Next", "version": "2.0.0.3 20260904033713", "binaire_verifie": False,
    }
    assert rapport["avertissements"][0]["origine"] == "slicer (Metadata/slice_info.config)"


def test_entetes_client_bbl_et_asn_melanges_restent_ambigus(tmp_path):
    """Deux familles d’en-têtes contradictoires ne deviennent pas une origine certaine."""
    path = tmp_path / "mixte.3mf"
    _archive(path, '''<config><header><header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-ACNext-Client-Type" value="slicer"/></header>
    <plate><metadata key="index" value="1"/></plate></config>''')

    rapport = controler(path)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert "origine du slicer" in rapport["raison"]


def test_xml_hostile_est_non_verifie(tmp_path):
    """Le lecteur d’archive refuse DTD et entités externes avant analyse."""
    path = tmp_path / "hostile.3mf"
    _archive(path, b'<!DOCTYPE x [<!ENTITY a "x">]><config/>')

    assert controler(path)["statut"] == "NON VÉRIFIÉ"


def test_archive_borne_et_xml_malforme_sont_non_verifies(tmp_path):
    """Les limites XML et erreurs de syntaxe produisent NON VÉRIFIÉ."""
    path = tmp_path / "malforme.3mf"
    _archive(path, b"<config><plate>")

    assert controler(path, taille_max_xml=4)["statut"] == "NON VÉRIFIÉ"
    assert controler(path)["statut"] == "NON VÉRIFIÉ"
