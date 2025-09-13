# main.py
# --- Мини-приложение Telegram для флоу «NANO BANANA» + Mini App (WebApp) ---
# Функции:
# 1) Пользователь загружает своё фото
# 2) Выбирает товар (белая/чёрная/лиловая сумка) — через inline‑кнопки ИЛИ через Mini App
# 3) Бот отправляет данные во внешний флоу (HTTP API)
# 4) Бот возвращает пользователю сгенерированное изображение
#
# Требования:
# - Python 3.10+
# - aiogram==3.*
# - httpx==0.27.*
# - python-dotenv (опц.)
#
# Запуск:
#   export BOT_TOKEN=xxxxxxxxxxxxxxxx
#   export FLOW_API_URL=https://example.com/generate
#   export FLOW_API_KEY=your_key
#   # (опц.) URL развёрнутого Mini App
#   export WEBAPP_URL=https://your-domain.tld/nano-banana-webapp/
#   python main.py

import asyncio
import logging
import os
from enum import Enum
from typing import Optional

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    URLInputFile,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

# ---------------------- Конфиг ----------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
FLOW_API_URL = os.environ.get("FLOW_API_URL", "https://example.com/generate")
FLOW_API_KEY = os.environ.get("FLOW_API_KEY", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL")  # если задано — покажем кнопку Mini App

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN (переменная окружения)")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("nano-banana-bot")

# ---------------------- FSM состояния ----------------------
class BagColor(str, Enum):
    WHITE = "white"
    BLACK = "black"
    LILAC = "lilac"  # лиловая

COLOR_LABELS = {
    BagColor.WHITE: "Белая сумка",
    BagColor.BLACK: "Чёрная сумка",
    BagColor.LILAC: "Лиловая сумка",
}

class GenStates(StatesGroup):
    waiting_photo = State()
    waiting_color = State()  # выбор цвета через inline‑кнопки ИЛИ через WebApp

# ---------------------- Клавиатуры ----------------------

def color_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=COLOR_LABELS[BagColor.WHITE], callback_data=f"color:{BagColor.WHITE}")],
        [InlineKeyboardButton(text=COLOR_LABELS[BagColor.BLACK], callback_data=f"color:{BagColor.BLACK}")],
        [InlineKeyboardButton(text=COLOR_LABELS[BagColor.LILAC], callback_data=f"color:{BagColor.LILAC}")],
    ])


def start_keyboard() -> Optional[ReplyKeyboardMarkup]:
    """Клавиатура с кнопкой открытия Mini App, если задан WEBAPP_URL."""
    if not WEBAPP_URL:
        return None
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Открыть мини‑приложение", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Или выбери цвет ниже",
    )

# ---------------------- Вспомогательные функции ----------------------
async def download_telegram_file(bot: Bot, file_id: str) -> bytes:
    """Скачать файл по file_id и вернуть байты."""
    file = await bot.get_file(file_id)
    file_bytes = await bot.download_file(file.file_path)
    return file_bytes.read()

