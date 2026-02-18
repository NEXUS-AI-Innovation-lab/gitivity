# Gitivity — Gateway IAM

Passerelle de provisionnement d'identités entre **MidPoint** et des services cibles (LDAP, MySQL, PostgreSQL, Odoo).
MidPoint envoie des opérations via RabbitMQ → la gateway les traite, soumet une validation à n8n, puis provisionne les utilisateurs.

## Architecture

```
MidPoint → [Connecteur Java] → RabbitMQ → Gateway Consumer
                                                ↓
                                          n8n (validation / notification)
                                                ↓
                                    Cibles : LDAP / MySQL / PostgreSQL / Odoo
                                                ↓
                                        PostgreSQL (audit) + Redis (état)
```

## Services (Docker)

| Service            | Port(s)         | Description                        |
|--------------------|-----------------|------------------------------------|
| Gateway API        | `8100`          | FastAPI — orchestrateur principal  |
| Gateway HTTP       | `5100`          | Flask — entitlements pour MidPoint |
| Approval Worker    | `5101`          | Simulateur workflow d'approbation  |
| n8n                | `5678`          | Automatisation des workflows       |
| RabbitMQ           | `5672` `15672`  | Broker de messages                 |
| PostgreSQL (IAM)   | `5430`          | Base de données principale         |
| Redis              | `6379`          | Suivi de l'état des approbations   |
| MidPoint           | `8080`          | IAM (profil `midpoint`)            |
| LDAP (ApacheDS)    | `10389`         | Cible (profil `targets`)           |
| MySQL              | `3306`          | Cible (profil `targets`)           |
| PostgreSQL (cible) | `5433`          | Cible (profil `targets`)           |
| Odoo               | `8069`          | Cible (profil `targets`)           |

## Démarrage rapide

```bash
# Copier et adapter la configuration
cp .env.example .env

# Démarrer les services de base
docker compose up -d

# Avec les services cibles (LDAP, MySQL, PostgreSQL, Odoo)
docker compose --profile targets up -d

# Avec MidPoint
docker compose --profile midpoint up -d
```

## Lancement sans Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# API
uvicorn app.main:app --host 0.0.0.0 --port 8100

# Consumer RabbitMQ (dans un autre terminal)
python scripts/start_consumer.py
```

## API principale

| Endpoint                              | Méthode | Description                  |
|---------------------------------------|---------|------------------------------|
| `/health`                             | GET     | Health check                 |
| `/api/v1/provisioning`                | GET     | Liste des opérations         |
| `/api/v1/provisioning/{id}`           | GET     | Détails d'une opération      |
| `/api/v1/provisioning/{id}/retry`     | POST    | Retry manuel                 |
| `/api/v1/audit/{operation_id}`        | GET     | Journal d'audit              |
| `/api/v1/connectors`                  | GET     | État des connecteurs         |
| `/api/v1/connectors/{name}/test`      | POST    | Tester un connecteur         |
| `/docs`                               | GET     | Swagger UI                   |

## Connecteur MidPoint (Java)

Le dossier `midpoint-connector/` contient un connecteur ConnId qui envoie les opérations MidPoint vers la gateway via RabbitMQ ou HTTP.

```bash
cd midpoint-connector
./gradlew clean jar
# JAR généré : build/libs/connector-restgateway-1.0.0-SNAPSHOT.jar

# Déploiement dans MidPoint Docker
docker cp build/libs/connector-restgateway-1.0.0-SNAPSHOT.jar midpoint:/opt/midpoint/var/icf-connectors/
docker restart midpoint
```

## Format de message RabbitMQ

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
  }
}
```

`operation_type` : `CREATE_USER`, `UPDATE_USER`, `DELETE_USER`
`target_service` : `MYSQL`, `POSTGRESQL`, `ODOO`, `LDAP`

## Tests

```bash
pytest                          # tous les tests
pytest tests/unit/              # tests unitaires uniquement
pytest --cov=app                # avec couverture
```

## Variables d'environnement principales

| Variable                       | Description                   | Défaut                       |
|--------------------------------|-------------------------------|------------------------------|
| `DATABASE_URL`                 | URL PostgreSQL                | —                            |
| `RABBITMQ_HOST`                | Hôte RabbitMQ                 | `localhost`                  |
| `RABBITMQ_QUEUE`               | Queue de messages             | `gateway-iam-provisioning`   |
| `N8N_VALIDATION_WEBHOOK_URL`   | Webhook validation n8n        | —                            |
| `N8N_NOTIFICATION_WEBHOOK_URL` | Webhook notification n8n      | —                            |
| `RETRY_MAX_ATTEMPTS`           | Tentatives max avant DLQ      | `3`                          |
| `MIDPOINT_URL`                 | URL MidPoint                  | `http://localhost:8080/midpoint` |

Voir `.env.example` pour la liste complète.
