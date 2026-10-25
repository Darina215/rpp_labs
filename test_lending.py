import unittest
from datetime import date, datetime

from application import ExclusiveGateway, GatewayUnresolved, LendingProcess
from domain import (
    BookCopy,
    BookCopyFactory,
    CopyQuantity,
    DomainInvariantViolation,
    EntityId,
    InMemoryBookCopyRepository,
    InMemoryLoanRepository,
    InspectionResult,
    Loan,
    LoanFactory,
    Money,
    TransitionNotAllowed,
    add_working_days,
)
from infrastructure import (
    InMemoryAdministration,
    InMemoryFineLedger,
    InMemoryReaderCardRegistry,
    InMemoryReservationQueue,
    ManualClock,
)

MONDAY = datetime(2026, 10, 26, 10, 0)
READER = EntityId(1)
READER2 = EntityId(2)
COPY = EntityId(10)


class Base(unittest.TestCase):
    def setUp(self, quantity=1):
        self.clock = ManualClock(MONDAY)
        self.loans = InMemoryLoanRepository()
        self.copies = InMemoryBookCopyRepository()
        self.copy = BookCopy(COPY, EntityId(100), CopyQuantity(quantity))
        self.copies.save(self.copy)
        self.queue = InMemoryReservationQueue()
        self.ledger = InMemoryFineLedger()
        self.cards = InMemoryReaderCardRegistry()
        self.admin = InMemoryAdministration()
        self.process = LendingProcess(
            self.loans, self.copies, self.queue, self.ledger,
            self.cards, self.admin, self.clock,
        )

    def reserve_and_issue(self):
        loan_id = self.process.receive_reservation_request(READER, COPY)
        self.clock.advance(days=1)
        self.process.reader_comes_for_book(loan_id)
        return loan_id


class MainPath(Base):
    def test_full_happy_path(self):
        loan_id = self.process.receive_reservation_request(READER, COPY)
        loan = self.loans.get_by_id(loan_id)
        self.assertEqual(loan.state_name, "Забронирована")
        self.assertEqual(loan.reserved_until, datetime(2026, 10, 29, 10, 0))
        self.assertEqual(self.copy.available_quantity, 0)

        self.clock.advance(days=1)
        self.process.reader_comes_for_book(loan_id)
        self.assertEqual(loan.state_name, "Выдана")
        self.assertEqual(loan.due_date, date(2026, 11, 10))
        self.assertEqual((self.copy.reserved_quantity, self.copy.active_loans), (0, 1))

        self.clock.set(datetime(2026, 11, 8, 9, 0))
        self.assertEqual(self.process.on_return_reminder(self.clock.now()), [loan_id])
        self.assertEqual(self.process.on_return_reminder(self.clock.now()), [])

        self.clock.set(datetime(2026, 11, 10, 9, 0))
        self.process.return_book(loan_id, InspectionResult(False))
        self.assertEqual(loan.state_name, "Возвращена без штрафов")
        self.assertEqual(self.copy.available_quantity, 1)
        names = [type(e).__name__ for e in loan.events]
        self.assertEqual(
            names, ["ReservationConfirmed", "BookIssued", "ReminderSent", "BookReturned"]
        )

    def test_unavailable_book_queues_reader(self):
        self.process.receive_reservation_request(READER, COPY)
        loan_id = self.process.receive_reservation_request(READER2, COPY)
        loan = self.loans.get_by_id(loan_id)
        self.assertEqual(loan.state_name, "В очереди")
        self.assertTrue(loan.is_finished)
        self.assertTrue(self.queue.has_waiting(COPY))


