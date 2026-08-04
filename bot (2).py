import asyncio
import os
import re
import aiohttp
from datetime import datetime, date
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery,
    ReplyKeyboardRemove,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database import Database

# ─── SOZLAMALAR ────────────────────────────────────────────────
API_TOKEN        = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_ID         = int(os.getenv("ADMIN_ID", "123456789"))
CHANNEL_ID       = int(os.getenv("CHANNEL_ID", "-1001234567890"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "yourchannel")
CARD_NUMBER      = os.getenv("CARD_NUMBER", "8600000000000000")
CARD_OWNER       = os.getenv("CARD_OWNER", "Familiya I")   # ← karta egasi ismi
TGLION_API_KEY   = os.getenv("TGLION_API_KEY", "YOUR_TGLION_KEY")
TGLION_YOUR_ID   = os.getenv("TGLION_YOUR_ID", "YOUR_TGLION_ID")
TGLION_BASE      = "https://TG-Lion.net"
ORDERS_CHANNEL   = os.getenv("ORDERS_CHANNEL", "-1001234567890")

# HUMO bot Telegram ID — o'zgarmaydi
HUMO_BOT_ID = 5537718006

BLOCKED_COUNTRIES = {"CO", "NG", "ZW"}

# ─── BOT SOZLASH ───────────────────────────────────────────────
bot         = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="html"))
storage     = MemoryStorage()
dp          = Dispatcher(storage=storage)
router      = Router()
admin_router = Router()

db = Database()

# ─── HOLATLAR ──────────────────────────────────────────────────
class PayStates(StatesGroup):
    wait_amount = State()
    wait_check  = State()

class HumoPayStates(StatesGroup):
    wait_amount = State()

class BalanceChangeState(StatesGroup):
    wait_user_id = State()
    wait_amount  = State()

class AdminSearchState(StatesGroup):
    wait_phone = State()

class BroadcastState(StatesGroup):
    wait_message = State()

class PhoneState(StatesGroup):
    wait_phone = State()

class AdminSettingsState(StatesGroup):
    wait_daily_bonus         = State()
    wait_referral_bonus      = State()
    wait_bulk_percent        = State()
    wait_channel_id          = State()
    wait_channel_username    = State()
    wait_price_value         = State()
    wait_orders_channel_id   = State()
    wait_orders_channel_username = State()
    wait_card_number         = State()
    wait_card_owner          = State()

# ─── MENYULAR ──────────────────────────────────────────────────
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📞 Nomer olish"),   KeyboardButton(text="🛒 Buyurtmalarim")],
        [KeyboardButton(text="💰 Hisobim"),        KeyboardButton(text="💳 Hisob To'ldirish")],
        [KeyboardButton(text="💸 Pul Ishlash"),    KeyboardButton(text="📕 Qo'llanma")],
        [KeyboardButton(text="🆘 Qo'llab-quvvatlash")],
    ],
    resize_keyboard=True
)

def phone_request_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ─── SOZLAMALARNI DB DAN OLISH ─────────────────────────────────
async def get_setting(key: str, default):
    val = await db.get_setting(key)
    if val is None:
        return default
    try:
        return type(default)(val)
    except:
        return default

