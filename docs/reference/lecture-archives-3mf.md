# Lecture bornée des archives 3MF

Les contrôles de lecture utilisent `benchab.archives_3mf.Archive3MF` pour ouvrir
un ZIP une seule fois par contexte. L’index refuse les noms ambigus, absolus ou
traversants, les doublons, les liens symboliques, plus de 2 048 membres, un
membre supérieur à 512 Mio ou un total déclaré supérieur à 2 Gio. Une lecture
est limitée à la taille déclarée, à la limite par membre et au cumul de 2 Gio
de flux décompressés lus par contexte, y compris les relectures. Le XML et la
configuration JSON ont chacun un plafond de 32 Mio. La source est ouverte avec
refus de liens symboliques et ouverture non bloquante, puis vérifiée comme
fichier régulier par `fstat`; l’identité du descripteur est revérifiée à la
fermeture.

Le XML se lit avec `xml()` : DTD et entités sont refusées en UTF-8, UTF-16 et
UTF-32 avant ElementTree. Les réglages se lisent avec `reglages()`, qui délègue
au dictionnaire typé existant sans ajouter de normalisation. Le contexte est
lecture seule ; les écritures ZIP explicites en modes `w` et `a` gardent leur
parcours d’intégrité propre.

Les budgets ont été comparés à 273 archives privées existantes : la plus grande
mesurée déclarait 9 580 886 octets au total et un membre maximal de 7 053 957
octets. Les archives témoins S/M/L restent donc sous les limites par plusieurs
ordres de grandeur.

Les contrôles déjà migrés sont U20 (plaques-interface), U22 (règles), U25
(débit G-code), capacités machine, envoi, supports requis, décollement,
orientations des supports, identifiants filaments, revue locale et vérification
des réglages. `finition_parois` analyse un instantané déjà borné à 64 Mio avec
son parcours mémoire protégé ; `paquet_3mf` valide des octets fournis et garde
son propre validateur mémoire borné. Les écritures ZIP en mode `w` ou `a` ne
sont pas concernées.
