"""Unit tests for IMAP connection behavior."""

import imaplib
from email.message import EmailMessage
import unittest
from unittest.mock import Mock, patch

from baldwin.email import DEFAULT_IMAP_FOLDER, EmailFetchError, EmailService, MailboxFolders


class EmailServiceConnectionTests(unittest.TestCase):
    """Tests for selecting SSL or STARTTLS based on IMAP port."""

    @patch("baldwin.email.email_service.imaplib.IMAP4_SSL")
    def test_connect_mailbox_uses_ssl_for_port_993(self, imap4_ssl: Mock) -> None:
        """Port 993 should use implicit SSL."""
        mail = Mock()
        mail.select.return_value = ("OK", [b""])
        mail.search.return_value = ("OK", [b""])
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
        mail.select.side_effect = [("OK", [b""]), ("OK", [b""])]
        mail.search.side_effect = [("OK", [b"1"]), ("OK", [b"2"])]
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
        mail.select.return_value = ("OK", [b""])
        mail.search.return_value = ("OK", [b""])
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
        folders = MailboxFolders.from_values(["INBOX, Archive", "Receipts", "Archive"])

        self.assertEqual(folders.folders, ("INBOX", "Archive", "Receipts"))


if __name__ == "__main__":
    unittest.main()
    