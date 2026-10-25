"""Веб-адаптер на Flask для процесса «Выдача книг в библиотеке».

Домен и прикладной слой Flask не импортируют. Все зависимости создаются
один раз при старте приложения в фабрике create_app().
"""
from __future__ import annotations

import dataclasses
import logging
import os
from datetime import date, datetime

from flask import Flask, jsonify, request

from application import ClockMovedBackwards, GatewayUnresolved, LendingProcess
from domain import (
    BookCopy,
    CopyQuantity,
    DomainInvariantViolation,
    EntityId,
    EntityNotFound,
    InMemoryBookCopyRepository,
    InMemoryLoanRepository,
    InspectionResult,
    InvalidValueObject,
    LoanDate,
    Money,
    TransitionNotAllowed,
)
from infrastructure import (
    AppClock,
    InMemoryAdministration,
    InMemoryFineLedger,
    InMemoryReaderCardRegistry,
    InMemoryReservationQueue,
)

log = logging.getLogger("library.web")


# ---------------------------------------------------------------------------
# Логирование и обработчики исключений
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _register_error_handlers(app: Flask) -> None:
    """Единый формат ошибок: {"error": "<код>", "message": "<описание>"}."""

    # 404 — сущность не найдена. Регистрируем раньше DomainInvariantViolation
    # (хотя Flask выбирает по MRO и порядок не критичен).
    @app.errorhandler(EntityNotFound)
    def handle_not_found(e):
        return jsonify(error="EntityNotFound", message=str(e)), 404

    # 422 — ошибка валидации объекта-значения.
    @app.errorhandler(InvalidValueObject)
    def handle_validation(e):
        return jsonify(error="InvalidValueObject", message=str(e)), 422

    # 422 — прочие нарушения инвариантов домена.
    @app.errorhandler(DomainInvariantViolation)
    def handle_invariant(e):
        return jsonify(error="DomainInvariantViolation", message=str(e)), 422

    # 409 — недопустимый переход состояния.
    @app.errorhandler(TransitionNotAllowed)
    def handle_transition(e):
        return jsonify(error="TransitionNotAllowed", message=str(e)), 409

    # 422 — попытка перевести часы приложения назад.
    @app.errorhandler(ClockMovedBackwards)
    def handle_clock_backwards(e):
        return jsonify(error="ClockMovedBackwards", message=str(e)), 422

    # 500 — неразрешённый исключающий шлюз (ошибка конфигурации процесса).
    @app.errorhandler(GatewayUnresolved)
    def handle_gateway(e):
        return jsonify(error="GatewayUnresolved", message=str(e)), 500


# ---------------------------------------------------------------------------
# Фабрика приложения
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    _configure_logging()

    app = Flask(__name__)

    # Часы, репозитории и порты создаются один раз при старте.
    clock = AppClock()
    loans = InMemoryLoanRepository()
    copies = InMemoryBookCopyRepository()
    queue = InMemoryReservationQueue()
    ledger = InMemoryFineLedger()
    cards = InMemoryReaderCardRegistry()
    admin = InMemoryAdministration()
    process = LendingProcess(loans, copies, queue, ledger, cards, admin, clock)

    if os.environ.get("SEED_DATA", "true").lower() in ("1", "true", "yes"):
        copies.save(BookCopy(EntityId(10), EntityId(100), CopyQuantity(3)))
        copies.save(BookCopy(EntityId(20), EntityId(200), CopyQuantity(1)))

    _register_error_handlers(app)

    # --- служебные маршруты ------------------------------------------------

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/internal/tick")
    def tick():
        data = request.get_json(silent=True) or {}
        target_str = data.get("now")
        if target_str is not None:
            if not isinstance(target_str, str):
                raise InvalidValueObject(
                    "Поле 'now' должно быть строкой ISO 8601."
                )
            try:
                target = datetime.fromisoformat(target_str)
            except ValueError:
                raise InvalidValueObject(
                    f"Некорректный формат времени: {target_str}"
                )
            clock.set_to(target)  # может выбросить ClockMovedBackwards

        now = clock.now()
        processed = process.tick(now)
        return jsonify(
            now=now.isoformat(),
            processed_loan_ids=[lid.value for lid in processed],
        )

    # --- бизнес-операции ---------------------------------------------------

    @app.post("/loans")
    def create_loan():
        data = request.get_json(silent=True) or {}
        reader_id = _require_int(data, "reader_id")
        copy_id = _require_int(data, "copy_id")
        loan_id = process.receive_reservation_request(
            EntityId(reader_id), EntityId(copy_id)
        )
        log.info(
            "Создана бронь loan_id=%s reader_id=%s copy_id=%s",
            loan_id.value, reader_id, copy_id,
        )
        return _loan_payload(process, loan_id), 201

    @app.post("/loans/<int:loan_id>/come")
    def come_for_book(loan_id: int):
        process.reader_comes_for_book(EntityId(loan_id))
        return _loan_payload(process, EntityId(loan_id))

    @app.post("/loans/<int:loan_id>/extend")
    def extend_loan(loan_id: int):
        process.request_extension(EntityId(loan_id))
        return _loan_payload(process, EntityId(loan_id))

    @app.post("/loans/<int:loan_id>/return")
    def return_book(loan_id: int):
        data = request.get_json(silent=True) or {}
        inspection = _parse_inspection(data)
        process.return_book(EntityId(loan_id), inspection)
        return _loan_payload(process, EntityId(loan_id))

    @app.post("/readers/<int:reader_id>/pay-debt")
    def pay_debt(reader_id: int):
        process.pay_reader_debt(EntityId(reader_id))
        return jsonify(reader_id=reader_id, ok=True)

    # --- чтение ------------------------------------------------------------

    @app.get("/loans/<int:loan_id>")
    def get_loan(loan_id: int):
        return _loan_payload(process, EntityId(loan_id))

    return app


# ---------------------------------------------------------------------------
# Вспомогательные функции веб-адаптера
# ---------------------------------------------------------------------------

def _require_int(data: dict, key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidValueObject(
            f"Поле '{key}' обязательно и должно быть целым числом."
        )
    return value


def _parse_inspection(data: dict) -> InspectionResult:
    damaged = data.get("damaged")
    if not isinstance(damaged, bool):
        raise InvalidValueObject(
            "Поле 'damaged' обязательно и должно быть логическим."
        )
    if damaged:
        fine = data.get("damage_fine")
        if isinstance(fine, bool) or not isinstance(fine, int):
            raise InvalidValueObject(
                "При повреждении поле 'damage_fine' обязательно (int)."
            )
        return InspectionResult(damaged=True, damage_fine=Money(fine))
    if data.get("damage_fine", 0) != 0:
        raise InvalidValueObject(
            "При отсутствии повреждения штраф не начисляется."
        )
    return InspectionResult(damaged=False)


def _loan_payload(process: LendingProcess, loan_id: EntityId) -> dict:
    loan = process.get_loan(loan_id)
    return {
        "loan_id": loan.id.value,
        "reader_id": loan.reader_id.value,
        "copy_id": loan.copy_id.value,
        "state": loan.state_name,
        "is_finished": loan.is_finished,
        "allowed_events": list(loan.allowed_events),
        "events": [_event_to_dict(e) for e in loan.events],
    }


def _event_to_dict(event) -> dict:
    result = {"type": type(event).__name__}
    for f in dataclasses.fields(event):
        result[f.name] = _json_value(getattr(event, f.name))
    return result


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, LoanDate):
        return value.value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, EntityId):
        return value.value
    if isinstance(value, Money):
        return value.rubles
    return value