# ─── TG-LION API ───────────────────────────────────────────────
async def api_get(action: str, extra: dict = None) -> dict:
    params = {"action": action, "apiKey": TGLION_API_KEY, "YourID": TGLION_YOUR_ID}
    if extra:
        params.update(extra)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(TGLION_BASE, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                return await r.json(content_type=None)
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def api_available_countries() -> dict:
    return await api_get("available_countries")

async def api_get_balance_tglion() -> dict:
    return await api_get("get_balance")

async def api_buy_number(country_code: str) -> dict:
    return await api_get("getNumber", {"country_code": country_code})

async def api_get_code(number: str) -> dict:
    return await api_get("getCode", {"number": number})

# ══════════════════════════════════════════════════════════════
#  HUMO SMS ORQALI AVTOMATIK TO'LOV
# ══════════════════════════════════════════════════════════════

def parse_humo_amount(text: str) -> int | None:
    """
    HUMO botidan kelgan SMS dan summani ajratib oladi.
    Turli xil formatlarni qo'llab-quvvatlaydi.
    """
    patterns = [
        r'\+?\s*([\d][\d\s]*[\d])[.,]?\d*\s*UZS',
        r'([\d][\d\s]{3,})[.,]\d{2}\s*UZS',
        r'Hisobingizga\s+([\d\s,]+)\s*UZS',
        r'(\d{4,})\s*so\'?m',
        r'summa[:\s]+([\d\s]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = re.sub(r'[\s,]', '', match.group(1))
            try:
                amount = int(raw)
                if 1000 <= amount <= 10_000_000:
                    return amount
            except:
                continue
    return None


async def humo_confirm_payment(pay_id: str, user_id: int, amount: int, fullname: str):
    """To'lovni tasdiqlaydi va xabar yuboradi."""
    await db.update_balance(user_id, amount)
    await db.update_total_deposited(user_id, amount)
    await db.delete_pending_payment(pay_id)
    user_balance = await db.get_balance(user_id)

    try:
        await bot.send_message(
            user_id,
            f"✅ To'lovingiz tasdiqlandi!\n\n"
            f"💰 Hisobingizga <b>{amount:,} so'm</b> qo'shildi!\n"
            f"💼 Joriy balans: <b>{user_balance:,} so'm</b>"
        )
    except:
        pass

    sav = datetime.now().strftime("%H:%M:%S | %Y-%m-%d")
    try:
        await bot.send_message(
            ADMIN_ID,
            f"✅ HUMO orqali to'lov avtomatik tasdiqlandi!\n\n"
            f"👤 {fullname} (<code>{user_id}</code>)\n"
            f"💰 +{amount:,} so'm\n"
            f"💼 Yangi balans: {user_balance:,} so'm\n"
            f"⏰ {sav}"
        )
    except:
        pass


# ─── HUMO BOT XABARLARINI TINGLASH ────────────────────────────
# XATO TUZATILDI: bu handler avval `router`da edi va F.forward_origin filtri
# juda keng edi — oddiy foydalanuvchi istalgan xabarni forward qilsa, u xabar
# "yutilib" ketardi (hech qanday javob qaytmasdi). Endi u faqat admin_router
# ichida, va filtrning o'zida ADMIN_ID tekshiriladi — shu bilan oddiy
# foydalanuvchilarning /start va boshqa xabarlariga hech qanday ta'sir qilmaydi.
@admin_router.message(F.forward_origin, F.from_user.id == ADMIN_ID)
async def humo_forward_handler(msg: Message):
    """
    Admin chatida @HUMOcardbot dan forward xabar kelsa — avtomatik tasdiqlanadi.
    Mijozga bu jarayon ko'rinmaydi.
    """
    # Forward qilingan xabar HUMO botdan ekanini tekshirish
    origin = msg.forward_origin
    sender_id = None
    if hasattr(origin, 'sender_user') and origin.sender_user:
        sender_id = origin.sender_user.id
    elif hasattr(origin, 'sender_chat') and origin.sender_chat:
        sender_id = origin.sender_chat.id

    if sender_id != HUMO_BOT_ID:
        return  # Boshqa forward — e'tibor bermaymiz

    text = msg.text or msg.caption or ""
    amount = parse_humo_amount(text)

    if not amount:
        await msg.answer(
            f"⚠️ HUMO SMS keldi, lekin summa aniqlanmadi.\n"
            f"Qo'lda: <code>/addbal USER_ID SUMMA</code>"
        )
        return

    pending_list = await db.find_pending_by_amount(amount)

    if not pending_list:
        await msg.answer(
            f"💰 HUMO: <b>{amount:,} so'm</b> keldi.\n"
            f"❌ Mos kutayotgan to'lov topilmadi.\n\n"
            f"Qo'lda: <code>/addbal USER_ID {amount}</code>"
        )
        return

    if len(pending_list) == 1:
        pay = pending_list[0]
        await humo_confirm_payment(pay["pay_id"], pay["user_id"], amount, pay["fullname"])
        await msg.answer(f"✅ Avtomatik tasdiqlandi! {pay['fullname']} → +{amount:,} so'm")
    else:
        # Bir nechta mos — admin tanlaydi
        buttons = []
        for pay in pending_list:
            buttons.append([InlineKeyboardButton(
                text=f"👤 {pay['fullname']} — {pay['amount']:,} so'm",
                callback_data=f"humo_pick:{pay['pay_id']}:{amount}"
            )])
        buttons.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="humo_cancel")])
        await msg.answer(
            f"💰 HUMO: <b>{amount:,} so'm</b> — kimga tasdiqlaysiz?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )


@admin_router.callback_query(F.data.startswith("humo_pick:"))
async def humo_pick_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    _, pay_id, amount_str = call.data.split(":", 2)
    amount = int(amount_str)
    pay = await db.get_pending_payment(pay_id)
    if not pay:
        return await call.answer("❌ Allaqachon tasdiqlangan!", show_alert=True)
    await humo_confirm_payment(pay["pay_id"], pay["user_id"], amount, pay["fullname"])
    await call.message.edit_text(f"✅ {pay['fullname']} → +{amount:,} so'm tasdiqlandi!")
    await call.answer()


@admin_router.callback_query(F.data == "humo_cancel")
async def humo_cancel_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("❌ Bekor qilindi.")
    await call.answer()


# ─── ADMIN BUYRUQ: QO'LDA BALANS QO'SHISH ─────────────────────
@admin_router.message(F.text.regexp(r'^/addbal\s+\d+\s+\d+$'))
async def addbal_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.strip().split()
    uid   = int(parts[1])
    amount = int(parts[2])
    user = await db.get_user(uid)
    if not user:
        return await msg.answer("❌ Foydalanuvchi topilmadi!")
    await db.update_balance(uid, amount)
    await db.update_total_deposited(uid, amount)
    bal = await db.get_balance(uid)
    try:
        await bot.send_message(uid, f"✅ Hisobingizga <b>{amount:,} so'm</b> qo'shildi!\n💼 Balans: <b>{bal:,} so'm</b>")
    except:
        pass
    await msg.answer(f"✅ {user['fullname']} ga {amount:,} so'm qo'shildi. Yangi balans: {bal:,} so'm")

# ══════════════════════════════════════════════════════════════
#  HISOB TO'LDIRISH — HUMO USLUBI (rasmga o'xshash)
# ══════════════════════════════════════════════════════════════

@router.message(F.text == "💳 Hisob To'ldirish")
async def topup_menu(msg: Message, state: FSMContext):
    await msg.answer(
        "💳 <b>Hisob To'ldirish</b>\n\n"
        "Necha so'm to'lamoqchisiz?\n\n"
        "📌 Minimal: <b>1 000 so'm</b>\n"
        "📌 Maksimal: <b>10 000 000 so'm</b>\n\n"
        "Faqat son kiriting:"
    )
    await state.set_state(HumoPayStates.wait_amount)


@router.message(HumoPayStates.wait_amount)
async def humo_pay_amount(msg: Message, state: FSMContext):
    if not msg.text or not msg.text.strip().isdigit():
        await msg.answer("❌ Faqat raqam kiriting!")
        return

    amount = int(msg.text.strip())
    if not (1000 <= amount <= 10_000_000):
        await msg.answer(
            "❌ Miqdor noto'g'ri!\n"
            "⬇️ Minimal: <b>1 000 so'm</b>\n"
            "⬆️ Maksimal: <b>10 000 000 so'm</b>"
        )
        return

    await state.clear()

    user_id  = msg.from_user.id
    fullname = msg.from_user.full_name

    # Pending payment yaratish
    pay_id = f"humo_{user_id}_{amount}_{msg.message_id}"
    await db.add_pending_payment(pay_id, user_id, amount, fullname)

    card   = await get_setting("card_number", CARD_NUMBER)
    owner  = await get_setting("card_owner", CARD_OWNER)

    # Rasmga o'xshash UI
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏧 Kartani raqamni Nusxa olish", callback_data=f"copy_card:{card}")],
        [InlineKeyboardButton(text="💸 To'lov miqdorni Nusxa olish", callback_data=f"copy_amount:{amount}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel_pay:{pay_id}")],
    ])

    await msg.answer(
        f"✅ <b>To'lov miqdori qabul qilindi!</b>\n\n"
        f"💸 To'lashingiz kerak: <b>{amount:,} so'm</b>\n"
        f"🏧 Karta Egasi: <b>{owner}</b>\n"
        f"💳 Karta: <b>{card}</b>\n\n"
        f"👆 Ushbu tepadagi kartaga siz istalgan to'lov tizimi orqali <b>{amount:,} so'm</b> to'ling! "
        f"Undan 1 tiyin ko'p ham kam ham to'lov qilmang faqat belgilangan miqdorda to'lov qiling "
        f"va pul sizning hisobingizga avtomatik tushurib beriladi!\n\n"
        f"⚠️ <i>Diqqat: 5 daqiqa ichida to'lov qilmasangiz to'lovingiz qabul qilinmaydi!!!</i>\n\n"
        f"✅ To'lovni bajaring va to'lov avtomatik 1-2 daqiqa ichida tasdiqlanadi!",
        reply_markup=kb
    )

    # 5 daqiqadan keyin pending o'chirish (timeout)
    asyncio.create_task(_pay_timeout(pay_id, user_id, amount))


async def _pay_timeout(pay_id: str, user_id: int, amount: int):
    """5 daqiqa o'tsa to'lov avtomatik bekor qilinadi."""
    await asyncio.sleep(300)  # 5 daqiqa
    pay = await db.get_pending_payment(pay_id)
    if pay:
        await db.delete_pending_payment(pay_id)
        try:
            await bot.send_message(
                user_id,
                f"⏰ <b>{amount:,} so'm</b>lik to'lovingiz vaqti o'tdi va bekor qilindi.\n"
                f"Qayta to'ldirish uchun '💳 Hisob To'ldirish' tugmasini bosing."
            )
        except:
            pass