async def call_flow_api(user_image_bytes: bytes, bag_color: BagColor, user_id: int) -> Optional[bytes]:
    """Вызов внешнего флоу. Возвращает байты итогового изображения или None при ошибке.

    Ожидается, что внешний сервис принимает multipart/form-data:
      - image: файл изображения (jpg/png)
      - color: {white|black|lilac}
      - user_id: числовой идентификатор пользователя Telegram
    и отвечает изображением (image/jpeg или image/png) либо JSON с ошибкой.
    """
    headers = {}
    if FLOW_API_KEY:
        headers["Authorization"] = f"Bearer {FLOW_API_KEY}"

    files = {
        "image": ("user_photo.jpg", user_image_bytes, "image/jpeg"),
    }
    data = {
        "color": bag_color.value,
        "user_id": str(user_id),
    }

    timeout = httpx.Timeout(60.0, connect=15.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.post(FLOW_API_URL, headers=headers, data=data, files=files)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "image" in content_type:
                return resp.content
            if "application/json" in content_type:
                j = resp.json()
                img_url = j.get("image_url")
                if img_url:
                    img_resp = await client.get(img_url)
                    img_resp.raise_for_status()
                    return img_resp.content
            logger.error("FLOW_API ответил неожиданным типом: %s", content_type)
            return None
        except httpx.HTTPError as e:
            logger.exception("Ошибка при вызове FLOW_API: %s", e)
            return None

# ---------------------- Инициализация бота ----------------------
bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ---------------------- Хэндлеры ----------------------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    kb = start_keyboard()
    text = (
        "<b>Привет!</b> Давай сгенерируем фото с сумкой NANO BANANA.

"
        "Шаг 1 — пришли своё <b>фото</b> (как изображение, не файлом).
"
        + ("
Можно также открыть Mini App для выбора цвета." if kb else "")
    )
    await message.answer(text, reply_markup=kb or ReplyKeyboardRemove())
    await state.set_state(GenStates.waiting_photo)

@dp.message(GenStates.waiting_photo, F.photo)
async def got_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo_file_id=file_id)

    kb = start_keyboard()
    await message.answer(
        "Шаг 2 — выбери <b>цвет сумки</b>:
"
        "Можно кнопками ниже или в мини‑приложении.",
        reply_markup=kb or ReplyKeyboardRemove(),
    )
    await message.answer("Быстрый выбор цвета:", reply_markup=color_keyboard())
    await state.set_state(GenStates.waiting_color)

@dp.message(GenStates.waiting_photo)
async def not_photo_warn(message: Message):
    await message.reply("Мне нужно именно фото. Пожалуйста, отправь изображение.")

@dp.callback_query(GenStates.waiting_color, F.data.startswith("color:"))
async def choose_color(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    _, color_raw = cb.data.split(":", 1)
    try:
        color = BagColor(color_raw)
    except ValueError:
        await cb.message.reply("Неизвестный цвет. Попробуй ещё раз.")
        return

    data = await state.get_data()
    file_id = data.get("photo_file_id")
    if not file_id:
        await cb.message.reply("Не нашёл фото в сессии. Отправь фото ещё раз: /start")
        await state.clear()
        return

    await cb.message.edit_text(
        f"Окей, цвет: <b>{COLOR_LABELS[color]}</b>.

Шаг 3 — обрабатываю фото во флоу… ⏳"
    )

    try:
        photo_bytes = await download_telegram_file(cb.message.bot, file_id)
    except Exception:
        logger.exception("Не удалось скачать фото из Telegram")
        await cb.message.answer("Не удалось скачать фото. Пришли его ещё раз командой /start.")
        await state.clear()
        return

    gen_bytes = await call_flow_api(photo_bytes, color, cb.from_user.id)

    if not gen_bytes:
        await cb.message.answer(
            "😔 Не получилось получить изображение из флоу. Попробуй ещё раз позже или измени фото.
Команда для перезапуска: /start"
        )
        await state.clear()
        return

    await cb.message.answer_photo(photo=gen_bytes, caption="Готово! Если хочешь ещё вариант — /start")
    await state.clear()

# --- Obработчик данных из Mini App ---
@dp.message(GenStates.waiting_color, F.web_app_data)
async def webapp_color_handler(message: Message, state: FSMContext):
    # Убираем клавиатуру
    await message.answer("Получил выбор в мини‑приложении. Готовлю результат…", reply_markup=ReplyKeyboardRemove())

    import json
    try:
        payload = json.loads(message.web_app_data.data)
        color = BagColor(payload.get("color", ""))
    except Exception:
        await message.answer("Не удалось распознать выбор цвета из Mini App. Попробуй ещё раз.")
        return

    data = await state.get_data()
    file_id = data.get("photo_file_id")
    if not file_id:
        await message.answer("Не найдено фото в сессии. Пожалуйста, сначала отправь фото: /start")
        await state.clear()
        return

    try:
        photo_bytes = await download_telegram_file(message.bot, file_id)
    except Exception:
        logger.exception("Не удалось скачать фото из Telegram (Mini App flow)")
        await message.answer("Не удалось скачать фото. Пришли его ещё раз командой /start.")
        await state.clear()
        return

    gen_bytes = await call_flow_api(photo_bytes, color, message.from_user.id)

    if not gen_bytes:
        await message.answer("😔 Не получилось получить изображение из флоу. Попробуй позже или измени фото. /start")
        await state.clear()
        return

    await message.answer_photo(photo=gen_bytes, caption="Готово! Ещё вариант — /start")
    await state.clear()

# Фоллбек: фото вне сценария — сразу перекидываем в поток
@dp.message(F.photo)
async def photo_outside_flow(message: Message, state: FSMContext):
    await state.set_state(GenStates.waiting_photo)
    await got_photo(message, state)

@dp.message()
async def fallback(message: Message):
    await message.answer("Нажми /start и следуй шагам: 1) фото → 2) цвет (кнопки или Mini App) → 3) результат.")

# ---------------------- Точка входа ----------------------
async def main():
    logger.info("Бот запускается…")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")


"""
====================
ФАЙЛ: webapp/index.html (Telegram Mini App)
====================
Пример простого мини‑приложения для выбора цвета. Размести где угодно (S3/Cloudflare Pages/Netlify/Vercel/GitHub Pages) и укажи URL в WEBAPP_URL.

Содержимое:

<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NANO BANANA — Mini App</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    :root { --bg:#111; --fg:#fff; --muted:#aaa; --card:#1a1a1a; }
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,-apple-system,Segoe UI,Roboto}
    .wrap{max-width:520px;margin:0 auto;padding:24px}
    .card{background:var(--card);border-radius:16px;padding:20px;box-shadow:0 2px 12px rgba(0,0,0,.25)}
    h1{font-size:20px;margin:0 0 12px}
    .grid{display:grid;gap:12px}
    .opt{display:flex;align-items:center;gap:10px;padding:12px;border-radius:12px;background:#222;cursor:pointer}
    .opt input{margin:0}
    .hint{color:var(--muted);font-size:13px}
    button{width:100%;padding:14px 16px;border:0;border-radius:12px;font-weight:600;cursor:pointer}
    .primary{background:#2ea44f;color:#fff}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Выбери цвет сумки</h1>
      <div class="grid">
        <label class="opt"><input type="radio" name="color" value="white" checked><span>Белая</span></label>
        <label class="opt"><input type="radio" name="color" value="black"><span>Чёрная</span></label>
        <label class="opt"><input type="radio" name="color" value="lilac"><span>Лиловая</span></label>
      </div>
      <p class="hint">Фото пришли сообщением в чат боту, а здесь просто выбери цвет. После нажатия кнопки бот начнёт генерацию.</p>
      <div style="height:12px"></div>
      <button class="primary" id="submit">Отправить выбор</button>
    </div>
  </div>
<script>
  const tg = window.Telegram?.WebApp;
  tg?.ready();
  document.getElementById('submit').addEventListener('click', () => {
    const val = document.querySelector('input[name="color"]:checked')?.value || 'white';
    tg?.sendData(JSON.stringify({ color: val }));
  });
</script>
</body>
</html>
"""
