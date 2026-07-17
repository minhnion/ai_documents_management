#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-python:3.11-slim}"
PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK="${DOCKER_NETWORK:-}"
DB_HOST_VALUE="${DB_HOST:-db}"
DB_PORT_VALUE="${DB_PORT:-5432}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
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

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import AsyncSessionLocal
from app.core.exceptions import AppException
from app.core.roles import ROLE_HEALTH_DEPARTMENT, ROLE_HEALTH_STATION
from app.models.user import User
from app.services.auth_service import AuthService


XLSX_PATH = Path("/app/scripts/Danh_sach_168_tram_y_te_dang_ky_MediBot_email_theo_ten.xlsx")
PARENT_EMAIL = "soytetphcm@gmail.com"
SOURCE_EMAIL_DOMAIN = "example.com"
IMPORTED_EMAIL_DOMAIN = "gmail.com"
SOURCE_EMAIL_PREFIX = "soyte"
TARGET_EMAIL_PREFIX = "tramyte"

XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NAME_HEADER_KEYS = {"ho va ten", "hoten", "ten", "full name", "name"}
EMAIL_HEADER_KEYS = {"email", "mail", "e mail", "e-mail"}


@dataclass(frozen=True)
class EmailCorrection:
    row_number: int
    full_name: str
    old_email: str
    new_email: str


def normalize_header(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip().lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_email(value: str) -> str:
    email = value.strip().lower().replace("mailto:", "")
    return re.sub(r"\s+", "", email).strip(";,. ")


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


def build_correction(source_email: str, *, row_number: int, full_name: str) -> EmailCorrection:
    normalized = normalize_email(source_email)
    local_part, separator, domain = normalized.rpartition("@")
    if not separator or domain != SOURCE_EMAIL_DOMAIN:
        raise ValueError(
            f"Row {row_number}: expected an email ending with @{SOURCE_EMAIL_DOMAIN}, got {source_email!r}."
        )
    if not local_part.startswith(SOURCE_EMAIL_PREFIX):
        raise ValueError(
            f"Row {row_number}: expected email prefix {SOURCE_EMAIL_PREFIX!r}, got {source_email!r}."
        )

    old_email = f"{local_part}@{IMPORTED_EMAIL_DOMAIN}"
    new_email = f"{TARGET_EMAIL_PREFIX}{local_part[len(SOURCE_EMAIL_PREFIX):]}@{IMPORTED_EMAIL_DOMAIN}"
    if not EMAIL_RE.match(old_email) or not EMAIL_RE.match(new_email):
        raise ValueError(f"Row {row_number}: invalid converted email.")
    return EmailCorrection(row_number, full_name, old_email, new_email)


def load_corrections(xlsx_path: Path) -> list[EmailCorrection]:
    rows = load_worksheet_rows(xlsx_path)
    header_row, name_column, email_column = find_account_columns(rows)
    corrections: list[EmailCorrection] = []
    seen_old_emails: set[str] = set()
    seen_new_emails: set[str] = set()

    for row_number, values in rows:
        if row_number <= header_row:
            continue
        source_email = values.get(email_column, "")
        if not source_email.strip():
            continue
        full_name = values.get(name_column, "").strip()
        if not full_name:
            raise ValueError(f"Row {row_number}: health station name is required.")

        correction = build_correction(
            source_email,
            row_number=row_number,
            full_name=full_name,
        )
        if correction.old_email in seen_old_emails:
            raise ValueError(f"Row {row_number}: duplicate source email {correction.old_email}.")
        if correction.new_email in seen_new_emails:
            raise ValueError(f"Row {row_number}: duplicate target email {correction.new_email}.")

        corrections.append(correction)
        seen_old_emails.add(correction.old_email)
        seen_new_emails.add(correction.new_email)

    if not corrections:
        raise ValueError("No health station emails were found in the workbook.")
    return corrections


def ensure_active_parent(parent: User | None) -> User:
    if parent is None:
        raise RuntimeError(f"Parent health department account not found: {PARENT_EMAIL}")
    if parent.role != ROLE_HEALTH_DEPARTMENT:
        raise RuntimeError(
            f"Parent account {PARENT_EMAIL} must have role '{ROLE_HEALTH_DEPARTMENT}', "
            f"current role is '{parent.role}'."
        )
    if not parent.is_active:
        raise RuntimeError(f"Parent account is inactive: {PARENT_EMAIL}")
    return parent


async def fix_health_station_emails(args: argparse.Namespace) -> int:
    corrections = load_corrections(args.xlsx)

    async with AsyncSessionLocal() as session:
        auth_service = AuthService(session)
        parent = ensure_active_parent(
            await auth_service.get_user_by_email(PARENT_EMAIL)
        )
        parent_id = int(parent.user_id)

        old_emails = [correction.old_email for correction in corrections]
        new_emails = [correction.new_email for correction in corrections]
        result = await session.execute(
            select(User).where(User.email.in_(old_emails + new_emails))
        )
        users_by_email = {user.email: user for user in result.scalars().all()}

        to_update: list[tuple[EmailCorrection, User]] = []
        already_fixed: list[EmailCorrection] = []
        conflicts: list[str] = []

        for correction in corrections:
            old_user = users_by_email.get(correction.old_email)
            new_user = users_by_email.get(correction.new_email)

            if old_user is not None and new_user is not None:
                conflicts.append(
                    f"row {correction.row_number}: both {correction.old_email} and "
                    f"{correction.new_email} already exist."
                )
                continue

            if new_user is not None:
                if (
                    new_user.role == ROLE_HEALTH_STATION
                    and int(new_user.parent_id or 0) == parent_id
                    and new_user.is_active
                ):
                    already_fixed.append(correction)
                else:
                    conflicts.append(
                        f"row {correction.row_number}: target {correction.new_email} "
                        f"belongs to an unexpected account (role={new_user.role}, "
                        f"user_id={new_user.user_id})."
                    )
                continue

            if old_user is None:
                conflicts.append(
                    f"row {correction.row_number}: source account {correction.old_email} not found."
                )
                continue

            if old_user.role != ROLE_HEALTH_STATION or int(old_user.parent_id or 0) != parent_id:
                conflicts.append(
                    f"row {correction.row_number}: source {correction.old_email} is not a "
                    f"health station under {PARENT_EMAIL} (role={old_user.role}, "
                    f"parent_id={old_user.parent_id})."
                )
                continue

            to_update.append((correction, old_user))

        if conflicts:
            raise RuntimeError("\n".join(conflicts))

        print(f"Loaded {len(corrections)} health station email correction(s) from {args.xlsx}")
        action = "Would update" if args.dry_run else "Updated"
        print(f"{action}: {len(to_update)}")
        print(f"Already corrected: {len(already_fixed)}")

        if args.dry_run:
            await session.rollback()
        else:
            for correction, user in to_update:
                user.email = correction.new_email
            await session.commit()

    for correction, _ in to_update:
        print(f"  row {correction.row_number}: {correction.old_email} -> {correction.new_email}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Correct health station emails from soyte to tramyte in the database."
    )
    parser.add_argument("--xlsx", type=Path, default=XLSX_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview changes without updating the database.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        return asyncio.run(fix_health_station_emails(parse_args()))
    except (AppException, OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PYTHON
