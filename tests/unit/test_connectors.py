"""Unit tests for provisioning connectors"""
from unittest.mock import AsyncMock

import pytest

from app.core.connectors.factory import ConnectorFactory, get_connector
from app.core.connectors.mysql_connector import MySQLConnector
from app.core.connectors.postgresql_connector import PostgreSQLConnector
from app.core.connectors.mongodb_connector import MongoDBConnector
from app.core.connectors.ldap_connector import LDAPConnector
from app.config.target_catalog import TargetDefinition
from app.utils.enums import TargetService
from app.utils.exceptions import ConnectorNotFoundError


class TestConnectorFactory:
    """Tests for ConnectorFactory"""

    def test_create_mysql_connector(self):
        """Test creating MySQL connector"""
        connector = ConnectorFactory.create(TargetService.MYSQL)
        assert isinstance(connector, MySQLConnector)

    def test_create_postgresql_connector(self):
        """Test creating PostgreSQL connector"""
        connector = ConnectorFactory.create(TargetService.POSTGRESQL)
        assert isinstance(connector, PostgreSQLConnector)

    def test_create_mongodb_connector(self):
        connector = ConnectorFactory.create(TargetService.MONGODB)
        assert isinstance(connector, MongoDBConnector)

    def test_create_unsupported_service(self):
        """Test that unsupported service raises error when removed from factory"""
        from app.core.connectors.ldap_connector import LDAPConnector
        ConnectorFactory._connectors.pop(TargetService.LDAP, None)
        try:
            with pytest.raises(ConnectorNotFoundError):
                ConnectorFactory.create(TargetService.LDAP)
        finally:
            ConnectorFactory._connectors[TargetService.LDAP] = LDAPConnector

    def test_get_available_services(self):
        """Test getting available services"""
        services = ConnectorFactory.get_available_services()
        assert TargetService.MYSQL in services
        assert TargetService.POSTGRESQL in services
        assert TargetService.ODOO in services
        assert TargetService.MONGODB in services

    def test_is_service_available(self):
        """Test checking service availability"""
        assert ConnectorFactory.is_service_available(TargetService.MYSQL) is True
        assert ConnectorFactory.is_service_available(TargetService.LDAP) is True

    def test_get_connector_convenience_function(self):
        """Test get_connector convenience function"""
        connector = get_connector(TargetService.MYSQL)
        assert isinstance(connector, MySQLConnector)


class TestLDAPConnector:
    def test_uses_explicit_users_base_dn(self):
        connector = LDAPConnector().configure_target(
            TargetDefinition(
                id="ldap-test",
                type="ldap",
                display_name="LDAP Test",
                connection={
                    "base_dn": "dc=lissi,dc=fr",
                    "users_base_dn": "ou=users,ou=olga,dc=lissi,dc=fr",
                },
            )
        )

        assert connector._get_user_dn("test-new-ldap") == (
            "uid=test-new-ldap,ou=users,ou=olga,dc=lissi,dc=fr"
        )

    def test_users_base_dn_has_legacy_safe_fallback(self):
        connector = LDAPConnector().configure_target(
            TargetDefinition(
                id="ldap-legacy",
                type="ldap",
                display_name="LDAP Legacy",
                connection={"base_dn": "dc=example,dc=org"},
            )
        )

        assert connector._get_user_dn("alice") == (
            "uid=alice,ou=Users,dc=example,dc=org"
        )


class TestMySQLConnector:
    """Tests for MySQLConnector"""

    @pytest.fixture
    def mysql_connector(self):
        """MySQL connector instance"""
        return MySQLConnector()

    def test_service_name(self, mysql_connector):
        """Test service name property"""
        assert mysql_connector.service_name == TargetService.MYSQL

    def test_native_account_quoting(self, mysql_connector):
        assert mysql_connector._quote_account("gateway-base", "%") == "`gateway-base`@`%`"

    def test_mysql_role_candidates_normalize_list_and_prefixes(self, mysql_connector):
        assert mysql_connector._mysql_role_candidates(
            ["mysql", "mysql.admin", " admin ", "mysql.admin"]
        ) == ["mysql.admin", "admin"]

    @pytest.mark.asyncio
    async def test_resolve_native_mysql_role_accepts_list_payload(self, mysql_connector):
        cursor = AsyncMock()
        cursor.fetchone.side_effect = [None, (1,)]

        resolved = await mysql_connector._resolve_native_mysql_role(
            cursor,
            ["mysql", "mysql.admin"],
        )

        assert resolved == "admin"
        assert cursor.execute.await_args_list[0].args[1] == ("mysql.admin",)
        assert cursor.execute.await_args_list[1].args[1] == ("admin",)


class TestPostgreSQLConnector:
    """Tests for PostgreSQLConnector"""

    @pytest.fixture
    def pg_connector(self):
        """PostgreSQL connector instance"""
        return PostgreSQLConnector()

    def test_service_name(self, pg_connector):
        """Test service name property"""
        assert pg_connector.service_name == TargetService.POSTGRESQL

    def test_native_role_is_not_remapped(self, pg_connector):
        assert pg_connector._roles_to_pg_roles(["accounting"]) == ["accounting"]


class TestMongoDBConnector:
    @pytest.fixture
    def mongodb_connector(self):
        return MongoDBConnector()

    def test_service_name(self, mongodb_connector):
        assert mongodb_connector.service_name == TargetService.MONGODB

    def test_normalize_association_uid(self, mongodb_connector):
        roles = mongodb_connector._normalize_roles(
            ["mongodb"],
            {"mongodbRoles": ["readWrite@target_db", "dbAdmin@target_db"]},
            "target_db",
        )
        assert roles == [
            {"role": "readWrite", "db": "target_db"},
            {"role": "dbAdmin", "db": "target_db"},
        ]

    def test_service_marker_is_not_granted(self, mongodb_connector):
        roles = mongodb_connector._normalize_roles(["mongodb"], {}, "target_db")
        assert roles == []

    def test_roles_are_deduplicated(self, mongodb_connector):
        roles = mongodb_connector._normalize_roles(
            None,
            {"mongodbRoles": ["read@target_db", "read@target_db"]},
            "target_db",
        )
        assert roles == [{"role": "read", "db": "target_db"}]
