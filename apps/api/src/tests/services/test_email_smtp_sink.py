import socketserver
import threading
from types import SimpleNamespace
from unittest.mock import patch

from src.services.email.utils import send_email


class _SMTPSinkHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 synthetic-smtp ESMTP\r\n")
        in_data = False
        message_lines: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                break
            command = line.decode("utf-8", errors="replace").strip()
            if in_data:
                if command == ".":
                    self.server.messages.append(b"".join(message_lines))  # type: ignore[attr-defined]
                    message_lines = []
                    in_data = False
                    self.wfile.write(b"250 queued\r\n")
                else:
                    message_lines.append(line)
                continue
            verb = command.split(" ", 1)[0].upper()
            if verb in {"EHLO", "HELO"}:
                self.wfile.write(b"250-synthetic-smtp\r\n250 OK\r\n")
            elif verb in {"MAIL", "RCPT", "RSET", "NOOP"}:
                self.wfile.write(b"250 OK\r\n")
            elif verb == "DATA":
                in_data = True
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif verb == "QUIT":
                self.wfile.write(b"221 Bye\r\n")
                break
            else:
                self.wfile.write(b"502 Unsupported\r\n")


class _SMTPSinkServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address):
        super().__init__(server_address, _SMTPSinkHandler)
        self.messages: list[bytes] = []


def test_send_email_to_local_synthetic_smtp_sink():
    server = _SMTPSinkServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        mailing = SimpleNamespace(
            email_provider="smtp",
            system_email_address="system@example.test",
            resend_api_key=None,
            smtp_host="127.0.0.1",
            smtp_port=server.server_address[1],
            smtp_username="",
            smtp_password="",
            smtp_use_tls=False,
        )
        with patch(
            "src.services.email.utils.get_learnhouse_config",
            return_value=SimpleNamespace(mailing_config=mailing),
        ):
            result = send_email(
                "synthetic-recipient@example.test",
                "合成通知測試",
                "<p>這是本機 SMTP sink，不會寄送到真實郵箱。</p>",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result == {"id": None, "to": "synthetic-recipient@example.test"}
    assert len(server.messages) == 1
    message = server.messages[0].decode("utf-8", errors="replace")
    assert "synthetic-recipient@example.test" in message
    assert "Content-Type: text/html" in message
