"""Доменный слой библиотеки (лабораторная работа №3).

Основа — код лабораторной №2. Жизненный цикл агрегата Loan («Выдача книги»,
от запроса на бронирование до закрытия) реализован паттерном «Состояние».
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional, Protocol


# ---------------------------------------------------------------------------
# Доменные исключения
# ---------------------------------------------------------------------------

class DomainInvariantViolation(Exception):
    """Нарушение правила предметной области."""


class InvalidValueObject(DomainInvariantViolation):
    """Некорректное значение объекта."""


class TransitionNotAllowed(Exception):
    """Событие недопустимо в текущем состоянии сущности."""

    def __init__(self, state: str, event: str):
        self.state = state
        self.event = event
        super().__init__(f"Событие «{event}» недопустимо в состоянии «{state}».")


# ---------------------------------------------------------------------------
# Объекты-значения
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EntityId:
    value: int

    def __post_init__(self):
        if self.value <= 0:
            raise InvalidValueObject("Идентификатор должен быть больше нуля.")


@dataclass(frozen=True)
class PersonName:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("Имя читателя не может быть пустым.")


@dataclass(frozen=True)
class BookTitle:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("Название книги не может быть пустым.")


@dataclass(frozen=True)
class CopyQuantity:
    value: int

    def __post_init__(self):
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value <= 0:
            raise InvalidValueObject("Количество экземпляров должно быть целым числом больше нуля.")


@dataclass(frozen=True)
class LoanTerm:
    days: int

    def __post_init__(self):
        if isinstance(self.days, bool) or not isinstance(self.days, int) or self.days <= 0:
            raise InvalidValueObject("Срок выдачи должен быть положительным числом дней.")


@dataclass(frozen=True)
class Money:
    rubles: int

    def __post_init__(self):
        if isinstance(self.rubles, bool) or not isinstance(self.rubles, int) or self.rubles < 0:
            raise InvalidValueObject("Сумма должна быть целым неотрицательным числом рублей.")

    def __add__(self, other: "Money") -> "Money":  # изменение лабораторной №3
        return Money(self.rubles + other.rubles)


@dataclass(frozen=True)
class LoanDate:
    value: date

    def __post_init__(self):
        if not isinstance(self.value, date):
            raise InvalidValueObject("Дата должна быть объектом date.")


@dataclass(frozen=True)
class InspectionResult:
    """Результат осмотра возвращённой книги (задача, которую выполняет человек)."""
    damaged: bool
    damage_fine: Money = Money(0)

    def __post_init__(self):
        if not isinstance(self.damaged, bool):
            raise InvalidValueObject("Признак повреждения должен быть логическим.")
        if not isinstance(self.damage_fine, Money):
            raise InvalidValueObject("Штраф за повреждение должен быть объектом Money.")
        if self.damaged and self.damage_fine.rubles == 0:
            raise InvalidValueObject("При повреждении книги штраф должен быть больше нуля.")
        if not self.damaged and self.damage_fine.rubles != 0:
            raise InvalidValueObject("Штраф за повреждение возможен только при повреждении.")


def add_working_days(start: datetime, days: int) -> datetime:
    """Прибавляет рабочие дни (сб и вс пропускаются, календаря праздников нет)."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


# ---------------------------------------------------------------------------
# Сущности предметной области
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Book:
    id: EntityId
    author: str
    title: BookTitle
    category: str

    def __post_init__(self):
        if not self.author.strip():
            raise InvalidValueObject("Автор книги не может быть пустым.")
        if not self.category.strip():
            raise InvalidValueObject("Категория книги не может быть пустой.")


@dataclass(frozen=True)
class Reader:
    id: EntityId
    full_name: PersonName


# ---------------------------------------------------------------------------
# Агрегат экземпляров книги
# (изменение: добавлены брони; инвариант active_loans + reserved <= quantity)
# ---------------------------------------------------------------------------