@router.callback_query(F.data.startswith("copy_card:"))
async def copy_card_cb(call: CallbackQuery):
    card = call.data.split(":", 1)[1]
    await call.answer(f"✅ Karta raqami: {card}", show_alert=True)


@router.callback_query(F.data.startswith("copy_amount:"))
async def copy_amount_cb(call: CallbackQuery):
    amount = call.data.split(":", 1)[1]
    await call.answer(f"✅ To'lov miqdori: {int(amount):,} so'm", show_alert=True)


@router.callback_query(F.data.startswith("cancel_pay:"))
async def cancel_pay_cb(call: CallbackQuery):
    pay_id = call.data.split(":", 1)[1]
    pay = await db.get_pending_payment(pay_id)
    if pay:
        await db.delete_pending_payment(pay_id)
    await call.message.edit_text("❌ To'lov bekor qilindi.")
    await call.answer()

# ══════════════════════════════════════════════════════════════
#  OBUNA, START, TELEFON
# ══════════════════════════════════════════════════════════════

async def check_subscription(user_id: int) -> bool:
    """
    XATO TUZATILDI: avval bare `except:` ishlatilgan bo'lib, har qanday xato
    (tarmoq uzilishi, timeout va h.k.) foydalanuvchini "obunachi emas" deb
    hisoblardi — hatto haqiqatda obuna bo'lgan bo'lsa ham. Endi faqat
    Telegram "user topilmadi / kanalda emas" xatosini obunasiz deb hisoblaymiz,
    boshqa (vaqtinchalik) xatolarda foydalanuvchini bloklamaymiz.
    """
    try:
        ch_id = await get_setting("required_channel_id", CHANNEL_ID)
        m = await bot.get_chat_member(int(ch_id), user_id)
        return m.status in ("creator", "administrator", "member")
    except TelegramBadRequest:
        return False
    except Exception:
        # Vaqtinchalik xato (tarmoq va h.k.) — foydalanuvchini jazolamaymiz
        return True

async def get_sub_kb():
    ch_un = await get_setting("required_channel_username", CHANNEL_USERNAME)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Kanalga obuna bo'lish", url=f"https://t.me/{ch_un}")],
        [InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub")]
    ])

def welcome_text(first_name: str) -> str:
    name = (first_name or "").strip()
    hello = f"👋 Assalomu alaykum, <b>{name}</b>!" if name else "👋 Assalomu alaykum!"
    return f"{hello}\nXush kelibsiz! Kerakli bo'limni tanlang 👇"

async def send_main(target, first_name: str = ""):
    """
    XATO TUZATILDI: xush kelibsiz matni ikki joyda (start_handler va
    phone_received ichida) alohida-alohida qo'lda yozilgan edi — vaqt
    o'tishi bilan ular bir-biridan farqlanib qolishi mumkin edi. Endi
    yagona joydan chiqadi va foydalanuvchi ismi bilan shaxsiylashtirilgan.
    """
    text = welcome_text(first_name)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=main_menu)
    else:
        await target.message.answer(text, reply_markup=main_menu)

async def _extract_referrer_id(msg: Message) -> int | None:
    args = msg.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1][4:])
            if referrer_id != msg.from_user.id:
                return referrer_id
        except ValueError:
            pass
    return None

@router.message(CommandStart())
async def start_handler(msg: Message, state: FSMContext):
    await state.clear()
    referrer_id = await _extract_referrer_id(msg)

    # XATO TUZATILDI: referal ID avval faqat FSM (MemoryStorage) holatida
    # saqlanardi. Bot deploy/qayta ishga tushganda MemoryStorage butunlay
    # o'chadi — natijada "obunani tasdiqlang" yoki "telefon yuboring"
    # bosqichida qolgan foydalanuvchining referali yo'qolib qolar edi.
    # Endi referal darhol PostgreSQL'ga (pending_referrers) yoziladi va
    # deploydan omon qoladi.
    if referrer_id:
        await db.add_pending_referrer(msg.from_user.id, referrer_id)

    if not await check_subscription(msg.from_user.id):
        await msg.answer("⚠️ Botdan foydalanish uchun avval kanalga obuna bo'ling!", reply_markup=await get_sub_kb())
        return

    user = await db.get_user(msg.from_user.id)
    if not user or not user.get("phone"):
        await msg.answer(
            "🚨 Botdan foydalanishni davom ettirish uchun pastdagi "
            "«📲 Telefon raqamni yuborish» tugmasini bosing:",
            reply_markup=phone_request_kb()
        )
        await state.set_state(PhoneState.wait_phone)
        return

    await db.add_user(msg.from_user.id, msg.from_user.full_name, str(msg.from_user.username or ""), referrer_id)
    await send_main(msg, msg.from_user.first_name)

@router.callback_query(F.data == "check_sub")
async def check_sub_cb(call: CallbackQuery, state: FSMContext):
    if not await check_subscription(call.from_user.id):
        return await call.answer("❌ Siz hali obuna bo'lmagansiz!", show_alert=True)
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass

    user = await db.get_user(call.from_user.id)
    if not user or not user.get("phone"):
        await bot.send_message(
            call.from_user.id,
            "✅ Obuna tasdiqlandi!\n\n"
            "🚨 Botdan foydalanishni davom ettirish uchun pastdagi "
            "«📲 Telefon raqamni yuborish» tugmasini bosing:",
            reply_markup=phone_request_kb()
        )
        await state.set_state(PhoneState.wait_phone)
        await call.answer()
        return

    await bot.send_message(call.from_user.id, "✅ Obuna tasdiqlandi!", reply_markup=main_menu)
    await call.answer()

