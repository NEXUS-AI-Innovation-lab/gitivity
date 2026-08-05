# Catalogue modulaire des bases cibles

Le fichier [`config/targets.yaml`](../config/targets.yaml) est désormais la
source de vérité des bases provisionnées par Gateway IAM. Ajouter une nouvelle
**instance** MySQL, PostgreSQL, MongoDB, LDAP ou Odoo ne demande aucune
modification du code Python, de l'API, du consumer RabbitMQ ou du dashboard.

## Principe

Chaque entrée décrit :

- `id` : identifiant unique et stable de l'instance ;
- `type` : famille technique du connecteur ;
- `display_name` : nom lisible dans l'interface ;
- `aliases` : noms de rôles MidPoint qui sélectionnent cette instance ;
- `connection` : paramètres de connexion ;
- `routing.entitlement_attributes` : attributs MidPoint qui sélectionnent la
  cible même sans alias de rôle ;
- `routing.delete_mode` : `delete`, ou `update` pour un nettoyage sans
  suppression du compte (cas LDAP) ;
- `entitlements` : fournisseur de découverte, identifiant d'association et
  nom de l'entitlement natif de base ;
- `deployment.environment` : variables ajoutées automatiquement à
  `.env.docker` par Ansible pour toutes les instances ;
- `connection.group_object_classes` : classes LDAP utilisées pour découvrir
  les groupes de cette cible, par exemple `groupOfNames` ou `posixGroup`.

Les valeurs `${VARIABLE:-défaut}` sont remplacées par les variables
d'environnement lors du chargement. Le fichier est relu automatiquement dès
que sa date de modification change.

## Ajouter une nouvelle instance

Exemple : une seconde base PostgreSQL dédiée au reporting.

1. Dupliquer l'entrée PostgreSQL dans `config/targets.yaml` et l'adapter :

   ```yaml
   - id: reporting
     type: postgresql
     display_name: PostgreSQL Reporting
     enabled: true
     aliases: [reporting, reporting-db]
     connection:
       host: ${REPORTING_DB_HOST}
       port: ${REPORTING_DB_PORT:-5432}
       user: ${REPORTING_DB_USER}
       password: ${REPORTING_DB_PASSWORD}
       database: ${REPORTING_DB_NAME:-reporting}
       connect_timeout: 10
     routing:
       entitlement_attributes: [postgresqlRole]
     entitlements:
       provider: postgresql_roles
       identifier: "{target}.{name}"
       association_ref: postgresqlProfile
       base_name: gateway-base
     deployment:
       environment:
         REPORTING_DB_HOST: reporting-db.internal
         REPORTING_DB_PORT: 5432
         REPORTING_DB_USER: provisioner
         REPORTING_DB_PASSWORD: un-secret-fort
         REPORTING_DB_NAME: reporting
   ```

2. Relancer le playbook Ansible habituel. Il valide la cible, génère
   `.env.docker`, crée idempotemment l'entitlement natif `gateway-base`, découvre
   les entitlements réels, génère un rôle MidPoint par entitlement avec un OID
   déterministe, puis les importe par upsert. Aucun rôle/XML ne doit être déclaré
   manuellement dans le catalogue.

3. Vérifier que la cible apparaît et répond :

   ```bash
   curl http://10.10.0.1:8100/api/v1/connectors/gateway
   curl -X POST http://10.10.0.1:8100/api/v1/connectors/gateway/reporting/test
   ```

4. Tester un provisionnement :

   ```bash
   python scripts/send_test_message.py --service reporting --action create
   ```

Le message natif accepte aussi bien l'`id` que l'un des `aliases` :

```json
{
  "request_id": "test-reporting-001",
  "operation_type": "CREATE_USER",
  "target_service": "reporting",
  "user_data": {
    "username": "catalog_test",
    "password": "A7!vQ2#kL9@z",
    "attributes": {}
  }
}
```

## Ajouter plusieurs rôles MongoDB

Le routage est indépendant du nombre de rôles natifs. MidPoint peut envoyer :

```json
{
  "roles": ["mongodb"],
  "mongodbRoles": ["readWrite", "dbAdmin"]
}
```

Les rôles MidPoint sont générés individuellement depuis ces rôles natifs. Le
consumer retire uniquement le préfixe d'instance avant l'appel au connecteur.

## Entitlements

Gateway HTTP lit le même catalogue et échoue fermé si une cible n'est pas
joignable : aucune liste fictive de secours n'est produite. Le manifeste normalisé
d'une cible est disponible sur :

```bash
curl "http://10.10.0.1:5100/entitlements/manifest?target=mongodb"
```

La liste non sensible des cibles est disponible sur :

```bash
curl http://10.10.0.1:5100/targets
```

Les secrets ne sont jamais retournés par l'API des connecteurs.

## Disparition d'un entitlement

Après un upsert réussi des rôles, Ansible transmet les manifestes complets à
l'API. Un entitlement précédemment inventorié mais absent crée une demande
durable en PostgreSQL et avertit la chaîne d'approbation actuelle. La demande
n'expire pas ; le lien signé est à usage unique, expire et peut être renouvelé.
Après approbation de tous les niveaux, Gateway vérifie à nouveau la cible,
désassigne le rôle de tous les objets MidPoint qui le référencent, puis supprime
uniquement le rôle MidPoint. L'entitlement natif n'est jamais supprimé.

Les anciens XML statiques ont été retirés. Au premier déploiement, leurs OID sont
enregistrés dans le même workflow : ils ne sont donc pas supprimés sans décision.

Pour le moment, `deployment.environment` suit le mécanisme existant et peut
contenir les valeurs directement. L'adoption d'Ansible Vault pourra se faire
ultérieurement sans modifier le générateur.

## Comportement interne

Le consumer résout l'alias ou l'attribut d'entitlement vers un `target_id`. Le
factory choisit ensuite la famille de connecteur indiquée par `type` et lui
injecte la configuration de cette instance. L'historique Prisma conserve la
famille (`POSTGRESQL`, `MONGODB`, etc.) pour rester compatible avec les données
existantes ; l'identifiant précis est conservé dans `original_message.target_id`
et utilisé pour le routage, les approbations et l'état Redis.

## Limite volontaire

Une nouvelle **instance** d'une famille supportée est 100 % déclarative. Une
nouvelle technologie (par exemple Oracle si aucun connecteur Oracle n'existe)
nécessite forcément l'implémentation de son protocole de provisioning. Cette
implémentation reste isolée dans un connecteur ; le catalogue, le consumer,
l'API et le dashboard n'ont pas à être réécrits pour chaque instance.

## Retour arrière

Pour désactiver une cible sans supprimer sa configuration :

```yaml
enabled: false
```

Après rechargement, ses alias ne routent plus de messages et elle disparaît de
l'API. Les opérations historiques restent consultables.
