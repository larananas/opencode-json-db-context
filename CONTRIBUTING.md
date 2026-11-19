# Contribuer

Merci de contribuer à **OpenCode JSON DB Context** ! Les modifications doivent rester ciblées et reproductibles.

## Avant de proposer une modification

1. Ouvrez une Issue expliquant le problème, sans aucune donnée privée.
2. Limitez les modifications au périmètre nécessaire ; conservez le format JSON et le comportement SQL en lecture seule.
3. Écrivez le code, les commentaires techniques, les tests et les instructions destinées aux agents en **anglais**. Le README et la documentation destinée aux utilisateurs sont en **français**.
4. Ajoutez de véritables tests de régression et exécutez `python3 -m unittest discover -s tests -v` ainsi que `python3 scripts/check_publication.py`.
5. Vérifiez les pipelines multiplateformes et les intégrations SQL avant de demander la fusion.

Ne contournez pas les contrôles, ne marquez pas les tests échoués comme ignorés et n'assouplissez pas les validations pour obtenir un statut vert.

## Moteurs SQL

Les tests publics utilisent SQLite, MariaDB, MySQL et PostgreSQL. Les contributions Oracle et SQL Server doivent être accompagnées d'instructions permettant une vérification réelle sur un serveur de test. Les bases documentaires comme MongoDB restent hors périmètre.

## Confidentialité

Utilisez exclusivement des schémas et enregistrements fictifs dans les tests, captures, Issues et pull requests. Les exports réels et les fichiers `.local/` ne doivent pas être ajoutés au dépôt.
