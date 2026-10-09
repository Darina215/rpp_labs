from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Protocol


class DomainInvariantViolation(Exception):
    pass


class InvalidValueObject(DomainInvariantViolation):
    pass


@dataclass(frozen=True)
class EntityId:
    value: int

    def __post_init__(self):
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value <= 0:
            raise InvalidValueObject("ID должен быть положительным целым числом.")


@dataclass(frozen=True)
class SpotNumber:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("Номер парковочного места не может быть пустым.")


@dataclass(frozen=True)
class PlateNumber:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("Государственный номер не может быть пустым.")


@dataclass(frozen=True)
class Money:
    amount: int
    currency: str = "RUB"

    def __post_init__(self):
        if isinstance(self.amount, bool) or not isinstance(self.amount, int) or self.amount < 0:
            raise InvalidValueObject("Денежная сумма не может быть отрицательной.")
        if not self.currency.strip():
            raise InvalidValueObject("Валюта не может быть пустой.")


@dataclass(frozen=True)
class ParkingRate:
    hourly_price: int
    currency: str = "RUB"

    def __post_init__(self):
        if isinstance(self.hourly_price, bool) or not isinstance(self.hourly_price, int) or self.hourly_price <= 0:
            raise InvalidValueObject("Стоимость часа должна быть положительной.")
        if not self.currency.strip():
            raise InvalidValueObject("Валюта не может быть пустой.")


@dataclass(frozen=True)
class ParkingDuration:
    seconds: int

    def __post_init__(self):
        if isinstance(self.seconds, bool) or not isinstance(self.seconds, int) or self.seconds < 0:
            raise InvalidValueObject("Длительность парковки не может быть отрицательной.")


#Агрегат парковочное место
class ParkingSpot:
    FREE = "свободно"
    OCCUPIED = "занято"

    def __init__(self, spot_id: EntityId, number: SpotNumber):
        self._id = spot_id
        self._number = number
        self._active_session_id = None

    @property
    def id(self):
        return self._id

    @property
    def number(self):
        return self._number

    @property
    def status(self):
        return self.OCCUPIED if self._active_session_id is not None else self.FREE

    @property
    def is_free(self):
        return self._active_session_id is None

    def occupy(self, session_id: EntityId):
        if not self.is_free:
            raise DomainInvariantViolation("Парковочное место уже занято.")
        self._active_session_id = session_id

    def release(self, session_id: EntityId):
        if self._active_session_id != session_id:
            raise DomainInvariantViolation("На месте нет указанной активной сессии.")
        self._active_session_id = None

    def has_session(self, session_id: EntityId):
        return self._active_session_id == session_id


#Объект автомобиль

class Car:
    def __init__(self, car_id: EntityId, plate_number: PlateNumber):
        self._id = car_id
        self._plate_number = plate_number

    @property
    def id(self):
        return self._id

    @property
    def plate_number(self):
        return self._plate_number


#Агрегат парковочная сессия

@dataclass(frozen=True)
class ParkingTime:
    value: datetime

    def __post_init__(self):
        if not isinstance(self.value, datetime):
            raise InvalidValueObject("Укажите корректные дату и время.")


class ParkingSession:
    ACTIVE = "активна"
    CLOSED = "закрыта"

    def __init__(
        self,
        session_id: EntityId,
        spot_id: EntityId,
        car_id: EntityId,
        entry_time: ParkingTime,
        rate: ParkingRate,
    ):
        self._id = session_id
        self._spot_id = spot_id
        self._car_id = car_id
        self._entry_time = entry_time.value
        self._rate = rate
        self._exit_time = None
        self._cost = Money(0, rate.currency)
        self._status = self.ACTIVE

    @property
    def id(self):
        return self._id

    @property
    def spot_id(self):
        return self._spot_id

    @property
    def car_id(self):
        return self._car_id

    @property
    def entry_time(self):
        return self._entry_time

    @property
    def exit_time(self):
        return self._exit_time

    @property
    def rate(self):
        return self._rate

    @property
    def cost(self):
        return self._cost

    @property
    def status(self):
        return self._status

    def close(self, exit_time: ParkingTime):
        if self._status == self.CLOSED:
            raise DomainInvariantViolation("Парковочная сессия уже закрыта.")

        duration = (exit_time.value - self._entry_time).total_seconds()

        if duration < 0:
            raise DomainInvariantViolation(
                "Время выезда не может быть раньше времени въезда."
            )

        hours = max(1, ceil(duration / 3600))
        self._exit_time = exit_time.value
        self._cost = Money(hours * self._rate.hourly_price, self._rate.currency)
        self._status = self.CLOSED


#Доменный сервис въезда и выезда

class ParkingService:
    def enter(self, spot: ParkingSpot, session: ParkingSession):
        if session.spot_id != spot.id:
            raise DomainInvariantViolation(
                "Сессия относится к другому парковочному месту."
            )

        if session.status != ParkingSession.ACTIVE:
            raise DomainInvariantViolation(
                "Нельзя начать закрытую парковочную сессию."
            )

        spot.occupy(session.id)

    def exit(
        self,
        spot: ParkingSpot,
        session: ParkingSession,
        exit_time: ParkingTime,
    ):
        if session.spot_id != spot.id:
            raise DomainInvariantViolation(
                "Сессия относится к другому парковочному месту."
            )

        if not spot.has_session(session.id):
            raise DomainInvariantViolation(
                "На месте нет указанной активной сессии."
            )

        session.close(exit_time)
        spot.release(session.id)


#Фабрики восстановления объектов

class ParkingSpotFactory:
    @staticmethod
    def restore(state):
        spot = ParkingSpot(
            EntityId(state["id"]),
            SpotNumber(state["number"]),
        )

        active_session_id = state["active_session_id"]
        if active_session_id is not None:
            spot.occupy(EntityId(active_session_id))

        return spot


class ParkingSessionFactory:
    @staticmethod
    def restore(state):
        rate = ParkingRate(
            state["hourly_price"],
            state["currency"],
        )

        session = ParkingSession(
            EntityId(state["id"]),
            EntityId(state["spot_id"]),
            EntityId(state["car_id"]),
            ParkingTime(state["entry_time"]),
            rate,
        )

        if state["status"] == ParkingSession.ACTIVE:
            if state["exit_time"] is not None or state["cost"] != 0:
                raise DomainInvariantViolation(
                    "Некорректное состояние активной сессии."
                )

        elif state["status"] == ParkingSession.CLOSED:
            if not isinstance(state["exit_time"], datetime):
                raise DomainInvariantViolation(
                    "Для закрытой сессии необходимо время выезда."
                )

            session.close(ParkingTime(state["exit_time"]))

            if session.cost.amount != state["cost"]:
                raise DomainInvariantViolation(
                    "Стоимость сессии не соответствует расчёту."
                )

        else:
            raise DomainInvariantViolation("Неизвестный статус сессии.")

        return session