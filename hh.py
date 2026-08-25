import asyncio
import os
import tempfile
import logging
import edge_tts
import aiohttp
import urllib.parse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)
from aiogram import F
from deep_translator import MyMemoryTranslator   # используем MyMemory

# ================== ТОКЕН БОТА ==================
BOT_TOKEN = "8833141239:AAEH7Q8A5m0XffB15wgsb8dyXhSD9yE1YnU"   # замените на свой
# ===============================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# ---------- Голоса ----------
VOICES = {
    "мужчина": "ru-RU-DmitryNeural",
    "женщина": "ru-RU-SvetlanaNeural"
}
current_voice = VOICES["мужчина"]

# ---------- Режимы пользователей ----------
user_mode = {}
user_last_prompt = {}

# ---------- Клавиатуры ----------
def main_menu():
    buttons = [
        [InlineKeyboardButton(text="🎤 Озвучка текста", callback_data="mode_tts")],
        [InlineKeyboardButton(text="🖼 Генерация картинки", callback_data="mode_image")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def voice_keyboard():
    buttons = [
        [InlineKeyboardButton(text="👨 Мужчина", callback_data="voice_мужчина")],
        [InlineKeyboardButton(text="👩 Женщина", callback_data="voice_женщина")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def image_back_keyboard():
    buttons = [
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def after_generation_keyboard(prompt):
    buttons = [
        [InlineKeyboardButton(text="🔄 Сгенерировать снова", callback_data=f"regenerate_{prompt}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ---------- Функция перевода (через MyMemory) ----------
def translate_to_english(text: str) -> str:
    try:
        translator = MyMemoryTranslator(source='ru-RU', target='en-US')
        translated = translator.translate(text)
        return translated
    except Exception as e:
        logging.warning(f"Ошибка перевода: {e}, оставляем оригинал")
        return text

# ---------- Хендлеры ----------
@dp.message(Command("start"))
async def start(message: types.Message):
    user_mode[message.from_user.id] = "tts"
    await message.answer(
        "🎯 **Добро пожаловать в бот!**\n\n"
        "Я умею:\n"
        "🎤 **Озвучивать текст** – выбери мужской или женский голос.\n"
        "🖼 **Генерировать картинки** – просто опиши что хочешь увидеть.\n\n"
        "Выбери режим кнопкой ниже и наслаждайся! 😊",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data.startswith("mode_"))
async def mode_selection(callback: types.CallbackQuery):
    mode = callback.data.split("_")[1]
    user_mode[callback.from_user.id] = mode
    if mode == "tts":
        await callback.message.edit_text(
            "🎤 Режим **озвучки**. Выбери голос:",
            reply_markup=voice_keyboard()
        )
    else:
        await callback.message.edit_text(
            "🖼 Режим **генерации картинок**.\n"
            "Отправь описание (промт) на русском или английском.\n"
            "Я автоматически переведу на английский для лучшего результата.",
            reply_markup=image_back_keyboard()
        )
    await callback.answer()

@dp.callback_query(F.data.startswith("voice_"))
async def voice_selection(callback: types.CallbackQuery):
    global current_voice
    voice_key = callback.data.split("_")[1]
    current_voice = VOICES[voice_key]
    await callback.answer(f"✅ Голос изменён на {voice_key}")
    await callback.message.edit_text(
        f"Текущий голос: **{voice_key}**\nОтправь текст для озвучки.",
        reply_markup=voice_keyboard()
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("🎯 Выбери режим работы:", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("regenerate_"))
async def regenerate_image(callback: types.CallbackQuery):
    prompt = callback.data.split("_", 1)[1]
    await callback.answer("🔄 Повторная генерация...")
    await generate_and_send_image(callback.message, prompt)

# ---------- Генерация картинки (с переводом) ----------
async def generate_and_send_image(message: types.Message, prompt: str):
    try:
        prompt_en = translate_to_english(prompt)
        if prompt_en != prompt:
            await message.answer(f"📝 Переведено: {prompt_en}")

        encoded = urllib.parse.quote(prompt_en)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=768&height=768&model=flux&nologo=true"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    user_last_prompt[message.from_user.id] = prompt
                    await message.reply_photo(
                        BufferedInputFile(image_data, filename="generated.jpg"),
                        caption=f"🖼 По запросу: {prompt}",
                        reply_markup=after_generation_keyboard(prompt)
                    )
                else:
                    await message.answer(f"❌ Ошибка генерации (HTTP {resp.status})")
    except Exception as e:
        logging.exception("Ошибка генерации картинки:")
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")

# ---------- Обработка текстовых сообщений ----------
@dp.message()
async def handle_text(message: types.Message):
    text = message.text
    if not text:
        return

    user_id = message.from_user.id
    mode = user_mode.get(user_id, "tts")

    if mode == "tts":
        await message.answer("🎧 Озвучиваю...")
        try:
            communicate = edge_tts.Communicate(text, current_voice)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp_path = tmp.name
            await communicate.save(tmp_path)

            audio = FSInputFile(tmp_path)
            await message.reply_audio(audio, title="Озвучка", performer=current_voice)
            os.unlink(tmp_path)

        except Exception as e:
            if current_voice == VOICES["мужчина"]:
                await message.answer("⚠️ Мужской голос временно недоступен, использую женский.")
                try:
                    fallback_voice = VOICES["женщина"]
                    communicate = edge_tts.Communicate(text, fallback_voice)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                        tmp_path = tmp.name
                    await communicate.save(tmp_path)
                    audio = FSInputFile(tmp_path)
                    await message.reply_audio(audio, title="Озвучка (женский)", performer=fallback_voice)
                    os.unlink(tmp_path)
                except Exception as e2:
                    logging.exception("Ошибка озвучки (оба голоса):")
                    await message.answer(f"❌ Ошибка озвучки: {str(e2)[:200]}")
            else:
                logging.exception("Ошибка озвучки:")
                await message.answer(f"❌ Ошибка озвучки: {str(e)[:200]}")

    else:
        await message.answer("🖼 Генерирую картинку...")
        await generate_and_send_image(message, text)

# ---------- Запуск ----------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())