"""MCP tool registrations for Baldwin email workflows."""

from __future__ import annotations

from typing import Any

from function_app import HANDLERS, app


@app.mcp_tool()
def scan_mail(days: int = 1, folders: list[str] | None = None) -> dict[str, Any]:
    """Fetch recent IMAP emails from one or more folders and persist them to PostgreSQL."""
    return HANDLERS.scan_mail_payload(days, folders)


@app.mcp_tool()
def summarize_email(body: str) -> dict[str, str]:
    """Generate a concise local summary from an email body."""
    return HANDLERS.summarize_email_payload(body)


@app.mcp_tool()
def build_digest(summaries: list[Any], audience: str = "robert") -> str:
    """Combine multiple summaries into a Markdown digest."""
    return HANDLERS.build_digest_content(summaries, audience)


@app.mcp_tool()
def send_digest(to: str, subject: str, content: str) -> dict[str, str]:
    """Send a prepared digest email over SMTP."""
    return HANDLERS.send_digest_payload(to, subject, content)