# XATO TUZATILDI: avval bu handler faqat `PhoneState.wait_phone` holatida
# ishlagan. Bot qayta ishga tushganda (MemoryStorage tozalanganda)
# foydalanuvchi shu holatdan chiqib qolardi va tugmani bossa ham hech
# qanday javob kelmasdi — bot "jim" bo'lib qolgandek ko'rinardi. Endi
# kontakt istalgan vaqt qabul qilinadi, referal esa DB'dan olinadi.
@router.message(F.content_type == "contact")
async def phone_received(msg: Message, state: FSMContext):
    if msg.contact.user_id and msg.contact.user_id != msg.from_user.id:
        await msg.answer("⚠️ Iltimos, faqat o'z telefon raqamingizni yuboring!", reply_markup=phone_request_kb())
        return

    phone = msg.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    user_id  = msg.from_user.id
    fullname = msg.from_user.full_name
    username = str(msg.from_user.username or "")

    # Avval FSM holatidan, topilmasa DB'dagi doimiy yozuvdan referalni olamiz
    data = await state.get_data()
    referrer_id = data.get("referrer_id") or await db.get_pending_referrer(user_id)
    await state.clear()

    already_had_phone = bool((await db.get_user(user_id) or {}).get("phone"))

    await db.add_user(user_id, fullname, username, referrer_id)
    await db.update_phone(user_id, phone)
    await db.delete_pending_referrer(user_id)

    if referrer_id and referrer_id != user_id and not already_had_phone:
        if phone.startswith("+998"):
            ref_bonus = int(await get_setting("referral_bonus", 500))
            await db.update_balance(referrer_id, ref_bonus)
            await db.add_referral(referrer_id, user_id)
            try:
                await bot.send_message(
                    referrer_id,
                    f"🎉 Siz taklif qilgan foydalanuvchi botga qo'shildi!\n"
                    f"💰 Hisobingizga <b>{ref_bonus:,} so'm</b> qo'shildi!"
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                pass
        else:
            await db.add_referral(referrer_id, user_id)

    await msg.answer("✅ Telefon raqamingiz tasdiqlandi!", reply_markup=ReplyKeyboardRemove())
    await send_main(msg, msg.from_user.first_name)

@router.message(PhoneState.wait_phone)
async def phone_wrong(msg: Message):
    await msg.answer("⚠️ Iltimos, «📲 Telefon raqamni yuborish» tugmasini bosing!", reply_markup=phone_request_kb())

# ─── HISOBIM ───────────────────────────────────────────────────
@router.message(F.text == "💰 Hisobim")
async def show_balance(msg: Message):
    user = await db.get_user(msg.from_user.id)
    if not user:
        await db.add_user(msg.from_user.id, msg.from_user.full_name, "")
        user = await db.get_user(msg.from_user.id)
    text = (
        f"👤 <b>Shaxsiy kabinetingiz</b>\n\n"
        f"🆔 Tartib ID: <b>{user['tartib_id']}</b>\n"
        f"🆔 Shaxsiy ID: <code>{user['user_id']}</code>\n"
        f"📱 Telefon: <code>{user['phone'] or 'Kiritilmagan'}</code>\n\n"
        f"💰 Balans: <b>{user['balance']:,.0f} so'm</b>\n"
        f"💵 Kiritgan pullaringiz: <b>{user['total_deposited']:,.0f} so'm</b>\n\n"
        f"⚡ Hisobingizni to'ldiring va xizmatlardan foydalanishni davom eting!"
    )
    orders_channel = await get_setting("orders_channel_username", "")
    buttons = [[InlineKeyboardButton(text="💳 Hisob to'ldirish", callback_data="goto_topup")]]
    if orders_channel:
        buttons.append([InlineKeyboardButton(text="📦 Buyurtmalar kanali", url=f"https://t.me/{orders_channel}")])
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "goto_topup")
async def goto_topup(call: CallbackQuery, state: FSMContext):
    await call.message.answer(
        "💳 <b>Hisob To'ldirish</b>\n\n"
        "Necha so'm to'lamoqchisiz?\n\n"
        "📌 Minimal: <b>1 000 so'm</b>\n"
        "📌 Maksimal: <b>10 000 000 so'm</b>\n\n"
        "Faqat son kiriting:"
    )
    await state.set_state(HumoPayStates.wait_amount)
    await call.answer()

