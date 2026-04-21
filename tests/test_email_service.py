"""Unit tests for IMAP connection behavior and folder state inspection."""

import imaplib
from email.header import Header
from email.message import EmailMessage
import unittest
from unittest.mock import Mock, patch

from baldwin.email import (
    DEFAULT_IMAP_FOLDER,
    EmailFetchError,
    EmailService,
    MailboxFolders,
)


class EmailServiceConnectionTests(unittest.TestCase):
    """Tests for selecting SSL or STARTTLS based on IMAP port."""

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_connect_mailbox_uses_ssl_for_port_993(self, imap4_ssl: Mock) -> None:
        """Port 993 should use implicit SSL."""
        mail = Mock()
        mail.select.return_value = ("OK", [b"0"])
        mail.search.return_value = ("OK", [b""])
        mail.uid.return_value = ("OK", [b""])
        mail.response.side_effect = [
            (b"UIDVALIDITY", [b"999"]),
            (b"UIDNEXT", [b"1"]),
        ]
        imap4_ssl.return_value = mail
        service = EmailService(
            "user@example.com",
            "password",
            imap_host="imap.example.com",
            imap_port=993,
        )

        result = service.fetch_emails(1)

        imap4_ssl.assert_called_once_with("imap.example.com", 993)
        mail.login.assert_called_once_with("user@example.com", "password")
        mail.logout.assert_called_once_with()
        self.assertEqual(result, [])

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_fetch_emails_wraps_imap_errors_with_causality(self, imap4_ssl: Mock) -> None:
        """IMAP failures should be translated into the shared email fetch error."""
        mail = Mock()
        mail.login.side_effect = imaplib.IMAP4.error("invalid credentials")
        imap4_ssl.return_value = mail
        service = EmailService(
            "user@example.com",
            "password",
            imap_host="imap.example.com",
            imap_port=993,
        )

        with self.assertRaises(EmailFetchError) as captured:
            service.fetch_emails(1)

        self.assertIsInstance(captured.exception.__cause__, imaplib.IMAP4.error)
        mail.logout.assert_called_once_with()

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_fetch_emails_raises_domain_error_for_inbox_selection_failures(self, imap4_ssl: Mock) -> None:
        """Folder selection failures should raise a semantic email fetch error."""
        mail = Mock()
        mail.select.return_value = ("NO", [b""])
        imap4_ssl.return_value = mail
        service = EmailService(
            "user@example.com",
            "password",
            imap_host="imap.example.com",
            imap_port=993,
        )

        with self.assertRaises(EmailFetchError) as captured:
            service.fetch_emails(1)

        self.assertEqual(str(captured.exception), f"Unable to select IMAP folder '{DEFAULT_IMAP_FOLDER}'.")
        self.assertIsNone(captured.exception.__cause__)
        mail.logout.assert_called_once_with()

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_fetch_emails_aggregates_requested_folders_in_order(self, imap4_ssl: Mock) -> None:
        """The service should select and aggregate messages from each requested folder in order."""
        first_message = EmailMessage()
        first_message["Subject"] = "Inbox subject"
        first_message["From"] = "sender@example.com"
        first_message["Date"] = "Fri, 11 Apr 2026 09:15:00 +0000"
        first_message.set_content("Inbox body")

        second_message = EmailMessage()
        second_message["Subject"] = "Archive subject"
        second_message["From"] = "sender@example.com"
        second_message["Date"] = "Fri, 11 Apr 2026 09:15:00 +0000"
        second_message.set_content("Archive body")

        mail = Mock()
        mail.select.side_effect = [("OK", [b"1"]), ("OK", [b"1"])]
        mail.search.side_effect = [("OK", [b"1"]), ("OK", [b"2"])]
        mail.uid.side_effect = [
            ("OK", [b"101"]),
            ("OK", [b"202"]),
        ]
        mail.response.side_effect = [
            (b"UIDVALIDITY", [b"999"]),
            (b"UIDNEXT", [b"102"]),
            (b"UIDVALIDITY", [b"1000"]),
            (b"UIDNEXT", [b"203"]),
        ]
        mail.fetch.side_effect = [
            ("OK", [(b"1", first_message.as_bytes())]),
            ("OK", [(b"2", second_message.as_bytes())]),
        ]
        imap4_ssl.return_value = mail
        service = EmailService(
            "user@example.com",
            "password",
            imap_host="imap.example.com",
            imap_port=993,
        )

        result = service.fetch_emails(1, MailboxFolders.from_values(["INBOX", "Archive"]))

        self.assertEqual([email_message.folder for email_message in result], ["INBOX", "Archive"])
        self.assertEqual([email_message.subject for email_message in result], ["Inbox subject", "Archive subject"])
        self.assertEqual(mail.select.call_args_list[0].args[0], "INBOX")
        self.assertEqual(mail.select.call_args_list[1].args[0], "Archive")
        mail.logout.assert_called_once_with()

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_fetch_emails_includes_imap_flags_and_keywords(self, imap4_ssl: Mock) -> None:
        """Body fetches should preserve IMAP system flags and custom keywords on parsed emails."""
        message = EmailMessage()
        message["Message-ID"] = "<message-1@example.com>"
        message["Subject"] = "Inbox subject"
        message["From"] = "sender@example.com"
        message["Date"] = "Fri, 11 Apr 2026 09:15:00 +0000"
        message.set_content("Inbox body")

        mail = Mock()
        mail.select.return_value = ("OK", [b"1"])
        mail.search.return_value = ("OK", [b"1"])
        mail.uid.return_value = ("OK", [b"101"])
        mail.response.side_effect = [
            (b"UIDVALIDITY", [b"999"]),
            (b"UIDNEXT", [b"102"]),
        ]
        mail.fetch.return_value = (
            "OK",
            [
                (
                    b"1 (FLAGS (\\Seen \\Flagged custom-tag) BODY[] {123}",
                    message.as_bytes(),
                )
            ],
        )
        imap4_ssl.return_value = mail
        service = EmailService("user@example.com", "password")

        result = service.fetch_emails(1, MailboxFolders.from_values(["INBOX"]))

        self.assertEqual(result[0].imap_flags, ["\\Seen", "\\Flagged", "custom-tag"])
        self.assertEqual(result[0].imap_keywords, ["custom-tag"])

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_get_folder_status_returns_uid_state(self, imap4_ssl: Mock) -> None:
        """Folder inspection should expose UIDVALIDITY, UIDNEXT, and current UID membership."""
        mail = Mock()
        mail.select.return_value = ("OK", [b"2"])
        mail.uid.return_value = ("OK", [b"101 102"])
        mail.response.side_effect = [
            (b"UIDVALIDITY", [b"999"]),
            (b"UIDNEXT", [b"103"]),
        ]
        imap4_ssl.return_value = mail
        service = EmailService("user@example.com", "password")

        status = service.get_folder_status("INBOX")

        self.assertEqual(status.folder, "INBOX")
        self.assertEqual(status.message_count, 2)
        self.assertEqual(status.uidvalidity, 999)
        self.assertEqual(status.uidnext, 103)
        self.assertEqual(status.uids, (101, 102))
        self.assertEqual(mail.uid.call_args.args, ("search", "ALL"))

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_fetch_emails_by_uid_range_sets_imap_uid_on_messages(self, imap4_ssl: Mock) -> None:
        """UID-based fetch should preserve the server UID on parsed email payloads."""
        message = EmailMessage()
        message["Message-ID"] = "<message-1@example.com>"
        message["Subject"] = "Inbox subject"
        message["From"] = "sender@example.com"
        message["Date"] = "Fri, 11 Apr 2026 09:15:00 +0000"
        message.set_content("Inbox body")

        mail = Mock()
        mail.select.return_value = ("OK", [b"1"])
        mail.uid.side_effect = [
            ("OK", [b"101"]),
            ("OK", [b"101"]),
            ("OK", [(b"101", message.as_bytes())]),
        ]
        mail.response.side_effect = [
            (b"UIDVALIDITY", [b"999"]),
            (b"UIDNEXT", [b"102"]),
        ]
        imap4_ssl.return_value = mail
        service = EmailService("user@example.com", "password")

        result = service.fetch_emails_by_uid_range("INBOX", 101, 101)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].imap_uid, 101)
        self.assertEqual(result[0].folder, "INBOX")
        self.assertEqual(mail.uid.call_args_list[1].args, ("search", "UID 101:101"))

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_fetch_emails_by_uid_range_includes_imap_flags_and_keywords(self, imap4_ssl: Mock) -> None:
        """UID-based fetches should preserve IMAP flags and derived keywords."""
        message = EmailMessage()
        message["Message-ID"] = "<message-1@example.com>"
        message["Subject"] = "Inbox subject"
        message["From"] = "sender@example.com"
        message["Date"] = "Fri, 11 Apr 2026 09:15:00 +0000"
        message.set_content("Inbox body")

        mail = Mock()
        mail.select.return_value = ("OK", [b"1"])
        mail.uid.side_effect = [
            ("OK", [b"101"]),
            ("OK", [b"101"]),
            (
                "OK",
                [
                    (
                        b"101 (FLAGS (\\Seen project-x) BODY[] {123}",
                        message.as_bytes(),
                    )
                ],
            ),
        ]
        mail.response.side_effect = [
            (b"UIDVALIDITY", [b"999"]),
            (b"UIDNEXT", [b"102"]),
        ]
        imap4_ssl.return_value = mail
        service = EmailService("user@example.com", "password")

        result = service.fetch_emails_by_uid_range("INBOX", 101, 101)

        self.assertEqual(result[0].imap_flags, ["\\Seen", "project-x"])
        self.assertEqual(result[0].imap_keywords, ["project-x"])

    def test_split_recipients_accepts_header_objects(self) -> None:
        """Recipient parsing should handle email.header.Header values returned by the stdlib parser."""
        recipients = EmailService._split_recipients(Header("reply@example.com, second@example.com", "utf-8"))

        self.assertEqual(recipients, ["reply@example.com", "second@example.com"])

    def test_parse_message_normalizes_header_objects_in_headers_map(self) -> None:
        """Stored header metadata should coerce Header objects into plain strings for the Email model."""
        message = EmailMessage()
        message["Message-ID"] = "<message-1@example.com>"
        message["Subject"] = "Inbox subject"
        message["From"] = "sender@example.com"
        message["Date"] = "Fri, 11 Apr 2026 09:15:00 +0000"
        message["Reply-To"] = Header("reply@example.com", "utf-8")
        message.set_content("Inbox body")

        parsed = EmailService("user@example.com", "password")._parse_message(message, "INBOX")

        self.assertEqual(parsed.reply_to, ["reply@example.com"])
        self.assertEqual(parsed.headers["Reply-To"], "reply@example.com")

    @patch("baldwin.email.email_service.EmailService._create_tls_context")
    @patch("baldwin.email.email_service.imaplib.IMAP4")
    def test_connect_mailbox_uses_starttls_for_non_ssl_ports(
        self,
        imap4: Mock,
        create_tls_context: Mock,
    ) -> None:
        """Non-993 ports should use STARTTLS on a plain IMAP connection."""
        context = object()
        create_tls_context.return_value = context
        mail = Mock()
        mail.select.return_value = ("OK", [b"0"])
        mail.search.return_value = ("OK", [b""])
        mail.uid.return_value = ("OK", [b""])
        mail.response.side_effect = [
            (b"UIDVALIDITY", [b"999"]),
            (b"UIDNEXT", [b"1"]),
        ]
        imap4.return_value = mail

        service = EmailService(
            "user@example.com",
            "password",
            imap_host="imap.example.com",
            imap_port=143,
        )

        result = service.fetch_emails(1)

        imap4.assert_called_once_with("imap.example.com", 143)
        create_tls_context.assert_called_once_with()
        mail.starttls.assert_called_once_with(ssl_context=context)
        mail.login.assert_called_once_with("user@example.com", "password")
        mail.logout.assert_called_once_with()
        self.assertEqual(result, [])


class MailboxFoldersTests(unittest.TestCase):
    """Tests for normalized IMAP folder selection."""

    def test_from_values_supports_commas_and_deduplicates_preserving_order(self) -> None:
        """The from_values factory should parse comma-separated folder names, deduplicate them, and preserve order."""
        folders = MailboxFolders.from_values(["INBOX, Archive", "Receipts", "Archive"])

        self.assertEqual(folders.folders, ("INBOX", "Archive", "Receipts"))


if __name__ == "__main__":
    unittest.main()
