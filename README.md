# SMTP 

Скрипт отправляет все изображения из указанного каталога на почту как вложения.

## Запуск

```bash
python smtp.py -s SERVER -t TO -f FROM -d DIRECTORY
```

## Параметры

```text
-h, --help          справка
--ssl              использовать защищенное соединение
-s, --server       SMTP-сервер в формате адрес[:порт]
-t, --to           адрес получателя
-f, --from         адрес отправителя
--subject          тема письма
--auth             использовать авторизацию
-v, --verbose      вывод SMTP-команд и ответов сервера
-d, --directory    каталог с изображениями
```

## Примеры запуска

Отправка без авторизации:

```bash
python smtp.py -s localhost:25 -t user@example.com -f test@example.com -d photos
```

Отправка через SMTP с авторизацией и SSL/STARTTLS:

```bash
python smtp.py -s smtp.gmail.com:587 -t receiver@example.com -f sender@gmail.com --auth --ssl -d photos
```

Запуск с выводом SMTP-диалога:

```bash
python smtp.py -s smtp.gmail.com:587 -t receiver@example.com -f sender@gmail.com --auth --ssl -v -d photos
```

Использование текущего каталога:

```bash
python smtp.py -s smtp.gmail.com:587 -t receiver@example.com -f sender@gmail.com --auth --ssl -d .
```

## Поддерживаемые изображения

```text
.jpg
.jpeg
.png
.gif
.bmp
.webp
```

## Примечание

Если используется Gmail, нужен пароль приложения.
