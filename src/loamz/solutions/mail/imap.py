"""Read a mailbox over IMAP, read-only.

    ImapConnector("imap.gmail.com", "me@gmail.com", password).fetch(since=yesterday, limit=100)

The connection opens the folder read-only and fetches with `BODY.PEEK`, so nothing is marked as read, moved or deleted. Use an
app password (Gmail, Outlook and iCloud all offer them). `message_to_record` turns raw RFC 822 bytes into a `Record` and is
usable on its own (an exported `.eml`, a different transport).
"""

from __future__ import annotations

import imaplib
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from loamz.solutions.kit.records import Record

MAX_TEXT = 20_000


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


def body_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        return ""
    text = html_to_text(content) if part.get_content_type() == "text/html" else content
    return text.strip()[:MAX_TEXT]


def when(message: EmailMessage) -> datetime:
    try:
        stamp = parsedate_to_datetime(message["Date"]) if message["Date"] else None
    except (TypeError, ValueError):
        stamp = None
    if stamp is None:
        return datetime.now(UTC)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def message_to_record(raw: bytes, source: str, uid: str) -> Record:
    message = message_from_bytes(raw, policy=policy.default)
    name, address = parseaddr(str(message["From"] or ""))
    recipients = [a for _, a in getaddresses([str(message["To"] or "")]) if a]
    copied = [a for _, a in getaddresses([str(message["Cc"] or "")]) if a]
    attachments = [p.get_filename() for p in message.iter_attachments() if p.get_filename()]
    return Record(
        id=f"{source}:{uid}",
        source=source,
        kind="email",
        title=str(message["Subject"] or "(no subject)").strip(),
        text=body_text(message),
        at=when(message),
        author=address.lower(),
        metadata={
            "from_name": name,
            "to": [a.lower() for a in recipients],
            "cc": [a.lower() for a in copied],
            "message_id": str(message["Message-ID"] or ""),
            "in_reply_to": str(message["In-Reply-To"] or ""),
            "list_unsubscribe": bool(message["List-Unsubscribe"]),
            "bulk": str(message["Precedence"] or "").lower() in ("bulk", "list", "junk")
            or bool(message["Auto-Submitted"] and str(message["Auto-Submitted"]).lower() != "no"),
            "attachments": attachments,
        },
    )


class ImapConnector:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        *,
        folder: str = "INBOX",
        port: int = 993,
        ssl: bool = True,
        factory: Callable[..., imaplib.IMAP4] | None = None,
    ) -> None:
        self.host, self.user, self.password, self.folder, self.port = (
            host,
            user,
            password,
            folder,
            port,
        )
        self.factory = factory or (imaplib.IMAP4_SSL if ssl else imaplib.IMAP4)
        self.name = f"imap:{user}"

    def fetch(self, since: datetime | None = None, limit: int = 200) -> Iterator[Record]:
        client = self.factory(self.host, self.port)
        try:
            client.login(self.user, self.password)
            client.select(f'"{self.folder}"', readonly=True)
            criteria = ["ALL"] if since is None else ["SINCE", since.strftime("%d-%b-%Y")]
            status, found = client.uid("SEARCH", None, *criteria)
            if status != "OK":
                raise RuntimeError("The mail server refused the search")
            uids = (found[0] or b"").split()[-limit:]
            for uid in reversed(uids):  # newest first
                status, data = client.uid("FETCH", uid, "(BODY.PEEK[])")
                if status != "OK" or not data or not isinstance(data[0], tuple):
                    continue
                yield message_to_record(data[0][1], f"{self.name}:{self.folder}", uid.decode())
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
