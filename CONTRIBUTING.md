# Guide de Contribution

Merci de contribuer au projet Gateway IAM !

## Workflow Git (GitFlow)

### Branches

- `main` : Code de production, déployé en prod
- `develop` : Branche d'intégration pour le développement
- `feature/*` : Nouvelles fonctionnalités
- `bugfix/*` : Corrections de bugs
- `enhancement/*` : Améliorations

### Processus

1. **Créer une branche** depuis `develop`

   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/ma-fonctionnalite
   ```

2. **Développer** avec des commits atomiques

   ```bash
   git add .
   git commit -m "feat: description courte"
   ```

3. **Pousser** et créer une Merge Request

   ```bash
   git push -u origin feature/ma-fonctionnalite
   ```

4. **Review** et merge vers `develop`

### Convention de commits

Format : `<type>: <description>`

Types :

- `feat` : Nouvelle fonctionnalité
- `fix` : Correction de bug
- `docs` : Documentation
- `refactor` : Refactoring (pas de changement fonctionnel)
- `test` : Ajout/modification de tests
- `chore` : Maintenance (dépendances, config, etc.)

## Standards de code

### Style Python

- Formatteur : **Black**
- Linter : **Ruff**
- Types : annotations Python 3.11+

```bash
# Formatter
black app/ tests/

# Linter
ruff check app/ tests/
```

### Structure des fichiers

```
app/
├── api/          # Endpoints FastAPI
├── config/       # Configuration
├── core/         # Logique métier
│   ├── broker/   # Consumers Kafka/RabbitMQ
│   └── connectors/  # Connecteurs target services
├── db/           # Accès données (Prisma)
├── models/       # Modèles de domaine et schemas
├── services/     # Services (n8n, audit)
└── utils/        # Utilitaires (enums, exceptions)
```

### Tests

- Tests unitaires dans `tests/unit/`
- Tests d'intégration dans `tests/integration/`
- Fixtures partagées dans `tests/conftest.py`

```bash
# Lancer les tests
pytest

# Avec couverture
pytest --cov=app --cov-report=html
```

## Ajouter un nouveau connecteur

1. Créer `app/core/connectors/<service>_connector.py`
2. Hériter de `ProvisioningConnector`
3. Implémenter les méthodes abstraites :
   - `connect()` / `disconnect()`
   - `health_check()`
   - `provision_user()`, `update_user()`, `delete_user()`
4. Ajouter le mapping dans `ConnectorFactory._connectors`
5. Ajouter la config dans `app/config/settings.py`
6. Mettre à jour `.env.example`
7. Ajouter des tests unitaires

## Code Review Checklist

- [ ] Le code suit les conventions de style
- [ ] Les tests passent
- [ ] Pas de secrets dans le code
- [ ] Documentation mise à jour si nécessaire
- [ ] Commit messages clairs
