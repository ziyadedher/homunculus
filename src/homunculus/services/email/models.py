from dataclasses import dataclass


@dataclass(frozen=True)
class EmailSummary:
    id: str
    thread_id: str
    subject: str
    sender: str
    date: str
    snippet: str


@dataclass(frozen=True)
class EmailDetail:
    id: str
    thread_id: str
    subject: str
    sender: str
    to: list[str]
    date: str
    body_text: str
