import csv
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass
class ImportConfig:
    base_url: str
    username: str
    password: str
    csv_path: str
    timeout: int = 30
    verify_ssl: bool = True
    retry_count: int = 2
    retry_sleep_sec: float = 1.5


class InvoiceBatchImporter:
    def __init__(self, config: ImportConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.token: Optional[str] = None
        self.role: Optional[str] = None

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        last_exc = None
        for attempt in range(self.config.retry_count + 1):
            try:
                return self.session.request(
                    method=method,
                    url=self._url(path),
                    timeout=self.config.timeout,
                    verify=self.config.verify_ssl,
                    **kwargs,
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.config.retry_count:
                    time.sleep(self.config.retry_sleep_sec)
                else:
                    raise last_exc

    def login(self) -> None:
        resp = self._request(
            "POST",
            "/auth/login",
            json={
                "username": self.config.username,
                "password": self.config.password,
            },
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Login failed: {resp.status_code} - {resp.text}")

        data = resp.json()
        token = data.get("access_token")
        user = data.get("user", {})

        if not token:
            raise RuntimeError("Login succeeded but no access_token was returned")

        self.token = token
        self.role = (user.get("role") or "").strip().upper()

        if self.role not in {"ADMIN", "ONLINE_ADMIN"}:
            raise RuntimeError(
                f"Unsupported role for this importer: {self.role}. "
                f"Allowed roles are ADMIN and ONLINE_ADMIN."
            )

        self.session.headers["Authorization"] = f"Bearer {token}"

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().upper() in {"1", "TRUE", "YES", "Y"}

    @staticmethod
    def _to_int(value: Any, field_name: str) -> int:
        try:
            return int(str(value).strip())
        except Exception as exc:
            raise ValueError(f"Invalid integer for {field_name}: {value}") from exc

    @staticmethod
    def _to_float(value: Any, field_name: str) -> float:
        try:
            number = float(str(value).strip())
            if number <= 0:
                raise ValueError
            return number
        except Exception as exc:
            raise ValueError(f"Invalid positive number for {field_name}: {value}") from exc

    def get_payment_method_for_logged_in_user(self) -> str:
        if self.role == "ADMIN":
            return "CASH"
        if self.role == "ONLINE_ADMIN":
            return "ONLINE"
        raise RuntimeError(f"Unsupported role for collection: {self.role}")

    def find_resident(self, building: str, floor: str, apartment: str) -> Dict[str, Any]:
        resp = self._request(
            "GET",
            "/admin/residents",
            params={
                "building": building,
                "floor": floor,
                "apartment": apartment,
            },
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to search resident B{building}/F{floor}/A{apartment}: "
                f"{resp.status_code} - {resp.text}"
            )

        residents = resp.json()
        if not isinstance(residents, list) or len(residents) == 0:
            raise RuntimeError(
                f"Resident not found for building={building}, floor={floor}, apartment={apartment}"
            )

        if len(residents) > 1:
            raise RuntimeError(
                f"Multiple residents found for building={building}, floor={floor}, apartment={apartment}"
            )

        return residents[0]

    def get_resident_invoices(self, user_id: int) -> List[Dict[str, Any]]:
        resp = self._request("GET", f"/admin/residents/{user_id}/invoices")

        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to get invoices for resident {user_id}: "
                f"{resp.status_code} - {resp.text}"
            )

        payload = resp.json()
        return payload.get("invoices", [])

    @staticmethod
    def find_invoice_for_month(
        invoices: List[Dict[str, Any]],
        year: int,
        month: int,
    ) -> Optional[Dict[str, Any]]:
        for inv in invoices:
            if int(inv.get("year", 0)) == year and int(inv.get("month", 0)) == month:
                return inv
        return None

    def create_invoice(
        self,
        user_id: int,
        year: int,
        month: int,
        amount: float,
        due_date: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "year": year,
            "month": month,
            "amount": amount,
        }

        if due_date:
            payload["due_date"] = due_date
        if notes:
            payload["notes"] = notes

        resp = self._request("POST", "/admin/invoices", json=payload)

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to create invoice for user_id={user_id}, year={year}, month={month}: "
                f"{resp.status_code} - {resp.text}"
            )

        data = resp.json()
        return data["invoice"]

    def collect_payment(
        self,
        user_id: int,
        invoice_id: int,
        amount: float,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "user_id": user_id,
            "invoice_id": invoice_id,
            "amount": amount,
            "method": self.get_payment_method_for_logged_in_user(),
        }

        if notes:
            payload["notes"] = notes

        resp = self._request("POST", "/admin/collect", json=payload)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to collect payment for invoice_id={invoice_id}: "
                f"{resp.status_code} - {resp.text}"
            )

        return resp.json()

    def process_row(self, row: Dict[str, str]) -> Dict[str, Any]:
        building = str(row.get("building", "")).strip()
        floor = str(row.get("floor", "")).strip()
        apartment = str(row.get("apartment", "")).strip()

        if not building or not floor or not apartment:
            raise ValueError("building, floor, apartment are required")

        year = self._to_int(row.get("year"), "year")
        month = self._to_int(row.get("month"), "month")
        if not 1 <= month <= 12:
            raise ValueError(f"Invalid month: {month}")

        invoice_amount = self._to_float(row.get("invoice_amount"), "invoice_amount")
        payment_amount = self._to_float(row.get("payment_amount"), "payment_amount")

        due_date = (row.get("due_date") or "").strip() or None
        invoice_notes = (row.get("invoice_notes") or "").strip() or None
        payment_notes = (row.get("payment_notes") or "").strip() or None

        create_invoice_if_missing = self._to_bool(
            row.get("create_invoice_if_missing"),
            default=True,
        )
        skip_if_paid = self._to_bool(
            row.get("skip_if_paid"),
            default=True,
        )

        resident = self.find_resident(building, floor, apartment)
        user_id = resident["id"]

        invoices = self.get_resident_invoices(user_id)
        invoice = self.find_invoice_for_month(invoices, year, month)

        created_invoice = False

        if invoice is None:
            if not create_invoice_if_missing:
                raise RuntimeError(
                    f"No invoice found for resident {building}/{floor}/{apartment} "
                    f"for {year}-{month:02d}, and create_invoice_if_missing is FALSE"
                )

            invoice = self.create_invoice(
                user_id=user_id,
                year=year,
                month=month,
                amount=invoice_amount,
                due_date=due_date,
                notes=invoice_notes,
            )
            created_invoice = True

        invoice_id = invoice["id"]
        status = str(invoice.get("status", "")).upper()

        if status == "PAID":
            if skip_if_paid:
                return {
                    "success": True,
                    "action": "SKIPPED_ALREADY_PAID",
                    "resident_id": user_id,
                    "invoice_id": invoice_id,
                    "year": year,
                    "month": month,
                    "message": "Invoice already paid, skipped.",
                    "payment_method_used": self.get_payment_method_for_logged_in_user(),
                }
            raise RuntimeError(f"Invoice {invoice_id} is already PAID")

        collect_result = self.collect_payment(
            user_id=user_id,
            invoice_id=invoice_id,
            amount=payment_amount,
            notes=payment_notes,
        )

        return {
            "success": True,
            "action": "CREATED_AND_COLLECTED" if created_invoice else "COLLECTED",
            "resident_id": user_id,
            "invoice_id": invoice_id,
            "year": year,
            "month": month,
            "created_invoice": created_invoice,
            "payment_method_used": self.get_payment_method_for_logged_in_user(),
            "api_message": collect_result.get("message"),
        }

    def process_csv(self) -> List[Dict[str, Any]]:
        self.login()

        results: List[Dict[str, Any]] = []

        with open(self.config.csv_path, mode="r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)

            required_columns = {
                "building",
                "floor",
                "apartment",
                "year",
                "month",
                "invoice_amount",
                "payment_amount",
            }

            if not reader.fieldnames:
                raise ValueError("CSV file has no header row")

            missing = required_columns - set(reader.fieldnames)
            if missing:
                raise ValueError(f"CSV is missing required columns: {sorted(missing)}")

            for row_number, row in enumerate(reader, start=2):
                try:
                    result = self.process_row(row)
                    result["row_number"] = row_number
                    results.append(result)
                    print(
                        f"[ROW {row_number}] SUCCESS - {result['action']} "
                        f"(invoice_id={result.get('invoice_id')}, method={result.get('payment_method_used')})"
                    )
                except Exception as exc:
                    error_result = {
                        "success": False,
                        "row_number": row_number,
                        "action": "ERROR",
                        "error": str(exc),
                        "building": row.get("building"),
                        "floor": row.get("floor"),
                        "apartment": row.get("apartment"),
                        "year": row.get("year"),
                        "month": row.get("month"),
                    }
                    results.append(error_result)
                    print(f"[ROW {row_number}] ERROR - {exc}")

        return results


def save_results(results: List[Dict[str, Any]], output_csv: str = "import_results.csv") -> None:
    fieldnames = sorted({key for row in results for key in row.keys()})

    with open(output_csv, mode="w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main() -> None:
    if len(sys.argv) < 5:
        print(
            "Usage:\n"
            "python bulk_import_invoices.py <BASE_URL> <USERNAME> <PASSWORD> <CSV_PATH>\n\n"
            "Example:\n"
            "python bulk_import_invoices.py "
            "http://127.0.0.1:5000 "
            "online_admin "
            "your_password "
            "invoices_import.csv"
        )
        sys.exit(1)

    base_url = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    csv_path = sys.argv[4]

    config = ImportConfig(
        base_url=base_url,
        username=username,
        password=password,
        csv_path=csv_path,
        timeout=30,
        verify_ssl=True,
        retry_count=2,
        retry_sleep_sec=1.5,
    )

    importer = InvoiceBatchImporter(config)
    results = importer.process_csv()
    save_results(results)

    total = len(results)
    success = sum(1 for r in results if r.get("success"))
    failed = total - success

    print("\n========== SUMMARY ==========")
    print(f"Logged-in role : {importer.role}")
    print(f"Total rows     : {total}")
    print(f"Succeeded      : {success}")
    print(f"Failed         : {failed}")
    print("Detailed results saved to import_results.csv")


if __name__ == "__main__":
    main()