# ─── NOMER OLISH ───────────────────────────────────────────────
async def build_countries_page(page: int):
    data = await api_available_countries()
    if data.get("status") != "ok" or not data.get("countries"):
        return None, 0
    countries        = data["countries"]
    filtered         = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    sorted_countries = sorted(filtered.items(), key=lambda x: float(x[1].get("price", 999)))
    markup_prices    = await db.get_all_markup_prices()
    total_pages      = (len(sorted_countries) - 1) // 10 + 1
    start = page * 10
    end   = start + 10
    buttons = []
    for code, info in sorted_countries[start:end]:
        qty       = info.get("qty", 0)
        name      = info.get("name", code)
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or int(usd_price * 12500 * 1.3)
        buttons.append([InlineKeyboardButton(
            text=f"{name} — {uzs_price:,} so'm | 📦 {qty} dona",
            callback_data=f"buy:{code}:{uzs_price}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"countries_page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if end < len(sorted_countries):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"countries_page:{page+1}"))
    buttons.append(nav)
    buttons.append([
        InlineKeyboardButton(text="📊 TOP 10 davlatlar", callback_data="top10_countries"),
        InlineKeyboardButton(text="🎉 Arzon raqamlar",   callback_data="cheap_countries"),
    ])
    return buttons, total_pages

@router.message(F.text == "📞 Nomer olish")
async def get_number_menu(msg: Message):
    loading_msg = await msg.answer("⏳ Mavjud davlatlar ro'yxati yuklanmoqda...")
    buttons, total_pages = await build_countries_page(0)
    try:
        await loading_msg.delete()
    except:
        pass
    if not buttons:
        await msg.answer("❌ Davlatlar ro'yxatini olishda xatolik. Keyinroq urinib ko'ring.")
        return
    await msg.answer(
        f"🌍 <b>Mavjud davlatlar ro'yxati:</b>\n<i>1/{total_pages}</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@router.callback_query(F.data.startswith("countries_page:"))
async def countries_page_cb(call: CallbackQuery):
    page = int(call.data.split(":")[1])
    buttons, total_pages = await build_countries_page(page)
    if not buttons:
        return await call.answer("Xatolik!", show_alert=True)
    try:
        await call.message.edit_text(
            f"🌍 <b>Mavjud davlatlar ro'yxati:</b>\n<i>{page+1}/{total_pages}</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except:
        pass
    await call.answer()

@router.callback_query(F.data == "top10_countries")
async def top10_countries(call: CallbackQuery):
    data = await api_available_countries()
    if data.get("status") != "ok":
        return await call.answer("Xatolik!", show_alert=True)
    countries     = data["countries"]
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    sorted_c      = sorted(filtered.items(), key=lambda x: int(x[1].get("qty", 0)), reverse=True)[:10]
    markup_prices = await db.get_all_markup_prices()
    buttons = []
    for code, info in sorted_c:
        qty       = info.get("qty", 0)
        name      = info.get("name", code)
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or int(usd_price * 12500 * 1.3)
        buttons.append([InlineKeyboardButton(
            text=f"{name} — {uzs_price:,} so'm | 📦 {qty} dona",
            callback_data=f"buy:{code}:{uzs_price}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="countries_page:0")])
    await call.message.edit_text("📊 <b>TOP 10 (soni bo'yicha):</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data == "cheap_countries")
async def cheap_countries(call: CallbackQuery):
    data = await api_available_countries()
    if data.get("status") != "ok":
        return await call.answer("Xatolik!", show_alert=True)
    countries     = data["countries"]
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    sorted_c      = sorted(filtered.items(), key=lambda x: float(x[1].get("price", 999)))[:10]
    markup_prices = await db.get_all_markup_prices()
    buttons = []
    for code, info in sorted_c:
        qty       = info.get("qty", 0)
        name      = info.get("name", code)
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or int(usd_price * 12500 * 1.3)
        buttons.append([InlineKeyboardButton(
            text=f"{name} — {uzs_price:,} so'm | 📦 {qty} dona",
            callback_data=f"buy:{code}:{uzs_price}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="countries_page:0")])
    await call.message.edit_text("🎉 <b>Eng arzon raqamlar:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()

# ─── NOMER SOTIB OLISH ─────────────────────────────────────────
@router.callback_query(F.data.startswith("buy:"))
async def buy_number(call: CallbackQuery):
    parts        = call.data.split(":")
    country_code = parts[1]
    uzs_price    = int(parts[2])
    user_id      = call.from_user.id
    if country_code.upper() in BLOCKED_COUNTRIES:
        return await call.answer("❌ Bu davlat raqamlari mavjud emas!", show_alert=True)
    bal = await db.get_balance(user_id)
    if bal < uzs_price:
        return await call.answer(
            f"❌ Hisobingizda mablag' yetarli emas!\n"
            f"Raqam narxi: {uzs_price:,} so'm\nBalansingiz: {bal:,} so'm",
            show_alert=True
        )
    info_text = (
        "🚀 <b>Bizning bot orqali taqdim etilayotgan akkauntlar</b> — tayyor "
        "ochilgan Telegram akkauntlar bazasidan olinadi.\n\n"
        "⚠️ Kod faqat <b>Telegraph</b> ilovasi orqali yuborilishi lozim!\n\n"
        "‼️ Sotib olingan akkauntlar uchun hech qanday kafolat yo'q ❌\n\n"
        "✅ Raqamni to'g'ri ishlatish — butunlay foydalanuvchi mas'uliyatidadir 🛡\n\n"
        "👆 Qoidalar bilan tanishib '✅ Davom etish' tugmasini bosing!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Davom etish", callback_data=f"confirm_buy:{country_code}:{uzs_price}")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="countries_page:0")]
    ])
    await call.message.edit_text(info_text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("confirm_buy:"))
async def confirm_buy(call: CallbackQuery):
    parts        = call.data.split(":")
    country_code = parts[1]
    uzs_price    = int(parts[2])
    user_id      = call.from_user.id
    bal = await db.get_balance(user_id)
    if bal < uzs_price:
        return await call.answer("❌ Balans yetarli emas!", show_alert=True)
    await call.message.edit_text("⏳ Raqam sotib olinmoqda... Iltimos kuting.")
    result = await api_buy_number(country_code)
    if result.get("status") != "ok":
        err = result.get("message", "Noma'lum xato")
        await call.message.edit_text(
            f"❌ Raqam olishda xatolik: {err}\n\nQayta urinib ko'ring.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="countries_page:0")]
            ])
        )
        return
    number   = result.get("Number", "")
    api_name = result.get("name", country_code)
    await db.update_balance(user_id, -uzs_price)
    order_id = await db.log_purchase(user_id, number, country_code, api_name, uzs_price)
    try:
        orders_ch = await get_setting("orders_channel_id", ORDERS_CHANNEL)
        await bot.send_message(
            int(orders_ch),
            f"🛒 <b>Yangi TG Akkaunt buyurtmasi</b>\n\n"
            f"👤 Foydalanuvchi: <code>{user_id}</code>\n"
            f"🌍 Mamlakat: {api_name}\n"
            f"📞 Raqam: <code>{number}</code>\n"
            f"💰 Narx: {uzs_price:,} so'm\n"
            f"🆔 Buyurtma #{order_id}"
        )
    except:
        pass
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Kodni olish", callback_data=f"getcode:{number}")]
    ])
    await call.message.edit_text(
        f"✅ <b>Raqam muvaffaqiyatli olindi!</b>\n\n"
        f"📞 Sizning raqamingiz: <code>{number}</code>\n"
        f"💰 Narxi: {uzs_price:,} so'm\n\n"
        f"⚠️ Faqat norasmiy ilovalardan foydalaning!\n"
        f"📱 Masalan: Aka, Telegraph, Plus...\n\n"
        f"💡 Keyin esa botga kirib <b>Kodni olish</b> tugmasini bosing!",
        reply_markup=kb
    )
    await call.answer()

@router.callback_query(F.data.startswith("getcode:"))
async def get_code(call: CallbackQuery):
    number  = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    purchase = await db.get_purchase_by_phone(user_id, number)
    if not purchase:
        return await call.answer("❌ Bu raqam sizga tegishli emas!", show_alert=True)
    await call.answer("⏳ Kod olinmoqda...")
    result = await api_get_code(number)
    if result.get("status") != "ok":
        err = result.get("message", "Noma'lum xato")
        kb  = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Qayta urinish", callback_data=f"getcode:{number}")]
        ])
        return await call.message.edit_text(f"❌ Kod olishda xatolik: {err}", reply_markup=kb)
    code     = result.get("code", "Topilmadi")
    password = result.get("pass", "")
    text = f"📨 <b>{number}</b> raqami uchun:\n\n🔑 Kirish kodi: <code>{code}</code>\n"
    if password:
        text += f"🔐 2-bosqichli parol: <code>{password}</code>\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Qayta olish", callback_data=f"getcode:{number}")]
    ])
    await call.message.edit_text(text, reply_markup=kb)

# ─── BUYURTMALARIM ─────────────────────────────────────────────
@router.message(F.text == "🛒 Buyurtmalarim")
async def my_orders(msg: Message):
    orders = await db.get_purchases(msg.from_user.id)
    if not orders:
        await msg.answer("🛒 Siz hali raqam sotib olmagansiz.\n📞 Raqam olish uchun '📞 Nomer olish' tugmasini bosing.")
        return
    await msg.answer(f"🛒 <b>Sizning buyurtmalaringiz ({len(orders)} ta):</b>")
    for i, order in enumerate(orders[:20], 1):
        phone       = order['phone']
        country     = order['country_name']
        price       = order['price']
        bought_date = order['created_at']
        try:
            if isinstance(bought_date, str):
                d = datetime.strptime(bought_date[:19], '%Y-%m-%d %H:%M:%S')
            else:
                d = bought_date
            formatted = d.strftime('%d.%m.%Y %H:%M')
        except:
            formatted = str(bought_date)
        text = (
            f"<b>{i}. 🌍 {country}</b>\n"
            f"📞 <code>{phone}</code>\n"
            f"💰 Narx: {price:,} so'm\n"
            f"📅 Sana: {formatted}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Kodni ko'rish", callback_data=f"getcode:{phone}"),
             InlineKeyboardButton(text="🔄 Qayta olish",   callback_data=f"getcode:{phone}")]
        ])
        await msg.answer(text, reply_markup=kb)