class Reservation(Base):
    def test_timeout_without_queue_returns_book_to_fund(self):
        loan_id = self.process.receive_reservation_request(READER, COPY)
        self.assertEqual(self.process.on_reservation_timeout(datetime(2026, 10, 28, 10, 0)), [])
        self.assertEqual(
            self.process.on_reservation_timeout(datetime(2026, 10, 29, 10, 0)), [loan_id]
        )
        loan = self.loans.get_by_id(loan_id)
        self.assertEqual(loan.state_name, "Бронь снята")
        self.assertEqual(self.copy.available_quantity, 1)

    def test_timeout_with_queue_reassigns_and_loops(self):
        loan_id = self.process.receive_reservation_request(READER, COPY)
        self.process.receive_reservation_request(READER2, COPY)
        self.process.on_reservation_timeout(datetime(2026, 10, 29, 10, 0))
        loan = self.loans.get_by_id(loan_id)
        self.assertEqual(loan.state_name, "Забронирована")
        self.assertEqual(loan.reader_id, READER2)
        self.assertFalse(self.queue.has_waiting(COPY))
        self.assertEqual(self.copy.reserved_quantity, 1)

    def test_working_days_skip_weekend(self):
        friday = datetime(2026, 10, 30, 12, 0)
        self.assertEqual(add_working_days(friday, 3), datetime(2026, 11, 4, 12, 0))

    def test_debt_denies_issue_and_releases_reservation(self):
        self.ledger.add_fine(READER, EntityId(99), Money(100))
        loan_id = self.process.receive_reservation_request(READER, COPY)
        self.process.reader_comes_for_book(loan_id)
        loan = self.loans.get_by_id(loan_id)
        self.assertEqual(loan.state_name, "Выдача отклонена")
        self.assertEqual(self.copy.available_quantity, 1)


class Extension(Base):
    def test_extension_once_only(self):
        loan_id = self.reserve_and_issue()
        loan = self.loans.get_by_id(loan_id)
        self.process.request_extension(loan_id)
        self.assertEqual(loan.state_name, "Продлена")
        self.assertEqual(loan.due_date, date(2026, 11, 17))
        self.process.request_extension(loan_id)
        self.assertEqual(loan.state_name, "Продлена")
        self.assertEqual(type(loan.events[-1]).__name__, "ExtensionRefused")
        self.assertEqual(loan.due_date, date(2026, 11, 17))

    def test_extension_refused_when_queue(self):
        loan_id = self.reserve_and_issue()
        self.queue.enqueue(COPY, READER2)
        self.process.request_extension(loan_id)
        loan = self.loans.get_by_id(loan_id)
        self.assertEqual(loan.state_name, "Выдана")
        self.assertEqual(loan.due_date, date(2026, 11, 10))
        self.assertEqual(type(loan.events[-1]).__name__, "ExtensionRefused")


class Overdue(Base):
    def test_overdue_with_damage_fines(self):
        loan_id = self.reserve_and_issue()
        loan = self.loans.get_by_id(loan_id)
        self.clock.set(datetime(2026, 11, 12, 9, 0))
        self.assertEqual(self.process.on_due_date_passed(self.clock.now()), [loan_id])
        self.assertEqual(loan.state_name, "Просрочена")
        self.clock.set(datetime(2026, 11, 13, 9, 0))  # 3 дня просрочки
        self.process.return_book(loan_id, InspectionResult(True, Money(500)))
        self.assertEqual(loan.state_name, "Возвращена со штрафом")
        self.assertEqual(loan.fine, Money(650))
        self.assertTrue(self.ledger.has_unpaid_fines(READER))

    def test_overdue_without_damage(self):
        loan_id = self.reserve_and_issue()
        self.clock.set(datetime(2026, 11, 11, 9, 0))
        self.process.return_book(loan_id, InspectionResult(False))
        self.assertEqual(self.loans.get_by_id(loan_id).fine, Money(50))

    def test_card_blocked_after_30_days(self):
        loan_id = self.reserve_and_issue()
        loan = self.loans.get_by_id(loan_id)
        self.clock.set(datetime(2026, 11, 11, 9, 0))
        self.process.on_due_date_passed(self.clock.now())
        self.assertEqual(self.process.on_block_deadline(datetime(2026, 12, 9, 9, 0)), [])
        self.assertEqual(
            self.process.on_block_deadline(datetime(2026, 12, 10, 9, 0)), [loan_id]
        )
        self.assertEqual(loan.state_name, "Билет заблокирован")
        self.assertTrue(self.cards.is_blocked(READER))
        self.assertEqual(self.admin.cases, [(READER, loan_id)])
        with self.assertRaises(DomainInvariantViolation):
            self.process.receive_reservation_request(READER, COPY)

    def test_overdue_book_counts_as_debt(self):
        self.reserve_and_issue()
        self.clock.set(datetime(2026, 11, 12, 9, 0))
        self.copy._quantity = 2  # второй экземпляр для другого брони
        second = self.process.receive_reservation_request(READER, COPY)
        self.process.reader_comes_for_book(second)
        self.assertEqual(self.loans.get_by_id(second).state_name, "Выдача отклонена")


