"""Application enums matching Prisma schema"""
from enum import Enum


class OperationType(str, Enum):
    """Types of provisioning operations"""

    CREATE_USER = "CREATE_USER"
    UPDATE_USER = "UPDATE_USER"
    DELETE_USER = "DELETE_USER"
    CREATE_ROLE = "CREATE_ROLE"
    UPDATE_ROLE = "UPDATE_ROLE"
    DELETE_ROLE = "DELETE_ROLE"
    ASSIGN_ROLE = "ASSIGN_ROLE"
    REVOKE_ROLE = "REVOKE_ROLE"


class OperationStatus(str, Enum):
    """Status of a provisioning operation.

    State machine transitions:
      PENDING → VALIDATING → VALIDATED → APPROVAL_PENDING → PROCESSING → SUCCESS
                                                          ↘ FAILED
      Any state → RETRYING → (retries same state) → DLQ (max retries exceeded)
    """

    PENDING = "PENDING"  # Initial state when message received
    VALIDATING = "VALIDATING"  # Calling n8n for validation
    VALIDATED = "VALIDATED"  # n8n approved the operation
    APPROVAL_PENDING = "APPROVAL_PENDING"  # Waiting for manual/automated approval
    PROCESSING = "PROCESSING"  # Provisioning to target service
    SUCCESS = "SUCCESS"  # Operation completed successfully
    FAILED = "FAILED"  # Operation failed (not retriable)
    RETRYING = "RETRYING"  # Scheduled for retry
    DLQ = "DLQ"  # Sent to Dead Letter Queue (max retries exceeded)


class TargetService(str, Enum):
    """Target services for provisioning"""

    MYSQL = "MYSQL"
    POSTGRESQL = "POSTGRESQL"
    ODOO = "ODOO"
    LDAP = "LDAP"
    MONGODB = "MONGODB"


class BrokerType(str, Enum):
    """Supported message broker types"""

    KAFKA = "kafka"
    RABBITMQ = "rabbitmq"


class ActorType(str, Enum):
    """Types of actors for audit logging"""

    SYSTEM = "system"
    USER = "user"
    RETRY_MANAGER = "retry_manager"
    API = "api"
