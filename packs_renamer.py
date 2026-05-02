"""
Telegram Sticker Pack Renamer
Массовое переименование стикерпаков и эмодзи-паков через MTProto
"""

import asyncio
import re
import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv, set_key

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TimeElapsedColumn, TimeRemainingColumn, MofNCompleteColumn, TaskProgressColumn
)
from rich.rule import Rule
from rich.text import Text
from rich import box
from rich.columns import Columns
from rich.padding import Padding

from telethon import TelegramClient
from telethon.tl.functions.messages import GetMyStickersRequest
from telethon.tl.functions.stickers import RenameStickerSetRequest
from telethon.tl.types import InputStickerSetID
from telethon.errors import FloodWaitError, StickersetInvalidError

console = Console()
ENV_FILE = Path(".env")


# ─────────────────────────────────────────────
#  .ENV helpers
# ─────────────────────────────────────────────

def load_env() -> dict:
    load_dotenv(ENV_FILE)
    return {
        "API_ID":   os.getenv("TG_API_ID", ""),
        "API_HASH": os.getenv("TG_API_HASH", ""),
        "PHONE":    os.getenv("TG_PHONE", ""),
    }


def save_env(key: str, value: str):
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    set_key(str(ENV_FILE), key, value)


# ─────────────────────────────────────────────
#  TITLE
# ─────────────────────────────────────────────

def print_header():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Telegram Sticker Pack Renamer by sxnrls with ❤️[/bold cyan]\n"
        "[dim]Массовое переименование стикерпаков и эмодзи-паков через MTProto[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()


# ─────────────────────────────────────────────
#  SETUP — запрос конфига
# ─────────────────────────────────────────────

def ask_credentials(saved: dict) -> dict:
    """Спросить API_ID / API_HASH / PHONE. Если сохранены — предложить повторно использовать."""
    creds = dict(saved)
    has_saved = all(creds.get(k) for k in ("API_ID", "API_HASH", "PHONE"))

    if has_saved:
        console.print(Panel(
            f"[dim]API_ID:[/dim]   [green]{creds['API_ID']}[/green]\n"
            f"[dim]API_HASH:[/dim] [green]{creds['API_HASH'][:6]}{'•' * (len(creds['API_HASH']) - 6)}[/green]\n"
            f"[dim]Телефон:[/dim]  [green]{creds['PHONE']}[/green]",
            title="[bold]Сохранённые данные[/bold]",
            border_style="green",
        ))
        if Confirm.ask("[bold]Использовать сохранённые данные?[/bold]", default=True):
            return creds

    console.print(Rule("[dim]Данные для входа[/dim]"))
    console.print(
        "[dim]Получите API_ID и API_HASH на [link=https://my.telegram.org/apps]my.telegram.org/apps[/link][/dim]\n"
    )

    creds["API_ID"]   = Prompt.ask("[bold]API_ID[/bold] [dim](число)[/dim]")
    creds["API_HASH"] = Prompt.ask("[bold]API_HASH[/bold] [dim](строка)[/dim]")
    creds["PHONE"]    = Prompt.ask("[bold]Номер телефона[/bold] [dim](+7XXXXXXXXXX)[/dim]")

    save_env("TG_API_ID",   creds["API_ID"])
    save_env("TG_API_HASH", creds["API_HASH"])
    save_env("TG_PHONE",    creds["PHONE"])
    console.print("[dim]✓ Данные сохранены в .env[/dim]\n")

    return creds


def ask_run_params() -> dict:
    """Спросить параметры конкретного запуска: new_username, тип паков, исключения."""
    console.print(Rule("[dim]Параметры запуска[/dim]"))

    # ── Новый @username ──
    new_username = Prompt.ask(
        "[bold]Новый @username[/bold] [dim](без знака @)[/dim]"
    ).strip().lstrip("@")

    # ── Тип паков ──
    console.print()
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_row("1", "Стикерпаки и эмодзи-паки [dim](все)[/dim]")
    table.add_row("2", "Только стикерпаки")
    table.add_row("3", "Только эмодзи-паки")
    console.print(table)

    pack_type = IntPrompt.ask(
        "[bold]Что переименовать?[/bold]",
        choices=["1", "2", "3"],
        default=1
    )
    pack_filter = {1: "both", 2: "stickers", 3: "emoji"}[pack_type]

    # ── Исключения ──
    console.print()
    console.print("[dim]Исключения: введите short_name паков через запятую,\n"
                  "или нажмите Enter, если исключений нет.[/dim]")
    raw_exc = Prompt.ask("[bold]Исключить паки[/bold]", default="")
    exclusions = {s.strip().lower() for s in raw_exc.split(",") if s.strip()}

    if exclusions:
        console.print(f"[dim]Исключено паков: {len(exclusions)}[/dim]")

    console.print()
    return {
        "new_username": new_username,
        "pack_filter":  pack_filter,
        "exclusions":   exclusions,
    }


# ─────────────────────────────────────────────
#  TITLE TRANSFORMATION
# ─────────────────────────────────────────────

def make_new_title(old_title: str, new_username: str) -> str:
    """
    Заменяет первый @xxx на @new_username.
    Если перед @ стоит не пробел — вставляет пробел (чтобы @ был кликабельным).
    Если @ нет — добавляет ' @new_username' в конец.
    """
    def replacer(m):
        pos = m.start()
        need_space = pos > 0 and old_title[pos - 1] not in (" ", "\t", "\n")
        prefix = " " if need_space else ""
        return f"{prefix}@{new_username}"

    if re.search(r"@\w+", old_title):
        return re.sub(r"@\w+", replacer, old_title, count=1)

    return f"{old_title.rstrip()} @{new_username}"


