"""IMAP mailbox access helpers and email models."""

import datetime
import email
import imaplib
import re
import ssl
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel

from baldwin.exceptions import EmailFetchError

DEFAULT_IMAP_FOLDER = "INBOX"
IMAP_TRANSPORT_ERROR = imaplib.IMAP4.error # pylint: disable=C0103
CLOSE_SESSION_ERROR_MESSAGE = "Failed to close the IMAP mailbox session."
_SYSTEM_IMAP_FLAGS = {
    "\\Answered",
    "\\Deleted",
    "\\Draft",
    "\\Flagged",
    "\\Recent",
    "\\Seen",
}


@dataclass(frozen=True)
class MailboxFolderStatus:
    """Current server-side IMAP state for a mailbox folder."""

    folder: str
    message_count: int
    uidvalidity: int
    uidnext: int | None
    uids: tuple[int, ...]


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
        """Create a MailboxFolders instance from raw input values, applying normalization and defaults."""
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
    imap_uid: Optional[int] = None
    imap_flags: Optional[List[str]] = None
    imap_keywords: Optional[List[str]] = None


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

    @staticmethod
    def _parse_int_bytes(raw_value: bytes | bytearray | memoryview | str | None) -> int | None:
        if raw_value is None:
            return None
        if isinstance(raw_value, memoryview):
            value = raw_value.tobytes().decode("ascii")
        elif isinstance(raw_value, bytearray):
            value = bytes(raw_value).decode("ascii")
        elif isinstance(raw_value, bytes):
            value = raw_value.decode("ascii")
        else:
            value = raw_value
        value = value.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _read_numeric_response(self, mail: imaplib.IMAP4, name: str) -> int | None:
        response = mail.response(name)
        if not isinstance(response, tuple) or len(response) < 2:
            return None

        values = response[1]
        if not isinstance(values, (list, tuple)):
            return None

        for raw_value in values:
            parsed_value = self._parse_int_bytes(raw_value)
            if parsed_value is not None:
                return parsed_value
        return None

    @staticmethod
    def _parse_uid_list(data: Sequence[bytes] | None) -> tuple[int, ...]:
        if not data or not data[0]:
            return ()

        raw_values = data[0].decode("ascii", errors="ignore").split()
        parsed_values: list[int] = []
        for raw_value in raw_values:
            try:
                parsed_values.append(int(raw_value))
            except ValueError:
                continue
        return tuple(parsed_values)

    @staticmethod
    def _deduplicate_ordered(values: Sequence[str]) -> list[str]:
        ordered: list[str] = []
        for value in values:
            if value and value not in ordered:
                ordered.append(value)
        return ordered

    @classmethod
    def _parse_imap_flags(
        cls,
        fetch_descriptor: bytes | bytearray | memoryview | str | None,
    ) -> tuple[list[str], list[str]]:
        if fetch_descriptor is None:
            return [], []

        if isinstance(fetch_descriptor, memoryview):
            descriptor = fetch_descriptor.tobytes().decode("ascii", errors="ignore")
        elif isinstance(fetch_descriptor, bytearray):
            descriptor = bytes(fetch_descriptor).decode("ascii", errors="ignore")
        elif isinstance(fetch_descriptor, bytes):
            descriptor = fetch_descriptor.decode("ascii", errors="ignore")
        else:
            descriptor = fetch_descriptor

        match = re.search(r"FLAGS \(([^)]*)\)", descriptor)
        if match is None:
            return [], []

        flags = cls._deduplicate_ordered(match.group(1).split())
        keywords = [flag for flag in flags if flag not in _SYSTEM_IMAP_FLAGS and not flag.startswith("\\")]
        return flags, keywords

    def _parse_message(self, message: Message, folder: str, imap_uid: int | None = None) -> Email:
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
            imap_uid=imap_uid,
            imap_flags=None,
            imap_keywords=None,
        )

    def _build_email(
        self,
        message: Message,
        folder: str,
        *,
        imap_uid: int | None = None,
        imap_flags: Sequence[str] | None = None,
        imap_keywords: Sequence[str] | None = None,
    ) -> Email:
        parsed_message = self._parse_message(message, folder, imap_uid=imap_uid)
        return parsed_message.model_copy(
            update={
                "imap_flags": list(imap_flags) if imap_flags else None,
                "imap_keywords": list(imap_keywords) if imap_keywords else None,
            }
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

    def _fetch_email_batch(
        self,
        mail: imaplib.IMAP4,
        email_id: bytes,
        folder: str,
        *,
        use_uid: bool = False,
    ) -> List[Email]:
        identifier = email_id.decode("ascii")
        if use_uid:
            status, message_data = mail.uid("fetch", identifier, "(FLAGS BODY.PEEK[])")
        else:
            status, message_data = mail.fetch(identifier, "(FLAGS BODY.PEEK[])")
        if status != "OK":
            raise EmailFetchError(
                f"Unable to fetch email payload for id={identifier} in folder '{folder}'."
            )

        parsed_messages: List[Email] = []

        for response_part in message_data:
            if not isinstance(response_part, tuple) or len(response_part) < 2:
                continue

            raw_message = response_part[1]
            if not isinstance(raw_message, (bytes, bytearray)):
                continue

            message = email.message_from_bytes(bytes(raw_message))
            imap_flags, imap_keywords = self._parse_imap_flags(response_part[0])
            parsed_messages.append(
                self._build_email(
                    message,
                    folder,
                    imap_uid=self._parse_int_bytes(identifier) if use_uid else None,
                    imap_flags=imap_flags,
                    imap_keywords=imap_keywords,
                )
            )

        return parsed_messages

    def _select_folder_status(self, mail: imaplib.IMAP4, folder: str) -> MailboxFolderStatus:
        status, data = mail.select(folder)
        if status != "OK":
            raise EmailFetchError(f"Unable to select IMAP folder '{folder}'.")

        status, uid_data = mail.uid("search", "UTF-8", "ALL")
        if status != "OK":
            raise EmailFetchError(f"Unable to enumerate IMAP UIDs for folder '{folder}'.")

        message_count = self._parse_int_bytes(data[0] if data else None) or 0
        return MailboxFolderStatus(
            folder=folder,
            message_count=message_count,
            uidvalidity=self._read_numeric_response(mail, "UIDVALIDITY") or 0,
            uidnext=self._read_numeric_response(mail, "UIDNEXT"),
            uids=self._parse_uid_list(uid_data),
        )

    def _fetch_folder_emails(
        self,
        mail: imaplib.IMAP4,
        folder: str,
        days: int,
    ) -> List[Email]:
        self._select_folder_status(mail, folder)

        status, data = mail.search(None, self._build_since_query(days))
        if status != "OK":
            raise EmailFetchError(f"Unable to search IMAP folder '{folder}'.")

        email_ids = data[0].split() if data and data[0] else []
        emails: List[Email] = []
        for email_id in email_ids:
            emails.extend(self._fetch_email_batch(mail, email_id, folder))
        return emails

    def _fetch_folder_emails_by_uid_range(
        self,
        mail: imaplib.IMAP4,
        folder: str,
        start_uid: int,
        end_uid: int | None = None,
    ) -> List[Email]:
        if start_uid < 1:
            raise ValueError("start_uid must be greater than 0")

        self._select_folder_status(mail, folder)
        uid_range = f"{start_uid}:{end_uid or '*'}"
        status, data = mail.uid("search", "UTF-8", f"UID {uid_range}")
        if status != "OK":
            raise EmailFetchError(
                f"Unable to search IMAP UIDs in folder '{folder}' for range {uid_range}."
            )

        emails: List[Email] = []
        for email_uid in self._parse_uid_list(data):
            emails.extend(
                self._fetch_email_batch(
                    mail,
                    str(email_uid).encode("ascii"),
                    folder,
                    use_uid=True,
                )
            )
        return emails

    def get_folder_status(self, folder: str) -> MailboxFolderStatus:
        """Return IMAP state metadata for a single folder."""
        mail: imaplib.IMAP4 | None = None
        pending_error: BaseException | None = None
        try:
            mail = self._connect_mailbox()
            mail.login(self.imap_user, self.imap_pass)
            return self._select_folder_status(mail, folder)
        except EmailFetchError as exc:
            pending_error = exc
            raise
        except (IMAP_TRANSPORT_ERROR, OSError) as exc:
            pending_error = exc
            raise EmailFetchError(f"Failed to inspect IMAP folder state: {folder}.") from exc
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except (IMAP_TRANSPORT_ERROR, OSError) as exc:
                    if pending_error is None:
                        raise EmailFetchError(CLOSE_SESSION_ERROR_MESSAGE) from exc

    def fetch_emails_by_uid_range(
        self,
        folder: str,
        start_uid: int,
        end_uid: int | None = None,
    ) -> List[Email]:
        """Fetch emails by IMAP UID range within a single folder."""
        mail: imaplib.IMAP4 | None = None
        pending_error: BaseException | None = None
        try:
            mail = self._connect_mailbox()
            mail.login(self.imap_user, self.imap_pass)
            return self._fetch_folder_emails_by_uid_range(mail, folder, start_uid, end_uid)
        except EmailFetchError as exc:
            pending_error = exc
            raise
        except (IMAP_TRANSPORT_ERROR, OSError) as exc:
            pending_error = exc
            raise EmailFetchError(
                f"Failed to fetch IMAP UIDs from folder '{folder}' starting at {start_uid}."
            ) from exc
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except (IMAP_TRANSPORT_ERROR, OSError) as exc:
                    if pending_error is None:
                        raise EmailFetchError(CLOSE_SESSION_ERROR_MESSAGE) from exc

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
        except (IMAP_TRANSPORT_ERROR, OSError) as exc:
            pending_error = exc
            raise EmailFetchError(f"Failed to fetch emails from IMAP folders: {folder_selection}.") from exc
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except (IMAP_TRANSPORT_ERROR, OSError) as exc:
                    if pending_error is None:
                        raise EmailFetchError(CLOSE_SESSION_ERROR_MESSAGE) from exc
