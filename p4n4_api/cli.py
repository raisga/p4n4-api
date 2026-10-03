"""`p4n4-api` command: serve the API (default) or manage users."""

from __future__ import annotations

import argparse
import getpass
import sys

from p4n4_api import db, logs, users
from p4n4_api.config import load_settings


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise users.UserError("Passwords don't match.")
    return password


def _users(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        if args.action == "list":
            rows = users.list_all(conn)
            if not rows:
                print("No users. Create one with: p4n4-api users add <name> --role admin")
            for row in rows:
                print(f"{row['username']:<32} {row['role']:<9} created {row['created_at']}")
            return 0
        if args.action == "bootstrap":
            password = users.bootstrap_admin(conn, args.username)
            if password is None:
                print("Users already exist; nothing to do.")
            else:
                name = users.normalize_username(args.username)
                print(f"Created admin '{name}' with password: {password}")
                print("It won't be shown again. Change it with: POST /api/v1/auth/password")
            return 0
        name = users.normalize_username(args.username)
        if args.action == "add":
            user = users.create(conn, name, _read_password(args), args.role)
            print(f"Created {user.role} '{user.username}'.")
            return 0
        if users.get(conn, name) is None:
            raise users.UserError(f"No user '{name}'.")
        if args.action == "remove":
            users.delete(conn, name)
            print(f"Removed '{name}'.")
        elif args.action == "passwd":
            users.set_password(conn, name, _read_password(args))
            print(f"Changed the password for '{name}' and signed them out everywhere.")
        elif args.action == "role":
            users.set_role(conn, name, args.role)
            print(f"'{name}' is now {args.role}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="p4n4-api", description="P4N4 REST API gateway.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Run the API server (the default)")

    users_parser = sub.add_parser("users", help="Manage operator and admin accounts")
    actions = users_parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="List users")
    bootstrap = actions.add_parser(
        "bootstrap",
        help="Create the first admin with a generated password, if there are no users yet",
    )
    bootstrap.add_argument("username", nargs="?", default="admin")
    for action, help_text in (("add", "Create a user"), ("passwd", "Change a password")):
        p = actions.add_parser(action, help=help_text)
        p.add_argument("username")
        if action == "add":
            p.add_argument("--role", choices=users.ROLES, default="operator")
        p.add_argument("--password-stdin", action="store_true", help="Read the password from stdin")
    actions.add_parser("remove", help="Delete a user").add_argument("username")
    role = actions.add_parser("role", help="Change a user's role")
    role.add_argument("username")
    role.add_argument("role", choices=users.ROLES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "users":
        try:
            return _users(args)
        except users.UserError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    import uvicorn

    settings = load_settings()
    # The app reads X-Forwarded-For itself, only from P4N4_API_TRUSTED_PROXIES. uvicorn's own
    # handling would also trust 127.0.0.1, letting any local process pick its address.
    # Logging: our formatter and request IDs for uvicorn's lines too, and our access log
    # (with request ID and duration) instead of uvicorn's.
    uvicorn.run(
        "p4n4_api.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        access_log=False,
        log_config=logs.log_config(settings.log_format, settings.log_level),
    )
    return 0
