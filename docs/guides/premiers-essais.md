# Premiers essais d’une machine ou d’une bobine

Choisir ce que l’essai doit établir : vérifier une nouvelle machine, comparer
une bobine à une référence, ou mesurer un ajustement pour une pièce. Consigner
dans `prive/` la machine, la buse, la bobine et le profil utilisés. Conserver une
référence et ne changer qu’un facteur à la fois pour pouvoir interpréter l’écart.

1. **Préparer le contexte.** Pour une autre machine, suivre le [guide d’adaptation](autre-imprimante.md).
   Pour une bobine, suivre le [guide matière](matieres-et-retours.md), relever la
   plage déclarée et garder les profils installés intacts. S’il existe un essai
   embarqué adapté, identifier la procédure du fabricant, le modèle et la version
   auxquels elle s’applique. Sinon, passer directement au témoin. Un résultat
   embarqué se consigne avec ses conditions ; il ne remplace pas le témoin.
2. **Préparer un témoin.** Utiliser un témoin BENCHAB adapté, en commençant par S
   pour le parcours numérique. Pour qualifier une autre machine, reprendre
   ensuite S, M et L selon le guide d’adaptation. Suivre le [cycle de préparation](preparer-une-piece.md),
   relever la plaque, le verdict, les écarts et l’empreinte du fichier prévu.
   Si le verdict est FAIL, traiter le motif avant de retenir la préparation.
3. **Comparer les températures si c’est la question.** Utiliser, si elle est
   disponible, une tour préparée dans l’atelier, avec des paliers choisis dans
   la plage déclarée. Garder les autres conditions fixes. Conserver le lien
   entre zone et consigne, puis relire le G-code pour vérifier les changements
   demandés. Si cette correspondance manque, ne pas attribuer un résultat à
   une température. L’examen du G-code vérifie les consignes, pas la température
   physique de la buse.
4. **Mesurer le jeu si la pièce doit s’assembler.** Choisir une éprouvette de
   l’atelier qui correspond à l’ajustement : trou, emboîtement ou filetage.
   Pour les trous, consulter `benchab conception eprouvette-trous -- --help`
   et la procédure de mesure du [guide matière](matieres-et-retours.md).
   Noter la cote nominale, la cote relevée en mm, la méthode et le résultat
   de montage. Une cote dessinée ou une simulation ne valide pas l’ajustement.
5. **Saisir le retour réel.** Si l’utilisateur a imprimé, relier l’observation
   au [retour d’impression](retour-impression.md) et à la plaque effectivement
   exécutée, lorsque son identité est connue. Consulter
   `benchab fabrication retour -- --help` pour les entrées acceptées. Noter
   les zones, les défauts, les mesures et les limites ; garder les photos et
   fichiers dans `prive/`. Une plage testée vaut pour les conditions consignées.

| Type de résultat | À conserver |
| --- | --- |
| Contrôle numérique | Empreinte, paramètres déclarés, contrôles et leur verdict |
| Observation | Pièce exécutée si identifiable, zone, constat, photo éventuelle |
| Validation physique | Critère fixé avant l’essai, méthode, mesure et portée ; sans mesure ou essai requis, NON VÉRIFIÉ |

La préparation et la relecture sont réalisables sans connexion machine. Ce
guide ne déclenche aucun essai embarqué, chauffage ou lancement d’impression.
