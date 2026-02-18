# Dumps - Donnees de reference

Ce dossier contient les exports/imports pour initialiser les services sur une nouvelle machine.

## LDAP (obligatoire)

Le serveur LDAP (ApacheDS) doit etre initialise avec la structure de base.

### Exporter depuis la machine actuelle

```bash
# Export LDIF complet
ldapsearch -x -H ldap://localhost:10389 \
  -D "uid=admin,ou=system" -w secret \
  -b "dc=openmicroscopy,dc=org" \
  "(objectClass=*)" > dumps/ldap-full.ldif

# Export uniquement les groupes
ldapsearch -x -H ldap://localhost:10389 \
  -D "uid=admin,ou=system" -w secret \
  -b "dc=openmicroscopy,dc=org" \
  "(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))" > dumps/ldap-groups.ldif
```

### Importer sur la nouvelle machine

```bash
# Attendre que ApacheDS demarre
sleep 10

# Importer le LDIF
ldapadd -x -H ldap://localhost:10389 \
  -D "uid=admin,ou=system" -w secret \
  -f dumps/ldap-full.ldif
```

### Structure LDAP attendue

```
dc=openmicroscopy,dc=org
├── ou=Users          # Utilisateurs provisionnes
├── ou=Groups         # Groupes LDAP (entitlements)
│   ├── cn=Developers
│   ├── cn=Admins
│   └── ...
└── ou=system         # Admin ApacheDS
```

## Odoo (optionnel)

Si Odoo a des donnees specifiques a conserver :

```bash
# Backup de la base Odoo
docker exec odoo-db pg_dump -U odoo odoo > dumps/odoo-backup.sql

# Restore
docker exec -i odoo-db psql -U odoo odoo < dumps/odoo-backup.sql
```

## MidPoint (optionnel)

Les roles et la ressource MidPoint sont dans `midpoint/` (fichiers XML).
Ils doivent etre importes manuellement via l'interface MidPoint :
1. Configuration > Import object
2. Importer `midpoint/ressource.xml`
3. Importer les roles depuis `midpoint/roles/`