# ─── PUL ISHLASH ───────────────────────────────────────────────
@router.message(F.text == "💸 Pul Ishlash")
async def earn_money(msg: Message):
    user_id = msg.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Referal Orqali", callback_data=f"referral:{user_id}")],
        [InlineKeyboardButton(text="🎁 Kunlik Bonus",   callback_data="daily_bonus")],
    ])
    await msg.answer("💸 <b>Pul Ishlash uchun bo'limni tanlang:</b>", reply_markup=kb)

@router.callback_query(F.data.startswith("referral:"))
async def show_referral(call: CallbackQuery):
    user_id   = int(call.data.split(":")[1])
    me        = await bot.get_me()
    ref_link  = f"https://t.me/{me.username}?start=ref_{user_id}"
    ref_count = await db.get_referral_count(user_id)
    earnings  = await db.get_referral_earnings(user_id)
    ref_bonus = int(await get_setting("referral_bonus", 500))
    text = (
        f"👥 <b>Referal tizimi</b>\n\n"
        f"🔗 Sizning referal havolangiz:\n<code>{ref_link}</code>\n\n"
        f"👤 Jalb qilgan do'stlaringiz: <b>{ref_count} ta</b>\n"
        f"💰 Referal daromad: <b>{earnings:,} so'm</b>\n\n"
        f"💡 Har bir jalb qilgan do'stingiz uchun <b>{ref_bonus:,} so'm</b> bonus!\n"
        f"⚠️ Bonus faqat <b>+998</b> (O'zbekiston) raqamli foydalanuvchilar uchun."
    )
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_earn")]
    ]))
    await call.answer()

@router.callback_query(F.data == "daily_bonus")
async def daily_bonus_cb(call: CallbackQuery):
    user_id     = call.from_user.id
    today       = date.today().isoformat()
    last        = await db.get_last_bonus_date(user_id)
    daily_bonus = int(await get_setting("daily_bonus", 200))
    if last == today:
        await call.answer("❌ Kunlik bonusni allaqachon oldingiz! Ertaga qayta urinib ko'ring.", show_alert=True)
        return
    await db.update_balance(user_id, daily_bonus)
    await db.set_last_bonus_date(user_id, today)
    await call.answer(f"🎁 Kunlik bonus: {daily_bonus:,} so'm hisobingizga qo'shildi!", show_alert=True)

@router.callback_query(F.data == "back_earn")
async def back_earn(call: CallbackQuery):
    try:
        await call.message.delete()
    except:
        pass
    await earn_money(call.message)
    await call.answer()

@router.message(F.text == "📕 Qo'llanma")
async def guide_menu(msg: Message):
    text = (
        "<b>📕 Botdan foydalanish qo'llanmasi:</b>\n\n"
        "1. <b>📞 Nomer olish</b> — Telegram uchun virtual raqam sotib olish.\n"
        "2. <b>🛒 Buyurtmalarim</b> — Sotib olgan raqamlaringiz tarixi.\n"
        "3. <b>💰 Hisobim</b> — Joriy balansingizni tekshirish.\n"
        "4. <b>💳 Hisob To'ldirish</b> — Bot hisobingizni pul bilan to'ldirish.\n"
        "5. <b>💸 Pul Ishlash</b> — Referal va kunlik bonus.\n"
        "6. <b>🆘 Qo'llab-quvvatlash</b> — Admin bilan bog'lanish."
    )
    await msg.answer(text)

@router.message(F.text == "🆘 Qo'llab-quvvatlash")
async def support_menu(msg: Message):
    await msg.answer(
        f"🆘 <b>Qo'llab-quvvatlash</b>\n\n"
        f"Savol va muammolar uchun adminga murojaat qiling:\n"
        f"<a href='tg://user?id={ADMIN_ID}'>👤 Admin bilan bog'lanish</a>"
    )

# ─── ADMIN: QO'LDA TO'LOV TASDIQLASH ──────────────────────────
@admin_router.callback_query(F.data.startswith("tasdiq:"))
async def admin_confirm(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    pay_id = call.data.split(":", 1)[1]
    pay    = await db.get_pending_payment(pay_id)
    if not pay:
        return await call.answer("❌ Bu to'lov allaqachon ko'rib chiqilgan!", show_alert=True)
    user_id  = pay['user_id']
    amount   = pay['amount']
    fullname = pay['fullname']
    await db.update_balance(user_id, amount)
    await db.update_total_deposited(user_id, amount)
    await db.delete_pending_payment(pay_id)
    try:
        await bot.send_message(user_id, f"✅ Hisobingiz admin tomonidan <b>{amount:,} so'm</b>ga to'ldirildi!")
    except:
        pass
    try:
        await call.message.edit_caption(caption=f"✅ {fullname} (<code>{user_id}</code>) hisobi {amount:,} so'mga to'ldirildi.")
    except:
        pass
    await call.answer("✅ Tasdiqlandi!")

@admin_router.callback_query(F.data.startswith("rad:"))
async def admin_reject(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    pay_id = call.data.split(":", 1)[1]
    pay    = await db.get_pending_payment(pay_id)
    if not pay:
        return await call.answer("❌ Allaqachon ko'rib chiqilgan!", show_alert=True)
    user_id  = pay['user_id']
    amount   = pay['amount']
    fullname = pay['fullname']
    await db.delete_pending_payment(pay_id)
    try:
        await bot.send_message(user_id, f"❌ Sizning <b>{amount:,} so'm</b>lik to'lovingiz rad etildi.")
    except:
        pass
    try:
        await call.message.edit_caption(caption=f"❌ {fullname} (<code>{user_id}</code>) to'lovi rad etildi.")
    except:
        pass
    await call.answer("❌ Rad etildi!")

# ─── ADMIN PANEL ───────────────────────────────────────────────
async def show_admin_panel(target):
    users_count  = await db.count_users()
    orders_count = await db.count_orders()
    api_bal      = await api_get_balance_tglion()
    api_balance  = api_bal.get("balance", "N/A")
    daily_bonus_val = await get_setting("daily_bonus", 200)
    ref_bonus_val   = await get_setting("referral_bonus", 500)
    channel_un_val  = await get_setting("required_channel_username", CHANNEL_USERNAME)
    text = (
        f"🔐 <b>Admin panel</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count}</b>\n"
        f"🛒 Jami buyurtmalar: <b>{orders_count}</b>\n"
        f"💰 TG-Lion balansi: <b>{api_balance}</b>\n\n"
        f"⚙️ <b>Joriy sozlamalar:</b>\n"
        f"🎁 Kunlik bonus: <b>{daily_bonus_val} so'm</b>\n"
        f"👥 Referal bonus: <b>{ref_bonus_val} so'm</b>\n"
        f"📢 Kanal: <b>@{channel_un_val}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika",           callback_data="adm_stats"),
         InlineKeyboardButton(text="👥 Foydalanuvchilar",     callback_data="adm_users")],
        [InlineKeyboardButton(text="➕➖ Balans o'zgartirish", callback_data="adm_balance"),
         InlineKeyboardButton(text="💵 Narx sozlash",         callback_data="adm_prices")],
        [InlineKeyboardButton(text="📞 Raqam qidirish",       callback_data="adm_search"),
         InlineKeyboardButton(text="📣 Xabar yuborish",       callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="⚙️ Bot sozlamalari",      callback_data="adm_settings"),
         InlineKeyboardButton(text="🔄 Yangilash",            callback_data="adm_refresh")],
    ])
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
    else:
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except:
            await target.message.answer(text, reply_markup=kb)
        await target.answer()

