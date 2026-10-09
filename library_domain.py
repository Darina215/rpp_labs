from dataclasses import dataclass
from datetime import date
from typing import Protocol


# Доменные исключения

class DomainInvariantViolation(Exception):
    """Нарушение правила предметной области."""


class InvalidValueObject(DomainInvariantViolation):
    """Некорректное значение объекта."""


# Объекты-значения

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


@dataclass(frozen=True)
class LoanDate:
    value: date

    def __post_init__(self):
        if not isinstance(self.value, date):
            raise InvalidValueObject("Дата должна быть объектом date.")


# Сущности предметной области

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


# Агрегат экземпляров книги

class BookCopy:
    def __init__(self, copy_id: EntityId, book_id: EntityId, quantity: CopyQuantity):
        self._id = copy_id
        self._book_id = book_id
        self._quantity = quantity.value
        self._active_loans = 0

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
    def available_quantity(self):
        return self._quantity - self._active_loans

    def issue_copy(self):
        if self._active_loans >= self._quantity:
            raise DomainInvariantViolation(
                "Нет доступных экземпляров этой книги."
            )
        self._active_loans += 1

    def return_copy(self):
        if self._active_loans <= 0:
            raise DomainInvariantViolation(
                "Нет выданных экземпляров для возврата."
            )
        self._active_loans -= 1


# Агрегат выдачи книги

class Loan:
    ISSUED = "выдана"
    CLOSED = "закрыта"
    FINE_AMOUNT = 1000

    def __init__(
        self,
        loan_id: EntityId,
        reader_id: EntityId,
        copy_id: EntityId,
        issue_date: LoanDate,
        term: LoanTerm,
    ):
        self._id = loan_id
        self._reader_id = reader_id
        self._copy_id = copy_id
        self._issue_date = issue_date.value
        self._due_date = date.fromordinal(
            issue_date.value.toordinal() + term.days
        )
        self._return_date = None
        self._status = self.ISSUED
        self._fine = Money(0)
        self._issue_registered = False
    
    @property
    def issue_registered(self):
        return self._issue_registered

    def mark_issue_registered(self):
        if self._issue_registered:
            raise DomainInvariantViolation(
                "Эта выдача уже учтена."
            )

        if self._status != self.ISSUED:
            raise DomainInvariantViolation(
                "Нельзя учесть закрытую выдачу."
            )

        self._issue_registered = True    

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
    def issue_date(self):
        return self._issue_date

    @property
    def due_date(self):
        return self._due_date

    @property
    def return_date(self):
        return self._return_date

    @property
    def status(self):
        return self._status

    @property
    def fine(self):
        return self._fine

    def close(self, return_date: LoanDate):
        if self._status == self.CLOSED:
            raise DomainInvariantViolation(
                "Эта выдача уже закрыта."
            )

        if return_date.value < self._issue_date:
            raise DomainInvariantViolation(
                "Дата возврата не может быть раньше даты выдачи."
            )

        self._return_date = return_date.value
        self._status = self.CLOSED

        if return_date.value > self._due_date:
            self._fine = Money(self.FINE_AMOUNT)


# Доменный сервис библиотеки

class LibraryService:
    def issue_book(
        self,
        loan: Loan,
        book_copy: BookCopy,
    ):
        if loan.status != Loan.ISSUED:
            raise DomainInvariantViolation(
                "Нельзя выдать книгу по закрытой записи."
            )

        if loan.copy_id != book_copy.id:
            raise DomainInvariantViolation(
                "Выдача связана с другим экземпляром книги."
            )

        if loan.issue_registered:
            raise DomainInvariantViolation(
                "Эта выдача уже учтена."
            )

        book_copy.issue_copy()
        loan.mark_issue_registered()

    
    def return_book(
        self,
        loan: Loan,
        book_copy: BookCopy,
        return_date: LoanDate,
    ):
        if loan.copy_id != book_copy.id:
            raise DomainInvariantViolation(
                "Выдача связана с другим экземпляром книги."
            )

        if loan.status != Loan.ISSUED:
            raise DomainInvariantViolation(
                "Эта выдача уже закрыта."
            )

        if book_copy.available_quantity >= book_copy.quantity:
            raise DomainInvariantViolation(
                "Нет выданных экземпляров для возврата."
            )

        if return_date.value < loan.issue_date:
            raise DomainInvariantViolation(
                "Дата возврата не может быть раньше даты выдачи."
            )

        loan.close(return_date)
        book_copy.return_copy()


