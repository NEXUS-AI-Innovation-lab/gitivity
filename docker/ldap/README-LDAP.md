# Configuration LDAP Docker - Documentation Technique

## Architecture

```
MidPoint (IAM)
    │
    │  REST Gateway Connector (Java)
    ▼
Gateway HTTP (Flask :5100)
    │
    │  ldap3 / python-ldap
    ▼
OpenLDAP (osixia/openldap)
    │  port 389 (interne Docker)
    │  port 10389 (hôte)
    ▼
phpLDAPadmin (:8080)   ← Interface graphique pour la démo
```

---

## Démarrage

```bash
# Lancer LDAP + phpLDAPadmin (profil targets)
docker compose --profile targets up -d ldap phpldapadmin

# Vérifier que le conteneur est sain
docker compose ps ldap

# Voir les logs de bootstrap (premier démarrage)
docker logs gitivity-ldap
```

---

## Points de Configuration (pour la démo)

### 1. Image et version

```yaml
image: osixia/openldap:1.5.0
```

`osixia/openldap` est l'image de référence pour OpenLDAP dans Docker.
Version fixée à `1.5.0` pour la reproductibilité.

---

### 2. Variables d'environnement

| Variable | Valeur | Rôle |
|---|---|---|
| `LDAP_ORGANISATION` | `OpenMicroscopy Corp` | Nom affiché de l'organisation |
| `LDAP_DOMAIN` | `openmicroscopy.org` | Génère automatiquement `dc=openmicroscopy,dc=org` |
| `LDAP_ADMIN_PASSWORD` | `secret` | Mot de passe du compte admin |
| `LDAP_TLS` | `false` | Désactive TLS (acceptable en réseau Docker interne) |
| `LDAP_READONLY_USER` | `false` | Pas d'utilisateur en lecture seule |
| `LDAP_REMOVE_CONFIG_AFTER_SETUP` | `false` | Garde la config accessible après init |

**DN Admin généré automatiquement :** `cn=admin,dc=openmicroscopy,dc=org`

---

### 3. Ports

```yaml
ports:
  - "10389:389"
```

- `389` : port LDAP standard à l'intérieur du conteneur
- `10389` : port exposé sur l'hôte (évite le conflit avec un LDAP système existant)

Les services **dans** Docker (gateway-http) utilisent le port `389` directement.
MidPoint et les outils sur l'hôte utilisent le port `10389`.

---

### 4. Volumes

```yaml
volumes:
  - ldap_data:/var/lib/ldap          # Données de l'annuaire (persistantes)
  - ldap_slapd_config:/etc/ldap/slapd.d  # Configuration slapd (schémas, ACL)
  - ./docker/ldap/bootstrap.ldif:/container/service/slapd/assets/config/bootstrap/ldif/custom/bootstrap.ldif:ro
```

- **`ldap_data`** : stocke toutes les entrées LDAP (OUs, groupes, utilisateurs)
- **`ldap_slapd_config`** : stocke la configuration du serveur (modules, index, ACL)
- **`bootstrap.ldif`** : monté en lecture seule, chargé **une seule fois** au premier démarrage pour créer la structure initiale

> Pour forcer un rechargement du bootstrap : `docker volume rm gitivity_ldap_data gitivity_ldap_slapd_config` puis relancer.

---

### 5. Bootstrap LDIF (`docker/ldap/bootstrap.ldif`)

Chargé automatiquement au premier démarrage. Crée :

```
dc=openmicroscopy,dc=org       ← Root (créé par osixia via LDAP_DOMAIN)
├── ou=Users                    ← Conteneur des comptes utilisateurs
└── ou=Groups                   ← Conteneur des groupes
    ├── cn=user-poste-windows-non-admin
    ├── cn=user-windows-admin-local
    ├── cn=user-imprimante
    ├── cn=sharepoint
    ├── cn=wemail
    ├── cn=partage-disks
    ├── cn=admins
    ├── cn=developers
    ├── cn=app-users
    ├── cn=odoo-admin
    ├── cn=vpn-user
    └── cn=vpn-test
```

Tous les groupes utilisent `objectClass: groupOfUniqueNames` → attribut de membre : `uniqueMember`.

---

### 6. Healthcheck

```yaml
healthcheck:
  test: ldapsearch -x -H ldap://localhost:389 -b dc=openmicroscopy,dc=org -D cn=admin,... -w secret '(objectClass=organizationalUnit)'
  interval: 15s
  retries: 5
  start_period: 20s
```

Vérifie que slapd répond et que l'authentification admin fonctionne avant de déclarer le conteneur `healthy`. Les services dépendants (`phpldapadmin`, `gateway-http`) attendent ce statut.

