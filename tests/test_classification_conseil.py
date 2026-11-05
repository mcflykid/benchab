"""Régressions du classement déterministe avant le Conseil."""

import unittest

from benchab.vote import permissions as O


class Classification(unittest.TestCase):
    """Cas de classement repris ; catégories et niveaux sans unité, aucun agent ou outil lancé."""

    def test_01_tache_triviale_sans_conseil(self):
        """Une correction documentaire reste catégorie A, niveau 1, sans vote ni machine."""
        c = O.classer("corriger une faute dans la documentation")
        self.assertEqual((c["categorie"], c["niveau"], c["conseil"]), ("A", 1, "non"))

    def test_02_operation_par_script(self):
        """Une automatisation déclarée passe au niveau zéro ; aucune commande n’est exécutée."""
        c = O.classer(
            "synchroniser le dossier et verifier les empreintes",
            cfg={"automatisations": [{"mots": ["synchroniser"], "commande": "sync.py"}]},
        )
        self.assertEqual(c["niveau"], 0)
        self.assertEqual(c["automatisations"][0]["commande"], "sync.py")
        self.assertEqual(c["specialistes"], [])

    def test_03_specialistes_cibles(self):
        """Les spécialistes du jeu mécanique sont bornés et les mots entiers distingués ; aucun vote."""
        c = O.classer("modifier le jeu de la charnière")
        self.assertEqual((c["categorie"], c["niveau"]), ("B", 2))
        self.assertIn("P1.cinematique", c["specialistes"])
        self.assertIn("P3.compatibilite", c["specialistes"])
        self.assertLessEqual(len(c["specialistes"]), 5)
        self.assertEqual(O.classer("modifier le jeu de la charnière", "ECONOMIE")["specialistes"].__len__(), 3)
        self.assertNotIn("cle", O.classer("relancer le cycle")["indices"]["B"])

    def test_verificateur_reserve_meme_si_selection_saturee(self):
        """Chaque mode réserve le vérificateur dans son budget de rôles ; aucun agent lancé."""
        texte = "charniere cadre mecanisme cliquet came cle securite tolerance impression vitesse filament montage"
        for mode, cfg in O.G["modes"].items():
            c = O.classer(texte, mode)
            self.assertIn("P3.compatibilite", c["specialistes"])
            self.assertLessEqual(len(c["specialistes"]), cfg["specialistes_max"])

    def test_risques_thermiques_et_surplombs_mobilisent_la_verification(self):
        """Thermique et surplombs déclenchent B et les deux spécialistes ; aucune preuve physique."""
        for texte in ["refroidissement des oeillets", "surplomb du couvercle", "couches courtes en fin de piece"]:
            c = O.classer(texte)
            self.assertEqual(c["categorie"], "B")
            self.assertIn("P2.impression", c["specialistes"])
            self.assertIn("P3.compatibilite", c["specialistes"])

    def test_04_decision_au_conseil(self):
        """Modifier une pièce imprimée reste C et demande accord ; aucun droit accordé par le test."""
        c = O.classer("modifier une pièce déjà imprimée")
        self.assertEqual((c["categorie"], c["niveau"], c["accord_utilisateur"]), ("C", 3, True))