class BookCopy:
    def __init__(self, copy_id: EntityId, book_id: EntityId, quantity: CopyQuantity):
        self._id = copy_id
        self._book_id = book_id
        self._quantity = quantity.value
        self._active_loans = 0
        self._reserved = 0

    @property
    def id(self):
        return self._id

    @property
    def book_id(self):
        return self._book_id

    @property
    def quantity(self):
        return self._quantity

    @property
    def active_loans(self):
        return self._active_loans

    @property
    def reserved_quantity(self):
        return self._reserved

    @property
    def available_quantity(self):
        return self._quantity - self._active_loans - self._reserved

    def reserve(self):
        if self.available_quantity <= 0:
            raise DomainInvariantViolation("Нет доступных экземпляров этой книги.")
        self._reserved += 1

    def release_reservation(self):
        if self._reserved <= 0:
            raise DomainInvariantViolation("Нет забронированных экземпляров.")
        self._reserved -= 1

    def issue_reserved_copy(self):
        if self._reserved <= 0:
            raise DomainInvariantViolation("Нет забронированных экземпляров для выдачи.")
        self._reserved -= 1
        self._active_loans += 1

    def issue_copy(self):
        if self.available_quantity <= 0:
            raise DomainInvariantViolation("Нет доступных экземпляров этой книги.")
        self._active_loans += 1

    def return_copy(self):
        if self._active_loans <= 0:
            raise DomainInvariantViolation("Нет выданных экземпляров для возврата.")
        self._active_loans -= 1


# ---------------------------------------------------------------------------
# Доменные события (сообщения между участниками), журнал хранится в Loan
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DomainEvent:
    loan_id: EntityId
    occurred_at: datetime


@dataclass(frozen=True)
class ReservationConfirmed(DomainEvent):
    """Подтверждение брони читателю."""
    reader_id: EntityId
    reserved_until: datetime


@dataclass(frozen=True)
class ReaderQueued(DomainEvent):
    """Читатель уведомлён о постановке в очередь."""
    reader_id: EntityId


@dataclass(frozen=True)
class ReservationReassigned(DomainEvent):
    """Бронь после истечения срока назначена следующему читателю."""
    new_reader_id: EntityId
    reserved_until: datetime


@dataclass(frozen=True)
class ReservationExpired(DomainEvent):
    """Бронь снята, книга возвращена в фонд."""


@dataclass(frozen=True)
class IssueRefused(DomainEvent):
    reader_id: EntityId
    reason: str


@dataclass(frozen=True)
class BookIssued(DomainEvent):
    reader_id: EntityId
    due_date: date


@dataclass(frozen=True)
class ReminderSent(DomainEvent):
    reader_id: EntityId
    due_date: date


@dataclass(frozen=True)
class ExtensionGranted(DomainEvent):
    reader_id: EntityId
    new_due_date: date


@dataclass(frozen=True)
class ExtensionRefused(DomainEvent):
    reader_id: EntityId
    reason: str


@dataclass(frozen=True)
class BookBecameOverdue(DomainEvent):
    due_date: date


@dataclass(frozen=True)
class DamageActDrawn(DomainEvent):
    amount: Money


@dataclass(frozen=True)
class OverdueFineCharged(DomainEvent):
    amount: Money


@dataclass(frozen=True)
class BookReturned(DomainEvent):
    reader_id: EntityId
    total_fine: Money


@dataclass(frozen=True)
class ReaderCardBlocked(DomainEvent):
    reader_id: EntityId


@dataclass(frozen=True)
class CaseTransferredToAdministration(DomainEvent):
    reader_id: EntityId


# ---------------------------------------------------------------------------
# Паттерн «Состояние» для жизненного цикла выдачи книги
# ---------------------------------------------------------------------------

