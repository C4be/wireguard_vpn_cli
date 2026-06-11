from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from .remote import Local
from .wireguard import (
    ServerOptions,
    add_peer,
    diagnose,
    ensure_server,
    export_peer,
    list_peers,
    peer_status,
    repair_vpn,
    remove_peer,
    restart_wireguard,
    validate_peer_name,
)


class BotConfigError(RuntimeError):
    pass


def _load_aiogram():
    try:
        from aiogram import Bot, Dispatcher, F, Router
        from aiogram.filters import Command, CommandObject
        from aiogram.types import (
            BufferedInputFile,
            FSInputFile,
            KeyboardButton,
            Message,
            ReplyKeyboardMarkup,
        )
    except ImportError as exc:
        raise BotConfigError(
            "aiogram is not installed. Install bot dependencies with: "
            "python3 -m pip install -e '.[bot]'"
        ) from exc
    return (
        Bot,
        Dispatcher,
        F,
        Router,
        Command,
        CommandObject,
        BufferedInputFile,
        FSInputFile,
        KeyboardButton,
        Message,
        ReplyKeyboardMarkup,
    )


def _env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BotConfigError(f"Set {name} in the environment.")
    return value


def _admin_chat_ids() -> set[int]:
    raw = os.environ.get("VPNCTL_ADMIN_CHAT_IDS", "")
    chat_ids: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if item:
            chat_ids.add(int(item))
    return chat_ids


def run_bot() -> None:
    asyncio.run(_run_bot())


