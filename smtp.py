import argparse
import base64
import getpass
import mimetypes
import os
import socket
import ssl
import sys
from email.message import EmailMessage
from email.policy import SMTP

CRLF = b"\r\n"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


class SmtpError(Exception):
    pass


class SmtpClient:
    def __init__(self, host, port, allow_ssl=False, verbose=False, timeout=20):
        self.host = host
        self.port = port
        self.allow_ssl = allow_ssl
        self.verbose = verbose
        self.timeout = timeout
        self.sock = None
        self.file = None
        self.extensions = {}

    def _log(self, prefix, text):
        if self.verbose:
            print(f"{prefix} {text}")

    def connect(self):
        try:
            raw_socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise SmtpError(f"Не удалось подключиться к серверу: {exc}")

        if self.allow_ssl and self.port == 465:
            context = ssl.create_default_context()
            self.sock = context.wrap_socket(raw_socket, server_hostname=self.host)
        else:
            self.sock = raw_socket

        self.file = self.sock.makefile("rb")
        code, _ = self.read_response()
        if code != 220:
            raise SmtpError(f"Сервер не готов к работе, код ответа: {code}")

    def read_response(self):
        lines = []
        while True:
            raw_line = self.file.readline()
            if not raw_line:
                raise SmtpError("Сервер закрыл соединение")
            try:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            except UnicodeDecodeError:
                line = raw_line.decode(errors="replace").rstrip("\r\n")

            self._log("<<<", line)
            lines.append(line)

            if len(line) >= 4 and line[3] == " ":
                break
            if len(line) < 4:
                break

        try:
            code = int(lines[-1][:3])
        except ValueError:
            raise SmtpError("Некорректный ответ SMTP-сервера")

        return code, lines

    def send_line(self, line, hidden_text=None):
        self._log(">>>", hidden_text if hidden_text is not None else line)
        self.sock.sendall(line.encode("ascii") + CRLF)
        return self.read_response()

    def ehlo(self):
        code, lines = self.send_line("EHLO localhost")
        if code != 250:
            code, _ = self.send_line("HELO localhost")
            if code != 250:
                raise SmtpError(f"EHLO/HELO отклонён, код ответа: {code}")
            self.extensions = {}
            return

        self.extensions = self.parse_extensions(lines)

    @staticmethod
    def parse_extensions(lines):
        result = {}
        for line in lines[1:]:
            if len(line) <= 4:
                continue
            value = line[4:].strip()
            if not value:
                continue
            parts = value.split(maxsplit=1)
            name = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""
            result[name] = args
        return result

    def starttls_if_available(self):
        if not self.allow_ssl:
            return
        if isinstance(self.sock, ssl.SSLSocket):
            return
        if "STARTTLS" not in self.extensions:
            return

        code, _ = self.send_line("STARTTLS")
        if code != 220:
            raise SmtpError(f"STARTTLS отклонён, код ответа: {code}")

        context = ssl.create_default_context()
        self.sock = context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("rb")
        self.ehlo()

    def login(self, username, password):
        auth_line = self.extensions.get("AUTH", "")
        if "LOGIN" not in auth_line.upper():
            raise SmtpError("Сервер не поддерживает AUTH LOGIN")

        code, _ = self.send_line("AUTH LOGIN")
        if code != 334:
            raise SmtpError(f"AUTH LOGIN отклонён, код ответа: {code}")

        encoded_login = base64.b64encode(username.encode()).decode("ascii")
        code, _ = self.send_line(encoded_login, hidden_text="<login>")
        if code != 334:
            raise SmtpError(f"Логин отклонён, код ответа: {code}")

        encoded_password = base64.b64encode(password.encode()).decode("ascii")
        code, _ = self.send_line(encoded_password, hidden_text="<password hidden>")
        if code != 235:
            raise SmtpError(f"Авторизация не выполнена, код ответа: {code}")

    def send_mail(self, sender, recipient, message_bytes):
        if not message_bytes.endswith(CRLF):
            message_bytes += CRLF

        stuffed_message = dot_stuff(message_bytes)
        size_argument = ""
        if "SIZE" in self.extensions:
            size_argument = f" SIZE={len(stuffed_message)}"

        commands = [
            f"MAIL FROM:<{sender}>{size_argument}",
            f"RCPT TO:<{recipient}>",
            "DATA",
        ]

        if "PIPELINING" in self.extensions:
            for command in commands:
                self._log(">>>", command)
            self.sock.sendall(CRLF.join(command.encode("ascii") for command in commands) + CRLF)

            mail_code, _ = self.read_response()
            if mail_code != 250:
                raise SmtpError(f"MAIL FROM отклонён, код ответа: {mail_code}")

            rcpt_code, _ = self.read_response()
            if rcpt_code not in (250, 251):
                raise SmtpError(f"RCPT TO отклонён, код ответа: {rcpt_code}")

            data_code, _ = self.read_response()
            if data_code != 354:
                raise SmtpError(f"DATA отклонён, код ответа: {data_code}")
        else:
            code, _ = self.send_line(commands[0])
            if code != 250:
                raise SmtpError(f"MAIL FROM отклонён, код ответа: {code}")

            code, _ = self.send_line(commands[1])
            if code not in (250, 251):
                raise SmtpError(f"RCPT TO отклонён, код ответа: {code}")

            code, _ = self.send_line(commands[2])
            if code != 354:
                raise SmtpError(f"DATA отклонён, код ответа: {code}")

        self._log(">>>", "<message body hidden>")
        self.sock.sendall(stuffed_message + b".\r\n")
        code, _ = self.read_response()
        if code != 250:
            raise SmtpError(f"Письмо не принято сервером, код ответа: {code}")

    def quit(self):
        if self.sock is None:
            return
        try:
            self.send_line("QUIT")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def dot_stuff(data):
    if data.startswith(b"."):
        data = b"." + data
    return data.replace(b"\r\n.", b"\r\n..")


