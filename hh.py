import asyncio
import os
import tempfile
import logging
import aiosqlite
from datetime import datetime, timedelta
import edge_tts
import aiohttp
import urllib.parse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, LabeledPrice, PreCheckoutQuery, SuccessfulPayment
)
from aiogram import F
from deep_translator import MyMemoryTranslator

# ================== ТОКЕН БОТА ==================
BOT_TOKEN = "8833141239:AAEH7Q8A5m0XffB15wgsb8dyXhSD9yE1YnU"   # замените на свой
# ===============================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# ---------- База данных ----------
DB_PATH = "users.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                free_generations INTEGER DEFAULT 5,
                purchased_generations INTEGER DEFAULT 0,
                total_generated INTEGER DEFAULT 0,
                premium_until TIMESTAMP,
                last_promo_sent TIMESTAMP
            )
        ''')
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    'user_id': row[0],
                    'free_generations': row[1],
                    'purchased_generations': row[2],
                    'total_generated': row[3],
                    'premium_until': row[4],
                    'last_promo_sent': row[5]
                }
            return None

async def create_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT INTO users (user_id) VALUES (?)', (user_id,))
        await db.commit()

async def get_or_create_user(user_id):
    user = await get_user(user_id)
    if user is None:
        await create_user(user_id)
        user = await get_user(user_id)
    return user

async def update_user(user_id, **kwargs):
    set_clause = ', '.join(f'{k} = ?' for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f'UPDATE users SET {set_clause} WHERE user_id = ?', values)
        await db.commit()

def is_premium_active(user):
    if user['premium_until'] is None:
        return False
    try:
        until = datetime.fromisoformat(user['premium_until'])
        return until > datetime.now()
    except:
        return False

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
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
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

def buy_keyboard():
    buttons = [
        [InlineKeyboardButton(text="5 фото за 25⭐", callback_data="buy_5")],
        [InlineKeyboardButton(text="10 фото за 50⭐", callback_data="buy_10")],
        [InlineKeyboardButton(text="100 фото за 100⭐", callback_data="buy_100")],
        [InlineKeyboardButton(text="👑 Премиум (безлимит + видео) за 300⭐ (на месяц)", callback_data="buy_premium")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ---------- Функция перевода ----------
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
    user_id = message.from_user.id
    await get_or_create_user(user_id)
    user_mode[user_id] = "tts"
    await message.answer(
        "🎯 **Добро пожаловать в бот!**\n\n"
        "Я умею:\n"
        "🎤 **Озвучивать текст** – выбери мужской или женский голос.\n"
        "🖼 **Генерировать картинки** – просто опиши что хочешь увидеть.\n"
        "✨ **Генерировать можно всё, даже 18+.**\n\n"
        "У тебя есть **5 бесплатных генераций** картинок.\n"
        "После этого ты можешь купить дополнительные пакеты за **Telegram Stars** ⭐.\n\n"
        "Выбери режим кнопкой ниже и наслаждайся! 😊",
        reply_markup=main_menu()
    )

# ---------- Секретный промокод /soso (безлимит на месяц) ----------
@dp.message(Command("soso"))
async def secret_promo(message: types.Message):
    user_id = message.from_user.id
    user = await get_or_create_user(user_id)
    if is_premium_active(user):
        await message.answer("У вас уже активен премиум!")
        return
    until = datetime.now() + timedelta(days=30)
    await update_user(user_id, premium_until=until.isoformat())
    await message.answer("🎉 Поздравляем! Вам активирован безлимитный премиум на месяц (секретный промокод).")

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

@dp.callback_query(F.data == "profile")
async def profile_callback(callback: types.CallbackQuery):
    await show_profile(callback.message)
    await callback.answer()

@dp.message(Command("profile"))
async def profile_command(message: types.Message):
    await show_profile(message)

async def show_profile(message: types.Message):
    user_id = message.from_user.id
    user = await get_or_create_user(user_id)
    premium = is_premium_active(user)
    if premium:
        remaining = "∞ (безлимит)"
        until = datetime.fromisoformat(user['premium_until']).strftime("%d.%m.%Y %H:%M")
        premium_text = f"Да (до {until})"
    else:
        free = user['free_generations']
        purchased = user['purchased_generations']
        remaining = f"{free} (бесплатных) + {purchased} (купленных) = {free + purchased}"
        premium_text = "Нет"

    text = (
        f"👤 **Ваш профиль**\n\n"
        f"🖼 Всего сгенерировано картинок: {user['total_generated']}\n"
        f"📸 Осталось генераций: {remaining}\n"
        f"👑 Премиум: {premium_text}\n"
    )
    if not premium:
        text += "\nКупи премиум и получи безлимит фото + генерацию видео на месяц за 300⭐!"
        await message.answer(text, reply_markup=buy_keyboard())
    else:
        await message.answer(text, reply_markup=main_menu())

# ---------- Покупки через Telegram Stars ----------
@dp.callback_query(F.data.startswith("buy_"))
async def buy_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split("_")[1]

    packages = {
        "5": {"cost": 25, "amount": 5, "title": "5 генераций фото", "desc": "Дополнительные 5 генераций изображений"},
        "10": {"cost": 50, "amount": 10, "title": "10 генераций фото", "desc": "Дополнительные 10 генераций изображений"},
        "100": {"cost": 100, "amount": 100, "title": "100 генераций фото", "desc": "Дополнительные 100 генераций изображений"},
        "premium": {"cost": 300, "amount": None, "title": "Премиум на месяц", "desc": "Безлимитные генерации фото и видео на 30 дней"},
    }

    pkg = packages.get(action)
    if not pkg:
        await callback.answer("Неизвестный пакет.")
        return

    payload = f"{action}_{user_id}_{int(datetime.now().timestamp())}"

    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=pkg["title"],
            description=pkg["desc"],
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=pkg["title"], amount=pkg["cost"])],
            start_parameter="buy_stars",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Оплатить", pay=True)]
            ])
        )
        await callback.answer("Счёт отправлен!")
    except Exception as e:
        logging.exception("Ошибка отправки инвойса")
        await callback.answer(f"❌ Ошибка: {str(e)[:100]}")

@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    payment = message.successful_payment
    user_id = message.from_user.id
    payload = payment.invoice_payload
    try:
        parts = payload.split("_")
        action = parts[0]
        if int(parts[1]) != user_id:
            await message.answer("❌ Ошибка: несовпадение пользователя.")
            return
    except:
        await message.answer("❌ Ошибка формата платежа.")
        return

    user = await get_or_create_user(user_id)

    if action == "premium":
        until = datetime.now() + timedelta(days=30)
        await update_user(user_id, premium_until=until.isoformat())
        await message.answer(
            "👑 **Премиум активирован на месяц!**\n"
            "Теперь у вас безлимитные генерации фото и доступ к видео (в разработке)."
        )
    else:
        packages = {
            "5": 5,
            "10": 10,
            "100": 100
        }
        amount = packages.get(action)
        if amount is None:
            await message.answer("❌ Неизвестный пакет.")
            return
        new_purchased = user['purchased_generations'] + amount
        await update_user(user_id, purchased_generations=new_purchased)
        await message.answer(
            f"✅ Оплата прошла успешно!\n"
            f"Добавлено **{amount}** генераций.\n"
            f"Теперь у вас {user['free_generations'] + new_purchased} доступных генераций."
        )

# ---------- Генерация картинки (с проверкой лимитов) ----------
async def generate_and_send_image(message: types.Message, prompt: str):
    user_id = message.from_user.id
    user = await get_or_create_user(user_id)
    premium = is_premium_active(user)

    if not premium:
        remaining = user['free_generations'] + user['purchased_generations']
        if remaining <= 0:
            await message.answer(
                "❌ У вас закончились бесплатные и купленные генерации!\n"
                "Купите новый пакет:",
                reply_markup=buy_keyboard()
            )
            return
        # Уменьшаем счётчик
        if user['free_generations'] > 0:
            new_free = user['free_generations'] - 1
            await update_user(user_id, free_generations=new_free)
        else:
            new_purchased = user['purchased_generations'] - 1
            await update_user(user_id, purchased_generations=new_purchased)

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
                    user_last_prompt[user_id] = prompt
                    await update_user(user_id, total_generated=user['total_generated'] + 1)

                    # Отправляем фото
                    await message.reply_photo(
                        BufferedInputFile(image_data, filename="generated.jpg"),
                        caption=f"🖼 По запросу: {prompt}",
                        reply_markup=after_generation_keyboard(prompt)
                    )

                    # Отправляем информацию об остатке (если не премиум)
                    if not premium:
                        # Перечитываем пользователя, чтобы получить актуальные значения
                        updated_user = await get_user(user_id)
                        if updated_user:
                            free = updated_user['free_generations']
                            purchased = updated_user['purchased_generations']
                            await message.answer(
                                f"📊 Осталось генераций: {free} бесплатных + {purchased} купленных = {free + purchased}"
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

# ---------- Фоновая задача: еженедельная рассылка о премиуме ----------
async def weekly_promo():
    while True:
        now = datetime.now()
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour >= 12:
            pass
        else:
            next_sunday = now + timedelta(days=days_until_sunday)
            next_sunday = next_sunday.replace(hour=12, minute=0, second=0, microsecond=0)
            wait_seconds = (next_sunday - now).total_seconds()
            if wait_seconds < 0:
                wait_seconds += 7 * 24 * 3600
            await asyncio.sleep(wait_seconds)

        week_ago = datetime.now() - timedelta(days=7)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                'SELECT user_id, premium_until FROM users WHERE (premium_until IS NULL OR premium_until < ?) AND (last_promo_sent IS NULL OR last_promo_sent < ?)',
                (datetime.now().isoformat(), week_ago)
            ) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    user_id = row[0]
                    try:
                        await bot.send_message(
                            user_id,
                            "🌟 **Премиум-доступ!** 🌟\n\n"
                            "Получи **безлимит на генерацию фото** и доступ к **генерации видео** всего за **300 звёзд** на месяц!\n"
                            "Нажми /profile и выбери «Купить премиум».",
                            reply_markup=buy_keyboard()
                        )
                        await update_user(user_id, last_promo_sent=datetime.now())
                    except Exception as e:
                        logging.error(f"Не удалось отправить промо пользователю {user_id}: {e}")

# ---------- Запуск ----------
async def main():
    await init_db()
    asyncio.create_task(weekly_promo())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())