"""
This module provides functionality to interact with an IMAP email server.

It includes:
- A Pydantic model `Email` to represent email details.
- A service class `EmailService` to fetch emails from the inbox based on a specified time range.
"""

import datetime
import email
import imaplib
from email.header import decode_header
from email.message import Message
from typing import Dict, List, Optional

from pydantic import BaseModel


class Email(BaseModel):
    """
    Represents an email with its subject, sender, recipients, date, body, and headers.
    """
    id: Optional[str] = None
    subject: str
    sender: str
    to: Optional[List[str]] = None
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    reply_to: Optional[List[str]] = None
    date: str
    body: str
    headers: Dict[str, str]


class EmailService:
    """
    A service for interacting with an IMAP email server to fetch emails.
    """

    def __init__(
        self,
        imap_user: str,
        imap_pass: str,
        imap_host: str = "imap.mail.me.com",
        imap_port: int = 993,
    ):
        """
        Initializes the EmailService with user credentials.

        Args:
            imap_user (str): The IMAP username.
            imap_pass (str): The IMAP password.
            imap_host (str): The IMAP hostname.
            imap_port (int): The IMAP port.
        """

        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_user = imap_user
        self.imap_pass = imap_pass

    @staticmethod
    def _build_since_query(days: int) -> str:
        since_date = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
        return f'(SINCE "{since_date}")'

    @staticmethod
    def _decode_header_value(raw_value: Optional[str]) -> str:
        if not raw_value:
            return ""

        decoded_chunks = []
        for value, encoding in decode_header(raw_value):
            if isinstance(value, bytes):
                decoded_chunks.append(value.decode(encoding or "utf-8", errors="replace"))
            else:
                decoded_chunks.append(value)
        return "".join(decoded_chunks)

    @staticmethod
    def _split_recipients(raw_value: Optional[str]) -> Optional[List[str]]:
        if not raw_value:
            return None
        recipients = [address.strip() for address in raw_value.split(",") if address.strip()]
        return recipients or None

    @staticmethod
    def _decode_payload(part: Message) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            raw_payload = part.get_payload()
            return raw_payload if isinstance(raw_payload, str) else ""
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, (bytes, bytearray)):
            return ""

        charset = part.get_content_charset() or "utf-8"
        return bytes(payload).decode(charset, errors="replace")

    def _extract_body(self, message: Message) -> str:
        if message.is_multipart():
            for part in message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition") or "")
                if content_type == "text/plain" and "attachment" not in content_disposition.lower():
                    return self._decode_payload(part)
            return ""

        return self._decode_payload(message)

    def _parse_message(self, message: Message) -> Email:
        return Email(
            id=self._decode_header_value(message.get("Message-ID")),
            subject=self._decode_header_value(message.get("Subject")),
            sender=self._decode_header_value(message.get("From", "")),
            to=self._split_recipients(message.get("To")),
            cc=self._split_recipients(message.get("Cc")),
            bcc=self._split_recipients(message.get("Bcc")),
            reply_to=self._split_recipients(message.get("Reply-To")),
            date=message.get("Date", ""),
            body=self._extract_body(message),
            headers=dict(message.items()),
        )

    def _fetch_email_batch(self, mail: imaplib.IMAP4_SSL, email_id: bytes) -> List[Email]:
        _, message_data = mail.fetch(email_id.decode("ascii"), "(RFC822)")
        parsed_messages: List[Email] = []

        for response_part in message_data:
            if not isinstance(response_part, tuple) or len(response_part) < 2:
                continue

            raw_message = response_part[1]
            if not isinstance(raw_message, (bytes, bytearray)):
                continue

            message = email.message_from_bytes(bytes(raw_message))
            parsed_messages.append(self._parse_message(message))

        return parsed_messages

    def fetch_emails(self, days: int) -> List[Email]:
        """
        Fetches emails from the inbox received within the last specified number of days.

        Args:
            days (int): The number of days to look back for emails.

        Returns:
            List[Email]: A list of Email objects containing the fetched emails.
        """

        if days < 1:
            raise ValueError("days must be greater than 0")

        mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        try:
            mail.login(self.imap_user, self.imap_pass)
            status, _ = mail.select("inbox")
            if status != "OK":
                raise RuntimeError("Unable to select inbox.")

            _, data = mail.search(None, self._build_since_query(days))
            email_ids = data[0].split() if data and data[0] else []

            emails: List[Email] = []
            for email_id in email_ids:
                emails.extend(self._fetch_email_batch(mail, email_id))

            return emails
        finally:
            mail.logout()
            