import asyncio
import logging
import os
import io
import threading
import requests
import replicate
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==========================================
# 1. НАСТРОЙКИ (БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Проверка, что переменные заданы (чтобы бот не падал молча)
if not BOT_TOKEN or not REPLICATE_API_TOKEN:
    raise ValueError("❌ Ошибка: Не заданы переменные окружения BOT_TOKEN или REPLICATE_API_TOKEN!")

os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN
replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# АБСОЛЮТНЫЙ ПУТЬ К ПАПКЕ VOICES
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICES_DIR = os.path.join(BASE_DIR, "voices")

VOICES = [
    {"name": "Асхаб Тамаев", "emoji": "📕", "search": "асхаб"},
    {"name": "ВладА4", "emoji": "▶️", "search": "влад"},
    {"name": "Джокер", "emoji": "🃏", "search": "джокер"},
    {"name": "ИванЗоло", "emoji": "🎵", "search": "золо"},
    {"name": "Карнавал", "emoji": "🎭", "search": "карнавал"},
    {"name": "Мелстрой", "emoji": "🎧", "search": "мелстрой"},
    {"name": "Папич", "emoji": "🎮", "search": "папич"},
    {"name": "Хаметова", "emoji": "👩‍🎤", "search": "хаметова"},
]

user_selected_voice = {}
active_users = set()


def find_voice_file(search_key):
    if not os.path.exists(VOICES_DIR):
        return None
    for file in os.listdir(VOICES_DIR):
        if search_key.lower() in file.lower():
            return os.path.join(VOICES_DIR, file)
    return None


# ==========================================
# 2. FLASK СЕРВЕР (Для хостинга)
# ==========================================
app = Flask(__name__)


@app.route('/', methods=['GET'])
def index():
    return "✅ Бот Озвучка работает!", 200


def run_flask():
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)


# ==========================================
# 3. БОТ AIOGRAM
# ==========================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def get_voices_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    for i in range(0, len(VOICES), 2):
        row = []
        voice1 = VOICES[i]
        is_selected1 = user_selected_voice.get(user_id) == voice1
        text1 = f"✅ {voice1['name']}" if is_selected1 else f"{voice1['emoji']} {voice1['name']}"
        row.append(InlineKeyboardButton(text=text1, callback_data=f"voice_{i}"))

        if i + 1 < len(VOICES):
            voice2 = VOICES[i + 1]
            is_selected2 = user_selected_voice.get(user_id) == voice2
            text2 = f"✅ {voice2['name']}" if is_selected2 else f"{voice2['emoji']} {voice2['name']}"
            row.append(InlineKeyboardButton(text=text2, callback_data=f"voice_{i + 1}"))
        builder.row(*row)

    builder.row(InlineKeyboardButton(text="📢 Наш канал", url="https://t.me/your_channel"))
    return builder.as_markup()


def get_menu_text(user_id):
    selected = user_selected_voice.get(user_id)
    selected_text = f"✅ Выбран голос: <b>{selected['emoji']} {selected['name']}</b>" if selected else "🎙 Голос не выбран"
    return (
        "🔈 <b>Озвучка Free</b>\n\n"
        "Выбери голос из списка ниже и отправь любой текст — я его озвучу!\n\n"
        f"{selected_text}\n\n"
        "💬 <i>Напиши текст для озвучки прямо в чат...</i>"
    )


@dp.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
    await message.answer(
        get_menu_text(message.from_user.id),
        reply_markup=get_voices_keyboard(message.from_user.id),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("voice_"))
