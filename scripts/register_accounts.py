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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import AsyncSessionLocal
from app.core.exceptions import AppException
from app.services.auth_service import AuthService

DEFAULT_XLSX_PATH = Path(__file__).with_name("BV19-8_Đăng ký chatbot Ai.xlsx")
DEFAULT_PARENT_EMAIL = "namlh1610@gmail.com"
DEFAULT_PASSWORD = "ChangeMe123!"

XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NAME_HEADER_KEYS = {"ho va ten", "hoten", "ten", "full name", "name"}
EMAIL_HEADER_KEYS = {"email", "mail", "e mail", "e-mail"}


@dataclass(frozen=True)
class AccountRow:
    row_number: int
    full_name: str
    email: str


def normalize_header(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return text


def normalize_email(value: str) -> str:
    email = value.strip().lower().replace("mailto:", "")
    email = re.sub(r"\s+", "", email)
    return email.strip(";,.")


def column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index


def shared_strings(zf: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []

    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("x:si", XLSX_NS):
        text_parts = [node.text or "" for node in item.findall(".//x:t", XLSX_NS)]
        values.append("".join(text_parts))
    return values


def cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", XLSX_NS)).strip()

    value_node = cell.find("x:v", XLSX_NS)
    if value_node is None or value_node.text is None:
        return ""

    raw_value = value_node.text
    if cell_type == "s":
        try:
            return shared[int(raw_value)].strip()
        except (IndexError, ValueError):
            return ""
    return raw_value.strip()


def first_worksheet_name(zf: ZipFile) -> str:
    worksheet_names = sorted(
        name
        for name in zf.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    if not worksheet_names:
        raise ValueError("The workbook does not contain any worksheet XML files.")
    return worksheet_names[0]


def iter_sheet_rows(xlsx_path: Path) -> list[tuple[int, dict[int, str]]]:
    with ZipFile(xlsx_path) as zf:
        shared = shared_strings(zf)
        sheet_name = first_worksheet_name(zf)
        root = ET.fromstring(zf.read(sheet_name))

    rows: list[tuple[int, dict[int, str]]] = []
    for row in root.findall(".//x:sheetData/x:row", XLSX_NS):
        row_number = int(row.attrib.get("r", "0") or "0")
        values: dict[int, str] = {}
        for cell in row.findall("x:c", XLSX_NS):
            ref = cell.attrib.get("r", "")
            if not ref:
                continue
            value = cell_value(cell, shared)
            if value:
                values[column_index(ref)] = value
        rows.append((row_number, values))
    return rows


def find_account_columns(rows: list[tuple[int, dict[int, str]]]) -> tuple[int, int, int]:
    for row_number, values in rows:
        name_col: int | None = None
        email_col: int | None = None
        for col_index, value in values.items():
            header = normalize_header(value)
            compact_header = header.replace(" ", "")
            if header in NAME_HEADER_KEYS or compact_header in NAME_HEADER_KEYS:
                name_col = col_index
            if header in EMAIL_HEADER_KEYS or compact_header in EMAIL_HEADER_KEYS:
                email_col = col_index
        if name_col is not None and email_col is not None:
            return row_number, name_col, email_col

    raise ValueError("Cannot find header columns for full name and email in the workbook.")


def load_accounts(xlsx_path: Path) -> list[AccountRow]:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Excel file not found: {xlsx_path}")

    rows = iter_sheet_rows(xlsx_path)
    header_row, name_col, email_col = find_account_columns(rows)

    accounts: list[AccountRow] = []
    seen_emails: set[str] = set()
    for row_number, values in rows:
        if row_number <= header_row:
            continue

        email = normalize_email(values.get(email_col, ""))
        if not email:
            continue
        if not EMAIL_RE.match(email):
            print(f"Skip row {row_number}: invalid email {email!r}")
            continue
        if email in seen_emails:
            print(f"Skip row {row_number}: duplicated email in workbook {email}")
            continue

        full_name = values.get(name_col, "").strip() or email
        accounts.append(AccountRow(row_number=row_number, full_name=full_name, email=email))
        seen_emails.add(email)

    return accounts


async def register_accounts(args: argparse.Namespace) -> int:
    accounts = load_accounts(args.xlsx)
    parent_email = AuthService.normalize_email(args.parent_email)

    created: list[AccountRow] = []
    skipped: list[tuple[AccountRow, str]] = []
    failed: list[tuple[AccountRow, str]] = []

    async with AsyncSessionLocal() as session:
        auth_service = AuthService(session)
        parent = await auth_service.get_user_by_email(parent_email)
        if parent is None:
            raise RuntimeError(f"Parent hospital account not found: {parent_email}")
        if parent.role != AuthService.ROLE_HOSPITAL:
            raise RuntimeError(
                f"Parent account {parent_email} must have role '{AuthService.ROLE_HOSPITAL}', "
                f"current role is '{parent.role}'."
            )
        if not parent.is_active:
            raise RuntimeError(f"Parent hospital account is inactive: {parent_email}")

        print(
            f"Parent hospital: {parent.full_name or parent.email} "
            f"(user_id={parent.user_id}, email={parent.email})"
        )
        print(f"Loaded {len(accounts)} candidate account(s) from {args.xlsx}")

        for account in accounts:
            if account.email == parent_email:
                skipped.append((account, "parent hospital account"))
                continue

            existing_user = await auth_service.get_user_by_email(account.email)
            if existing_user is not None:
                skipped.append((account, f"already exists as user_id={existing_user.user_id}"))
                continue

            if args.dry_run:
                created.append(account)
                continue

            try:
                await auth_service.create_user(
                    current_user=parent,
                    email=account.email,
                    password=args.password,
                    role=AuthService.ROLE_DOCTOR,
                    full_name=account.full_name,
                    parent_id=int(parent.user_id),
                    is_active=True,
                    inherits_global_documents=bool(parent.inherits_global_documents),
                )
                created.append(account)
            except AppException as exc:
                failed.append((account, str(exc.detail)))

        if args.dry_run:
            await session.rollback()
        else:
            await session.commit()

    action = "Would create" if args.dry_run else "Created"
    print(f"{action}: {len(created)}")
    for account in created:
        print(f"  row {account.row_number}: {account.full_name} <{account.email}>")

    print(f"Skipped: {len(skipped)}")
    for account, reason in skipped:
        print(f"  row {account.row_number}: {account.email} ({reason})")

    print(f"Failed: {len(failed)}")
    for account, reason in failed:
        print(f"  row {account.row_number}: {account.email} ({reason})")

    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register doctor accounts from the BV19-8 chatbot signup workbook."
    )
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX_PATH)
    parser.add_argument("--parent-email", default=DEFAULT_PARENT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--dry-run", action="store_true", help="Preview accounts without writing to the database.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(register_accounts(args))
    except (OSError, ValueError, RuntimeError, SQLAlchemyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
