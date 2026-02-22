from __future__ import annotations

from email.message import EmailMessage

from agent_system.models.project_config import EmailApprovalConfig
from agent_system.models.task import Task, TaskStatus
from agent_system.services.email_approval import EmailApprovalService


def _build_raw_email(subject: str, sender: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "receiver@example.com"
    msg.set_content(body)
    return msg.as_bytes()


class _FakeImap:
    def __init__(self, raw_email: bytes) -> None:
        self._raw_email = raw_email
        self.search_calls: list[tuple[str, ...]] = []

    def __enter__(self) -> _FakeImap:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        return "OK", [b"login ok"]

    def select(self, mailbox: str) -> tuple[str, list[bytes]]:
        return "OK", [b"1"]

    def search(self, charset: str | None, *criteria: str) -> tuple[str, list[bytes]]:
        normalized = tuple(criteria)
        self.search_calls.append(normalized)
        if normalized == ("UNSEEN", "FROM", '"dy00@foxmail.com"'):
            return "OK", [b""]
        if normalized == ("FROM", '"dy00@foxmail.com"'):
            return "OK", [b"1"]
        return "OK", [b""]

    def fetch(self, msg_id: bytes, args: str) -> tuple[str, list[tuple[bytes, bytes]]]:
        return "OK", [(b"1", self._raw_email)]


class _FakeImapFactory:
    def __init__(self, raw_email: bytes) -> None:
        self.instance = _FakeImap(raw_email)

    def __call__(self, host: str, port: int) -> _FakeImap:
        return self.instance


def test_wait_for_reply_fallbacks_to_seen_messages(monkeypatch) -> None:
    token = "TASK-T1-123"
    raw = _build_raw_email(
        subject=f"Re: [AgentSystem] Supervisor暂停 T1 [{token}]",
        sender="dy00@foxmail.com",
        body="CONTINUE: 请继续\n",
    )
    factory = _FakeImapFactory(raw)

    monkeypatch.setattr("agent_system.services.email_approval.imaplib.IMAP4_SSL", factory)
    monkeypatch.setenv("IMAP_PW", "dummy")

    cfg = EmailApprovalConfig(
        enabled=True,
        imap_host="imap.qq.com",
        imap_port=993,
        imap_user="670788361@qq.com",
        imap_password_env="IMAP_PW",
        approval_sender="dy00@foxmail.com",
        poll_interval_sec=1,
        max_wait_sec=120,
    )
    svc = EmailApprovalService(cfg)
    task = Task(id="T1", title="t", description="d", status=TaskStatus.BLOCKED)

    decision = svc._wait_for_reply(task, token)

    assert decision.action == "continue"
    assert decision.hint == "请继续"
    assert ("UNSEEN", "FROM", '"dy00@foxmail.com"') in factory.instance.search_calls
    assert ("FROM", '"dy00@foxmail.com"') in factory.instance.search_calls