class LoanEvent(str, Enum):
    """События таблицы переходов. Значение совпадает с именем метода состояния."""
    CONFIRM_RESERVATION = "confirm_reservation"
    ENQUEUE = "enqueue"
    RESERVATION_TIMEOUT = "reservation_timeout"      # таймер: 3 рабочих дня
    ISSUE = "issue"
    DENY_ISSUE = "deny_issue"
    SEND_REMINDER = "send_reminder"                  # таймер: за 2 дня до срока
    EXTEND = "extend"
    REFUSE_EXTENSION = "refuse_extension"
    DUE_DATE_PASSED = "due_date_passed"              # таймер: наступил срок
    BLOCK_CARD = "block_card"                        # таймер: 30 дней после срока
    ACCEPT_RETURN = "accept_return"


class LoanState:
    """Базовое состояние: все события по умолчанию недопустимы."""

    name = ""
    is_final = False
    holds_book = False  # книга сейчас на руках у читателя

    def allowed_events(self) -> tuple:
        """Допустимые события = методы, переопределённые в классе состояния."""
        return tuple(
            e.value for e in LoanEvent
            if getattr(type(self), e.value) is not getattr(LoanState, e.value)
        )

    def _reject(self, event: LoanEvent):
        raise TransitionNotAllowed(self.name, event.value)

    def confirm_reservation(self, loan: "Loan", now: datetime) -> "LoanState":
        self._reject(LoanEvent.CONFIRM_RESERVATION)

    def enqueue(self, loan: "Loan", now: datetime, copy_available: bool) -> "LoanState":
        self._reject(LoanEvent.ENQUEUE)

    def reservation_timeout(
        self, loan: "Loan", now: datetime, next_reader_id: Optional[EntityId]
    ) -> "LoanState":
        self._reject(LoanEvent.RESERVATION_TIMEOUT)

    def issue(
        self, loan: "Loan", now: datetime, term: LoanTerm, reader_has_debt: bool
    ) -> "LoanState":
        self._reject(LoanEvent.ISSUE)

    def deny_issue(self, loan: "Loan", now: datetime, reader_has_debt: bool) -> "LoanState":
        self._reject(LoanEvent.DENY_ISSUE)

    def send_reminder(self, loan: "Loan", now: datetime) -> "LoanState":
        self._reject(LoanEvent.SEND_REMINDER)

    def extend(self, loan: "Loan", now: datetime, has_queue: bool) -> "LoanState":
        self._reject(LoanEvent.EXTEND)

    def refuse_extension(self, loan: "Loan", now: datetime, reason: str) -> "LoanState":
        self._reject(LoanEvent.REFUSE_EXTENSION)

    def due_date_passed(self, loan: "Loan", now: datetime) -> "LoanState":
        self._reject(LoanEvent.DUE_DATE_PASSED)

    def block_card(self, loan: "Loan", now: datetime) -> "LoanState":
        self._reject(LoanEvent.BLOCK_CARD)

    def accept_return(
        self, loan: "Loan", now: datetime, damage_fine: Money, overdue_fine: Money
    ) -> "LoanState":
        self._reject(LoanEvent.ACCEPT_RETURN)


class RequestedState(LoanState):
    """Запрошена (начальное): запрос на бронирование получен."""
    name = "Запрошена"

    def confirm_reservation(self, loan, now):
        loan._reserved_until = add_working_days(now, Loan.RESERVATION_WORKING_DAYS)
        loan._record(ReservationConfirmed(loan.id, now, loan.reader_id, loan._reserved_until))
        return ReservedState()

    def enqueue(self, loan, now, copy_available):
        if copy_available:
            raise DomainInvariantViolation("Нельзя поставить в очередь: книга доступна.")
        loan._record(ReaderQueued(loan.id, now, loan.reader_id))
        return QueuedState()


