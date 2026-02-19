# Gateway IAM

API de provisionnement IAM pour MidPoint. Reçoit des messages via RabbitMQ, coordonne la validation avec n8n, provisionne les utilisateurs vers des services cibles (MySQL, PostgreSQL, Odoo), et gère un système complet de retry et audit.

## Architecture

```
MidPoint → RabbitMQ → Gateway IAM → n8n (validation)
                            ↓
                      Target Service (MySQL/PostgreSQL/Odoo)
                            ↓
                      n8n (notification)
                            ↓
                      PostgreSQL (audit + state)
```

## Stack Technique

- **Python 3.11+**
- **FastAPI** - API REST
- **Prisma** - ORM avec PostgreSQL
- **aio_pika** - Consumer RabbitMQ async
- **httpx** - Client HTTP async pour n8n

## Prérequis

- Python 3.11+
- PostgreSQL 15+
- RabbitMQ 3.12+
- n8n (pour validation et notifications)

## Installation

### 1. Environnement virtuel

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration

```bash
cp .env.example .env
# Éditer .env avec votre configuration
```

### 3. Base de données

```bash
# Générer le client Prisma
prisma generate

# Appliquer les migrations
prisma migrate dev
```

### 4. Docker (optionnel)

```bash
cd docker
docker-compose up -d
```

Services démarrés par défaut :

| Service    | Port(s)           | Description                     |
| ---------- | ----------------- | ------------------------------- |
| PostgreSQL | `5432`            | Base de données                 |
| RabbitMQ   | `5672`, `15672`   | Broker + Management UI          |

**RabbitMQ Management UI** : <http://localhost:15672> (guest/guest)

#### Kafka (optionnel)

Si vous avez besoin de Kafka au lieu de RabbitMQ :

```bash
docker-compose --profile kafka up -d
```

Cela démarre également Zookeeper et Kafka UI (port `8080`).

## Lancement

### API Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Message Consumer

```bash
python scripts/start_consumer.py
```

## Interface de Gestion des Connecteurs

L'application dispose d'une **interface web moderne** permettant de visualiser et configurer les connecteurs en temps réel.

### Fonctionnalités

| Fonctionnalité | Description |
| -------------- | ----------- |
| **Connecteurs Gateway** | Affiche l'état des connecteurs locaux (MySQL, PostgreSQL, Odoo, LDAP) |
| **Connecteurs MidPoint** | Affiche les ressources (connecteurs Java) configurées dans MidPoint |
| **Indicateurs d'état** | Pastilles colorées indiquant l'état de chaque connecteur |
| **Configuration** | Icône de paramètres pour voir/modifier la configuration |
| **Test de connexion** | Bouton pour tester la connectivité en un clic |

### Accès à l'interface

```text
http://localhost:8000/dashboard/connectors
```

### Types de connecteurs

**Connecteurs Gateway** (gérés par cette application) :

- MySQL, PostgreSQL, Odoo, LDAP
- Configuration modifiable via l'interface (runtime uniquement)
- Test de connexion instantané

**Connecteurs MidPoint** (ressources Java dans MidPoint) :

- Affichage en lecture seule
- Test de connexion via l'API MidPoint
- Nécessite que MidPoint soit accessible (voir configuration)

### Configuration MidPoint

Pour afficher les connecteurs MidPoint, configurez les variables d'environnement :

```bash
MIDPOINT_URL="http://localhost:8080/midpoint"
MIDPOINT_USERNAME="administrator"
MIDPOINT_PASSWORD="5ecr3t"
```

## API Endpoints

| Endpoint                          | Méthode | Description                        |
| --------------------------------- | ------- | ---------------------------------- |
| `/health`                         | GET     | Health check                       |
| `/metrics`                        | GET     | Métriques (Prometheus-compatible)  |
| `/api/v1/provisioning`            | GET     | Liste des opérations (avec filtres)|
| `/api/v1/provisioning/{id}`       | GET     | Détails d'une opération            |
| `/api/v1/provisioning/{id}/retry` | POST    | Retry manuel                       |
| `/api/v1/audit/{operation_id}`    | GET     | Audit trail                        |
| `/api/v1/connectors`              | GET     | Liste des connecteurs et leur état |
| `/api/v1/connectors/{name}`       | GET     | Détails d'un connecteur            |
| `/api/v1/connectors/{name}`       | PUT     | Modifier la configuration          |
| `/api/v1/connectors/{name}/test`  | POST    | Tester la connexion                |

Documentation interactive : `/docs` (Swagger) ou `/redoc`

## Configuration

Variables d'environnement principales :

| Variable                       | Description              | Défaut                     |
| ------------------------------ | ------------------------ | -------------------------- |
| `DATABASE_URL`                 | URL PostgreSQL           | -                          |
| `BROKER_TYPE`                  | `rabbitmq` ou `kafka`    | `rabbitmq`                 |
| `RABBITMQ_HOST`                | Hôte RabbitMQ            | `localhost`                |
| `RABBITMQ_PORT`                | Port RabbitMQ            | `5672`                     |
| `RABBITMQ_QUEUE`               | Queue de messages        | `gateway-iam-provisioning` |
| `N8N_VALIDATION_WEBHOOK_URL`   | Webhook validation n8n   | -                          |
| `N8N_NOTIFICATION_WEBHOOK_URL` | Webhook notification n8n | -                          |
| `RETRY_MAX_ATTEMPTS`           | Tentatives max avant DLQ | `3`                        |
| `RETRY_BACKOFF_MULTIPLIER`     | Multiplicateur backoff   | `2.0`                      |

Voir `.env.example` pour la liste complète.

## Format des Messages

```json
{
  "request_id": "mp-req-001",
  "operation_type": "CREATE_USER",
  "target_service": "MYSQL",
  "user_data": {
    "username": "john.doe",
    "email": "john@example.com",
    "password": "SecurePass123!",
    "roles": ["read", "write"]
  },
  "metadata": {}
}
```

**operation_type** : `CREATE_USER`, `UPDATE_USER`, `DELETE_USER`, `CREATE_ROLE`, etc.

**target_service** : `MYSQL`, `POSTGRESQL`, `ODOO`

## Tests

```bash
# Tous les tests
pytest

# Tests unitaires uniquement
pytest tests/unit/

# Avec couverture
pytest --cov=app --cov-report=html
```

## Développement

### GitFlow

- `main` : Production
- `develop` : Intégration
- `feature/*` : Nouvelles fonctionnalités
- `bugfix/*` : Corrections

### Ajouter un nouveau connecteur

1. Créer `app/core/connectors/<service>_connector.py`
2. Hériter de `ProvisioningConnector`
3. Implémenter : `provision_user()`, `update_user()`, `delete_user()`, `health_check()`
4. Enregistrer dans `ConnectorFactory._connectors`
5. Ajouter `TargetService` enum si nécessaire

## License

MIT