async def _run_bot() -> None:
    (
        Bot,
        Dispatcher,
        F,
        Router,
        Command,
        CommandObject,
        BufferedInputFile,
        FSInputFile,
        KeyboardButton,
        Message,
        ReplyKeyboardMarkup,
    ) = _load_aiogram()

    token = _env_required("VPNCTL_BOT_TOKEN")
    admin_password = _env_required("VPNCTL_ADMIN_PASSWORD")
    endpoint = os.environ.get("VPNCTL_ENDPOINT", "").strip()
    ssh_port = int(os.environ.get("VPNCTL_SSH_PORT", "22"))
    listen_port = int(os.environ.get("VPNCTL_LISTEN_PORT", "443"))
    network = os.environ.get("VPNCTL_NETWORK", "10.66.66.0/24")
    dns = os.environ.get("VPNCTL_DNS", "1.1.1.1, 8.8.8.8")
    mtu = int(os.environ.get("VPNCTL_MTU", "1280"))

    remote = Local()
    router = Router()
    admin_chats = _admin_chat_ids()
    pending_actions: dict[int, str] = {}

    user_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Получить конфиг")],
            [KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
    )
    admin_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Получить конфиг"),
                KeyboardButton(text="Добавить устройство"),
            ],
            [KeyboardButton(text="Список устройств"), KeyboardButton(text="Статус VPN")],
            [
                KeyboardButton(text="Починить VPN"),
                KeyboardButton(text="Перезапустить VPN"),
            ],
            [KeyboardButton(text="Диагностика"), KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
    )

    def is_admin(message: Message) -> bool:
        return message.chat.id in admin_chats

    async def require_admin(message: Message) -> bool:
        if is_admin(message):
            return True
        await message.answer("Нужны права администратора. Напиши: /admin <пароль>")
        return False

    def keyboard_for(message: Message):
        return admin_keyboard if is_admin(message) else user_keyboard

    async def run_blocking(func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    def progress_to_lines(lines: list[str]):
        def _progress(message: str) -> None:
            lines.append(message)

        return _progress

    @router.message(Command("start", "help"))
    async def help_handler(message: Message) -> None:
        await message.answer(
            "Напиши имя устройства, например: dima-iphone\n"
            "Если такой пользователь есть, я отправлю WireGuard QR и .conf.\n\n"
            "Важно: один конфиг WireGuard = одно физическое устройство.\n"
            "Для второго телефона создай отдельное имя.\n\n"
            "Админ-команды:\n"
            "/admin <пароль>\n"
            "/setup [endpoint]\n"
            "/add <name>\n"
            "/remove <name>\n"
            "/list\n"
            "/status\n"
            "/repair\n"
            "/restart\n"
            "/diagnose"
            "\n\nСоздавай отдельное имя для каждого телефона или планшета.",
            reply_markup=keyboard_for(message),
        )

    @router.message(Command("admin"))
    async def admin_handler(message: Message, command: CommandObject) -> None:
        password = (command.args or "").strip()
        if password != admin_password:
            await message.answer("Пароль не подошел.")
            return
        admin_chats.add(message.chat.id)
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer("Админ-доступ включен для этого чата.")
        await message.answer("Выбери действие:", reply_markup=admin_keyboard)

    @router.message(Command("setup"))
    async def setup_handler(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        setup_endpoint = (command.args or "").strip() or endpoint
        if not setup_endpoint:
            await message.answer(
                "Укажи endpoint: /setup 151.244.251.86 или задай VPNCTL_ENDPOINT."
            )
            return
        lines: list[str] = []
        await message.answer("Начинаю настройку WireGuard...")
        try:
            state = await run_blocking(
                ensure_server,
                remote,
                ServerOptions(
                    endpoint=setup_endpoint,
                    listen_port=listen_port,
                    ssh_port=ssh_port,
                    network=network,
                    dns=dns,
                    mtu=mtu,
                ),
                progress=progress_to_lines(lines),
            )
        except Exception as exc:
            await message.answer(_chunk_text(f"Ошибка setup:\n{exc}"))
            return
        await message.answer(
            "WireGuard готов.\n"
            f"Endpoint: {state['endpoint']}:{state['listen_port']}\n"
            f"Network: {state['network']}\n\n"
            + "\n".join(f"- {line}" for line in lines[-12:])
        )

    @router.message(Command("add"))
    async def add_handler(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        name = (command.args or "").strip()
        if not name:
            await message.answer("Формат: /add dima-iphone")
            return
        try:
            peer = await run_blocking(add_peer, remote, name)
        except Exception as exc:
            await message.answer(f"Не смог добавить устройство: {exc}")
            return
        await message.answer(f"Устройство готово: {peer['name']} {peer['address']}")
        await send_peer_files(message, name)

    @router.message(Command("remove"))
    async def remove_handler(message: Message, command: CommandObject) -> None:
        if not await require_admin(message):
            return
        name = (command.args or "").strip()
        if not name:
            await message.answer("Формат: /remove dima-iphone")
            return
        try:
            removed = await run_blocking(remove_peer, remote, name)
        except Exception as exc:
            await message.answer(f"Не смог удалить пользователя: {exc}")
            return
        await message.answer(f"Удален: {name}" if removed else f"Не найден: {name}")

    @router.message(Command("list"))
    async def list_handler(message: Message) -> None:
        if not await require_admin(message):
            return
        try:
            peers = await run_blocking(list_peers, remote)
        except Exception as exc:
            await message.answer(f"Не смог прочитать пользователей: {exc}")
            return
        if not peers:
            await message.answer("Пользователей пока нет.")
            return
        await message.answer("\n".join(f"{p['name']}: {p['address']}" for p in peers))

    @router.message(Command("status"))
    async def status_handler(message: Message) -> None:
        if not await require_admin(message):
            return
        await send_status(message)

    @router.message(Command("repair"))
    async def repair_handler(message: Message) -> None:
        if not await require_admin(message):
            return
        await repair_vpn_from_bot(message)

    @router.message(Command("restart"))
    async def restart_handler(message: Message) -> None:
        if not await require_admin(message):
            return
        await message.answer("Перезапускаю WireGuard...")
        try:
            await run_blocking(restart_wireguard, remote)
        except Exception as exc:
            await message.answer(_chunk_text(f"Не смог перезапустить WireGuard:\n{exc}"))
            return
        await message.answer("WireGuard перезапущен.")

    @router.message(Command("diagnose"))
    async def diagnose_handler(message: Message) -> None:
        if not await require_admin(message):
            return
        await message.answer("Собираю диагностику...")
        try:
            output = await run_blocking(diagnose, remote)
        except Exception as exc:
            await message.answer(f"Не смог собрать диагностику: {exc}")
            return
        await message.answer(_chunk_text(output))

    async def send_peer_files(message: Message, name: str) -> None:
        try:
            validate_peer_name(name)
            with tempfile.TemporaryDirectory(prefix="vpnctl-bot-") as tmp:
                conf_path, qr_path = await run_blocking(
                    export_peer,
                    remote,
                    name,
                    Path(tmp),
                    with_qr=True,
                )
                if qr_path:
                    await message.answer_photo(
                        FSInputFile(qr_path),
                        caption=f"QR для WireGuard: {name}",
                    )
                await message.answer_document(
                    BufferedInputFile(
                        conf_path.read_bytes(),
                        filename=conf_path.name,
                    ),
                    caption=f"Конфиг WireGuard: {name}",
                )
        except KeyError:
            await message.answer("Такое устройство не найдено. Админ может создать: /add " + name)
        except Exception as exc:
            await message.answer(f"Не смог отправить конфиг: {exc}")

    async def send_status(message: Message) -> None:
        try:
            statuses = await run_blocking(peer_status, remote)
        except Exception as exc:
            await message.answer(f"Не смог получить статус: {exc}")
            return
        if not statuses:
            await message.answer("Устройств пока нет.")
            return
        lines = []
        for item in statuses:
            age = item["handshake_age"]
            if age is None:
                state_text = "не подключалось"
            elif age <= 120:
                state_text = f"онлайн, handshake {age} сек назад"
            else:
                state_text = f"давно не было handshake: {age // 60} мин назад"
            endpoint_text = item["endpoint"] or "endpoint отсутствует"
            lines.append(
                f"{item['name']} ({item['address']}): {state_text}\n"
                f"  {endpoint_text}\n"
                f"  rx={_format_bytes(item['rx'])}, tx={_format_bytes(item['tx'])}"
            )
        await message.answer("\n\n".join(lines))

    async def repair_vpn_from_bot(message: Message) -> None:
        await message.answer("Пересобираю конфиг и перезапускаю WireGuard...")
        lines: list[str] = []
        try:
            state = await run_blocking(
                repair_vpn,
                remote,
                progress=progress_to_lines(lines),
            )
        except Exception as exc:
            await message.answer(_chunk_text(f"Repair не удался:\n{exc}"))
            return
        await message.answer(
            "VPN пересобран и перезапущен.\n"
            f"Endpoint: {state['endpoint']}:{state['listen_port']}\n\n"
            + "\n".join(f"- {line}" for line in lines[-10:])
        )

    @router.message(F.text)
    async def name_handler(message: Message) -> None:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return

        action = pending_actions.pop(message.chat.id, "")
        if action == "get_config":
            await send_peer_files(message, text.split()[0])
            return
        if action == "add_device":
            if not await require_admin(message):
                return
            name = text.split()[0]
            try:
                peer = await run_blocking(add_peer, remote, name)
            except Exception as exc:
                await message.answer(f"Не смог добавить устройство: {exc}")
                return
            await message.answer(f"Устройство готово: {peer['name']} {peer['address']}")
            await send_peer_files(message, name)
            return
        if action == "remove_device":
            if not await require_admin(message):
                return
            name = text.split()[0]
            try:
                removed = await run_blocking(remove_peer, remote, name)
            except Exception as exc:
                await message.answer(f"Не смог удалить устройство: {exc}")
                return
            await message.answer(
                f"Удалено: {name}" if removed else f"Не найдено: {name}"
            )
            return

        if text == "Получить конфиг":
            pending_actions[message.chat.id] = "get_config"
            await message.answer("Напиши имя устройства, например dima-iphone.")
            return
        if text == "Добавить устройство":
            if not await require_admin(message):
                return
            pending_actions[message.chat.id] = "add_device"
            await message.answer("Напиши новое имя устройства, например dima-iphone.")
            return
        if text == "Список устройств":
            if not await require_admin(message):
                return
            await list_handler(message)
            return
        if text == "Статус VPN":
            if not await require_admin(message):
                return
            await send_status(message)
            return
        if text == "Починить VPN":
            if not await require_admin(message):
                return
            await repair_vpn_from_bot(message)
            return
        if text == "Перезапустить VPN":
            if not await require_admin(message):
                return
            await restart_handler(message)
            return
        if text == "Диагностика":
            if not await require_admin(message):
                return
            await diagnose_handler(message)
            return
        if text == "Помощь":
            await help_handler(message)
            return

        name = text.split()[0]
        await send_peer_files(message, name)

    bot = Bot(token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


def _chunk_text(text: str, limit: int = 3900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 80] + "\n\n...output truncated..."


def _format_bytes(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"