def split_server(value):
    if value.startswith("[") and "]" in value:
        host, _, tail = value[1:].partition("]")
        if tail.startswith(":") and tail[1:]:
            return host, int(tail[1:])
        return host, 25

    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            return host, int(port)

    return value, 25


def find_images(directory):
    result = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        extension = os.path.splitext(name)[1].lower()
        if extension in IMAGE_EXTENSIONS:
            result.append(path)
    return result


def build_message(sender, recipient, subject, image_paths):
    msg = EmailMessage(policy=SMTP)
    msg["From"] = sender if sender else "<>"
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content("Happy Pictures!\r\n")

    for path in image_paths:
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type:
            mime_type = "application/octet-stream"
        maintype, subtype = mime_type.split("/", 1)
        filename = os.path.basename(path)

        with open(path, "rb") as file:
            msg.add_attachment(
                file.read(),
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )

    return msg.as_bytes(policy=SMTP)


def create_parser():
    parser = argparse.ArgumentParser(description="Send all images from a directory by SMTP.")
    parser.add_argument("--ssl", action="store_true", help="allow SSL/STARTTLS if the server supports it")
    parser.add_argument("-s", "--server", required=True, help="SMTP server in host[:port] format, default port is 25")
    parser.add_argument("-t", "--to", required=True, help="recipient email address")
    parser.add_argument("-f", "--from", dest="from_addr", default="", help="sender email address, default is <>")
    parser.add_argument("--subject", default="Happy Pictures", help="email subject")
    parser.add_argument("--auth", action="store_true", help="ask for SMTP login and password after start")
    parser.add_argument("-v", "--verbose", action="store_true", help="show SMTP commands and responses without message body")
    parser.add_argument("-d", "--directory", default=os.getcwd(), help="directory with images, default is current directory")
    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()

    directory = os.path.abspath(args.directory)
    if not os.path.isdir(directory):
        print("Ошибка: указанный каталог не найден.", file=sys.stderr)
        return 1

    images = find_images(directory)
    if not images:
        print("Ошибка: в указанном каталоге нет изображений.", file=sys.stderr)
        return 1

    try:
        host, port = split_server(args.server)
    except ValueError:
        print("Ошибка: порт SMTP-сервера должен быть числом.", file=sys.stderr)
        return 1

    username = password = None
    if args.auth:
        username = input("Логин: ")
        password = getpass.getpass("Пароль: ")

    message = build_message(args.from_addr, args.to, args.subject, images)
    client = SmtpClient(host, port, allow_ssl=args.ssl, verbose=args.verbose)

    try:
        client.connect()
        client.ehlo()
        client.starttls_if_available()
        if args.auth:
            client.login(username, password)
        client.send_mail(args.from_addr, args.to, message)
        client.quit()
    except PermissionError:
        print("Ошибка: не хватает прав для работы с сокетом. Попробуйте запустить программу с правами администратора.", file=sys.stderr)
        return 1
    except (OSError, ssl.SSLError, SmtpError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        try:
            client.quit()
        except Exception:
            pass
        return 1

    print("Готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