@admin_router.message(F.text == "/admin")
async def admin_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_refresh")
async def adm_refresh(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    await show_admin_panel(call)

@admin_router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    stats         = await db.get_sales_stats()
    total_orders  = await db.count_orders()
    total_revenue = await db.get_total_revenue()
    text = "📊 <b>Sotuvlar statistikasi:</b>\n\n"
    for country, cnt, rev in stats:
        text += f"🌍 {country}: {cnt} ta — {rev:,} so'm\n"
    text += f"\n📦 Jami buyurtmalar: <b>{total_orders}</b>\n"
    text += f"💰 Jami daromad: <b>{total_revenue:,} so'm</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_refresh")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "adm_users")
async def adm_users(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    users = await db.get_all_users()
    text  = "👥 <b>Foydalanuvchilar balansi:</b>\n\n"
    for i, u in enumerate(users[:30], 1):
        text += f"{i}. {u['fullname']} (<code>{u['user_id']}</code>) — {u['balance']:,} so'm\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_refresh")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "adm_balance")
async def adm_balance_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("Foydalanuvchi ID sini kiriting:")
    await state.set_state(BalanceChangeState.wait_user_id)
    await call.answer()

@admin_router.message(BalanceChangeState.wait_user_id)
async def adm_balance_uid(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if not msg.text or not msg.text.isdigit():
        return await msg.answer("❌ Faqat raqam kiriting!")
    await state.update_data(uid=int(msg.text))
    await msg.answer("Qancha so'm qo'shmoqchisiz? (Ayirish uchun: -5000)")
    await state.set_state(BalanceChangeState.wait_amount)

@admin_router.message(BalanceChangeState.wait_amount)
async def adm_balance_amount(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    try:
        amount = int(msg.text)
    except:
        return await msg.answer("❌ Faqat son kiriting!")
    data = await state.get_data()
    uid  = data['uid']
    await db.update_balance(uid, amount)
    try:
        await bot.send_message(uid, f"ℹ️ Hisobingiz admin tomonidan <b>{amount:+,} so'm</b>ga o'zgartirildi.")
    except:
        pass
    await msg.answer(f"✅ <code>{uid}</code> ID foydalanuvchi balansi {amount:+,} so'mga o'zgartirildi.")
    await state.clear()
    await show_admin_panel(msg)

# ─── NARX SOZLASH ──────────────────────────────────────────────
@admin_router.callback_query(F.data == "adm_prices")
async def adm_prices(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Bita davlat narxini o'zgartirish",     callback_data="price_single")],
        [InlineKeyboardButton(text="📊 Barcha narxlarni % bilan o'zgartirish", callback_data="price_bulk")],
        [InlineKeyboardButton(text="📋 Barcha narxlarni ko'rish",              callback_data="price_list")],
        [InlineKeyboardButton(text="⬅️ Orqaga",                               callback_data="adm_refresh")],
    ])
    await call.message.edit_text("💵 <b>Narx sozlash:</b>", reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "price_single")
async def price_single_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    data          = await api_available_countries()
    countries     = data.get("countries", {})
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    markup_prices = await db.get_all_markup_prices()
    buttons = []
    for code, info in list(filtered.items())[:20]:
        name  = info.get("name", code)
        cur   = markup_prices.get(code.upper(), None)
        label = f"{cur:,} so'm" if cur else "Standart"
        buttons.append([InlineKeyboardButton(text=f"{name}: {label}", callback_data=f"setprice:{code}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_prices")])
    await call.message.edit_text("✏️ <b>Narx o'zgartirish — davlat tanlang:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@admin_router.callback_query(F.data == "price_list")
async def price_list(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    data          = await api_available_countries()
    countries     = data.get("countries", {})
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    markup_prices = await db.get_all_markup_prices()
    text = "📋 <b>Barcha davlatlar narxlari:</b>\n\n"
    for code, info in sorted(filtered.items(), key=lambda x: float(x[1].get("price", 999))):
        name      = info.get("name", code)
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or int(usd_price * 12500 * 1.3)
        text += f"🌍 {name} (<code>{code}</code>): <b>{uzs_price:,} so'm</b>\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_prices")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "price_bulk")
async def price_bulk_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text(
        "📊 <b>Barcha narxlarni foiz bilan o'zgartirish</b>\n\n"
        "Necha foizga oshirmoqchisiz?\n"
        "Misol: <code>20</code> — 20% oshirish\n"
        "Misol: <code>-10</code> — 10% kamaytirish"
    )
    await state.set_state(AdminSettingsState.wait_bulk_percent)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_bulk_percent)
async def price_bulk_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    try:
        percent = float(msg.text)
    except:
        return await msg.answer("❌ Faqat son kiriting!")
    data      = await api_available_countries()
    countries = data.get("countries", {})
    filtered  = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    markup_prices = await db.get_all_markup_prices()
    updated = 0
    for code, info in filtered.items():
        usd_price = float(info.get("price", 1))
        cur_price = markup_prices.get(code.upper()) or int(usd_price * 12500 * 1.3)
        new_price = int(cur_price * (1 + percent / 100))
        await db.set_markup_price(code.upper(), new_price)
        updated += 1
    await msg.answer(f"✅ {updated} ta davlat narxi {percent:+.0f}% ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data.startswith("setprice:"))
async def setprice_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    code = call.data.split(":")[1]
    await state.update_data(country=code)
    await call.message.edit_text(f"<b>{code}</b> uchun yangi narxni so'mda kiriting:")
    await state.set_state(AdminSettingsState.wait_price_value)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_price_value)
