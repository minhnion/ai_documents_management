#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-python:3.11-slim}"
PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK="${DOCKER_NETWORK:-}"
DB_HOST_VALUE="${DB_HOST:-db}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
DB_PORT_VALUE="${DB_PORT:-5432}"
SCRIPT_ARGS=("$@")

if [[ -z "$NETWORK" ]]; then
    NETWORK="$(docker inspect guideline-backend --format "{{range \$name, \$_ := .NetworkSettings.Networks}}{{println \$name}}{{end}}" 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "$NETWORK" ]]; then
    NETWORK="$(docker inspect guideline-db --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "$NETWORK" ]]; then
    NETWORK="$(docker network ls --format '{{.Name}}' | grep '_default$' | grep -E 'ai_documents_management|guideline|document' | head -n 1 || true)"
fi

DOCKER_ARGS=(
    run
    --rm
    -i
    -v "$PROJECT_DIR:/app"
    -w /app
)

if [[ -n "$NETWORK" ]]; then
    DOCKER_ARGS+=(--network "$NETWORK")
fi

if [[ -f "$ENV_FILE" ]]; then
    DOCKER_ARGS+=(--env-file "$ENV_FILE")
fi

DOCKER_ARGS+=(
    -e "DB_HOST=$DB_HOST_VALUE"
    -e "DB_PORT=$DB_PORT_VALUE"
)

docker "${DOCKER_ARGS[@]}" \
    "$IMAGE" \
    sh -lc 'pip install --no-cache-dir -r requirements.txt && python - "$@"' \
    sh "${SCRIPT_ARGS[@]}" <<'PYTHON'
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import AppException
from app.core.roles import ROLE_ADMIN, ROLE_HEALTH_DEPARTMENT, ROLE_HEALTH_STATION
from app.models.user import User
from app.services.auth_service import AuthService


XLSX_PATH = Path("/app/scripts/Danh_sach_168_tram_y_te_dang_ky_MediBot_email_theo_ten.xlsx")
PARENT_EMAIL = "soytetphcm@gmail.com"
PARENT_FULL_NAME = "Sở y tế thành phố Hồ Chí Minh"
DEFAULT_PASSWORD = "ChangeMe123!"
SOURCE_EMAIL_DOMAIN = "example.com"
TARGET_EMAIL_DOMAIN = "gmail.com"

XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NAME_HEADER_KEYS = {"ho va ten", "hoten", "ten", "full name", "name"}
EMAIL_HEADER_KEYS = {"email", "mail", "e mail", "e-mail"}


@dataclass(frozen=True)
class HealthStationAccount:
    row_number: int
    full_name: str
    email: str


def normalize_header(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip().lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_email(value: str) -> str:
    email = value.strip().lower().replace("mailto:", "")
    return re.sub(r"\s+", "", email).strip(";,.")



def strip_env_quotes(value: str) -> str:
    return value.strip().strip(chr(34)).strip(chr(39))

def spreadsheet_column_index(cell_ref: str) -> int:
    index = 0
    for char in "".join(char for char in cell_ref if char.isalpha()).upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def load_shared_strings(workbook: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []

    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.findall(".//x:t", XLSX_NS))
        for item in root.findall("x:si", XLSX_NS)
    ]


