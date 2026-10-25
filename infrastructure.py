"""Заглушки портов: данные хранятся в памяти."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from application import ClockMovedBackwards
from domain import EntityId, Money


# ---------------------------------------------------------------------------
# Часы
# ---------------------------------------------------------------------------

class SystemClock:
    """Системное время (только здесь, за пределами домена и прикладного слоя)."""

    def now(self) -> datetime:
        return datetime.now()


class ManualClock:
    """Управляемые часы для тестов и ручной проверки; время вперёд и назад не ограничено."""

    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


class AppClock:
    """Системное время со сдвигом (для веб-адаптера).

    Требование методички (Раздел IV, задача 4.1, п. 6): перевод часов
    приложения назад не допускается. Сдвиг хранится отдельно от системного
    времени, поэтому ``now()`` возвращает системное время плюс сдвиг.
    """

    def __init__(self):
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now() + self._offset

    def set_to(self, target: datetime) -> None:
        system_now = datetime.now()
        current = system_now + self._offset
        if target < current:
            raise ClockMovedBackwards(
                f"Нельзя перевести часы назад: "
                f"{target.isoformat()} < {current.isoformat()}"
            )
        self._offset = target - system_now


# ---------------------------------------------------------------------------
# Очередь на книгу
# ---------------------------------------------------------------------------

class InMemoryReservationQueue:
    def __init__(self):
        self._queues: dict = {}

    def enqueue(self, copy_id: EntityId, reader_id: EntityId) -> None:
        self._queues.setdefault(copy_id, []).append(reader_id)

    def has_waiting(self, copy_id: EntityId) -> bool:
        return bool(self._queues.get(copy_id))

    def peek_next(self, copy_id: EntityId) -> Optional[EntityId]:
        queue = self._queues.get(copy_id)
        return queue[0] if queue else None

    def pop_next(self, copy_id: EntityId) -> Optional[EntityId]:
        queue = self._queues.get(copy_id)
        return queue.pop(0) if queue else None


# ---------------------------------------------------------------------------
# Журнал штрафов
# ---------------------------------------------------------------------------

class InMemoryFineLedger:
    def __init__(self):
        self._unpaid: dict = {}

    def add_fine(self, reader_id: EntityId, loan_id: EntityId, amount: Money) -> None:
        self._unpaid.setdefault(reader_id, {})[loan_id] = amount

    def has_unpaid_fines(self, reader_id: EntityId) -> bool:
        return bool(self._unpaid.get(reader_id))

    def pay_all(self, reader_id: EntityId) -> None:
        self._unpaid.pop(reader_id, None)


# ---------------------------------------------------------------------------
# Реестр читательских билетов
# ---------------------------------------------------------------------------

class InMemoryReaderCardRegistry:
    def __init__(self):
        self._blocked: set = set()

    def block(self, reader_id: EntityId) -> None:
        self._blocked.add(reader_id)

    def is_blocked(self, reader_id: EntityId) -> bool:
        return reader_id in self._blocked


# ---------------------------------------------------------------------------
# Администрация
# ---------------------------------------------------------------------------

class InMemoryAdministration:
    def __init__(self):
        self.cases: list = []

    def transfer_case(self, reader_id: EntityId, loan_id: EntityId) -> None:
        self.cases.append((reader_id, loan_id))