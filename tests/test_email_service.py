"""Unit tests for IMAP connection behavior."""

import unittest
from unittest.mock import Mock, patch

from baldwin.email import EmailService


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


if __name__ == "__main__":
    unittest.main()