def read_cell(cell: ET.Element, shared_strings: list[str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", XLSX_NS)).strip()

    value_node = cell.find("x:v", XLSX_NS)
    if value_node is None or value_node.text is None:
        return ""

    value = value_node.text
    if cell.attrib.get("t") == "s":
        try:
            return shared_strings[int(value)].strip()
        except (IndexError, ValueError):
            return ""
    return value.strip()


def load_worksheet_rows(xlsx_path: Path) -> list[tuple[int, dict[int, str]]]:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Excel file not found: {xlsx_path}")

    with ZipFile(xlsx_path) as workbook:
        worksheet_names = sorted(
            name
            for name in workbook.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if not worksheet_names:
            raise ValueError("The workbook does not contain a worksheet.")

        shared_strings = load_shared_strings(workbook)
        root = ET.fromstring(workbook.read(worksheet_names[0]))

    rows: list[tuple[int, dict[int, str]]] = []
    for row in root.findall(".//x:sheetData/x:row", XLSX_NS):
        row_number = int(row.attrib.get("r", "0") or "0")
        values: dict[int, str] = {}
        for cell in row.findall("x:c", XLSX_NS):
            cell_ref = cell.attrib.get("r", "")
            if not cell_ref:
                continue
            value = read_cell(cell, shared_strings)
            if value:
                values[spreadsheet_column_index(cell_ref)] = value
        rows.append((row_number, values))
    return rows


def find_account_columns(rows: list[tuple[int, dict[int, str]]]) -> tuple[int, int, int]:
    for row_number, values in rows:
        name_column: int | None = None
        email_column: int | None = None
        for column, value in values.items():
            header = normalize_header(value)
            compact_header = header.replace(" ", "")
            if header in NAME_HEADER_KEYS or compact_header in NAME_HEADER_KEYS:
                name_column = column
            if header in EMAIL_HEADER_KEYS or compact_header in EMAIL_HEADER_KEYS:
                email_column = column
        if name_column is not None and email_column is not None:
            return row_number, name_column, email_column
    raise ValueError("Cannot find the 'Họ và tên' and 'Email' columns in the workbook.")


def convert_email_domain(email: str, *, row_number: int) -> str:
    normalized = normalize_email(email)
    local_part, separator, domain = normalized.rpartition("@")
    if not separator or not local_part or domain != SOURCE_EMAIL_DOMAIN:
        raise ValueError(
            f"Row {row_number}: expected an email ending with @{SOURCE_EMAIL_DOMAIN}, got {email!r}."
        )
    converted = f"{local_part}@{TARGET_EMAIL_DOMAIN}"
    if not EMAIL_RE.match(converted):
        raise ValueError(f"Row {row_number}: invalid email {converted!r}.")
    return converted


def load_accounts(xlsx_path: Path) -> list[HealthStationAccount]:
    rows = load_worksheet_rows(xlsx_path)
    header_row, name_column, email_column = find_account_columns(rows)

    accounts: list[HealthStationAccount] = []
    seen_emails: set[str] = set()
    for row_number, values in rows:
        if row_number <= header_row:
            continue

        source_email = values.get(email_column, "")
        if not source_email.strip():
            continue

        full_name = values.get(name_column, "").strip()
        if not full_name:
            raise ValueError(f"Row {row_number}: health station name is required.")

        email = convert_email_domain(source_email, row_number=row_number)
        if email in seen_emails:
            raise ValueError(f"Row {row_number}: duplicate email after conversion: {email}.")
        if email == PARENT_EMAIL:
            raise ValueError(f"Row {row_number}: station email cannot equal the parent department email.")

        accounts.append(HealthStationAccount(row_number, full_name, email))
        seen_emails.add(email)

    if not accounts:
        raise ValueError("No health station accounts were found in the workbook.")
    return accounts


def ensure_active_role(user: User | None, *, email: str, role: str) -> User:
    if user is None:
        raise RuntimeError(f"Account not found: {email}")
    if user.role != role:
        raise RuntimeError(f"Account {email} must have role '{role}', current role is '{user.role}'.")
    if not user.is_active:
        raise RuntimeError(f"Account {email} is inactive.")
    return user


async def register_health_stations(args: argparse.Namespace) -> int:
    accounts = load_accounts(args.xlsx)
    parent_email = AuthService.normalize_email(PARENT_EMAIL)
    created_accounts: list[HealthStationAccount] = []
    skipped_accounts: list[HealthStationAccount] = []

    async with AsyncSessionLocal() as session:
        auth_service = AuthService(session)
        parent = await auth_service.get_user_by_email(parent_email)
        parent_will_be_created = parent is None

        if parent is not None:
            parent = ensure_active_role(
                parent,
                email=parent_email,
                role=ROLE_HEALTH_DEPARTMENT,
            )
        else:
            admin_email = AuthService.normalize_email(strip_env_quotes(args.admin_email))
            admin = ensure_active_role(
                await auth_service.get_user_by_email(admin_email),
                email=admin_email,
                role=ROLE_ADMIN,
            )
            if not args.dry_run:
                parent = await auth_service.create_user(
                    current_user=admin,
                    email=parent_email,
                    password=DEFAULT_PASSWORD,
                    role=ROLE_HEALTH_DEPARTMENT,
                    full_name=PARENT_FULL_NAME,
                    is_active=True,
                    inherits_global_documents=True,
                )

        conflicts: list[str] = []
        for account in accounts:
            existing_user = await auth_service.get_user_by_email(account.email)
            if existing_user is None:
                created_accounts.append(account)
                continue

            is_existing_station = existing_user.role == ROLE_HEALTH_STATION
            has_expected_parent = parent is not None and existing_user.parent_id == parent.user_id
            if is_existing_station and has_expected_parent:
                skipped_accounts.append(account)
                continue

            conflicts.append(
                f"row {account.row_number}: {account.email} already exists "
                f"as role '{existing_user.role}' (user_id={existing_user.user_id})."
            )

        if conflicts:
            raise RuntimeError("\n".join(conflicts))

        print(f"Loaded {len(accounts)} health station account(s) from {args.xlsx}")
        if parent_will_be_created:
            print(f"{'Would create' if args.dry_run else 'Created'} parent: {PARENT_FULL_NAME} <{parent_email}>")
        else:
            print(f"Parent already exists: {PARENT_FULL_NAME} <{parent_email}>")

        if args.dry_run:
            await session.rollback()
        else:
            if parent is None:
                raise RuntimeError("Parent department was not created.")
            for account in created_accounts:
                await auth_service.create_user(
                    current_user=parent,
                    email=account.email,
                    password=DEFAULT_PASSWORD,
                    role=ROLE_HEALTH_STATION,
                    full_name=account.full_name,
                    parent_id=int(parent.user_id),
                    is_active=True,
                    inherits_global_documents=bool(parent.inherits_global_documents),
                )
            await session.commit()

    action = "Would create" if args.dry_run else "Created"
    print(f"{action} health stations: {len(created_accounts)}")
    for account in created_accounts:
        print(f"  row {account.row_number}: {account.full_name} <{account.email}>")
    print(f"Skipped existing health stations: {len(skipped_accounts)}")
    for account in skipped_accounts:
        print(f"  row {account.row_number}: {account.full_name} <{account.email}>")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the Ho Chi Minh City Health Department and its health station accounts."
    )
    parser.add_argument(
        "--xlsx",
        type=Path,
        default=XLSX_PATH,
        help="Path inside the Docker container to the source workbook.",
    )
    parser.add_argument(
        "--admin-email",
        default=settings.DEFAULT_ADMIN_EMAIL,
        help="Active admin email used only when the parent department needs to be created.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the workbook and show planned changes without writing to the database.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        return asyncio.run(register_health_stations(parse_args()))
    except (AppException, OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PYTHON
