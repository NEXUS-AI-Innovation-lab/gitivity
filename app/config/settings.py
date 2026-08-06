"""Application configuration using Pydantic Settings"""
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    # Application
    APP_NAME: str = "Gateway IAM Provisioning"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    TARGET_CATALOG_PATH: str = "config/targets.yaml"
    ENTITLEMENT_SYNC_CONFIG_PATH: str = "config/entitlement_sync.yaml"

    # Database
    DATABASE_URL: str

    # Message Broker (rabbitmq is the default)
    BROKER_TYPE: Literal["rabbitmq", "kafka"] = "rabbitmq"

    # RabbitMQ Configuration (default broker)
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_VHOST: str = "/"
    RABBITMQ_QUEUE: str = "gateway-iam-provisioning"
    RABBITMQ_EXCHANGE: str = "gateway-iam"
    RABBITMQ_ROUTING_KEY: str = "provisioning"

    # Kafka Configuration (optional - set BROKER_TYPE=kafka to use)
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_TOPIC: str = "midpoint.provisioning"
    KAFKA_CONSUMER_GROUP: str = "gateway-iam"
    KAFKA_AUTO_OFFSET_RESET: str = "earliest"
    KAFKA_ENABLE_AUTO_COMMIT: bool = True

    # Redis Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str | None = None
    REDIS_DB: int = 0

    # Approval Workflow Configuration
    APPROVAL_ENABLED: bool = True  # Set to False to bypass approval (useful in dev/test)
    APPROVAL_MODE: Literal["email", "auto"] = "email"  # auto = auto-approve after delay (tests)
    AUTO_APPROVE_DELAY: int = 5  # seconds before auto-approval (APPROVAL_MODE=auto only)
    ADMIN_APPROVAL_EMAIL: str = "samiourrad2005@example.com"  # fallback when approvers.json is empty
    GATEWAY_EXTERNAL_URL: str = "http://localhost:8100"  # Public URL used in approval email links
    GATEWAY_HTTP_URL: str = "http://gateway-http:5100"
    ENTITLEMENT_DECISION_TOKEN_DAYS: int = 7
    ENTITLEMENT_RECONCILE_TOKEN: str = ""

    # SMTP (approval + confirmation emails)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""  # falls back to SMTP_USER when empty
    SMTP_STARTTLS: bool = True
    SMTP_TIMEOUT: int = 30

    # Retry Configuration
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_INITIAL_DELAY: int = 5  # seconds before first retry
    RETRY_BACKOFF_MULTIPLIER: float = 2.0  # each retry waits delay * multiplier^attempt
    RETRY_MAX_DELAY: int = 300  # cap at 5 minutes regardless of multiplier

    # Target Service: MySQL
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "mysql"
    MYSQL_CONNECT_TIMEOUT: int = 10

    # Target Service: PostgreSQL
    POSTGRESQL_HOST: str = "localhost"
    POSTGRESQL_PORT: int = 5432
    POSTGRESQL_USER: str = "postgres"
    POSTGRESQL_PASSWORD: str = ""
    POSTGRESQL_DATABASE: str = "postgres"
    POSTGRESQL_CONNECT_TIMEOUT: int = 10

    # Target Service: MongoDB
    MONGODB_HOST: str = "localhost"
    MONGODB_PORT: int = 27017
    MONGODB_USER: str = "root"
    MONGODB_PASSWORD: str = ""
    MONGODB_DATABASE: str = "target_db"
    MONGODB_AUTH_SOURCE: str = "admin"
    MONGODB_CONNECT_TIMEOUT: int = 10

    # Target Service: Odoo
    ODOO_URL: str = "http://localhost:8069"
    ODOO_DB: str = "odoo"
    ODOO_USERNAME: str = "admin"
    ODOO_PASSWORD: str = "admin"
    ODOO_TIMEOUT: int = 30

    # Target Service: LDAP
    LDAP_HOST: str = "localhost"
    LDAP_PORT: int = 10389
    LDAP_USE_SSL: bool = False
    LDAP_BIND_DN: str = "uid=admin,ou=system"
    LDAP_BIND_PASSWORD: str = "secret"
    LDAP_BASE_DN: str = "dc=openmicroscopy,dc=org"
    LDAP_USER_OBJECT_CLASS: str = "inetOrgPerson"
    LDAP_CONNECT_TIMEOUT: int = 10

    # MidPoint Integration
    MIDPOINT_URL: str = "http://localhost:8080/midpoint"
    MIDPOINT_USERNAME: str = "administrator"
    MIDPOINT_PASSWORD: str = "5ecr3t"
    MIDPOINT_TIMEOUT: int = 30

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # json or text

    # API Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8100
    API_WORKERS: int = 1
    CORS_ORIGINS: list[str] = ["*"]


# Global settings instance
settings = Settings()
