# bot.py
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

import config

# ---- LOGGER ----
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---- BOT / DISPATCHER (fast mode) ----
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage(), fast_mode=True)

# ---- persistent media storage (file_ids) ----
MEDIA_STORE_PATH = Path("media_store.json")

def load_media_store():
    # load saved file_ids (if exist), merge with config.MEDIA defaults
    ms = dict(config.MEDIA or {})
    if MEDIA_STORE_PATH.exists():
        try:
            j = json.loads(MEDIA_STORE_PATH.read_text("utf-8"))
            ms.update(j)
        except Exception:
            logger.exception("Can't read media_store.json")
    return ms

def save_media_store(media_dict):
    try:
        MEDIA_STORE_PATH.write_text(json.dumps(media_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Can't write media_store.json")

MEDIA = load_media_store()  # in-memory media mapping
# pending map for /setmedia command: admin_id -> key (waiting for admin to send file)
PENDING_SET_MEDIA = {}

# ---- Questions (1..22) ----
QUESTIONS = [
    "1/22. Ism-familyangizni yozing:",
    "2/22. Telefon raqamingizni yozing:\n\nMisol: +998909998877",
    "3/22. Doimiy yashash manzilingizni yozing (propiska):",
    "4/22. O'z tug'ilgan kuningizni 01.01.2000 formatda yozing:",
    "5/22. Ma'lumotingiz (tugmani tanlang yoki yozing):",
    "6/22. Oldin qaysi korxonalarda va qaysi lavozimda ishlagansiz?\n\nMisol:\n1. Perfect Consulting Group - Sotuv menejeri\n2. Alora - sotuvchi\n3. Ishlamaganman",
    "7/22. Oila qurganmisiz?",
    "8/22. (служебный шаг — бот отправит голос, а затем ждёт voice от пользователя)",
    "9/22. Iltimos, ovozli xabar yuboring (mikrofonga yozib).",
    "10/22. Rus tilini qay darajada bilasiz?",
    "11/22. Iltimos, qisqa video yuboring (selfie video).",
    "12/22. Oxirgi ish joyingizdan siz haqingizda surishtirishimizga rozimisiz? (ha/yo'q)",
    "13/22. Oxirgi ish joyingizdan kim sizga tavsiya xati bera oladi, nomi, ishlash joyi, lavozimi, telefon raqami:\n\nMisol: Direktor - Malika Akramovna - Nona collection - +998909998877",
    "14/22. Bizning korxonada qancha muddat ishlamoqchisiz?",
    "15/22. Korxonada ishdan keyin ham qolib ishlash kerak bo‘lib qolsa ishlaysizmi?",
    "16/22. Sog‘ligingizda muammo yo‘qmi?",
    "17/22. Nima uchun ayrim odamlar ishga kech kelishadi?",
    "18/22. Nima uchun ayrim insonlar o'g'rilik qilishadi?",
    "19/22. Nima uchun ayrim ishchilar yaxshi ishlashadi, ayrimlari yomon? Bunga sabab nima?",
    "20/22. Oldingi ishxonangizda qancha maoshga ishlgansiz?",
    "21/22. Bizning ishxonamizda qancha maoshga ishlamoqchisiz?",
    "22/22. Qanday kurslarda o’qigansiz?"
]

# ---- Keyboards ----
def job_types_kb() -> InlineKeyboardMarkup:
    items = getattr(config, "JOB_TYPES", ["Sotuvchi", "Marketolog", "HR", "Omborchi", "Boshqa"])
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    row = []
    for t in items:
        row.append(InlineKeyboardButton(text=t, callback_data=f"job|{t}"))
        if len(row) == 2:
            kb.inline_keyboard.append(row)
            row = []
    if row:
        kb.inline_keyboard.append(row)
    return kb

def education_kb() -> InlineKeyboardMarkup:
    opts = ["o'rta", "o'rta maxsus", "oliy"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=o, callback_data=f"edu|{o}")] for o in opts])

