"""Прикладной слой: процесс «Выдача книг в библиотеке» (BPMN из раздела II).

Каждой задаче схемы соответствует метод с названием по задаче. Условия
исключающих шлюзов — отдельные функции-предикаты. Состояние выдачи меняется
только через методы событий агрегата Loan. Время берётся только из Clock.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional, Protocol, Sequence

from domain import (
    BookCopy,
    BookCopyRepository,
    DomainInvariantViolation,
    EntityId,
    InspectionResult,
    LibraryService,
    Loan,
    LoanDate,
    LoanRepository,
    LoanTerm,
    Money,
)


# ---------------------------------------------------------------------------
# Исключение шлюза
# ---------------------------------------------------------------------------

class GatewayUnresolved(Exception):
    """Ни одно условие исключающего шлюза не выполнено и потока по умолчанию нет."""


# ---------------------------------------------------------------------------
# Порты внешних участников (заглушки — в infrastructure.py)
# ---------------------------------------------------------------------------

class Clock(Protocol):
    def now(self) -> datetime:
        ...


class ReservationQueue(Protocol):
    def enqueue(self, copy_id: EntityId, reader_id: EntityId) -> None:
        ...

    def has_waiting(self, copy_id: EntityId) -> bool:
        ...

    def peek_next(self, copy_id: EntityId) -> Optional[EntityId]:
        ...

    def pop_next(self, copy_id: EntityId) -> Optional[EntityId]:
        ...


class FineLedger(Protocol):
    def add_fine(self, reader_id: EntityId, loan_id: EntityId, amount: Money) -> None:
        ...

    def has_unpaid_fines(self, reader_id: EntityId) -> bool:
        ...

    def pay_all(self, reader_id: EntityId) -> None:
        ...


class ReaderCardRegistry(Protocol):
    def block(self, reader_id: EntityId) -> None:
        ...

    def is_blocked(self, reader_id: EntityId) -> bool:
        ...


class AdministrationPort(Protocol):
    def transfer_case(self, reader_id: EntityId, loan_id: EntityId) -> None:
        ...


# ---------------------------------------------------------------------------
# Исключающий шлюз
# ---------------------------------------------------------------------------

Predicate = Callable[[Any], bool]


class ExclusiveGateway:
    def __init__(
        self,
        name: str,
        branches: Sequence[tuple],
        default: Optional[str] = None,
    ):
        self._name = name
        self._branches = list(branches)
        self._default = default

    def decide(self, context: Any) -> str:
        matched = [label for label, predicate in self._branches if predicate(context)]
        if len(matched) == 1:
            return matched[0]
        if not matched:
            if self._default is not None:
                return self._default
            raise GatewayUnresolved(f"Шлюз «{self._name}»: ни одно условие не выполнено.")
        raise GatewayUnresolved(
            f"Шлюз «{self._name}»: условия пересекаются ({', '.join(matched)})."
        )


# Предикаты условий шлюзов

def book_is_available(available: bool) -> bool:
    return available


def book_is_unavailable(available: bool) -> bool:
    return not available


def reader_has_debt(debt: bool) -> bool:
    return debt


def reader_has_no_debt(debt: bool) -> bool:
    return not debt


def next_reader_exists(next_reader_id: Optional[EntityId]) -> bool:
    return next_reader_id is not None


def no_next_reader(next_reader_id: Optional[EntityId]) -> bool:
    return next_reader_id is None


def extension_conditions_met(met: bool) -> bool:
    return met


def extension_conditions_not_met(met: bool) -> bool:
    return not met


def book_not_returned_in_time(loan: Loan) -> bool:
    return not loan.is_finished


def book_returned(loan: Loan) -> bool:
    return loan.is_finished


def book_is_damaged(inspection: InspectionResult) -> bool:
    return inspection.damaged


def book_is_not_damaged(inspection: InspectionResult) -> bool:
    return not inspection.damaged


def return_is_late(context: tuple) -> bool:
    loan, return_date = context
    return return_date.value > loan.due_date


def return_is_on_time(context: tuple) -> bool:
    loan, return_date = context
    return return_date.value <= loan.due_date


def fines_were_charged(total: Money) -> bool:
    return total.rubles > 0


def no_fines_were_charged(total: Money) -> bool:
    return total.rubles == 0


# ---------------------------------------------------------------------------
# Прикладной сервис процесса
# ---------------------------------------------------------------------------

class LendingProcess:
    def __init__(
        self,
        loans: LoanRepository,
        copies: BookCopyRepository,
        queue: ReservationQueue,
        ledger: FineLedger,
        cards: ReaderCardRegistry,
        administration: AdministrationPort,
        clock: Clock,
        library: Optional[LibraryService] = None,
    ):
        self._loans = loans
        self._copies = copies
        self._queue = queue
        self._ledger = ledger
        self._cards = cards
        self._administration = administration
        self._clock = clock
        self._library = library or LibraryService()

        self._availability_gateway = ExclusiveGateway(
            "Книга доступна?",
            [("да", book_is_available), ("нет", book_is_unavailable)],
        )
        self._debt_gateway = ExclusiveGateway(
            "Есть задолженность?",
            [("да", reader_has_debt), ("нет", reader_has_no_debt)],
        )
        self._next_reader_gateway = ExclusiveGateway(
            "Есть следующий читатель?",
            [("да", next_reader_exists), ("нет", no_next_reader)],
        )
        self._extension_gateway = ExclusiveGateway(
            "Условия продления выполнены?",
            [("да", extension_conditions_met), ("нет", extension_conditions_not_met)],
        )
        self._not_returned_gateway = ExclusiveGateway(
            "Книга не возвращена в срок?",
            [("да", book_not_returned_in_time), ("нет, книга возвращена", book_returned)],
        )
        self._damage_gateway = ExclusiveGateway(
            "Есть повреждения?",
            [("да", book_is_damaged), ("нет", book_is_not_damaged)],
        )
        self._overdue_gateway = ExclusiveGateway(
            "Есть просрочка?",
            [("да", return_is_late), ("нет", return_is_on_time)],
        )
        self._fines_gateway = ExclusiveGateway(
            "Начислены ли штрафы?",
            [("да", fines_were_charged), ("нет", no_fines_were_charged)],
        )

    # --- чтение -----------------------------------------------------------

    def get_loan(self, loan_id: EntityId) -> Loan:
        return self._loans.get_by_id(loan_id)

    # =======================================================================
    # Бронирование (стартовое сообщение «Получен запрос на бронирование»)
    # =======================================================================

    def receive_reservation_request(self, reader_id: EntityId, copy_id: EntityId) -> EntityId:
        now = self._clock.now()
        if self._cards.is_blocked(reader_id):
            raise DomainInvariantViolation("Читательский билет заблокирован.")
        copy = self._copies.get_by_id(copy_id)
        loan = Loan(self._loans.next_id(), reader_id, copy_id)

        available = self.check_book_availability(copy)
        branch = self._availability_gateway.decide(available)
        if branch == "да":
            self.create_reservation_and_notify_reader(loan, copy, now)
        else:
            self.put_reader_in_queue(loan, copy, now)
        return loan.id

    def check_book_availability(self, copy: BookCopy) -> bool:
        return copy.available_quantity > 0

    def create_reservation_and_notify_reader(self, loan: Loan, copy: BookCopy, now: datetime):
        self._library.reserve_book(loan, copy, now)
        self._loans.save(loan)
        self._copies.save(copy)

    def put_reader_in_queue(self, loan: Loan, copy: BookCopy, now: datetime):
        self._library.enqueue_reader(loan, copy, now)
        self._queue.enqueue(loan.copy_id, loan.reader_id)
        self._loans.save(loan)

    # =======================================================================
    # Читатель пришёл за книгой
    # =======================================================================

    def reader_comes_for_book(self, loan_id: EntityId) -> str:
        now = self._clock.now()
        loan = self._loans.get_by_id(loan_id)
        copy = self._copies.get_by_id(loan.copy_id)

        debt = self.check_reader_debt(loan.reader_id, now)
        branch = self._debt_gateway.decide(debt)
        if branch == "да":
            self.refuse_issue_until_debt_paid(loan, copy, now, debt)
        else:
            self.issue_book_for_14_days(loan, copy, now, debt)
        return loan.state_name

    def check_reader_debt(self, reader_id: EntityId, now: datetime) -> bool:
        """«Проверить просроченные книги и неоплаченные штрафы»."""
        has_overdue_books = any(
            loan.is_overdue(now) for loan in self._loans.find_by_reader(reader_id)
        )
        return has_overdue_books or self._ledger.has_unpaid_fines(reader_id)

    def refuse_issue_until_debt_paid(
        self, loan: Loan, copy: BookCopy, now: datetime, debt: bool
    ):
        self._library.deny_issue(loan, copy, now, debt)
        self._loans.save(loan)
        self._copies.save(copy)

    def issue_book_for_14_days(self, loan: Loan, copy: BookCopy, now: datetime, debt: bool):
        self._library.issue_book(loan, copy, now, LoanTerm(Loan.LOAN_TERM_DAYS), debt)
        self._loans.save(loan)
        self._copies.save(copy)

    def pay_reader_debt(self, reader_id: EntityId):
        """«Погасить задолженность» (задача читателя): оплата штрафов.
        Просроченные книги закрываются только возвратом."""
        self._ledger.pay_all(reader_id)

    # =======================================================================
    # Таймеры: on_<событие>(now) обрабатывают все сущности с истёкшим сроком
    # =======================================================================

    def on_reservation_timeout(self, now: datetime) -> list:
        """Таймер «3 рабочих дня» на ожидании прихода читателя по брони."""
        processed = []
        for loan in self._loans.list_all():
            if not loan.is_reservation_expired(now):
                continue
            copy = self._copies.get_by_id(loan.copy_id)
            next_reader_id = self.check_queue_for_book(loan.copy_id)
            branch = self._next_reader_gateway.decide(next_reader_id)
            if branch == "да":
                self.assign_reservation_to_next_reader(loan, copy, now, next_reader_id)
            else:
                self.return_book_to_library_fund(loan, copy, now)
            processed.append(loan.id)
        return processed

    def check_queue_for_book(self, copy_id: EntityId) -> Optional[EntityId]:
        return self._queue.peek_next(copy_id)

    def assign_reservation_to_next_reader(
        self, loan: Loan, copy: BookCopy, now: datetime, next_reader_id: EntityId
    ):
        self._library.expire_reservation(loan, copy, now, next_reader_id)
        self._queue.pop_next(loan.copy_id)
        self._loans.save(loan)
        self._copies.save(copy)

    def return_book_to_library_fund(self, loan: Loan, copy: BookCopy, now: datetime):
        self._library.expire_reservation(loan, copy, now, None)
        self._loans.save(loan)
        self._copies.save(copy)

    def on_return_reminder(self, now: datetime) -> list:
        """Непрерывающий таймер «за 2 дня до срока»."""
        processed = []
        for loan in self._loans.list_all():
            if loan.is_reminder_due(now):
                self.send_reminder_to_reader(loan, now)
                processed.append(loan.id)
        return processed

    def send_reminder_to_reader(self, loan: Loan, now: datetime):
        loan.send_reminder(now)
        self._loans.save(loan)

    def on_due_date_passed(self, now: datetime) -> list:
        """Событие «Наступил срок» при ожидании возврата."""
        processed = []
        for loan in self._loans.list_all():
            if not loan.is_due_date_passed(now):
                continue
            branch = self._not_returned_gateway.decide(loan)
            if branch == "да":
                self.mark_loan_overdue(loan, now)
                processed.append(loan.id)
        return processed

    def mark_loan_overdue(self, loan: Loan, now: datetime):
        loan.due_date_passed(now)
        self._loans.save(loan)

    def on_block_deadline(self, now: datetime) -> list:
        """Таймер «30 дней после срока»."""
        processed = []
        for loan in self._loans.list_all():
            if not loan.is_block_due(now):
                continue
            self.block_reader_card(loan)
            self.transfer_case_to_administration(loan)
            loan.block_card(now)  # конечное состояние «Билет заблокирован»
            self._loans.save(loan)
            processed.append(loan.id)
        return processed

    def block_reader_card(self, loan: Loan):
        self._cards.block(loan.reader_id)

    def transfer_case_to_administration(self, loan: Loan):
        self._administration.transfer_case(loan.reader_id, loan.id)

    # =======================================================================
    # Запрос продления
    # =======================================================================

    def request_extension(self, loan_id: EntityId) -> str:
        now = self._clock.now()
        loan = self._loans.get_by_id(loan_id)

        has_queue = self._queue.has_waiting(loan.copy_id)
        met = self.check_extension_conditions(loan, now, has_queue)
        branch = self._extension_gateway.decide(met)
        if branch == "да":
            self.extend_loan_by_7_days(loan, now, has_queue)
        else:
            self.refuse_extension_and_notify_reader(loan, now, has_queue)
        return loan.state_name

    def check_extension_conditions(self, loan: Loan, now: datetime, has_queue: bool) -> bool:
        """«Проверить: продление ещё не использовано и очереди нет»."""
        return loan.can_be_extended(now, has_queue)

    def extend_loan_by_7_days(self, loan: Loan, now: datetime, has_queue: bool):
        loan.extend(now, has_queue)
        self._loans.save(loan)

    def refuse_extension_and_notify_reader(self, loan: Loan, now: datetime, has_queue: bool):
        if loan.extension_used:
            reason = "Продление уже использовано."
        elif has_queue:
            reason = "На книгу есть очередь."
        else:
            reason = "Условия продления не выполнены."
        loan.refuse_extension(now, reason)
        self._loans.save(loan)

    # =======================================================================
    # Возврат книги
    # =======================================================================

    def return_book(self, loan_id: EntityId, inspection: InspectionResult) -> str:
        """Возврат: результат осмотра передаёт библиотекарь."""
        now = self._clock.now()
        loan = self._loans.get_by_id(loan_id)
        copy = self._copies.get_by_id(loan.copy_id)

        inspection = self.inspect_returned_book(loan, inspection)
        return_date = LoanDate(now.date())

        if self._damage_gateway.decide(inspection) == "да":
            damage_fine = self.draw_damage_act_and_charge_fine(loan, inspection)
        else:
            damage_fine = self.go_to_deadline_check()

        if self._overdue_gateway.decide((loan, return_date)) == "да":
            overdue_fine = self.charge_overdue_fine(loan, return_date)
        else:
            overdue_fine = self.skip_overdue_fine()

        self.save_return_result_and_fines(loan, copy, now, damage_fine, overdue_fine)
        return loan.state_name

    def inspect_returned_book(self, loan: Loan, inspection: InspectionResult) -> InspectionResult:
        """«Осмотреть возвращённую книгу» — задача человека, метод принимает её результат."""
        if not isinstance(inspection, InspectionResult):
            raise DomainInvariantViolation("Нужен результат осмотра книги.")
        return inspection

    def draw_damage_act_and_charge_fine(self, loan: Loan, inspection: InspectionResult) -> Money:
        """«Составить акт и начислить штраф за повреждение»."""
        return inspection.damage_fine

    def go_to_deadline_check(self) -> Money:
        """«Перейти к проверке срока»."""
        return Money(0)

    def charge_overdue_fine(self, loan: Loan, return_date: LoanDate) -> Money:
        """«Начислить штраф за каждый день просрочки»."""
        return loan.calculate_overdue_fine(return_date)

    def skip_overdue_fine(self) -> Money:
        """«Пропустить штраф за просрочку»."""
        return Money(0)

    def save_return_result_and_fines(
        self, loan: Loan, copy: BookCopy, now: datetime,
        damage_fine: Money, overdue_fine: Money,
    ):
        """«Сохранить результат возврата и штрафы» -> конечное состояние."""
        self._library.return_book(loan, copy, now, damage_fine, overdue_fine)
        self._loans.save(loan)
        self._copies.save(copy)
        if self._fines_gateway.decide(loan.fine) == "да":
            self._ledger.add_fine(loan.reader_id, loan.id, loan.fine)
