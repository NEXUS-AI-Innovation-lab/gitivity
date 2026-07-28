#!/usr/bin/env python3
"""
Send test provisioning messages to RabbitMQ.

Usage:
    python scripts/send_test_message.py --service mysql --action create
    python scripts/send_test_message.py --service postgresql --action create
    python scripts/send_test_message.py --service odoo --action create
    python scripts/send_test_message.py --service mongodb --action create
    python scripts/send_test_message.py --fixture create_user_mysql
    python scripts/send_test_message.py --custom '{"request_id": "...", ...}'
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime

import pika

# Default configuration
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "guest")
RABBITMQ_QUEUE = os.getenv("RABBITMQ_QUEUE", "provisioning_queue")

# Path to fixtures
FIXTURES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "tests",
    "fixtures",
    "sample_messages.json",
)


def load_fixtures() -> dict:
    """Load sample messages from fixtures file."""
    with open(FIXTURES_PATH) as f:
        return json.load(f)


def generate_message(service: str, action: str) -> dict:
    """Generate a test message for the specified service and action."""
    request_id = f"test-{uuid.uuid4().hex[:8]}"

    action_map = {
        "create": "CREATE_USER",
        "update": "UPDATE_USER",
        "delete": "DELETE_USER",
    }

    operation_type = action_map.get(action.lower(), "CREATE_USER")

    base_user_data = {
        "username": f"testuser_{uuid.uuid4().hex[:6]}",
        "email": f"testuser_{uuid.uuid4().hex[:6]}@example.com",
        "first_name": "Test",
        "last_name": "User",
        "password": "TestP@ssw0rd!",
        "roles": ["user"],
    }

    # Service-specific attributes
    service_attributes = {
        "mysql": {
            "host": "%",
            "database": "target_db",
        },
        "postgresql": {
            "can_login": True,
            "database": "target_db",
        },
        "odoo": {
            "name": f"{base_user_data['first_name']} {base_user_data['last_name']}",
            "lang": "en_US",
            "tz": "UTC",
        },
        "mongodb": {
            "mongodbDatabase": "target_db",
            "mongodbRoles": ["readWrite"],
        },
    }

    base_user_data["attributes"] = service_attributes.get(service.lower(), {})

    return {
        "request_id": request_id,
        "operation_type": operation_type,
        # May be a target ID or any alias declared in config/targets.yaml.
        "target_service": service,
        "user_data": base_user_data,
        "metadata": {
            "source": "test_script",
            "timestamp": datetime.utcnow().isoformat(),
            "correlation_id": f"corr-{uuid.uuid4().hex[:8]}",
        },
    }


def send_message(message: dict, queue: str = RABBITMQ_QUEUE) -> bool:
    """Send a message to RabbitMQ."""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
        parameters = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            credentials=credentials,
        )

        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()

        # Declare queue (creates if not exists)
        channel.queue_declare(queue=queue, durable=True)

        # Publish message
        channel.basic_publish(
            exchange="",
            routing_key=queue,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Persistent
                content_type="application/json",
            ),
        )

        connection.close()
        return True

    except Exception as e:
        print(f"Error sending message: {e}", file=sys.stderr)
        return False


def main():
    global RABBITMQ_HOST
    parser = argparse.ArgumentParser(
        description="Send test provisioning messages to RabbitMQ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Send a create user message for MySQL
    python scripts/send_test_message.py --service mysql --action create

    # Send a message from fixtures
    python scripts/send_test_message.py --fixture create_user_postgresql

    # Send a custom message
    python scripts/send_test_message.py --custom '{"request_id": "custom-001", ...}'

    # List available fixtures
    python scripts/send_test_message.py --list-fixtures
        """,
    )

    parser.add_argument(
        "--service",
        help="Target ID or alias from config/targets.yaml",
    )
    parser.add_argument(
        "--action",
        choices=["create", "update", "delete"],
        default="create",
        help="Operation type (default: create)",
    )
    parser.add_argument(
        "--fixture",
        help="Use a predefined fixture by name",
    )
    parser.add_argument(
        "--custom",
        help="Send a custom JSON message",
    )
    parser.add_argument(
        "--list-fixtures",
        action="store_true",
        help="List available fixtures",
    )
    parser.add_argument(
        "--queue",
        default=RABBITMQ_QUEUE,
        help=f"RabbitMQ queue name (default: {RABBITMQ_QUEUE})",
    )
    parser.add_argument(
        "--host",
        default=RABBITMQ_HOST,
        help=f"RabbitMQ host (default: {RABBITMQ_HOST})",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of messages to send (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print message without sending",
    )

    args = parser.parse_args()

    # Update host if specified
    RABBITMQ_HOST = args.host

    # List fixtures
    if args.list_fixtures:
        fixtures = load_fixtures()
        print("Available fixtures:")
        for name in fixtures.keys():
            print(f"  - {name}")
        return 0

    # Determine message to send
    if args.custom:
        try:
            message = json.loads(args.custom)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}", file=sys.stderr)
            return 1
    elif args.fixture:
        fixtures = load_fixtures()
        if args.fixture not in fixtures:
            print(f"Fixture '{args.fixture}' not found.", file=sys.stderr)
            print("Use --list-fixtures to see available fixtures.")
            return 1
        message = fixtures[args.fixture]
    elif args.service:
        message = generate_message(args.service, args.action)
    else:
        parser.print_help()
        return 1

    # Send messages
    for i in range(args.count):
        # Generate unique request_id for each message if sending multiple
        if args.count > 1 and not args.custom:
            if args.service:
                message = generate_message(args.service, args.action)
            else:
                message = message.copy()
                message["request_id"] = f"{message['request_id']}-{i+1}"

        print(f"\nMessage {i+1}/{args.count}:")
        print(json.dumps(message, indent=2))

        if not args.dry_run:
            if send_message(message, args.queue):
                print(f"✓ Message sent to queue '{args.queue}'")
            else:
                print(f"✗ Failed to send message", file=sys.stderr)
                return 1
        else:
            print("(dry-run, not sent)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