def marital_kb() -> InlineKeyboardMarkup:
    opts = ["turmush qurganman", "turmush qurmaganman", "ajrashganman"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=o, callback_data=f"marital|{o}")] for o in opts])

def rus_level_kb() -> InlineKeyboardMarkup:
    opts = ["a'lo", "yaxshi", "past", "bilmayman"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=o, callback_data=f"rus|{o}")] for o in opts])

def yesno_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ha", callback_data="consent|ha"),
         InlineKeyboardButton(text="yo'q", callback_data="consent|yoq")]
    ])

def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamini yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )

# ---- FSM states ----
class FormState(StatesGroup):
    waiting_job = State()
    q1 = State()
    q2 = State()
    q3 = State()
    q4 = State()
    q5 = State()
    q6 = State()
    q7 = State()
    q8 = State()   # service (bot sends voice prompt)
    q9 = State()   # strict voice expected
    q10 = State()  # rus level
    q11 = State()  # strict video expected
    q12 = State()
    q13 = State()
    q14 = State()
    q15 = State()
    q16 = State()
    q17 = State()
    q18 = State()
    q19 = State()
    q20 = State()
    q21 = State()
    q22 = State()

# ---- Utilities ----
def file_exists(path: str) -> bool:
    return bool(path) and os.path.isfile(path)

def validate_date(text: str) -> bool:
    try:
        datetime.strptime(text.strip(), "%d.%m.%Y")
        return True
    except Exception:
        return False

def validate_phone(text: str) -> bool:
    return bool(re.fullmatch(r"\+?\d[\d\s\-]{7,20}", text.strip()))

# ---- Admin helpers: /setmedia, /getmedia ----
@dp.message(Command("setmedia"))
async def cmd_setmedia(message: Message):
    # Usage: /setmedia start_video
    user_id = message.from_user.id
    if user_id not in config.ADMINS:
        return await message.answer("⛔ Faqat adminlar uchun.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Foydalanish: /setmedia <key>\nKey-lar: start_video, q9_voice_prompt, q11_video_prompt")
    key = parts[1].strip()
    if key not in ("start_video", "q9_voice_prompt", "q11_video_prompt"):
        return await message.answer("Noto'g'ri key. Ruxsat etilgan: start_video, q9_voice_prompt, q11_video_prompt")
    PENDING_SET_MEDIA[user_id] = key
    await message.answer(f"Yaxshi — endi {key} uchun media yuboring (video yoki voice). Bot file_id-ni saqlaydi.")

@dp.message(Command("getmedia"))
async def cmd_getmedia(message: Message):
    user_id = message.from_user.id
    if user_id not in config.ADMINS:
        return await message.answer("⛔ Faqat adminlar uchun.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Foydalanish: /getmedia <key>")
    key = parts[1].strip()
    value = MEDIA.get(key, "")
    if not value:
        return await message.answer(f"{key} hozircha o'rnatilmagan.")
    await message.answer(f"{key} => `{value}`", parse_mode="Markdown")