---

### 7. phpLDAPadmin

Interface graphique pour naviguer et administrer l'annuaire.

```yaml
image: osixia/phpldapadmin:latest
ports:
  - "8080:80"
environment:
  PHPLDAPADMIN_LDAP_HOSTS: "ldap"   # Nom DNS Docker du conteneur LDAP
  PHPLDAPADMIN_HTTPS: "false"        # HTTP simple pour la démo
```

**Accès :** `http://localhost:8080`
**Login :** `cn=admin,dc=openmicroscopy,dc=org`
**Mot de passe :** `secret`

---

## Connexion depuis MidPoint

MidPoint tourne dans Docker. Pour atteindre le LDAP depuis MidPoint :

```
Host: ldap          ← nom du service Docker (résolution DNS interne)
Port: 389           ← port interne (pas 10389)
Base DN: dc=openmicroscopy,dc=org
Bind DN: cn=admin,dc=openmicroscopy,dc=org
Password: secret
```

> **Attention :** Ne pas utiliser `localhost:10389` depuis un conteneur Docker — `localhost` désigne le conteneur lui-même.

---

## Connexion depuis Apache Directory Studio (hôte)

```
Host: localhost
Port: 10389
Base DN: dc=openmicroscopy,dc=org
Bind DN: cn=admin,dc=openmicroscopy,dc=org
Password: secret
Encryption: No encryption
```

---

## Groupes et leur usage dans MidPoint

| Groupe LDAP | DN complet | Rôle MidPoint associé |
|---|---|---|
| `user-poste-windows-non-admin` | `cn=user-poste-windows-non-admin,ou=Groups,dc=openmicroscopy,dc=org` | ROLE_WINDOWS_USER |
| `user-windows-admin-local` | `cn=user-windows-admin-local,ou=Groups,dc=openmicroscopy,dc=org` | ROLE_WINDOWS_ADMIN |
| `user-imprimante` | `cn=user-imprimante,ou=Groups,dc=openmicroscopy,dc=org` | ROLE_PRINT |
| `sharepoint` | `cn=sharepoint,ou=Groups,dc=openmicroscopy,dc=org` | ROLE_SHAREPOINT |
| `wemail` | `cn=wemail,ou=Groups,dc=openmicroscopy,dc=org` | ROLE_MAIL |
| `partage-disks` | `cn=partage-disks,ou=Groups,dc=openmicroscopy,dc=org` | ROLE_DISK_SHARE |

Quand MidPoint assigne un rôle à un utilisateur, le connecteur Java envoie le **DN complet** du groupe dans l'attribut `ldapGroups` du message JSON. La gateway Python ajoute alors l'utilisateur comme `uniqueMember` du groupe.

---

## Commandes utiles

```bash
# Lister tous les groupes
ldapsearch -x -H ldap://localhost:10389 \
  -D "cn=admin,dc=openmicroscopy,dc=org" -w secret \
  -b "ou=Groups,dc=openmicroscopy,dc=org" "(objectClass=groupOfUniqueNames)" cn description

# Lister tous les utilisateurs
ldapsearch -x -H ldap://localhost:10389 \
  -D "cn=admin,dc=openmicroscopy,dc=org" -w secret \
  -b "ou=Users,dc=openmicroscopy,dc=org" "(objectClass=inetOrgPerson)" uid cn mail

# Voir les membres d'un groupe
ldapsearch -x -H ldap://localhost:10389 \
  -D "cn=admin,dc=openmicroscopy,dc=org" -w secret \
  -b "cn=developers,ou=Groups,dc=openmicroscopy,dc=org" "(objectClass=*)" uniqueMember

# Supprimer les volumes pour repartir de zéro (re-bootstrap)
docker compose --profile targets down ldap phpldapadmin
docker volume rm gitivity_ldap_data gitivity_ldap_slapd_config
docker compose --profile targets up -d ldap phpldapadmin
```

---

## Intégration avec le connecteur Python (ldap_connector.py)

Le connecteur utilise `ldap3`. À la création d'un utilisateur :

1. Vérifie que `ou=Users,{base_dn}` existe (sinon le crée)
2. Crée l'entrée `inetOrgPerson` sous `uid={username},ou=Users,dc=openmicroscopy,dc=org`
3. Pour chaque DN de groupe dans `ldapGroups` : ajoute l'utilisateur comme `uniqueMember`

À la mise à jour : gère les ajouts **et suppressions** de groupes (diff entre groupes actuels et groupes cibles).

À la suppression : retire l'utilisateur de tous ses groupes, puis supprime l'entrée.