class ReservedState(LoanState):
    """Забронирована: ожидание прихода читателя (3 рабочих дня)."""
    name = "Забронирована"

    def reservation_timeout(self, loan, now, next_reader_id):
        if now < loan._reserved_until:
            raise DomainInvariantViolation("Срок брони ещё не истёк.")
        if next_reader_id is not None:
            # повторная бронь: возврат в то же состояние для следующего читателя
            loan._reader_id = next_reader_id
            loan._reserved_until = add_working_days(now, Loan.RESERVATION_WORKING_DAYS)
            loan._record(ReservationReassigned(loan.id, now, next_reader_id, loan._reserved_until))
            return ReservedState()
        loan._record(ReservationExpired(loan.id, now))
        return ReservationCancelledState()

    def issue(self, loan, now, term, reader_has_debt):
        if reader_has_debt:
            raise DomainInvariantViolation("Нельзя выдать книгу читателю с задолженностью.")
        if now > loan._reserved_until:
            raise DomainInvariantViolation("Срок брони истёк.")
        loan._issue_date = now.date()
        loan._due_date = loan._issue_date + timedelta(days=term.days)
        loan._record(BookIssued(loan.id, now, loan.reader_id, loan._due_date))
        return IssuedState()

    def deny_issue(self, loan, now, reader_has_debt):
        if not reader_has_debt:
            raise DomainInvariantViolation("Отказ в выдаче возможен только при задолженности.")
        loan._record(IssueRefused(
            loan.id, now, loan.reader_id, "Есть просроченные книги или неоплаченные штрафы."
        ))
        return IssueDeniedState()


class _ReturnableState(LoanState):
    """Общее поведение состояний, в которых книга на руках: возврат."""
    holds_book = True

    def accept_return(self, loan, now, damage_fine, overdue_fine):
        if not isinstance(damage_fine, Money) or not isinstance(overdue_fine, Money):
            raise InvalidValueObject("Штрафы должны быть объектами Money.")
        return_date = LoanDate(now.date())
        if return_date.value < loan._issue_date:
            raise DomainInvariantViolation("Дата возврата не может быть раньше даты выдачи.")
        if overdue_fine != loan.calculate_overdue_fine(return_date):
            raise DomainInvariantViolation("Штраф за просрочку не соответствует правилам.")

        total = damage_fine + overdue_fine
        loan._return_date = return_date.value
        loan._fine = total
        if damage_fine.rubles > 0:
            loan._record(DamageActDrawn(loan.id, now, damage_fine))
        if overdue_fine.rubles > 0:
            loan._record(OverdueFineCharged(loan.id, now, overdue_fine))
        loan._record(BookReturned(loan.id, now, loan.reader_id, total))
        if total.rubles > 0:
            return ReturnedWithFineState()
        return ReturnedCleanState()


class _ActiveLoanState(_ReturnableState):
    """Выдана / Продлена: ожидание возврата или запроса продления."""

    def send_reminder(self, loan, now):
        today = now.date()
        if loan._reminder_sent:
            raise DomainInvariantViolation("Напоминание по этой выдаче уже отправлено.")
        if today < loan._due_date - timedelta(days=Loan.REMINDER_DAYS_BEFORE_DUE):
            raise DomainInvariantViolation("До напоминания ещё рано.")
        if today > loan._due_date:
            raise DomainInvariantViolation("Срок возврата уже истёк.")
        loan._reminder_sent = True
        loan._record(ReminderSent(loan.id, now, loan.reader_id, loan._due_date))
        return self

    def refuse_extension(self, loan, now, reason):
        if not reason.strip():
            raise DomainInvariantViolation("Укажите причину отказа в продлении.")
        loan._record(ExtensionRefused(loan.id, now, loan.reader_id, reason))
        return self

    def due_date_passed(self, loan, now):
        if now.date() <= loan._due_date:
            raise DomainInvariantViolation("Срок возврата ещё не наступил.")
        loan._record(BookBecameOverdue(loan.id, now, loan._due_date))
        return OverdueState()


class IssuedState(_ActiveLoanState):
    """Выдана (срок 14 дней, продление ещё не использовано)."""
    name = "Выдана"

    def extend(self, loan, now, has_queue):
        if has_queue:
            raise DomainInvariantViolation("Продление невозможно: на книгу есть очередь.")
        if loan._extension_used:
            raise DomainInvariantViolation("Продление уже использовано.")
        if now.date() > loan._due_date:
            raise DomainInvariantViolation("Срок возврата уже истёк.")
        loan._due_date = loan._due_date + timedelta(days=Loan.EXTENSION_DAYS)
        loan._extension_used = True
        loan._reminder_sent = False  # таймер напоминания взводится заново
        loan._record(ExtensionGranted(loan.id, now, loan.reader_id, loan._due_date))
        return ExtendedState()