class Transitions(Base):
    def test_invalid_event_keeps_state(self):
        loan_id = self.reserve_and_issue()
        loan = self.loans.get_by_id(loan_id)
        with self.assertRaises(TransitionNotAllowed) as ctx:
            loan.confirm_reservation(self.clock.now())
        self.assertEqual(ctx.exception.state, "Выдана")
        self.assertEqual(loan.state_name, "Выдана")

        self.process.return_book(loan_id, InspectionResult(False))
        for call in (
            lambda: self.process.return_book(loan_id, InspectionResult(False)),
            lambda: self.process.reader_comes_for_book(loan_id),
            lambda: self.process.request_extension(loan_id),
        ):
            with self.assertRaises(TransitionNotAllowed):
                call()
        self.assertEqual(loan.state_name, "Возвращена без штрафов")
        self.assertEqual(loan.allowed_events, ())

    def test_allowed_events_per_state(self):
        loan_id = self.reserve_and_issue()
        loan = self.loans.get_by_id(loan_id)
        self.assertEqual(
            set(loan.allowed_events),
            {"send_reminder", "extend", "refuse_extension", "due_date_passed", "accept_return"},
        )

    def test_no_public_state_setter(self):
        loan = Loan(EntityId(1), READER, COPY)
        with self.assertRaises(AttributeError):
            loan.state_name = "Выдана"

    def test_wrong_fine_rejected(self):
        loan_id = self.reserve_and_issue()
        loan = self.loans.get_by_id(loan_id)
        self.clock.set(datetime(2026, 11, 13, 9, 0))
        with self.assertRaises(DomainInvariantViolation):
            loan.accept_return(self.clock.now(), Money(0), Money(0))
        self.assertEqual(loan.state_name, "Выдана")


class Gateway(unittest.TestCase):
    def test_unresolved(self):
        gateway = ExclusiveGateway("Тест", [("a", lambda x: False), ("b", lambda x: False)])
        with self.assertRaises(GatewayUnresolved):
            gateway.decide(1)

    def test_default_flow(self):
        gateway = ExclusiveGateway("Тест", [("a", lambda x: False)], default="по умолчанию")
        self.assertEqual(gateway.decide(1), "по умолчанию")


class Factories(Base):
    def test_loan_restore_roundtrip(self):
        loan = LoanFactory.restore({
            "id": 5, "reader_id": 1, "copy_id": 10, "state": "Продлена",
            "issue_date": date(2026, 10, 27), "due_date": date(2026, 11, 17),
            "extension_used": True,
        })
        self.assertEqual(loan.state_name, "Продлена")
        with self.assertRaises(DomainInvariantViolation):
            LoanFactory.restore({
                "id": 5, "reader_id": 1, "copy_id": 10, "state": "Выдана",
                "issue_date": date(2026, 10, 27), "due_date": date(2026, 11, 10),
                "return_date": date(2026, 11, 1),
            })

    def test_copy_restore_invariant(self):
        with self.assertRaises(DomainInvariantViolation):
            BookCopyFactory.restore(
                {"id": 1, "book_id": 1, "quantity": 2, "active_loans": 2, "reserved": 1}
            )


if __name__ == "__main__":
    unittest.main()
