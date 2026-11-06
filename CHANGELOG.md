# Changements

Versions livrées 1.0.0 et 1.0.1.

## 1.0.1

- `codes-sortie` (#1) — Harmonisation des verdicts des contrôles et signalement d’une ouverture du slicer à l’issue inconnue.
- `integrite-plaques` (#2) — Comparaison exacte de l’affectation des instances aux plaques et des liaisons d’assemblage.
- `debit-stopiteration` (#3) — Plaque sans index signalée NON VÉRIFIÉE et diagnostic d’usage pour les options incomplètes.
- `envoi-indexerror` (#4) — Lecture typée des cases de température et de débit et verdict NON VÉRIFIÉ si la case de l’extrudeur manque.
- `seance-verrou` (#5) — Jeton de fermeture, expiration, reprise et trace des fermetures forcées.
- `publication-commit` (#6) — Commit et export avant publication des fiches, reprise sans doublon et mesures réexportées.
- `delais-processus` (#7) — Délai borné pour les anciennes étapes, les lectures Git et le nettoyage du pilote Safari.
- `gcode-g28` (#8) — Référencement G28 pris en compte et axe E commun lors des changements d’outil.
- `u20-regex` (#9) — Lecture XML des objets et placements, transform facultatif et XML invalide signalé.
- `u36-sha` (#10) — Protection des copies renommées par les empreintes déclarées dans qualite.json.
- `frontiere-prive` (#11) — Contrôle Git de l’atelier privé réellement sélectionné.
- `trancher-temporaires` (#15) — ZIP relu avant remplacement atomique et nettoyage des échecs de restauration.
- `decisions-statuts` (#16) — Portée explicite des règles et confinement des preuves et références durables.
- `petits-defauts` (#22) — Arrondi des durées, diagnostic SystemExit, export UTF-8, initialisation SQLite et nettoyage du manifeste temporaire.
- `feuille-1-0-1` (#25) — Ordre de correction traçable et installation complète par défaut, vérifiée sur clone neuf.
- Installation neuve : Playwright inclus par défaut, verrou conservé et protections du dépôt actives dans le temporaire système.