class ExtendedState(_ActiveLoanState):
    """Продлена (продлевать повторно нельзя)."""
    name = "Продлена"


class OverdueState(_ReturnableState):
    """Просрочена: срок вышел, книга не возвращена."""
    name = "Просрочена"

    def block_card(self, loan, now):
        blocking_day = loan._due_date + timedelta(days=Loan.BLOCK_AFTER_DUE_DAYS)
        if now.date() < blocking_day:
            raise DomainInvariantViolation("С окончания срока ещё не прошло 30 дней.")
        loan._record(ReaderCardBlocked(loan.id, now, loan.reader_id))
        loan._record(CaseTransferredToAdministration(loan.id, now, loan.reader_id))
        return CardBlockedState()


class _FinalState(LoanState):
    is_final = True


class QueuedState(_FinalState):
    name = "В очереди"


class ReservationCancelledState(_FinalState):
    name = "Бронь снята"


class IssueDeniedState(_FinalState):
    name = "Выдача отклонена"


class ReturnedCleanState(_FinalState):
    name = "Возвращена без штрафов"


class ReturnedWithFineState(_FinalState):
    name = "Возвращена со штрафом"


class CardBlockedState(_FinalState):
    name = "Билет заблокирован"


ALL_STATES = (
    RequestedState, ReservedState, IssuedState, ExtendedState, OverdueState,
    QueuedState, ReservationCancelledState, IssueDeniedState,
    ReturnedCleanState, ReturnedWithFineState, CardBlockedState,
)
STATES_BY_NAME = {cls.name: cls for cls in ALL_STATES}


# ---------------------------------------------------------------------------
# Агрегат выдачи книги
# (изменение: жизненный цикл от запроса на бронь; вместо ISSUED/CLOSED и
# флага issue_registered — объект состояния; штраф за каждый день просрочки)
# ---------------------------------------------------------------------------

