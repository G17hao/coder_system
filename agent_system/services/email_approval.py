"""Supervisor 暂停后的邮件通知与审批服务"""

from __future__ import annotations

import imaplib
import logging
import os
import smtplib
import time
from dataclasses import dataclass
from email import message_from_bytes
from email.message import EmailMessage

from agent_system.models.project_config import EmailApprovalConfig
from agent_system.models.task import Task

logger = logging.getLogger(__name__)


@dataclass
class EmailApprovalDecision:
    """邮件审批结果"""

    action: str
    hint: str = ""
    sender: str = ""


class EmailApprovalService:
    """通过邮件通知并等待人工审批继续/停止"""

    def __init__(self, config: EmailApprovalConfig) -> None:
        self._config = config

    def request_and_wait(self, task: Task, progress_summary: str = "") -> EmailApprovalDecision:
        subject, token = self._build_subject(task)
        self._send_notification(task, subject, token, progress_summary)
        return self._wait_for_reply(task, token)

    def _build_subject(self, task: Task) -> tuple[str, str]:
        token = f"TASK-{task.id}-{int(time.time())}"
        subject = f"{self._config.subject_prefix} Supervisor暂停 {task.id} [{token}]"
        return subject, token

    def _send_notification(self, task: Task, subject: str, token: str, progress_summary: str = "") -> None:
        smtp_password = os.environ.get(self._config.smtp_password_env, "")
        if not smtp_password:
            raise RuntimeError(f"环境变量 {self._config.smtp_password_env} 未设置")

        sender = (self._config.notify_from or self._config.smtp_user).strip()
        receiver = self._config.notify_to.strip()
        if not sender or not receiver:
            raise RuntimeError("email_approval.notify_from/notify_to 未完整配置")

        summary_text = progress_summary.strip()

        body = (
            f"任务已被 Supervisor 暂停，需要人工决策。\n\n"
            f"任务ID: {task.id}\n"
            f"标题: {task.title}\n"
            f"状态: {task.status.value}\n"
            f"错误: {(task.error or '')[:1000]}\n\n"
            f"当前进度:\n{summary_text or '（无可用统计）'}\n\n"
            f"请回复邮件并在正文第一行写：\n"
            f"- CONTINUE: <可选提示词>\n"
            f"- STOP\n\n"
            f"审批令牌: {token}\n"
        )

        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = receiver
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP_SSL(self._config.smtp_host, self._config.smtp_port, timeout=30) as smtp:
            smtp.login(self._config.smtp_user, smtp_password)
            smtp.send_message(msg)

        logger.info("  [email] 已发送审批邮件到 %s", receiver)

    def _wait_for_reply(self, task: Task, token: str) -> EmailApprovalDecision:
        imap_password = os.environ.get(self._config.imap_password_env, "")
        if not imap_password:
            raise RuntimeError(f"环境变量 {self._config.imap_password_env} 未设置")

        deadline = time.time() + self._config.max_wait_sec
        approval_sender = self._config.approval_sender.lower().strip()

        with imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port) as client:
            client.login(self._config.imap_user, imap_password)
            client.select("INBOX")

            while time.time() < deadline:
                msg_ids = self._search_candidate_ids(client, approval_sender)
                if msg_ids:
                    for msg_id in reversed(msg_ids[-50:]):
                        status_fetch, message_data = client.fetch(msg_id, "(RFC822)")
                        if status_fetch != "OK" or not message_data:
                            continue
                        decision = self._parse_message(
                            raw_message=message_data,
                            token=token,
                            approval_sender=approval_sender,
                        )
                        if decision is not None:
                            return decision
                time.sleep(self._config.poll_interval_sec)

        logger.warning("  [email] 等待审批邮件超时（任务 %s）", task.id)
        return EmailApprovalDecision(action="stop")

    def _search_candidate_ids(self, client: imaplib.IMAP4_SSL, approval_sender: str) -> list[bytes]:
        if approval_sender:
            status, data = client.search(None, "UNSEEN", "FROM", f'"{approval_sender}"')
        else:
            status, data = client.search(None, "UNSEEN")

        unseen_ids = data[0].split() if status == "OK" and data and data[0] else []
        if unseen_ids:
            return unseen_ids

        if approval_sender:
            status_all, data_all = client.search(None, "FROM", f'"{approval_sender}"')
            return data_all[0].split() if status_all == "OK" and data_all and data_all[0] else []

        return []

    def _parse_message(
        self,
        raw_message: list[tuple[bytes, bytes] | bytes],
        token: str,
        approval_sender: str,
    ) -> EmailApprovalDecision | None:
        payload_bytes = b""
        for part in raw_message:
            if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                payload_bytes = bytes(part[1])
                break
        if not payload_bytes:
            return None

        msg = message_from_bytes(payload_bytes)
        subject = str(msg.get("Subject", ""))
        sender = str(msg.get("From", "")).lower()

        if token not in subject and token not in self._extract_text(msg):
            return None

        if approval_sender and approval_sender not in sender:
            return None

        body = self._extract_text(msg).strip()
        first_line = body.splitlines()[0].strip() if body else ""
        upper = first_line.upper()

        if upper.startswith("CONTINUE"):
            hint = ""
            if ":" in first_line:
                hint = first_line.split(":", 1)[1].strip()
            return EmailApprovalDecision(action="continue", hint=hint, sender=sender)

        if upper.startswith("STOP"):
            return EmailApprovalDecision(action="stop", sender=sender)

        return None

    @staticmethod
    def _extract_text(msg: EmailMessage) -> str:
        if msg.is_multipart():
            texts: list[str] = []
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype != "text/plain":
                    continue
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                try:
                    texts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    texts.append(payload.decode("utf-8", errors="replace"))
            return "\n".join(texts)

        payload = msg.get_payload(decode=True)
        if payload is None:
            return ""
        charset = msg.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except Exception:
            return payload.decode("utf-8", errors="replace")