async def handle_voice_selection(callback: types.CallbackQuery):
    index = int(callback.data.split("_")[1])
    selected_voice = VOICES[index]

    if user_selected_voice.get(callback.from_user.id) == selected_voice:
        await callback.answer("Этот голос уже выбран!", show_alert=False)
        return

    user_selected_voice[callback.from_user.id] = selected_voice

    try:
        await callback.message.edit_text(
            get_menu_text(callback.from_user.id),
            reply_markup=get_voices_keyboard(callback.from_user.id),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer(f"Выбран голос: {selected_voice['name']}")


# ==========================================
# 4. ПРЯМАЯ ОТПРАВКА ГОЛОСА (REQUESTS)
# ==========================================
def send_voice_to_telegram(chat_id: int, audio_content: bytes, caption: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVoice"
    files = {'voice': ('voice.wav', io.BytesIO(audio_content), 'audio/wav')}
    data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}

    response = requests.post(url, data=data, files=files, timeout=60)
    if response.status_code != 200:
        logging.error(f"❌ Ошибка Telegram: {response.text}")


# ==========================================
# 5. ОЗВУЧКА (ЧЕРЕЗ XTTS-V2)
# ==========================================
@dp.message(F.text & ~F.text.startswith('/'))
async def handle_text_to_speech(message: types.Message):
    user_id = message.from_user.id
    voice = user_selected_voice.get(user_id)

    if not voice:
        await message.answer("⚠️ Сначала выбери голос из списка! Нажми /start")
        return

    if user_id in active_users:
        await message.answer("⏳ Подождите, ваша предыдущая озвучка еще генерируется.")
        return

    user_text = message.text.strip()
    if not user_text: return

    if len(user_text) > 250:
        await message.answer("⚠️ Текст слишком длинный. Максимум 250 символов.")
        return

    file_path = find_voice_file(voice['search'])
    if not file_path:
        await message.answer(f"❌ Ошибка: Файл для '{voice['name']}' не найден в папке voices.")
        return

    active_users.add(user_id)
    msg = await message.answer(f"🎙 Озвучиваю текст... \nПожалуйста, подождите (10-20 сек).")

    try:
        loop = asyncio.get_event_loop()

        def upload_voice():
            with open(file_path, "rb") as f:
                file_obj = replicate_client.files.create(f)
                return file_obj.urls["get"]

        audio_url = await loop.run_in_executor(None, upload_voice)

        def run_xtts():
            return replicate_client.run(
                "lucataco/xtts-v2:684bc3855b37866c0c65add2ff39c78f3dea3f4ff103a436465326e0f438d55e",
                input={
                    "text": user_text,
                    "speaker": audio_url,
                    "language": "ru"
                }
            )

        output = None
        for attempt in range(3):
            try:
                output = await loop.run_in_executor(None, run_xtts)
                break
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "throttled" in error_str.lower():
                    if attempt < 2:
                        await msg.edit_text(f"⏳ Сервер перегружен. Ждем 20 сек... (Попытка {attempt + 2}/3)")
                        await asyncio.sleep(20)
                    else:
                        raise Exception("Очередь переполнена (429).")
                else:
                    raise e

        if not output:
            raise Exception("Модель вернула пустой результат.")

        if isinstance(output, list):
            audio_url_result = output[0]
        elif hasattr(output, 'url'):
            audio_url_result = output.url() if callable(output.url) else output.url
        else:
            audio_url_result = str(output)

        await msg.edit_text("⏳ Скачиваю и отправляю...")

        audio_response = requests.get(audio_url_result, timeout=60)
        audio_content = audio_response.content

        caption = f"🎙 Озвучено: {voice['emoji']} {voice['name']}"
        await loop.run_in_executor(None, send_voice_to_telegram, message.chat.id, audio_content, caption)

        await msg.delete()

    except Exception as e:
        logging.error(f"❌ Ошибка TTS: {repr(e)}")
        await msg.edit_text(f"❌ Ошибка озвучки. Попробуйте еще раз или выберите другой голос.")
    finally:
        active_users.discard(user_id)


# ==========================================
# 6. ЗАПУСК
# ==========================================
async def main():
    if os.path.exists(VOICES_DIR):
        logging.info(f"✅ Папка voices найдена. Файлы: {os.listdir(VOICES_DIR)}")
    else:
        logging.error(f"❌ ВНИМАНИЕ: Папка {VOICES_DIR} НЕ НАЙДЕНА! Загрузите файлы.")

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    logging.info("Бот Озвучка Free запущен...")
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот выключен")