class Loan:
    RESERVATION_WORKING_DAYS = 3
    LOAN_TERM_DAYS = 14
    EXTENSION_DAYS = 7
    REMINDER_DAYS_BEFORE_DUE = 2
    BLOCK_AFTER_DUE_DAYS = 30
    OVERDUE_FINE_PER_DAY = Money(50)

    def __init__(self, loan_id: EntityId, reader_id: EntityId, copy_id: EntityId):
        self._id = loan_id
        self._reader_id = reader_id
        self._copy_id = copy_id
        self._state: LoanState = RequestedState()
        self._reserved_until: Optional[datetime] = None
        self._issue_date: Optional[date] = None
        self._due_date: Optional[date] = None
        self._return_date: Optional[date] = None
        self._fine = Money(0)
        self._extension_used = False
        self._reminder_sent = False
        self._events: list = []

    # --- чтение ---------------------------------------------------------

    @property
    def id(self):
        return self._id

    @property
    def reader_id(self):
        return self._reader_id

    @property
    def copy_id(self):
        return self._copy_id

    @property
    def state_name(self) -> str:
        return self._state.name

    @property
    def status(self) -> str:  # совместимость с лабораторной №2
        return self._state.name

    @property
    def is_finished(self) -> bool:
        return self._state.is_final

    @property
    def allowed_events(self) -> tuple:
        return self._state.allowed_events()

    @property
    def reserved_until(self):
        return self._reserved_until

    @property
    def issue_date(self):
        return self._issue_date

    @property
    def due_date(self):
        return self._due_date

    @property
    def return_date(self):
        return self._return_date

    @property
    def fine(self):
        return self._fine

    @property
    def extension_used(self):
        return self._extension_used

    @property
    def reminder_sent(self):
        return self._reminder_sent

    @property
    def events(self) -> tuple:
        return tuple(self._events)

    def _record(self, event: DomainEvent):
        self._events.append(event)

    # --- предикаты (условия шлюзов и таймеров) ---------------------------

    def _can(self, event: LoanEvent) -> bool:
        return event.value in self._state.allowed_events()

    def require_allowed(self, event: LoanEvent):
        if not self._can(event):
            raise TransitionNotAllowed(self._state.name, event.value)

    def is_reservation_expired(self, now: datetime) -> bool:
        return (
            self._can(LoanEvent.RESERVATION_TIMEOUT)
            and self._reserved_until is not None
            and now >= self._reserved_until
        )

    def is_reminder_due(self, now: datetime) -> bool:
        if not self._can(LoanEvent.SEND_REMINDER) or self._reminder_sent:
            return False
        today = now.date()
        return self._due_date - timedelta(days=self.REMINDER_DAYS_BEFORE_DUE) <= today <= self._due_date

    def is_due_date_passed(self, now: datetime) -> bool:
        return self._can(LoanEvent.DUE_DATE_PASSED) and now.date() > self._due_date

    def is_block_due(self, now: datetime) -> bool:
        return (
            self._can(LoanEvent.BLOCK_CARD)
            and now.date() >= self._due_date + timedelta(days=self.BLOCK_AFTER_DUE_DAYS)
        )

    def is_overdue(self, now: datetime) -> bool:
        return self._state.holds_book and now.date() > self._due_date

    def can_be_extended(self, now: datetime, has_queue: bool) -> bool:
        return (
            self._can(LoanEvent.EXTEND)
            and not self._extension_used
            and not has_queue
            and now.date() <= self._due_date
        )

    def calculate_overdue_fine(self, return_date: LoanDate) -> Money:
        if self._due_date is None:
            raise DomainInvariantViolation("У выдачи нет срока возврата.")
        late_days = max(0, (return_date.value - self._due_date).days)
        return Money(late_days * self.OVERDUE_FINE_PER_DAY.rubles)

    # --- события (единственный способ изменить состояние) -----------------

    def confirm_reservation(self, now: datetime):
        self._state = self._state.confirm_reservation(self, now)

    def enqueue(self, now: datetime, copy_available: bool):
        self._state = self._state.enqueue(self, now, copy_available)

    def reservation_timeout(self, now: datetime, next_reader_id: Optional[EntityId]):
        self._state = self._state.reservation_timeout(self, now, next_reader_id)

    def issue(self, now: datetime, term: LoanTerm, reader_has_debt: bool):
        self._state = self._state.issue(self, now, term, reader_has_debt)

    def deny_issue(self, now: datetime, reader_has_debt: bool):
        self._state = self._state.deny_issue(self, now, reader_has_debt)

    def send_reminder(self, now: datetime):
        self._state = self._state.send_reminder(self, now)

    def extend(self, now: datetime, has_queue: bool):
        self._state = self._state.extend(self, now, has_queue)

    def refuse_extension(self, now: datetime, reason: str):
        self._state = self._state.refuse_extension(self, now, reason)

    def due_date_passed(self, now: datetime):
        self._state = self._state.due_date_passed(self, now)

    def block_card(self, now: datetime):
        self._state = self._state.block_card(self, now)

    def accept_return(self, now: datetime, damage_fine: Money, overdue_fine: Money):
        self._state = self._state.accept_return(self, now, damage_fine, overdue_fine)


# ---------------------------------------------------------------------------
# Доменный сервис библиотеки: согласует Loan и BookCopy.
# Порядок: предусловия по экземпляру -> событие Loan (может отказать) ->
# операция над экземпляром (после проверок не отказывает).
# ---------------------------------------------------------------------------

