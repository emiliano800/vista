"""Raw event sources.

Celonis ships a Windows desktop client that hooks OS-level input and
accessibility APIs. Here the same contract is modelled as an ``EventSource``
protocol; ``SyntheticSource`` produces realistic invoice-processing traffic
so the rest of the pipeline can be developed and tested without a client.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Protocol, TextIO

from taskmining.models import Annotation, EventType, RawEvent, read_jsonl


class EventSource(Protocol):
    def events(self) -> Iterable[RawEvent]: ...


class JsonlSource:
    def __init__(self, fp: TextIO):
        self._fp = fp

    def events(self) -> Iterable[RawEvent]:
        return read_jsonl(self._fp)


class SyntheticSource:
    """Generates accounts-payable style desktop sessions.

    Each case is an invoice processed roughly as: open email -> open PDF ->
    look up vendor in SAP -> enter invoice in SAP -> (optionally) check Excel
    -> reply to email. Variation is injected to produce several variants and
    idle gaps to exercise sessionization.
    """

    USERS = ("alice", "bob", "carol")

    OFFSCREEN_LABELS = (
        "Phone: chase overdue invoices",
        "Paper: file signed delivery notes",
        "Meeting: weekly AP review",
    )

    def __init__(self, n_cases: int = 40, seed: int = 7, start: datetime | None = None):
        self.n_cases = n_cases
        self.rng = random.Random(seed)
        self.start = start or datetime(2026, 3, 2, 8, 0, 0)
        self._events: list[RawEvent] | None = None
        self._annotations: list[Annotation] = []

    def events(self) -> Iterable[RawEvent]:
        if self._events is None:
            self._generate()
        return list(self._events or [])

    def annotations(self) -> list[Annotation]:
        """Employee statements covering the idle gaps the recorder cannot see,
        plus one analyst correction of a rule label."""
        if self._events is None:
            self._generate()
        return list(self._annotations)

    def _generate(self) -> None:
        out: list[RawEvent] = []
        anns: list[Annotation] = []
        t = self.start
        for i in range(self.n_cases):
            user = self.rng.choice(self.USERS)
            inv = f"INV-{100200 + i}"
            vendor = self.rng.choice(["ACME GmbH", "Globex", "Initech", "Umbrella"])
            email = f"{user}@vista.example"
            t += timedelta(seconds=self.rng.randint(5, 90))
            if self.rng.random() < 0.1:
                gap_start = t
                t += timedelta(minutes=self.rng.randint(20, 60))
                anns.append(
                    Annotation(
                        user=user,
                        start=gap_start + timedelta(minutes=1),
                        end=t - timedelta(minutes=1),
                        label=self.rng.choice(self.OFFSCREEN_LABELS),
                        note="self-reported in daily check-in",
                    )
                )
            t = self._case(out, t, user, inv, vendor, email)
        excel = [e for e in out if e.app == "Excel"]
        if excel:
            first = excel[0]
            burst = [e for e in excel if e.user == first.user and e.timestamp - first.timestamp < timedelta(minutes=1)]
            anns.append(
                Annotation(
                    user=first.user,
                    start=first.timestamp,
                    end=burst[-1].timestamp,
                    label="Reconcile AP tracker with SAP",
                    note="Analyst: tracker entry is a reconciliation step, not data entry",
                    author="analyst",
                )
            )
        self._events, self._annotations = out, anns

    def _e(self, out, t, user, typ, app, title, url="", element="", text="", **payload):
        out.append(RawEvent(t, user, typ, app, title, url, element, text, payload))

    def _case(self, out, t, user, inv, vendor, email) -> datetime:
        r = self.rng
        ev = self._e
        # 1. open email with invoice attached
        ev(out, t, user, EventType.FOCUS, "Outlook", f"Invoice {inv} from {vendor} - Message (HTML) - Outlook")
        t += timedelta(seconds=r.randint(2, 8))
        ev(out, t, user, EventType.CLICK, "Outlook", f"Invoice {inv} from {vendor} - Message (HTML) - Outlook", element="attachment")
        t += timedelta(seconds=r.randint(2, 6))
        # 2. read PDF
        ev(out, t, user, EventType.FOCUS, "Acrobat", f"{inv}.pdf - Adobe Acrobat Reader")
        for _ in range(r.randint(1, 4)):
            t += timedelta(seconds=r.randint(2, 10))
            ev(out, t, user, EventType.SCROLL, "Acrobat", f"{inv}.pdf - Adobe Acrobat Reader")
        t += timedelta(seconds=r.randint(1, 4))
        ev(out, t, user, EventType.COPY, "Acrobat", f"{inv}.pdf - Adobe Acrobat Reader", text=inv)
        # 3. vendor lookup in SAP (sometimes skipped for known vendors)
        if r.random() < 0.7:
            t += timedelta(seconds=r.randint(2, 6))
            ev(out, t, user, EventType.FOCUS, "SAP Logon", "Display Vendor: Initial Screen - SAP")
            t += timedelta(seconds=r.randint(1, 3))
            for ch in vendor[:6]:
                t += timedelta(milliseconds=r.randint(80, 300))
                ev(out, t, user, EventType.KEY, "SAP Logon", "Display Vendor: Initial Screen - SAP", text=ch)
            t += timedelta(seconds=r.randint(1, 3))
            ev(out, t, user, EventType.CLICK, "SAP Logon", "Display Vendor: Initial Screen - SAP", element="Enter")
        # 4. enter invoice in SAP MIRO
        t += timedelta(seconds=r.randint(2, 6))
        ev(out, t, user, EventType.FOCUS, "SAP Logon", "Enter Incoming Invoice: Company Code 1000 - SAP")
        t += timedelta(seconds=r.randint(1, 3))
        ev(out, t, user, EventType.PASTE, "SAP Logon", "Enter Incoming Invoice: Company Code 1000 - SAP", element="Reference", text=inv)
        amount = f"{r.randint(100, 99999)}.{r.randint(0, 99):02d}"
        for ch in amount:
            t += timedelta(milliseconds=r.randint(80, 300))
            ev(out, t, user, EventType.KEY, "SAP Logon", "Enter Incoming Invoice: Company Code 1000 - SAP", text=ch)
        # rework loop: go back to PDF to re-check amount
        if r.random() < 0.25:
            t += timedelta(seconds=r.randint(2, 5))
            ev(out, t, user, EventType.FOCUS, "Acrobat", f"{inv}.pdf - Adobe Acrobat Reader")
            t += timedelta(seconds=r.randint(3, 15))
            ev(out, t, user, EventType.FOCUS, "SAP Logon", "Enter Incoming Invoice: Company Code 1000 - SAP")
        t += timedelta(seconds=r.randint(1, 4))
        ev(out, t, user, EventType.CLICK, "SAP Logon", "Enter Incoming Invoice: Company Code 1000 - SAP", element="Post")
        # 5. sometimes record in tracking spreadsheet
        if r.random() < 0.5:
            t += timedelta(seconds=r.randint(2, 6))
            ev(out, t, user, EventType.FOCUS, "Excel", "AP_Tracker_2026.xlsx - Excel")
            t += timedelta(seconds=r.randint(1, 3))
            ev(out, t, user, EventType.PASTE, "Excel", "AP_Tracker_2026.xlsx - Excel", text=inv)
            for ch in amount:
                t += timedelta(milliseconds=r.randint(80, 300))
                ev(out, t, user, EventType.KEY, "Excel", "AP_Tracker_2026.xlsx - Excel", text=ch)
        # 6. reply to vendor
        t += timedelta(seconds=r.randint(2, 6))
        ev(out, t, user, EventType.FOCUS, "Outlook", f"RE: Invoice {inv} from {vendor} - Message (HTML) - Outlook")
        t += timedelta(seconds=r.randint(1, 3))
        body = f"Hi, invoice {inv} posted. Contact {email} or +49 30 1234567"
        for ch in body[:20]:
            t += timedelta(milliseconds=r.randint(60, 250))
            ev(out, t, user, EventType.KEY, "Outlook", f"RE: Invoice {inv} from {vendor} - Message (HTML) - Outlook", text=ch)
        t += timedelta(seconds=r.randint(1, 3))
        title = f"RE: Invoice {inv} from {vendor} - Message (HTML) - Outlook"
        ev(out, t, user, EventType.CLICK, "Outlook", title, element="Send", text=body)
        return t