# ─────────────────────────────────────────────
#  FETCH ALL PACKS
# ─────────────────────────────────────────────

async def fetch_all_my_packs(client: TelegramClient) -> list:
    all_sets = []
    offset_id = 0
    PAGE = 100

    with console.status("[cyan]Загружаем ваши паки...[/cyan]"):
        while True:
            result = await client(GetMyStickersRequest(offset_id=offset_id, limit=PAGE))
            batch = result.sets
            if not batch:
                break
            all_sets.extend(batch)
            if len(all_sets) >= result.count:
                break
            offset_id = batch[-1].set.id
            await asyncio.sleep(0.3)

    return all_sets


# ─────────────────────────────────────────────
#  MAIN RENAME LOOP
# ─────────────────────────────────────────────

async def rename_all(client: TelegramClient, params: dict, delay: float = 2.5):
    new_username = params["new_username"]
    pack_filter  = params["pack_filter"]   # "both" | "stickers" | "emoji"
    exclusions   = params["exclusions"]    # set of short_names

    all_sets = await fetch_all_my_packs(client)

    # Фильтрация по типу
    def passes_filter(s) -> bool:
        is_emoji = getattr(s, "emojis", False)
        if pack_filter == "stickers" and is_emoji:
            return False
        if pack_filter == "emoji" and not is_emoji:
            return False
        return True

    to_process = [c for c in all_sets if passes_filter(c.set)]
    total = len(to_process)

    # Сводка перед стартом
    console.print()
    info = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    info.add_column(style="dim", justify="right")
    info.add_column(style="bold")
    info.add_row("Всего найдено паков:", str(len(all_sets)))
    info.add_row("Будет обработано:",   str(total))
    info.add_row("Новый username:",     f"@{new_username}")
    info.add_row("Тип:",               {"both": "все", "stickers": "только стикеры", "emoji": "только эмодзи"}[pack_filter])
    if exclusions:
        info.add_row("Исключений:", str(len(exclusions)))
    console.print(Panel(info, title="[bold]Параметры запуска[/bold]", border_style="cyan"))
    console.print()

    if not Confirm.ask(f"[bold]Начать переименование {total} паков?[/bold]", default=True):
        console.print("[yellow]Отменено.[/yellow]")
        return

    stats = {"renamed": 0, "skipped": 0, "excluded": 0, "failed": 0}
    start = datetime.now()

    with Progress(
        SpinnerColumn(style="cyan"),
        MofNCompleteColumn(),
        BarColumn(bar_width=35, style="cyan", complete_style="bold green"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TextColumn("·"),
        TimeRemainingColumn(),
        TextColumn("[dim]{task.description}[/dim]"),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("", total=total)

        for covered in to_process:
            s = covered.set
            short = s.short_name
            old_title = s.title
            is_emoji = getattr(s, "emojis", False)
            kind = "emoji" if is_emoji else "sticker"

            # Обновляем описание прогресс-бара
            progress.update(task, description=f"[cyan]{short}[/cyan]")

            # Исключение
            if short.lower() in exclusions:
                progress.advance(task)
                stats["excluded"] += 1
                continue

            new_title = make_new_title(old_title, new_username)

            # Уже в порядке
            if old_title == new_title:
                progress.advance(task)
                stats["skipped"] += 1
                continue

            input_set = InputStickerSetID(id=s.id, access_hash=s.access_hash)

            for attempt in range(1, 4):
                try:
                    await client(RenameStickerSetRequest(stickerset=input_set, title=new_title))
                    stats["renamed"] += 1
                    progress.advance(task)
                    break

                except FloodWaitError as e:
                    wait = e.seconds + 5
                    progress.update(task, description=f"[yellow]FloodWait {e.seconds}с...[/yellow]")
                    await asyncio.sleep(wait)

                except StickersetInvalidError:
                    stats["failed"] += 1
                    progress.advance(task)
                    break

                except Exception:
                    if attempt == 3:
                        stats["failed"] += 1
                        progress.advance(task)
                    else:
                        await asyncio.sleep(3)

            await asyncio.sleep(delay)

    # Итог
    elapsed = str(datetime.now() - start).split(".")[0]
    console.print()
    result_table = Table(box=box.ROUNDED, border_style="dim", padding=(0, 2))
    result_table.add_column("", style="dim", justify="right")
    result_table.add_column("", style="bold")
    result_table.add_row("✓ Переименовано", f"[green]{stats['renamed']}[/green]")
    result_table.add_row("⊘ Без изменений", f"[dim]{stats['skipped']}[/dim]")
    if stats["excluded"]:
        result_table.add_row("⊘ Исключено",  f"[dim]{stats['excluded']}[/dim]")
    if stats["failed"]:
        result_table.add_row("✗ Ошибок",     f"[red]{stats['failed']}[/red]")
    result_table.add_row("⏱ Время",          elapsed)

    console.print(Panel(result_table, title="[bold]Готово[/bold]", border_style="green"))


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

async def main():
    print_header()

    saved = load_env()
    creds = ask_credentials(saved)
    params = ask_run_params()

    console.print(Rule())

    client = TelegramClient(
        "packs_renamer",
        int(creds["API_ID"]),
        creds["API_HASH"]
    )

    async with client:
        await client.start(phone=creds["PHONE"])
        me = await client.get_me()
        console.print(
            f"[dim]Вошли как:[/dim] [bold cyan]{me.first_name}[/bold cyan] "
            f"[dim](ID: {me.id})[/dim]\n"
        )
        await rename_all(client, params)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Прервано пользователем.[/yellow]")
    except Exception as e:
        console.print(f"\n[red bold]❌ Критическая ошибка:[/red bold] {e}")
        sys.exit(1)