class LibraryService:
    @staticmethod
    def _check_copy(loan: Loan, book_copy: BookCopy):
        if loan.copy_id != book_copy.id:
            raise DomainInvariantViolation("Выдача связана с другим экземпляром книги.")

    def reserve_book(self, loan: Loan, book_copy: BookCopy, now: datetime):
        self._check_copy(loan, book_copy)
        loan.require_allowed(LoanEvent.CONFIRM_RESERVATION)
        if book_copy.available_quantity <= 0:
            raise DomainInvariantViolation("Нет доступных экземпляров этой книги.")
        loan.confirm_reservation(now)
        book_copy.reserve()

    def enqueue_reader(self, loan: Loan, book_copy: BookCopy, now: datetime):
        self._check_copy(loan, book_copy)
        loan.enqueue(now, copy_available=book_copy.available_quantity > 0)

    def issue_book(
        self, loan: Loan, book_copy: BookCopy, now: datetime, term: LoanTerm,
        reader_has_debt: bool,
    ):
        self._check_copy(loan, book_copy)
        loan.require_allowed(LoanEvent.ISSUE)
        if book_copy.reserved_quantity <= 0:
            raise DomainInvariantViolation("Нет забронированных экземпляров для выдачи.")
        loan.issue(now, term, reader_has_debt)
        book_copy.issue_reserved_copy()

    def deny_issue(self, loan: Loan, book_copy: BookCopy, now: datetime, reader_has_debt: bool):
        self._check_copy(loan, book_copy)
        loan.require_allowed(LoanEvent.DENY_ISSUE)
        if book_copy.reserved_quantity <= 0:
            raise DomainInvariantViolation("Нет забронированных экземпляров.")
        loan.deny_issue(now, reader_has_debt)
        book_copy.release_reservation()

    def expire_reservation(
        self, loan: Loan, book_copy: BookCopy, now: datetime,
        next_reader_id: Optional[EntityId],
    ):
        self._check_copy(loan, book_copy)
        loan.require_allowed(LoanEvent.RESERVATION_TIMEOUT)
        if book_copy.reserved_quantity <= 0:
            raise DomainInvariantViolation("Нет забронированных экземпляров.")
        loan.reservation_timeout(now, next_reader_id)
        if next_reader_id is None:
            book_copy.release_reservation()  # без очереди книга возвращается в фонд

    def return_book(
        self, loan: Loan, book_copy: BookCopy, now: datetime,
        damage_fine: Money, overdue_fine: Money,
    ):
        self._check_copy(loan, book_copy)
        loan.require_allowed(LoanEvent.ACCEPT_RETURN)
        if book_copy.active_loans <= 0:
            raise DomainInvariantViolation("Нет выданных экземпляров для возврата.")
        loan.accept_return(now, damage_fine, overdue_fine)
        book_copy.return_copy()


# ---------------------------------------------------------------------------
# Интерфейсы репозиториев (изменение: next_id, list_all, find_by_reader)
# ---------------------------------------------------------------------------

class BookCopyRepository(Protocol):
    def get_by_id(self, copy_id: EntityId) -> BookCopy:
        ...

    def save(self, book_copy: BookCopy) -> None:
        ...


class LoanRepository(Protocol):
    def next_id(self) -> EntityId:
        ...

    def get_by_id(self, loan_id: EntityId) -> Loan:
        ...

    def save(self, loan: Loan) -> None:
        ...

    def list_all(self) -> list:
        ...

    def find_by_reader(self, reader_id: EntityId) -> list:
        ...


# ---------------------------------------------------------------------------
# Реализация репозиториев в памяти
# ---------------------------------------------------------------------------

class InMemoryBookCopyRepository:
    def __init__(self):
        self._items = {}

    def get_by_id(self, copy_id: EntityId) -> BookCopy:
        if copy_id not in self._items:
            raise DomainInvariantViolation("Экземпляр книги не найден.")
        return self._items[copy_id]

    def save(self, book_copy: BookCopy) -> None:
        self._items[book_copy.id] = book_copy