# When admin sends any media while pending, save file_id
@dp.message(F.voice | F.video | F.video_note | F.document)
async def handle_media_saving(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id in PENDING_SET_MEDIA:
        key = PENDING_SET_MEDIA.pop(user_id)
        file_id = None
        # prefer voice -> video -> document
        if message.voice:
            file_id = message.voice.file_id
        elif message.video:
            file_id = message.video.file_id
        elif message.video_note:
            file_id = message.video_note.file_id
        elif message.document:
            file_id = message.document.file_id
        if file_id:
            MEDIA[key] = file_id
            save_media_store(MEDIA)
            await message.answer(f"✅ Saved `{key}` as file_id:\n`{file_id}`", parse_mode="Markdown")
        else:
            await message.answer("⚠️ Файл принят, но не удалось получить file_id.")
        return
    # else: not setting media — normal flow will continue (other handlers)
    # do nothing here (other handlers will respond according to FSM)

# ---- Misc admin commands ----
@dp.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Sizning ID: <code>{message.from_user.id}</code>", parse_mode="HTML")

@dp.message(Command("list_admins"))
async def cmd_list_admins(message: Message):
    if message.from_user.id not in config.ADMINS:
        return await message.answer("⛔ Bu buyruq faqat adminlar uchun.")
    await message.answer("Adminlar:\n" + "\n".join(str(x) for x in config.ADMINS))

@dp.message(Command("add_admin"))
async def cmd_add_admin(message: Message):
    if message.from_user.id not in config.ADMINS:
        return await message.answer("⛔ Bu buyruq faqat bosh admin uchun.")
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer("Foydalanish: /add_admin 123456789")
    new_id = int(parts[1])
    if new_id in config.ADMINS:
        return await message.answer("Bu ID allaqachon admin.")
    config.ADMINS.append(new_id)
    await message.answer(f"✅ Admin qo'shildi: {new_id}")

@dp.message(Command("remove_admin"))
async def cmd_remove_admin(message: Message):
    if message.from_user.id not in config.ADMINS:
        return await message.answer("⛔ Bu buyruq faqat bosh admin uchun.")
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer("Foydalanish: /remove_admin 123456789")
    rem = int(parts[1])
    if rem not in config.ADMINS:
        return await message.answer("Bu ID adminlar ro'yxatida yo'q.")
    if rem == config.ADMINS[0]:
        return await message.answer("Asosiy adminni olib tashlab bo'lmaydi.")
    config.ADMINS.remove(rem)
    await message.answer(f"✅ Admin o'chirildi: {rem}")

# ---- START handler: send start_video (file_id or local file) + buttons ----
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    caption = "Assalomu alaykum! 👋 Ish turini tanlang va qisqa anketani to‘ldiring."
    start_vid = MEDIA.get("start_video") or ""

    sent = False
    if start_vid:
        # if saved as file_id (short strange string) -> send directly
        if not file_exists(start_vid):
            try:
                await message.answer_video(start_vid, caption=caption)
                sent = True
            except Exception:
                sent = False
        else:
            try:
                with open(start_vid, "rb") as f:
                    await message.answer_video(video=f, caption=caption)
                    sent = True
            except Exception:
                sent = False
    if not sent:
        await message.answer(caption)

    kb_msg = await message.answer("👇 Ish turini tanlang:", reply_markup=job_types_kb())
    await state.set_state(FormState.waiting_job)
    await state.update_data(menu_msg_id=kb_msg.message_id, answers={})

# ---- Callback: job selection (no state kw arg — check inside) ----
@dp.callback_query(F.data.startswith("job|"))
async def cb_job_choice(callback: CallbackQuery, state: FSMContext):
    # check that user is in waiting_job
    if await state.get_state() != FormState.waiting_job.state:
        await callback.answer("⚠️ Hozir bu tugmani bosish mumkin emas.", show_alert=True)
        return

    job = callback.data.split("|", 1)[1]
    data = await state.get_data()
    answers = data.get("answers", {})
    answers["Ish turi"] = job
    await state.update_data(answers=answers)

    # remove only buttons message (keep video)
    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.answer("Tanlandi ✅")
    await callback.message.answer(QUESTIONS[0])  # Q1
    await state.set_state(FormState.q1)

# ---- Q1..Q4 (text with phone contact and date validation) ----
@dp.message(FormState.q1)
async def q1_name(message: Message, state: FSMContext):
    answers = (await state.get_data()).get("answers", {})
    answers["Ism-familya"] = (message.text or "").strip()
    await state.update_data(answers=answers)
    await message.answer(QUESTIONS[1], reply_markup=contact_kb())
    await state.set_state(FormState.q2)

@dp.message(FormState.q2)
async def q2_phone(message: Message, state: FSMContext):
    phone = None
    if message.contact and message.contact.phone_number:
        phone = message.contact.phone_number
    elif message.text:
        t = message.text.strip()
        if validate_phone(t):
            phone = t
        else:
            return await message.answer("📞 Telefon raqamini to‘g‘ri formatda yozing. Misol: +998909998877", reply_markup=contact_kb())

    answers = (await state.get_data()).get("answers", {})
    answers["Telefon"] = phone
    await state.update_data(answers=answers)
    await message.answer(QUESTIONS[2], reply_markup=ReplyKeyboardRemove())
    await state.set_state(FormState.q3)

@dp.message(FormState.q3)
async def q3_address(message: Message, state: FSMContext):
    answers = (await state.get_data()).get("answers", {})
    answers["Manzil (propiska)"] = (message.text or "").strip()
    await state.update_data(answers=answers)
    await message.answer(QUESTIONS[3])
    await state.set_state(FormState.q4)

@dp.message(FormState.q4)
async def q4_dob(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if not validate_date(txt):
        return await message.answer("Tug'ilgan kuningizni 01.01.2000 formatda yozing.")
    answers = (await state.get_data()).get("answers", {})
    answers["Tug'ilgan sana"] = txt
    await state.update_data(answers=answers)
    await message.answer(QUESTIONS[4], reply_markup=education_kb())
    await state.set_state(FormState.q5)

# ---- Q5: education (btn or text) ----
@dp.callback_query(F.data.startswith("edu|"))
async def cb_q5(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != FormState.q5.state:
        await callback.answer("⚠️ Hozir bu tugmani bosish mumkin emas.", show_alert=True)
        return
    edu = callback.data.split("|", 1)[1]
    answers = (await state.get_data()).get("answers", {})
    answers["Ma'lumoti"] = edu
    await state.update_data(answers=answers)
    await callback.answer("Tanlandi ✅")
    await callback.message.answer(QUESTIONS[5], reply_markup=ReplyKeyboardRemove())
    await state.set_state(FormState.q6)

@dp.message(FormState.q5)
async def q5_text(message: Message, state: FSMContext):
    answers = (await state.get_data()).get("answers", {})
    answers["Ma'lumoti"] = (message.text or "").strip()
    await state.update_data(answers=answers)
    await message.answer(QUESTIONS[5], reply_markup=ReplyKeyboardRemove())
    await state.set_state(FormState.q6)

# ---- Q6 experience -> Q7 marital ----
@dp.message(FormState.q6)
async def q6_experience(message: Message, state: FSMContext):
    answers = (await state.get_data()).get("answers", {})
    answers["Ish tajribasi"] = (message.text or "").strip()
    await state.update_data(answers=answers)
    await message.answer(QUESTIONS[6], reply_markup=marital_kb())
    await state.set_state(FormState.q7)

# ---- Q7: marital -> then bot sends your voice (q9_voice_prompt) and sets state q9 (strict voice expected) ----
@dp.callback_query(F.data.startswith("marital|"))
async def cb_q7(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != FormState.q7.state:
        await callback.answer("⚠️ Hozir bu tugmani bosish mumkin emas.", show_alert=True)
        return
    marital = callback.data.split("|", 1)[1]
    answers = (await state.get_data()).get("answers", {})
    answers["Oilaviy holat"] = marital
    await state.update_data(answers=answers)
    await callback.answer("Tanlandi ✅")

    # send your prepared voice prompt (q9_voice_prompt)
    vfile = MEDIA.get("q9_voice_prompt", "")
    sent = False
    if vfile:
        if not file_exists(vfile):
            try:
                await callback.message.answer_voice(vfile, caption=QUESTIONS[8])
                sent = True
            except Exception:
                sent = False
        else:
            try:
                with open(vfile, "rb") as f:
                    await callback.message.answer_voice(voice=f, caption=QUESTIONS[8])
                    sent = True
            except Exception:
                sent = False
    if not sent:
        await callback.message.answer(QUESTIONS[8])

    await state.set_state(FormState.q9)

@dp.message(FormState.q7)
async def q7_text(message: Message, state: FSMContext):
    answers = (await state.get_data()).get("answers", {})
    answers["Oilaviy holat"] = (message.text or "").strip()
    await state.update_data(answers=answers)

    vfile = MEDIA.get("q9_voice_prompt", "")
    sent = False
    if vfile:
        if not file_exists(vfile):
            try:
                await message.answer_voice(vfile, caption=QUESTIONS[8])
                sent = True
            except Exception:
                sent = False
        else:
            try:
                with open(vfile, "rb") as f:
                    await message.answer_voice(voice=f, caption=QUESTIONS[8])
                    sent = True
            except Exception:
                sent = False
    if not sent:
        await message.answer(QUESTIONS[8])

    await state.set_state(FormState.q9)

# ---- Q9: strictly voice from user ----
@dp.message(FormState.q9)
async def q9_voice(message: Message, state: FSMContext):
    if not message.voice:
        return await message.answer("📢 Iltimos, ovozli xabar yuboring (voice).")
    answers = (await state.get_data()).get("answers", {})
    answers["Voice file_id"] = message.voice.file_id
    await state.update_data(answers=answers)

    # ask rus level (10/22)
    await message.answer(QUESTIONS[9], reply_markup=rus_level_kb())
    await state.set_state(FormState.q10)

# ---- Q10: rus level -> then bot sends your prepared video prompt for Q11 ----
@dp.callback_query(F.data.startswith("rus|"))
async def cb_q10(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != FormState.q10.state:
        await callback.answer("⚠️ Hozir bu tugmani bosish mumkin emas.", show_alert=True)
        return
    val = callback.data.split("|", 1)[1]
    answers = (await state.get_data()).get("answers", {})
    answers["Rus tili"] = val
    await state.update_data(answers=answers)
    await callback.answer("Tanlandi ✅")

    # send prepared video prompt (q11_video_prompt)
    vfile = MEDIA.get("q11_video_prompt", "")
    sent = False
    if vfile:
        if not file_exists(vfile):
            try:
                await callback.message.answer_video(vfile, caption=QUESTIONS[10])
                sent = True
            except Exception:
                sent = False
        else:
            try:
                with open(vfile, "rb") as f:
                    await callback.message.answer_video(video=f, caption=QUESTIONS[10])
                    sent = True
            except Exception:
                sent = False
    if not sent:
        await callback.message.answer(QUESTIONS[10])

    await state.set_state(FormState.q11)

@dp.message(FormState.q10)
async def q10_text(message: Message, state: FSMContext):
    answers = (await state.get_data()).get("answers", {})
    answers["Rus tili"] = (message.text or "").strip()
    await state.update_data(answers=answers)

    vfile = MEDIA.get("q11_video_prompt", "")
    sent = False
    if vfile:
        if not file_exists(vfile):
            try:
                await message.answer_video(vfile, caption=QUESTIONS[10])
                sent = True
            except Exception:
                sent = False
        else:
            try:
                with open(vfile, "rb") as f:
                    await message.answer_video(video=f, caption=QUESTIONS[10])
                    sent = True
            except Exception:
                sent = False
    if not sent:
        await message.answer(QUESTIONS[10])

    await state.set_state(FormState.q11)

# ---- Q11: strictly video from user ----
@dp.message(FormState.q11)
async def q11_video(message: Message, state: FSMContext):
    if not (message.video or message.video_note):
        return await message.answer("🎥 Iltimos, qisqa video yuboring (kamera orqali).")
    answers = (await state.get_data()).get("answers", {})
    vf = message.video.file_id if message.video else message.video_note.file_id
    answers["Video file_id"] = vf
    await state.update_data(answers=answers)

    await message.answer(QUESTIONS[11], reply_markup=yesno_kb())
    await state.set_state(FormState.q12)

# ---- Q12: consent (ha/yo'q) ----
@dp.callback_query(F.data.startswith("consent|"))
async def cb_q12(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != FormState.q12.state:
        # allow anyway if user wasn't in exact state
        pass
    val = callback.data.split("|", 1)[1]
    answers = (await state.get_data()).get("answers", {})
    answers["Rozilik (surishtirish)"] = val
    await state.update_data(answers=answers)
    await callback.answer("Qabul qilindi ✅")
    await callback.message.answer(QUESTIONS[12], reply_markup=ReplyKeyboardRemove())
    await state.set_state(FormState.q13)

# ---- Q13..Q22: text answers ----
async def save_text_and_next(message: Message, state: FSMContext, key: str, next_state: State | None, next_question_index: int | None):
    answers = (await state.get_data()).get("answers", {})
    answers[key] = (message.text or "").strip()
    await state.update_data(answers=answers)
    if next_state:
        # ask next question
        if next_question_index is not None:
            await message.answer(QUESTIONS[next_question_index])
        await state.set_state(next_state)
    else:
        await finish_and_send(message, state)

@dp.message(FormState.q13)
async def q13(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Tavsiya beruvchi", FormState.q14, 13)

@dp.message(FormState.q14)
async def q14(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Bizda qancha muddat ishlamoqchi", FormState.q15, 14)

@dp.message(FormState.q15)
async def q15(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Ishdan keyin qolish rozilik", FormState.q16, 15)

@dp.message(FormState.q16)
async def q16(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Sog'liq holati", FormState.q17, 16)

@dp.message(FormState.q17)
async def q17(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Nega kech kelishadi", FormState.q18, 17)

@dp.message(FormState.q18)
async def q18(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Nega o'g'rilik qilishadi", FormState.q19, 18)

@dp.message(FormState.q19)
async def q19(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Ish sifati sababi", FormState.q20, 19)

@dp.message(FormState.q20)
async def q20(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Oldingi maosh", FormState.q21, 20)

@dp.message(FormState.q21)
async def q21(message: Message, state: FSMContext):
    await save_text_and_next(message, state, "Istalgan maosh", FormState.q22, 21)

@dp.message(FormState.q22)
async def q22(message: Message, state: FSMContext):
    # final answer for 22
    answers = (await state.get_data()).get("answers", {})
    answers["Kurslar"] = (message.text or "").strip()
    await state.update_data(answers=answers)
    await finish_and_send(message, state)

# ---- Final: build report and send to admins ----
async def finish_and_send(message: Message, state: FSMContext):
    data = await state.get_data()
    answers = data.get("answers", {})
    user = message.from_user

    lines = [
        "📝 *Yangi anketa*",
        f"👤 Nomzod: {user.full_name} (@{user.username or '-'})",
        f"🆔 ID: `{user.id}`",
        f"💼 Ish turi: {answers.get('Ish turi','-')}",
        ""
    ]
    for k, v in answers.items():
        if k in ("Voice file_id", "Video file_id"):
            continue
        lines.append(f"*{k}:* {v}")
    text = "\n".join(lines)

    for admin_id in getattr(config, "ADMINS", []):
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown")
            # resend voice/video if exist
            if "Voice file_id" in answers:
                try:
                    await bot.send_voice(admin_id, answers["Voice file_id"], caption="📢 Nomzod ovozli javobi (9/22)")
                except Exception:
                    logger.exception("cant send voice")
            if "Video file_id" in answers:
                try:
                    await bot.send_video(admin_id, answers["Video file_id"], caption="🎥 Nomzod video javobi (11/22)")
                except Exception:
                    logger.exception("cant send video")
        except Exception:
            logger.exception("Failed to send to admin %s", admin_id)

    await message.answer("✅ Ma'lumotlaringiz qabul qilindi. Tez orada xabarini beramiz!", reply_markup=ReplyKeyboardRemove())
    await state.clear()

# ---- Start polling ----
async def main():
    logger.info("Bot started")
    # skip_updates=True чтобы бот не обрабатывал старые апдейты при перезапуске
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
