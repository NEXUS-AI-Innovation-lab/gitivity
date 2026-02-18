# Installation Rapide - Connecteur Midpoint + RabbitMQ

## ⚡ 3 étapes pour démarrer

### 1. Démarrer RabbitMQ

```bash
cd SAE5-All
docker-compose -f docker-compose-rabbitmq.yml up -d
```

### 2. Installer le connecteur

```bash
docker cp SAE5-All/idm-connector-rest-gateway/build/libs/connector-restgateway-1.0.0-SNAPSHOT.jar midpoint:/opt/midpoint/var/icf-connectors/
docker restart midpoint
```

### 3. Lancer le consumer

```bash
pip install pika
cd SAE5-All
python3 gateway-rabbitmq.py
```

## ✅ Test

Dans Midpoint, créez un utilisateur avec la ressource Gateway configurée avec :
- `useRabbitmq` = `true`
- `rabbitmqHost` = `172.17.0.1`
- `rabbitmqPort` = `5672`
- `rabbitmqUsername` = `admin`
- `rabbitmqPassword` = `admin123`
- `rabbitmqQueue` = `midpoint-operations`

Vous verrez le JSON dans le terminal du consumer ! 🎉

## 📚 Documentation complète

Voir [README-RABBITMQ.md](README-RABBITMQ.md)