class InMemoryLoanRepository:
    def __init__(self):
        self._items = {}
        self._last_id = 0

    def next_id(self) -> EntityId:
        self._last_id += 1
        return EntityId(self._last_id)

    def get_by_id(self, loan_id: EntityId) -> Loan:
        if loan_id not in self._items:
            raise DomainInvariantViolation("Запись о выдаче книги не найдена.")
        return self._items[loan_id]

    def save(self, loan: Loan) -> None:
        self._items[loan.id] = loan
        self._last_id = max(self._last_id, loan.id.value)

    def list_all(self) -> list:
        return list(self._items.values())

    def find_by_reader(self, reader_id: EntityId) -> list:
        return [loan for loan in self._items.values() if loan.reader_id == reader_id]


# ---------------------------------------------------------------------------
# Фабрики восстановления
# ---------------------------------------------------------------------------

class BookCopyFactory:
    @staticmethod
    def restore(state: dict) -> BookCopy:
        copy_id = EntityId(state["id"])
        book_id = EntityId(state["book_id"])
        quantity = CopyQuantity(state["quantity"])
        active_loans = state["active_loans"]
        reserved = state.get("reserved", 0)

        for value in (active_loans, reserved):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DomainInvariantViolation("Некорректное количество выданных или забронированных экземпляров.")
        if active_loans + reserved > quantity.value:
            raise DomainInvariantViolation("Выдано и забронировано больше экземпляров, чем есть.")

        book_copy = BookCopy(copy_id, book_id, quantity)
        book_copy._active_loans = active_loans
        book_copy._reserved = reserved
        return book_copy


class LoanFactory:
    """Восстановление выдачи: состояние хранится именем, журнал событий не восстанавливается."""

    @staticmethod
    def restore(state: dict) -> Loan:
        loan = Loan(
            EntityId(state["id"]),
            EntityId(state["reader_id"]),
            EntityId(state["copy_id"]),
        )

        state_cls = STATES_BY_NAME.get(state["state"])
        if state_cls is None:
            raise DomainInvariantViolation("Неизвестное состояние выдачи книги.")

        reserved_until = state.get("reserved_until")
        issue_date = state.get("issue_date")
        due_date = state.get("due_date")
        return_date = state.get("return_date")
        fine = Money(state.get("fine", 0))
        extension_used = state.get("extension_used", False)
        reminder_sent = state.get("reminder_sent", False)

        if state_cls is ReservedState:
            if not isinstance(reserved_until, datetime):
                raise DomainInvariantViolation("Для брони необходим срок её действия.")
        elif state_cls in (IssuedState, ExtendedState, OverdueState, CardBlockedState,
                           ReturnedCleanState, ReturnedWithFineState):
            if not isinstance(issue_date, date) or not isinstance(due_date, date):
                raise DomainInvariantViolation("Некорректные даты выдачи или возврата.")
            if due_date <= issue_date:
                raise DomainInvariantViolation("Срок возврата должен быть позже даты выдачи.")

        if state_cls in (IssuedState, ExtendedState, OverdueState, CardBlockedState):
            if return_date is not None or fine.rubles != 0:
                raise DomainInvariantViolation(
                    "У книги на руках не должно быть даты возврата или штрафа."
                )
        if state_cls is IssuedState and extension_used:
            raise DomainInvariantViolation("Выдана: продление не может быть использовано.")
        if state_cls is ExtendedState and not extension_used:
            raise DomainInvariantViolation("Продлена: продление должно быть использовано.")

        loan._state = state_cls()
        loan._reserved_until = reserved_until
        loan._issue_date = issue_date
        loan._due_date = due_date
        loan._extension_used = extension_used
        loan._reminder_sent = reminder_sent

        if state_cls in (ReturnedCleanState, ReturnedWithFineState):
            if not isinstance(return_date, date) or return_date < issue_date:
                raise DomainInvariantViolation("Некорректная дата возврата.")
            overdue = loan.calculate_overdue_fine(LoanDate(return_date))
            if fine.rubles < overdue.rubles:
                raise DomainInvariantViolation("Сохранённая сумма штрафа не соответствует правилам.")
            if (state_cls is ReturnedCleanState) != (fine.rubles == 0):
                raise DomainInvariantViolation("Состояние не соответствует сумме штрафа.")
            loan._return_date = return_date
            loan._fine = fine

        return loan
