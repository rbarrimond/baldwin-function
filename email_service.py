"""
This module provides functionality to interact with an IMAP email server.

It includes:
- A Pydantic model `Email` to represent email details.
- A service class `EmailService` to fetch emails from the inbox based on a specified time range.
"""

import imaplib
import email
from email.header import decode_header
from typing import List, Dict, Optional  # Updated to include Optional for nullable fields
from pydantic import BaseModel
import datetime

class Email(BaseModel):
    """
    Represents an email with its subject, sender, recipients, date, body, and headers.
    """
    subject: str
    sender: str
    to: Optional[List[str]]  # List of primary recipients
    cc: Optional[List[str]]  # List of CC recipients
    bcc: Optional[List[str]]  # List of BCC recipients
    date: str
    body: str
    headers: Dict[str, str]  # Email metadata

class EmailService:
    """
    A service for interacting with an IMAP email server to fetch emails.
    """

    def __init__(self, imap_user: str, imap_pass: str):
        """
        Initializes the EmailService with user credentials.

        Args:
            imap_user (str): The IMAP username.
            imap_pass (str): The IMAP password.
        """
        self.imap_host = 'imap.mail.me.com'
        self.imap_user = imap_user
        self.imap_pass = imap_pass

    def fetch_emails(self, days: int) -> List[Email]:
        """
        Fetches emails from the inbox received within the last specified number of days.

        Args:
            days (int): The number of days to look back for emails.

        Returns:
            List[Email]: A list of Email objects containing the fetched emails.
        """
        mail = imaplib.IMAP4_SSL(self.imap_host)
        mail.login(self.imap_user, self.imap_pass)
        mail.select("inbox")

        result, data = mail.search(None, '(SINCE "{}")'.format(
            (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
        ))

        email_ids = data[0].split()
        emails = []

        for email_id in email_ids:
            result, msg_data = mail.fetch(email_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8")
                    from_ = msg.get("From")
                    to_ = msg.get("To")
                    cc_ = msg.get("Cc")
                    bcc_ = msg.get("Bcc")
                    date_ = msg.get("Date")
                    body = ""
                    headers = dict(msg.items())  # Extract all headers as a dictionary
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))
                            if content_type == "text/plain" and "attachment" not in content_disposition:
                                body = part.get_payload(decode=True).decode()
                                break
                    else:
                        body = msg.get_payload(decode=True).decode()

                    emails.append(Email(
                        subject=subject,
                        sender=from_,
                        to=[addr.strip() for addr in to_.split(",")] if to_ else None,
                        cc=[addr.strip() for addr in cc_.split(",")] if cc_ else None,
                        bcc=[addr.strip() for addr in bcc_.split(",")] if bcc_ else None,
                        date=date_,
                        body=body,
                        headers=headers
                    ))

        mail.logout()
        return emails