# Интерфейсы репозиториев

class BookCopyRepository(Protocol):
    def get_by_id(self, copy_id: EntityId) -> BookCopy:
        ...

    def save(self, book_copy: BookCopy) -> None:
        ...


class LoanRepository(Protocol):
    def get_by_id(self, loan_id: EntityId) -> Loan:
        ...

    def save(self, loan: Loan) -> None:
        ...


# Реализация репозиториев в памяти

class InMemoryBookCopyRepository:
    def __init__(self):
        self._items = {}

    def get_by_id(self, copy_id: EntityId) -> BookCopy:
        if copy_id not in self._items:
            raise DomainInvariantViolation(
                "Экземпляр книги не найден."
            )
        return self._items[copy_id]

    def save(self, book_copy: BookCopy) -> None:
        self._items[book_copy.id] = book_copy


class InMemoryLoanRepository:
    def __init__(self):
        self._items = {}

    def get_by_id(self, loan_id: EntityId) -> Loan:
        if loan_id not in self._items:
            raise DomainInvariantViolation(
                "Запись о выдаче книги не найдена."
            )
        return self._items[loan_id]

    def save(self, loan: Loan) -> None:
        self._items[loan.id] = loan



# Фабрика восстановления экземпляра книги

class BookCopyFactory:
    @staticmethod
    def restore(state: dict) -> BookCopy:
        copy_id = EntityId(state["id"])
        book_id = EntityId(state["book_id"])
        quantity = CopyQuantity(state["quantity"])
        active_loans = state["active_loans"]

        if (
            isinstance(active_loans, bool)
            or not isinstance(active_loans, int)
            or active_loans < 0
            or active_loans > quantity.value
        ):
            raise DomainInvariantViolation(
                "Некорректное количество выданных экземпляров."
            )

        book_copy = BookCopy(copy_id, book_id, quantity)
        book_copy._active_loans = active_loans

        return book_copy


# Фабрика восстановления выдачи книги

class LoanFactory:
    @staticmethod
    def restore(state: dict) -> Loan:
        loan_id = EntityId(state["id"])
        reader_id = EntityId(state["reader_id"])
        copy_id = EntityId(state["copy_id"])

        issue_date = state["issue_date"]
        due_date = state["due_date"]
        return_date = state["return_date"]
        status = state["status"]
        saved_fine = Money(state["fine"])

        if not isinstance(issue_date, date) or not isinstance(due_date, date):
            raise DomainInvariantViolation(
                "Некорректные даты выдачи или возврата."
            )

        if due_date <= issue_date:
            raise DomainInvariantViolation(
                "Срок возврата должен быть позже даты выдачи."
            )

        term_days = (due_date - issue_date).days

        loan = Loan(
            loan_id,
            reader_id,
            copy_id,
            LoanDate(issue_date),
            LoanTerm(term_days),
        )
        loan._issue_registered = True

        if status == Loan.ISSUED:
            if return_date is not None or saved_fine.rubles != 0:
                raise DomainInvariantViolation(
                    "У активной выдачи не должно быть даты возврата или штрафа."
                )

        elif status == Loan.CLOSED:
            if not isinstance(return_date, date):
                raise DomainInvariantViolation(
                    "Для закрытой выдачи необходима дата возврата."
                )

            loan.close(LoanDate(return_date))

            if loan.fine != saved_fine:
                raise DomainInvariantViolation(
                    "Сохранённая сумма штрафа не соответствует правилам."
                )

        else:
            raise DomainInvariantViolation(
                "Неизвестный статус выдачи книги."
            )

        return loan