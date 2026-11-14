# Préparer une pièce

1. Créer le projet : `uv run benchab projet ma-piece --niveau S`.
   M ajoute les interfaces ; L ajoute les contrôles spécifiques. Décrire l’usage,
   les dimensions et les faces visibles dans `prive/projets/ma-piece/BESOIN.md`.
2. Commencer une séance : `uv run benchab seance debut`. Conserver les sources
   intactes, choisir les unités et les références, puis construire ou importer.
   Consulter `uv run benchab imports importer -- --help` et
   `uv run benchab conception cad-engine -- --help`.
3. Utiliser le Makefile du projet pour construire, préparer et vérifier les variantes.
   Chaque cible appelle le Python de l’atelier. Ne pas déplacer un livrable figé
   pour le réutiliser comme candidate modifiable. Au plus trois candidates.
4. Inspecter géométrie, assemblage, orientation, faces visibles et supports dans la
   [visionneuse](visionneuse.md). Déclarer matière, buse, bobine et emplacements ACE.
   Les jeux mesurés sont prioritaires ; toute valeur supposée reste signalée.
5. Trancher par le [lanceur unique](../reference/fabrication.md), puis
   `uv run benchab controle envoi prive/projets/ma-piece/candidats/candidate.gcode.3mf`.
   Ajouter les preuves déclaratives requises ; leur absence reste un blocage.
6. Avant le vote, obtenir la [relecture critique indépendante](../reference/vote.md).
   Les nouveaux risques rejoignent A4. En cas d’unanimité ou de contrôles tous PASS,
   ajouter les trois scénarios d’échec et les points de surveillance au dossier d’envoi.
7. Relire le fichier final dans le slicer et remettre à l’utilisateur le dossier
   d’envoi exact. Un contrôle numérique ne lance aucune machine et ne vaut pas
   observation physique. `uv run benchab seance fin` clôt le journal et l’export.

Les contrôles restent bloquants sur les zones sans revue valide. Une référence
réussie sur une autre géométrie ne remplace pas ces vérifications.
