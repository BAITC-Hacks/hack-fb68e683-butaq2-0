"""Deterministic, side-effect-free action adapters for configured demo scenarios."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from .contracts import ActionTrace, Catalog, Scenario


SUPPORTED_ACTIONS = frozenset(
    {
        "find_client",
        "get_policies",
        "get_policy",
        "get_bm_class",
        "calc_ogpo_price",
        "calc_casco_price",
        "calc_travel_price",
        "calc_property_price",
        "calc_accident_price",
        "create_policy",
        "renew_policy",
        "update_policy",
        "cancel_policy",
        "create_claim",
        "get_claim",
        "create_dispute",
        "book_inspection",
        "book_appointment",
        "check_coverage",
        "list_clinics",
        "resend_documents",
        "check_payment",
        "update_contact",
        "request_document",
        "get_offices",
        "kb_lookup",
        "send_sms",
        "create_callback",
        "create_complaint",
        "report_fraud",
        "transfer_to_operator",
    }
)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _first(rows: Any, predicate) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    return next((row for row in rows if isinstance(row, dict) and predicate(row)), None)


def _stable_id(
    prefix: str, session_id: str, action: str, values: dict[str, Any]
) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    number = int(
        hashlib.sha256(f"{session_id}:{action}:{payload}".encode()).hexdigest()[:8], 16
    )
    return f"{prefix}{number % 900000 + 100000}"


def _coerce_int(value: Any) -> int | None:
    try:
        return int(str(value).replace(" ", ""))
    except (TypeError, ValueError):
        return None


class SimulationEngine:
    """Run allow-listed starter-kit actions without mutating any source data."""

    def __init__(self, catalog: Catalog, scenario: Scenario, session_id: str):
        self.catalog = catalog
        self.scenario = scenario
        self.session_id = session_id
        self.backend = catalog.backend if isinstance(catalog.backend, dict) else {}
        self.knowledge = (
            catalog.knowledge if isinstance(catalog.knowledge, dict) else {}
        )

    def run(
        self, values: dict[str, Any], *, mode: str, utterance: str
    ) -> tuple[list[ActionTrace], dict[str, Any]]:
        working = dict(values)
        slug_prefix = str(self.scenario.details.get("slug", "")).split("_", 1)[0]
        product_values = self.catalog.slots.get("product_type", {}).get("values", [])
        inferred_product = (
            slug_prefix
            if slug_prefix in product_values
            else self.scenario.details.get("domain")
        )
        working.setdefault("product_type", inferred_product)
        working.setdefault(
            "topic",
            self.scenario.details.get("slug") or self.scenario.details.get("category"),
        )
        handoff = self.scenario.details.get("handoff")
        if isinstance(handoff, dict):
            working.setdefault("queue", handoff.get("queue"))
        traces: list[ActionTrace] = []
        declared = self.scenario.details.get("actions", [])
        if not isinstance(declared, list):
            return traces, working
        for name in declared:
            if name not in SUPPORTED_ACTIONS:
                traces.append(
                    ActionTrace(
                        name=str(name),
                        mode="skipped",
                        status="error",
                        error={
                            "code": "unsupported_action",
                            "message": "Action is not registered",
                        },
                    )
                )
                continue
            if name == "transfer_to_operator" and not self._handoff_required(
                utterance, working
            ):
                traces.append(ActionTrace(name=name, mode="skipped", status="skipped"))
                continue
            definition = self.catalog.actions.get(name, {})
            if not self._inputs_available(definition, working):
                traces.append(ActionTrace(name=name, mode="skipped", status="skipped"))
                continue
            irreversible = bool(definition.get("irreversible"))
            action_mode = (
                "preview"
                if irreversible and mode == "preview"
                else (
                    "simulate"
                    if irreversible
                    else ("handoff" if name == "transfer_to_operator" else "read")
                )
            )
            result = self._invoke(name, working, action_mode)
            if "error" in result:
                traces.append(
                    ActionTrace(
                        name=name,
                        mode=action_mode,
                        status="error",
                        error=result["error"],
                    )
                )
                continue
            working.update(
                {key: value for key, value in result.items() if key != "simulated"}
            )
            traces.append(
                ActionTrace(
                    name=name, mode=action_mode, status="success", result=result
                )
            )
        return traces, working

    @staticmethod
    def _inputs_available(definition: dict[str, Any], values: dict[str, Any]) -> bool:
        inputs = definition.get("inputs", [])
        if not isinstance(inputs, list):
            return False
        return all(
            any(
                values.get(candidate) not in (None, "", [])
                for candidate in str(group).split("|")
            )
            for group in inputs
        )

    def _handoff_required(self, utterance: str, values: dict[str, Any]) -> bool:
        handoff = self.scenario.details.get("handoff")
        if not isinstance(handoff, dict):
            return False
        condition = str(handoff.get("when", "")).casefold()
        text = utterance.casefold()
        if condition.startswith("always"):
            return True
        human_words = ("оператор", "человек", "менеджер", "маман", "адам", "қосыңыз")
        if any(word in text for word in human_words):
            return True
        if "already shared" in condition and any(
            word in text for word in ("сообщил", "передал", "айттым", "бердім")
        ):
            return True
        if "theft" in condition and any(
            word in text for word in ("угнал", "украл", "краж", "ұрлап", "жоғалды")
        ):
            return True
        if "injured" in condition and values.get("injured") is True:
            return True
        if (
            "payment found" in condition
            and values.get("payment_status") == "paid"
            and not values.get("policy_number")
        ):
            return True
        return False

    def _invoke(self, name: str, values: dict[str, Any], mode: str) -> dict[str, Any]:
        handler = getattr(self, f"_{name}")
        return handler(values, mode)

    def _find_client(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        phone, iin = v.get("phone"), v.get("iin")
        row = _first(
            self.backend.get("clients"),
            lambda item: (
                (phone and item.get("phone") == phone)
                or (iin and item.get("iin") == iin)
            ),
        )
        if not row:
            return _error("not_found", "Synthetic client was not found")
        return {
            "client_id": row.get("client_id"),
            "full_name": row.get("full_name"),
            "client": row,
        }

    def _get_policies(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        rows = [
            p
            for p in self.backend.get("policies", [])
            if p.get("client_id") == v.get("client_id")
        ]
        if not rows:
            return _error("not_found", "No synthetic policies found")
        result: dict[str, Any] = {"policies": rows}
        if len(rows) == 1:
            result["policy_number"] = rows[0].get("policy_number")
        return result

    def _get_policy(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        number = v.get("policy_number")
        plate = v.get("vehicle_plate") or v.get("culprit_vehicle_plate")
        row = _first(
            self.backend.get("policies"),
            lambda item: (
                (number and item.get("policy_number") == number)
                or (plate and item.get("details", {}).get("vehicle_plate") == plate)
            ),
        )
        if not row:
            return _error("not_found", "Synthetic policy was not found")
        today = self._snapshot_date()
        status = (
            "active"
            if str(row.get("start_date", "")) <= today <= str(row.get("end_date", ""))
            else "inactive"
        )
        return {**row, "status": status}

    def _get_bm_class(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        iins = v.get("drivers_iin") or [v.get("iin")]
        if not isinstance(iins, list):
            iins = [iins]
        result = {}
        for iin in filter(None, iins):
            row = _first(
                self.backend.get("clients"), lambda item: item.get("iin") == iin
            )
            result[str(iin)] = (row or {}).get(
                "bm_class",
                self.backend.get("defaults", {}).get("unknown_iin_bm_class", "3"),
            )
        return {"bm_classes": result, "bm_class": next(iter(result.values()), "3")}

    def _calc_ogpo_price(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        pricing = self.knowledge.get("products", {}).get("ogpo", {}).get("pricing", {})
        base = pricing.get("base_by_region_kzt", {}).get(
            str(v.get("region", "other")).lower()
        )
        vehicle = pricing.get("vehicle_type_coef", {}).get(v.get("vehicle_type"), 1)
        classes = list(
            (v.get("bm_classes") or {"default": v.get("bm_class", "3")}).values()
        )
        bm_table = pricing.get("bm_coef", {})
        bm = max((bm_table.get(str(item), 1) for item in classes), default=1)
        return {"price": round((base or 26000) * vehicle * bm)}

    def _calc_casco_price(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        value, year = _coerce_int(v.get("car_value")), _coerce_int(v.get("car_year"))
        if value is None or year is None:
            return _error("invalid_input", "Car value and year are required")
        age = int(self._snapshot_date()[:4]) - year
        if age > 15:
            return _error(
                "not_eligible",
                "Cars older than fifteen years require individual review",
            )
        rate = 0.04 if age <= 3 else 0.05 if age <= 7 else 0.065
        franchise = {0: 1, 50000: 0.9, 100000: 0.8}.get(
            _coerce_int(v.get("franchise")), 1
        )
        return {"price": round(value * rate * franchise), "package": "Standard"}

    def _calc_travel_price(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        age = _coerce_int(v.get("traveler_max_age")) or 0
        if age > 75:
            return _error(
                "not_eligible", "Travelers over seventy-five require an operator"
            )
        try:
            start, end = (
                date.fromisoformat(str(v["trip_start"])),
                date.fromisoformat(str(v["trip_end"])),
            )
        except (KeyError, ValueError):
            return _error("invalid_input", "Valid travel dates are required")
        country = str(v.get("trip_country", "")).casefold()
        zone = (
            "D"
            if any(x in country for x in ("usa", "canada", "сша", "канада"))
            else "C"
        )
        zone_data = (
            self.knowledge.get("products", {})
            .get("travel", {})
            .get("zones", {})
            .get(zone, {})
        )
        days = max(1, (end - start).days + 1)
        count = _coerce_int(v.get("travelers_count")) or 1
        price = int(
            zone_data.get("rate_per_day_kzt", 1100)
            * days
            * count
            * (2 if age >= 65 else 1)
        )
        return {
            "price": price,
            "zone": zone,
            "coverage": zone_data.get("coverage", "50 000 USD"),
        }

    def _calc_property_price(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        product = self.knowledge.get("products", {}).get("property", {})
        price = product.get("price_per_year_kzt", {}).get(str(v.get("sum_insured")))
        if price is None:
            return _error("invalid_input", "Unsupported insured sum")
        return {
            "price": round(
                price
                * (
                    product.get("house_coef", 1.5)
                    if v.get("property_type") == "house"
                    else 1
                )
            )
        }

    def _calc_accident_price(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        price = (
            self.knowledge.get("products", {})
            .get("accident", {})
            .get("price_per_year_kzt", {})
            .get(str(v.get("sum_insured")))
        )
        return (
            {"price": price}
            if price is not None
            else _error("invalid_input", "Unsupported insured sum")
        )

    def _create_policy(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        product = str(
            v.get("product_type") or self.scenario.details.get("domain") or "DEMO"
        ).upper()[:5]
        return {
            "policy_number": _stable_id(
                f"SIM-{product}-", self.session_id, "create_policy", v
            ),
            "simulated": True,
        }

    def _renew_policy(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        if not v.get("policy_number"):
            return _error("not_found", "Policy number is required")
        return {
            "policy_number": v["policy_number"],
            "price": v.get("premium") or v.get("price"),
            "simulated": True,
        }

    def _update_policy(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {"extra_premium": v.get("extra_premium", 0), "simulated": True}

    def _cancel_policy(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        premium = _coerce_int(v.get("premium")) or 200000
        return {"refund_amount": round(premium * 0.45), "simulated": True}

    def _create_claim(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {
            "claim_number": _stable_id("SIM-CL-", self.session_id, "create_claim", v),
            "simulated": True,
        }

    def _get_claim(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        row = _first(
            self.backend.get("claims"),
            lambda item: (
                item.get("claim_number") == v.get("claim_number")
                or (v.get("client_id") and item.get("client_id") == v.get("client_id"))
            ),
        )
        return (
            dict(row) if row else _error("not_found", "Synthetic claim was not found")
        )

    def _create_dispute(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {
            "ticket_id": _stable_id("SIM-D-", self.session_id, "create_dispute", v),
            "simulated": True,
        }

    def _book_inspection(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        points = self.knowledge.get("inspection_points", [])
        point = _first(
            points,
            lambda item: (
                str(item.get("city", "")).casefold()
                == str(v.get("city", "")).casefold()
            ),
        )
        if not point:
            return _error("no_availability", "No inspection point in the selected city")
        return {
            "slot_datetime": f"{v.get('preferred_date')} 10:00",
            "address": point.get("address"),
            "simulated": True,
        }

    def _book_appointment(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        clinic = _first(
            self.knowledge.get("clinics"),
            lambda item: (
                str(item.get("city", "")).casefold()
                == str(v.get("city", "")).casefold()
                and (
                    not v.get("doctor_specialty")
                    or v.get("doctor_specialty") in item.get("specialties", [])
                )
            ),
        )
        if not clinic:
            return _error("no_availability", "No matching clinic slot was found")
        return {
            "clinic_name": clinic.get("name"),
            "slot_datetime": f"{v.get('preferred_date')} 09:30",
            "simulated": True,
        }

    def _check_coverage(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        service = str(v.get("service_name", "")).casefold()
        covered = not any(
            word in service for word in ("implant", "cosmet", "имплант", "космет")
        )
        return {"covered": covered, "note": "Synthetic package rules were applied"}

    def _list_clinics(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        rows = [
            row
            for row in self.knowledge.get("clinics", [])
            if str(row.get("city", "")).casefold() == str(v.get("city", "")).casefold()
        ]
        return {"clinics": rows} if rows else _error("not_found", "No clinics found")

    def _resend_documents(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        target = (
            v.get("email")
            or (v.get("client") or {}).get("email")
            or "synthetic contact"
        )
        return {"sent_to": target, "simulated": True}

    def _check_payment(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        row = _first(
            self.backend.get("payments"),
            lambda item: (
                (v.get("client_id") and item.get("client_id") == v.get("client_id"))
                or (
                    v.get("payment_date")
                    and str(item.get("paid_at", item.get("date", ""))).startswith(
                        str(v.get("payment_date"))
                    )
                )
            ),
        )
        return (
            {
                "payment_status": row.get("status"),
                "amount": row.get("amount", row.get("amount_kzt")),
            }
            if row
            else _error("not_found", "Synthetic payment was not found")
        )

    def _update_contact(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {
            "updated_field": v.get("contact_field"),
            "new_value": v.get("new_value"),
            "simulated": True,
        }

    def _request_document(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {
            "sent_to": v.get("email"),
            "document_type": v.get("document_type"),
            "simulated": True,
        }

    def _get_offices(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        row = _first(
            self.knowledge.get("offices"),
            lambda item: (
                str(item.get("city", "")).casefold()
                == str(v.get("city", "")).casefold()
            ),
        )
        return dict(row) if row else _error("not_found", "No office found")

    def _kb_lookup(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        topic = str(
            v.get("topic")
            or self.scenario.details.get("slug")
            or self.scenario.details.get("category")
            or "scenario"
        )
        return {"topic": topic, "knowledge_available": bool(self.knowledge)}

    def _send_sms(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {"sent_to": v.get("phone", "synthetic contact"), "simulated": True}

    def _create_callback(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {
            "callback_time": v.get("callback_time"),
            "phone": v.get("phone"),
            "simulated": True,
        }

    def _create_complaint(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {
            "ticket_id": _stable_id("SIM-T-", self.session_id, "create_complaint", v),
            "simulated": True,
        }

    def _report_fraud(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        return {
            "ticket_id": _stable_id("SIM-F-", self.session_id, "report_fraud", v),
            "simulated": True,
        }

    def _transfer_to_operator(self, v: dict[str, Any], mode: str) -> dict[str, Any]:
        handoff = self.scenario.details.get("handoff") or {}
        return {
            "queue": handoff.get("queue", "operator_general"),
            "context_prepared": True,
            "simulated": True,
        }

    def _snapshot_date(self) -> str:
        meta = self.backend.get("meta", {})
        return str(
            meta.get("as_of_date")
            or self.knowledge.get("meta", {}).get("as_of_date")
            or datetime.now().date()
        )
