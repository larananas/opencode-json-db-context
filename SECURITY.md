# Politique de sécurité

Ce logiciel inspecte des bases de données et peut exporter des **données confidentielles**. Ne publiez jamais de vrais fichiers `.env`, exports JSON, données nominatives ou secrets dans les Issues, les discussions ou les pull requests.

## Signaler une vulnérabilité

Utilisez de préférence les **GitHub Security Advisories** du dépôt si le signalement privé est activé. Si cette option n'est pas disponible, ouvrez une Issue **sans données exploitables ni identifiants**, pour demander un canal de communication privé. Ne publiez aucun mot de passe ou exemple de données réelles.

Incluez une description du comportement, la version du logiciel et des étapes de reproduction **avec des données fictives**. Aucun délai de correction n'est garanti.

## Limites de sécurité

- Le MCP fourni est en lecture seule, mais les connexions possèdent toujours les autorisations réelles de votre compte SQL. Utilisez des comptes dédiés en lecture seule.
- Les contenus de `.env` collés dans un chat peuvent être envoyés au fournisseur du modèle. Préférez `.local/`.
- Les exports dans `export/` contiennent par défaut de **vraies valeurs**. Le masquage facultatif est indicatif, et ne remplace ni l'anonymisation métier ni une revue humaine.
- L'interdiction des outils OpenCode ne constitue pas une sandbox système ; les paramètres globaux de l'utilisateur et les autres plugins peuvent disposer de permissions distinctes.
- Les options TLS diffèrent selon les moteurs. Pour une connexion distante, vérifiez le chiffrement et l'authentification des certificats.

Consultez le [README](README.md) avant toute extraction de données de production.