async def setprice_amount(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if not msg.text or not msg.text.isdigit():
        return await msg.answer("❌ Faqat son kiriting!")
    price = int(msg.text)
    data  = await state.get_data()
    code  = data['country']
    await db.set_markup_price(code.upper(), price)
    await msg.answer(f"✅ {code} uchun narx: {price:,} so'm ga o'rnatildi.")
    await state.clear()
    await show_admin_panel(msg)

# ─── ADMIN SOZLAMALARI ─────────────────────────────────────────
@admin_router.callback_query(F.data == "adm_settings")
async def adm_settings(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    daily_bonus_val = await get_setting("daily_bonus", 200)
    ref_bonus_val   = await get_setting("referral_bonus", 500)
    channel_un      = await get_setting("required_channel_username", CHANNEL_USERNAME)
    orders_ch_un    = await get_setting("orders_channel_username", "")
    card            = await get_setting("card_number", CARD_NUMBER)
    owner           = await get_setting("card_owner", CARD_OWNER)
    text = (
        f"⚙️ <b>Bot sozlamalari</b>\n\n"
        f"🎁 Kunlik bonus: <b>{daily_bonus_val} so'm</b>\n"
        f"👥 Referal bonus: <b>{ref_bonus_val} so'm</b>\n"
        f"📢 Majburiy kanal: <b>@{channel_un}</b>\n"
        f"📦 Buyurtmalar kanali: <b>@{orders_ch_un or 'Sozlanmagan'}</b>\n"
        f"💳 Karta raqami: <b>{card}</b>\n"
        f"👤 Karta egasi: <b>{owner}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Kunlik bonusni o'zgartirish",   callback_data="set_daily_bonus")],
        [InlineKeyboardButton(text="👥 Referal bonusni o'zgartirish",  callback_data="set_ref_bonus")],
        [InlineKeyboardButton(text="📢 Majburiy kanalni o'zgartirish", callback_data="set_channel")],
        [InlineKeyboardButton(text="📦 Buyurtmalar kanalini sozlash",  callback_data="set_orders_channel")],
        [InlineKeyboardButton(text="💳 Karta raqamini o'zgartirish",   callback_data="set_card_number")],
        [InlineKeyboardButton(text="👤 Karta egasini o'zgartirish",    callback_data="set_card_owner")],
        [InlineKeyboardButton(text="⬅️ Orqaga",                       callback_data="adm_refresh")],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "set_daily_bonus")
async def set_daily_bonus_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🎁 Yangi kunlik bonus miqdorini so'mda kiriting:")
    await state.set_state(AdminSettingsState.wait_daily_bonus)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_daily_bonus)
async def set_daily_bonus_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    if not msg.text or not msg.text.isdigit(): return await msg.answer("❌ Faqat son kiriting!")
    await db.set_setting("daily_bonus", msg.text)
    await msg.answer(f"✅ Kunlik bonus {int(msg.text):,} so'm ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_ref_bonus")
async def set_ref_bonus_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("👥 Yangi referal bonus miqdorini so'mda kiriting:")
    await state.set_state(AdminSettingsState.wait_referral_bonus)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_referral_bonus)
async def set_ref_bonus_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    if not msg.text or not msg.text.isdigit(): return await msg.answer("❌ Faqat son kiriting!")
    await db.set_setting("referral_bonus", msg.text)
    await msg.answer(f"✅ Referal bonus {int(msg.text):,} so'm ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_channel")
async def set_channel_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📢 Yangi majburiy kanal ID sini kiriting:\nMisol: <code>-1001234567890</code>")
    await state.set_state(AdminSettingsState.wait_channel_id)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_channel_id)
async def set_channel_id_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try: int(msg.text)
    except: return await msg.answer("❌ Kanal ID raqam bo'lishi kerak!")
    await db.set_setting("required_channel_id", msg.text)
    await msg.answer("✅ Kanal ID saqlandi! Endi kanal username kiriting (@siz username):")
    await state.set_state(AdminSettingsState.wait_channel_username)

@admin_router.message(AdminSettingsState.wait_channel_username)
async def set_channel_username_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    username = msg.text.strip().lstrip("@")
    await db.set_setting("required_channel_username", username)
    await msg.answer(f"✅ Kanal @{username} ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_orders_channel")
async def set_orders_channel_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📦 Buyurtmalar kanal ID sini kiriting:\nMisol: <code>-1001234567890</code>")
    await state.set_state(AdminSettingsState.wait_orders_channel_id)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_orders_channel_id)
async def set_orders_channel_id_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try: int(msg.text)
    except: return await msg.answer("❌ Kanal ID raqam bo'lishi kerak!")
    await db.set_setting("orders_channel_id", msg.text)
    await msg.answer("✅ Buyurtmalar kanal ID saqlandi! Endi username kiriting:")
    await state.set_state(AdminSettingsState.wait_orders_channel_username)

@admin_router.message(AdminSettingsState.wait_orders_channel_username)
async def set_orders_channel_username_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    username = msg.text.strip().lstrip("@")
    await db.set_setting("orders_channel_username", username)
    await msg.answer(f"✅ Buyurtmalar kanali @{username} ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_card_number")
async def set_card_number_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("💳 Yangi karta raqamini kiriting:\nMisol: <code>8600 1234 5678 9012</code>")
    await state.set_state(AdminSettingsState.wait_card_number)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_card_number)
async def set_card_number_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await db.set_setting("card_number", msg.text.strip())
    await msg.answer(f"✅ Karta raqami o'zgartirildi: <code>{msg.text.strip()}</code>")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_card_owner")
async def set_card_owner_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("👤 Karta egasining ismini kiriting:\nMisol: <code>Qurbonov Q</code>")
    await state.set_state(AdminSettingsState.wait_card_owner)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_card_owner)
async def set_card_owner_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await db.set_setting("card_owner", msg.text.strip())
    await msg.answer(f"✅ Karta egasi o'zgartirildi: <b>{msg.text.strip()}</b>")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_search")
async def adm_search(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📞 Qidirilayotgan telefon raqamni kiriting:")
    await state.set_state(AdminSearchState.wait_phone)
    await call.answer()

@admin_router.message(AdminSearchState.wait_phone)
async def adm_search_phone(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    phone  = msg.text.strip()
    result = await db.find_purchase_by_phone(phone)
    if not result:
        await msg.answer(f"❌ <code>{phone}</code> raqami bo'yicha xarid topilmadi.")
    else:
        text = (
            f"✅ <b>Raqam topildi!</b>\n\n"
            f"📞 Raqam: <code>{result['phone']}</code>\n"
            f"🌍 Davlat: {result['country_name']}\n"
            f"📅 Sotilgan sana: {result['created_at']}\n\n"
            f"👤 <b>Xaridor:</b>\n"
            f"Ism: {result['fullname']}\n"
            f"ID: <code>{result['user_id']}</code>"
        )
        await msg.answer(text)
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📣 Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:")
    await state.set_state(BroadcastState.wait_message)
    await call.answer()

@admin_router.message(BroadcastState.wait_message)
async def broadcast_send(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    users      = await db.get_all_users()
    sent       = 0
    failed     = 0
    status_msg = await msg.answer(f"📣 Yuborilmoqda... 0/{len(users)}")
    for i, u in enumerate(users):
        try:
            await bot.copy_message(u['user_id'], msg.chat.id, msg.message_id)
            sent += 1
        except:
            failed += 1
        if i % 20 == 0:
            try:
                await status_msg.edit_text(f"📣 Yuborilmoqda... {i+1}/{len(users)}")
            except:
                pass
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"📣 Xabar yuborildi:\n✅ {sent} ta\n❌ {failed} ta")
    await state.clear()
    await show_admin_panel(msg)

# ─── ISHGA TUSHIRISH ───────────────────────────────────────────
async def main():
    await db.init()
    dp.include_router(admin_router)
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("✅ Bot ishga tushdi! PostgreSQL ulandi ✅")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
