"""Unit tests for provisioning connectors"""
import pytest

from app.core.connectors.factory import ConnectorFactory, get_connector
from app.core.connectors.mysql_connector import MySQLConnector, ROLE_TO_PRIVILEGES
from app.core.connectors.postgresql_connector import (
    PostgreSQLConnector,
    ROLE_TO_PG_ROLES,
)
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

    def test_create_unsupported_service(self):
        """Test that unsupported service raises error"""
        from app.core.connectors.ldap_connector import LDAPConnector

        ConnectorFactory._connectors.pop(TargetService.LDAP)
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

    def test_is_service_available(self):
        """Test checking service availability"""
        assert ConnectorFactory.is_service_available(TargetService.MYSQL) is True
        assert ConnectorFactory.is_service_available(TargetService.LDAP) is True

    def test_get_connector_convenience_function(self):
        """Test get_connector convenience function"""
        connector = get_connector(TargetService.MYSQL)
        assert isinstance(connector, MySQLConnector)


class TestMySQLConnector:
    """Tests for MySQLConnector"""

    @pytest.fixture
    def mysql_connector(self):
        """MySQL connector instance"""
        return MySQLConnector()

    def test_service_name(self, mysql_connector):
        """Test service name property"""
        assert mysql_connector.service_name == TargetService.MYSQL

    def test_roles_to_privileges_read(self, mysql_connector):
        """Test role mapping for read role"""
        privileges = mysql_connector._roles_to_privileges(["read"])
        assert "SELECT" in privileges

    def test_roles_to_privileges_write(self, mysql_connector):
        """Test role mapping for write role"""
        privileges = mysql_connector._roles_to_privileges(["write"])
        assert "SELECT" in privileges
        assert "INSERT" in privileges
        assert "UPDATE" in privileges
        assert "DELETE" in privileges

    def test_roles_to_privileges_admin(self, mysql_connector):
        """Test role mapping for admin role"""
        privileges = mysql_connector._roles_to_privileges(["admin"])
        assert "ALL PRIVILEGES" in privileges

    def test_roles_to_privileges_custom(self, mysql_connector):
        """Test that unknown roles are ignored"""
        privileges = mysql_connector._roles_to_privileges(["EXECUTE"])
        assert len(privileges) == 0

    def test_roles_to_privileges_multiple(self, mysql_connector):
        """Test mapping multiple roles"""
        privileges = mysql_connector._roles_to_privileges(["read", "write"])
        # Should deduplicate privileges
        assert "SELECT" in privileges
        assert "INSERT" in privileges


class TestPostgreSQLConnector:
    """Tests for PostgreSQLConnector"""

    @pytest.fixture
    def pg_connector(self):
        """PostgreSQL connector instance"""
        return PostgreSQLConnector()

    def test_service_name(self, pg_connector):
        """Test service name property"""
        assert pg_connector.service_name == TargetService.POSTGRESQL

    def test_roles_to_pg_roles_read(self, pg_connector):
        """Test role mapping for read role"""
        pg_roles = pg_connector._roles_to_pg_roles(["read"])
        assert "pg_read_all_data" in pg_roles

    def test_roles_to_pg_roles_write(self, pg_connector):
        """Test role mapping for write role"""
        pg_roles = pg_connector._roles_to_pg_roles(["write"])
        assert "pg_write_all_data" in pg_roles

    def test_roles_to_pg_roles_superuser_excluded(self, pg_connector):
        """Test that superuser role is not in pg_roles (handled as attribute)"""
        pg_roles = pg_connector._roles_to_pg_roles(["superuser"])
        assert len(pg_roles) == 0  # superuser is handled separately

    def test_roles_to_pg_roles_custom(self, pg_connector):
        """Test that unknown roles are ignored"""
        pg_roles = pg_connector._roles_to_pg_roles(["custom_role"])
        assert len(pg_roles) == 0


class TestRoleMappings:
    """Test role mapping constants"""

    def test_mysql_role_mappings_exist(self):
        """Test MySQL role mappings are defined"""
        assert "read" in ROLE_TO_PRIVILEGES
        assert "write" in ROLE_TO_PRIVILEGES
        assert "admin" in ROLE_TO_PRIVILEGES

    def test_postgresql_role_mappings_exist(self):
        """Test PostgreSQL role mappings are defined"""
        assert "read" in ROLE_TO_PG_ROLES
        assert "write" in ROLE_TO_PG_ROLES
        assert "admin" in ROLE_TO_PG_ROLES
