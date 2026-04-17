"""IMAP mailbox access helpers and email models."""

import datetime
import email
import imaplib
import ssl
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel

from baldwin.exceptions import EmailFetchError

DEFAULT_IMAP_FOLDER = "INBOX"


@dataclass(frozen=True)
class MailboxFolders:
    """Normalized ordered IMAP folder selection."""

    folders: tuple[str, ...]

    @classmethod
    def from_values(
        cls,
        values: Sequence[str] | None = None,
        *,
        default_values: Sequence[str] | None = None,
    ) -> "MailboxFolders":
        folders = cls._normalize(values)
        if not folders:
            folders = cls._normalize(default_values)
        if not folders:
            folders = (DEFAULT_IMAP_FOLDER,)
        return cls(folders=folders)

    @staticmethod
    def _normalize(values: Sequence[str] | None) -> tuple[str, ...]:
        if not values:
            return ()

        normalized: list[str] = []
        for value in values:
            for folder in value.split(","):
                candidate = folder.strip()
                if candidate and candidate not in normalized:
                    normalized.append(candidate)
        return tuple(normalized)

    def __str__(self) -> str:
        return ", ".join(self.folders)


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
    folder: Optional[str] = None


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

    def _parse_message(self, message: Message, folder: str) -> Email:
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
            folder=folder,
        )

    @staticmethod
    def _create_tls_context() -> ssl.SSLContext:
        tls_context = ssl.create_default_context()
        tls_context.check_hostname = True
        tls_context.verify_mode = ssl.CERT_REQUIRED
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        return tls_context

    def _connect_mailbox(self) -> imaplib.IMAP4:
        if self.imap_port == 993:
            return imaplib.IMAP4_SSL(self.imap_host, self.imap_port)

        mail = imaplib.IMAP4(self.imap_host, self.imap_port)
        mail.starttls(ssl_context=self._create_tls_context())
        return mail

    def _fetch_email_batch(self, mail: imaplib.IMAP4, email_id: bytes, folder: str) -> List[Email]:
        status, message_data = mail.fetch(email_id.decode("ascii"), "(BODY.PEEK[])")
        if status != "OK":
            raise EmailFetchError(
                f"Unable to fetch email payload for id={email_id.decode('ascii')} in folder '{folder}'."
            )

        parsed_messages: List[Email] = []

        for response_part in message_data:
            if not isinstance(response_part, tuple) or len(response_part) < 2:
                continue

            raw_message = response_part[1]
            if not isinstance(raw_message, (bytes, bytearray)):
                continue

            message = email.message_from_bytes(bytes(raw_message))
            parsed_messages.append(self._parse_message(message, folder))

        return parsed_messages

    def _fetch_folder_emails(
        self,
        mail: imaplib.IMAP4,
        folder: str,
        days: int,
    ) -> List[Email]:
        status, _ = mail.select(folder)
        if status != "OK":
            raise EmailFetchError(f"Unable to select IMAP folder '{folder}'.")

        status, data = mail.search(None, self._build_since_query(days))
        if status != "OK":
            raise EmailFetchError(f"Unable to search IMAP folder '{folder}'.")

        email_ids = data[0].split() if data and data[0] else []
        emails: List[Email] = []
        for email_id in email_ids:
            emails.extend(self._fetch_email_batch(mail, email_id, folder))
        return emails

    def fetch_emails(
        self,
        days: int,
        folders: MailboxFolders | Sequence[str] | None = None,
    ) -> List[Email]:
        """
        Fetches emails from one or more IMAP folders received within the last specified number of days.

        Args:
            days (int): The number of days to look back for emails.

        Returns:
            List[Email]: A list of Email objects containing the fetched emails.
        """

        if days < 1:
            raise ValueError("days must be greater than 0")

        folder_selection = folders if isinstance(folders, MailboxFolders) else MailboxFolders.from_values(folders)

        mail: imaplib.IMAP4 | None = None
        pending_error: BaseException | None = None
        try:
            mail = self._connect_mailbox()
            mail.login(self.imap_user, self.imap_pass)
            emails: List[Email] = []
            for folder in folder_selection.folders:
                emails.extend(self._fetch_folder_emails(mail, folder, days))

            return emails
        except EmailFetchError as exc:
            pending_error = exc
            raise
        except (imaplib.IMAP4.error, OSError) as exc:
            pending_error = exc
            raise EmailFetchError(f"Failed to fetch emails from IMAP folders: {folder_selection}.") from exc
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except (imaplib.IMAP4.error, OSError) as exc:
                    if pending_error is None:
                        raise EmailFetchError("Failed to close the IMAP mailbox session.") from exc
            