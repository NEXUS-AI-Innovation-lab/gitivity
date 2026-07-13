#!/usr/bin/env python3
"""
Check provisioning operation status via the Gateway IAM API.

Usage:
    python scripts/check_provisioning.py --operation-id <id>
    python scripts/check_provisioning.py --list
    python scripts/check_provisioning.py --list --status FAILED
    python scripts/check_provisioning.py --retry <id>
"""

import argparse
import json
import os
import sys

import requests

# Default configuration
API_BASE_URL = os.environ.get("GATEWAY_API_URL", "http://localhost:8000")


def get_operation(operation_id: str) -> dict | None:
    """Get details of a specific provisioning operation."""
    try:
        response = requests.get(f"{API_BASE_URL}/api/v1/provisioning/{operation_id}")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"Operation '{operation_id}' not found.", file=sys.stderr)
        else:
            print(f"HTTP error: {e}", file=sys.stderr)
        return None
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return None


def list_operations(
    status: str | None = None,
    target_service: str | None = None,
    limit: int = 20,
) -> list[dict] | None:
    """List provisioning operations with optional filters."""
    try:
        params = {"limit": limit}
        if status:
            params["status"] = status
        if target_service:
            params["target_service"] = target_service

        response = requests.get(f"{API_BASE_URL}/api/v1/provisioning", params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return None


def get_audit_logs(operation_id: str) -> list[dict] | None:
    """Get audit logs for a specific operation."""
    try:
        response = requests.get(f"{API_BASE_URL}/api/v1/audit/{operation_id}")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return None


def retry_operation(operation_id: str) -> dict | None:
    """Retry a failed operation."""
    try:
        response = requests.post(f"{API_BASE_URL}/api/v1/provisioning/{operation_id}/retry")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e.response.text}", file=sys.stderr)
        return None
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return None


def check_health() -> dict | None:
    """Check API health status."""
    try:
        response = requests.get(f"{API_BASE_URL}/health")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return None


def format_operation(op: dict, verbose: bool = False) -> str:
    """Format an operation for display."""
    status_colors = {
        "SUCCESS": "\033[92m",  # Green
        "FAILED": "\033[91m",   # Red
        "PROCESSING": "\033[93m",  # Yellow
        "PENDING": "\033[94m",  # Blue
        "RETRYING": "\033[95m",  # Magenta
        "DLQ": "\033[91m",      # Red
    }
    reset = "\033[0m"

    status = op.get("status", "UNKNOWN")
    color = status_colors.get(status, "")

    lines = [
        f"ID: {op.get('id', 'N/A')}",
        f"MidPoint ID: {op.get('midpoint_id', 'N/A')}",
        f"Status: {color}{status}{reset}",
        f"Target: {op.get('target_service', 'N/A')}",
        f"Action: {op.get('operation_type', 'N/A')}",
        f"Created: {op.get('created_at', 'N/A')}",
    ]

    if verbose:
        lines.extend([
            f"Updated: {op.get('updated_at', 'N/A')}",
            f"Retry Count: {op.get('retry_count', 0)}",
            f"Validation ID: {op.get('validation_id', 'N/A')}",
        ])
        if op.get("error_message"):
            lines.append(f"Error: {op.get('error_message')}")
        if op.get("payload"):
            lines.append(f"Payload: {json.dumps(op.get('payload'), indent=2)}")

    return "\n".join(lines)


def format_table(operations: list[dict]) -> str:
    """Format operations as a table."""
    if not operations:
        return "No operations found."

    # Header
    header = f"{'ID':<36} {'Status':<12} {'Service':<12} {'Action':<12} {'Created':<20}"
    separator = "-" * len(header)

    lines = [header, separator]

    for op in operations:
        line = (
            f"{op.get('id', 'N/A'):<36} "
            f"{op.get('status', 'N/A'):<12} "
            f"{op.get('target_service', 'N/A'):<12} "
            f"{op.get('operation_type', 'N/A'):<12} "
            f"{str(op.get('created_at', 'N/A'))[:19]:<20}"
        )
        lines.append(line)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Check provisioning operation status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Check specific operation
    python scripts/check_provisioning.py --operation-id abc-123

    # List all operations
    python scripts/check_provisioning.py --list

    # List failed operations
    python scripts/check_provisioning.py --list --status FAILED

    # Retry a failed operation
    python scripts/check_provisioning.py --retry abc-123

    # Check API health
    python scripts/check_provisioning.py --health
        """,
    )

    parser.add_argument(
        "--operation-id", "-o",
        help="Operation ID to check",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List operations",
    )
    parser.add_argument(
        "--status", "-s",
        choices=["PENDING", "VALIDATING", "VALIDATED", "PROCESSING", "SUCCESS", "FAILED", "RETRYING", "DLQ"],
        help="Filter by status",
    )
    parser.add_argument(
        "--service",
        choices=["MYSQL", "POSTGRESQL", "ODOO", "LDAP", "MONGODB"],
        help="Filter by target service",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of results (default: 20)",
    )
    parser.add_argument(
        "--retry", "-r",
        help="Retry a failed operation by ID",
    )
    parser.add_argument(
        "--audit", "-a",
        help="Show audit logs for an operation",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Check API health",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show verbose output",
    )
    parser.add_argument(
        "--api-url",
        default=API_BASE_URL,
        help=f"API base URL (default: {API_BASE_URL})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON",
    )

    args = parser.parse_args()

    # Update API URL if specified
    global API_BASE_URL
    API_BASE_URL = args.api_url

    # Health check
    if args.health:
        result = check_health()
        if result:
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Status: {result.get('status', 'unknown')}")
                print(f"Database: {result.get('database', 'unknown')}")
        return 0 if result else 1

    # Retry operation
    if args.retry:
        result = retry_operation(args.retry)
        if result:
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"✓ Operation {args.retry} queued for retry")
                print(format_operation(result, args.verbose))
            return 0
        return 1

    # Show audit logs
    if args.audit:
        logs = get_audit_logs(args.audit)
        if logs:
            if args.json:
                print(json.dumps(logs, indent=2))
            else:
                print(f"Audit logs for operation {args.audit}:\n")
                for log in logs:
                    print(f"  [{log.get('created_at', 'N/A')}] {log.get('event_type', 'N/A')}: {log.get('details', '')}")
            return 0
        return 1

    # Get specific operation
    if args.operation_id:
        result = get_operation(args.operation_id)
        if result:
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(format_operation(result, args.verbose))
            return 0
        return 1

    # List operations
    if args.list:
        results = list_operations(args.status, args.service, args.limit)
        if results is not None:
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                print(format_table(results))
            return 0
        return 1

    # No action specified
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
