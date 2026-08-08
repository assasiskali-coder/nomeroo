import asyncio
import os
import aiohttp
from datetime import datetime, date, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F, Router, BaseMiddleware
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery, TelegramObject,
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
CARD_OWNER       = os.getenv("CARD_OWNER", "Familiya I")
TGLION_API_KEY   = os.getenv("TGLION_API_KEY", "YOUR_TGLION_KEY")
TGLION_YOUR_ID   = os.getenv("TGLION_YOUR_ID", "YOUR_TGLION_ID")
TGLION_BASE      = "https://TG-Lion.net"
ORDERS_CHANNEL   = os.getenv("ORDERS_CHANNEL", "-1001234567890")

BLOCKED_COUNTRIES = {"CO", "NG", "ZW"}
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

# ─── BOT SOZLASH ───────────────────────────────────────────────
bot          = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="html"))
storage      = MemoryStorage()
dp           = Dispatcher(storage=storage)
router       = Router()
admin_router = Router()

db = Database()

# ─── HOLATLAR ──────────────────────────────────────────────────
class PayStates(StatesGroup):
    wait_amount = State()
    wait_check  = State()

class BalanceChangeState(StatesGroup):
    wait_user_id = State()
    wait_amount  = State()

class AdminSearchState(StatesGroup):
    wait_phone      = State()
    wait_profile_id = State()

class BroadcastState(StatesGroup):
    wait_message = State()

class PhoneState(StatesGroup):
    wait_phone = State()

class AdminSettingsState(StatesGroup):
    wait_daily_bonus             = State()
    wait_referral_bonus          = State()
    wait_bulk_percent            = State()
    wait_channel_id              = State()
    wait_channel_username        = State()
    wait_price_value             = State()
    wait_orders_channel_id       = State()
    wait_orders_channel_username = State()
    wait_card_number             = State()
    wait_card_owner              = State()

# ─── MENYULAR ──────────────────────────────────────────────────
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📞 Nomer olish"),   KeyboardButton(text="🛒 Buyurtmalarim")],
        [KeyboardButton(text="💰 Hisobim"),        KeyboardButton(text="💳 Hisob to'ldirish")],
        [KeyboardButton(text="💸 Pul ishlash"),    KeyboardButton(text="📕 Qo'llanma")],
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

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
    resize_keyboard=True
)

CANCEL_WORDS = {"/cancel", "❌ bekor qilish", "bekor qilish", "/bekor"}

def is_cancel_text(text: str | None) -> bool:
    return bool(text) and text.strip().lower() in CANCEL_WORDS

# ─── YORDAMCHI FUNKSIYALAR ─────────────────────────────────────
async def get_setting(key: str, default):
    val = await db.get_setting(key)
    if val is None:
        return default
    try:
        return type(default)(val)
    except Exception:
        return default

async def is_maintenance_mode() -> bool:
    val = await db.get_setting("maintenance_mode")
    return val == "1"

def country_flag(code: str) -> str:
    """ISO-2 davlat kodidan bayroq emoji hosil qiladi (masalan UZ -> 🇺🇿)."""
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return "🌍"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)

def now_tashkent() -> datetime:
    return datetime.now(TASHKENT_TZ)

def today_utc_bounds():
    """Toshkent vaqti bo'yicha 'bugun'ning UTC chegaralarini qaytaradi (naive UTC)."""
    local_now = now_tashkent()
    start_local = datetime.combine(local_now.date(), dt_time.min, tzinfo=TASHKENT_TZ)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc

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
#  TA'MIRLASH REJIMI — oddiy foydalanuvchilar botni admin band
#  bo'lganda yoki texnik ishlar paytida ishlata olmasligi uchun
# ══════════════════════════════════════════════════════════════

MAINTENANCE_TEXT = (
    "🔧 <b>Texnik ishlar olib borilmoqda</b>\n\n"
    "Bot hozircha vaqtincha ishlamayapti. Iltimos, birozdan so'ng "
    "qayta urinib ko'ring.\n\n"
    "Qulaysizlik uchun uzr so'raymiz 🙏"
)

class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        if user and user.id != ADMIN_ID and await is_maintenance_mode():
            if isinstance(event, Message):
                await event.answer(MAINTENANCE_TEXT)
            elif isinstance(event, CallbackQuery):
                await event.answer("🔧 Bot texnik ishlar tufayli vaqtincha ishlamayapti.", show_alert=True)
            return
        return await handler(event, data)

router.message.middleware(MaintenanceMiddleware())
router.callback_query.middleware(MaintenanceMiddleware())

# ══════════════════════════════════════════════════════════════
#  TO'LOVNI TASDIQLASH (admin qo'lda tasdiqlaydi)
# ══════════════════════════════════════════════════════════════

async def confirm_payment(pay_id: str, user_id: int, amount: int, fullname: str):
    """Adminning tasdig'idan so'ng balansga pul qo'shadi, xabar yuboradi va
    bухgalteriya kitobiga (transactions) yozib qo'yadi."""
    await db.update_balance(user_id, amount)
    await db.update_total_deposited(user_id, amount)
    await db.delete_pending_payment(pay_id)
    await db.log_transaction(user_id, "topup", amount, note="admin_tasdiq")
    user_balance = await db.get_balance(user_id)

    try:
        await bot.send_message(
            user_id,
            f"✅ <b>To'lovingiz tasdiqlandi!</b>\n\n"
            f"💰 Hisobingizga <b>{amount:,} so'm</b> qo'shildi.\n"
            f"💼 Joriy balans: <b>{user_balance:,} so'm</b>\n\n"
            f"Endi xohlagan xizmatdan foydalanishingiz mumkin 🎉"
        )
    except Exception:
        pass

    sav = now_tashkent().strftime("%H:%M:%S | %d.%m.%Y")
    try:
        await bot.send_message(
            ADMIN_ID,
            f"✅ To'lov tasdiqlandi!\n\n"
            f"👤 {fullname} (<code>{user_id}</code>)\n"
            f"💰 +{amount:,} so'm\n"
            f"💼 Yangi balans: {user_balance:,} so'm\n"
            f"⏰ {sav}"
        )
    except Exception:
        pass


# ─── ADMIN BUYRUQ: QO'LDA BALANS QO'SHISH ─────────────────────
@admin_router.message(F.text.regexp(r'^/addbal\s+\d+\s+\d+$'))
async def addbal_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    parts  = msg.text.strip().split()
    uid    = int(parts[1])
    amount = int(parts[2])
    user = await db.get_user(uid)
    if not user:
        return await msg.answer("❌ Bunday ID li foydalanuvchi topilmadi. ID ni tekshirib qayta yuboring.")
    await db.update_balance(uid, amount)
    await db.update_total_deposited(uid, amount)
    await db.log_transaction(uid, "topup", amount, note="admin_addbal")
    bal = await db.get_balance(uid)
    try:
        await bot.send_message(uid, f"✅ Hisobingizga <b>{amount:,} so'm</b> qo'shildi!\n💼 Balans: <b>{bal:,} so'm</b>")
    except Exception:
        pass
    await msg.answer(f"✅ {user['fullname']} ga {amount:,} so'm qo'shildi.\n💼 Yangi balans: {bal:,} so'm")

# ══════════════════════════════════════════════════════════════
#  HISOB TO'LDIRISH — foydalanuvchi chek yuboradi, admin tasdiqlaydi
# ══════════════════════════════════════════════════════════════

TOPUP_INTRO = (
    "💳 <b>Hisob to'ldirish</b>\n\n"
    "Necha so'mlik to'lov qilmoqchisiz? Faqat son yuboring (masalan: <code>50000</code>).\n\n"
    "📌 Minimal: <b>1 000 so'm</b>\n"
    "📌 Maksimal: <b>10 000 000 so'm</b>"
)

@router.message(F.text.func(is_cancel_text), StateFilter(PayStates.wait_amount, PayStates.wait_check))
async def pay_cancel_any(msg: Message, state: FSMContext):
    data   = await state.get_data()
    pay_id = data.get("pay_id")
    if pay_id:
        await db.delete_pending_payment(pay_id)
    await state.clear()
    await msg.answer("❌ To'lov bekor qilindi.", reply_markup=main_menu)


@router.message(F.text == "💳 Hisob to'ldirish")
async def topup_menu(msg: Message, state: FSMContext):
    await msg.answer(
        TOPUP_INTRO + "\n\n⚠️ Summani diqqat bilan tekshiring — botga tushgan mablag' qaytarilmaydi.\n"
        "Fikringizdan qaytsangiz, pastdagi «❌ Bekor qilish» tugmasini bosing.",
        reply_markup=cancel_kb
    )
    await state.set_state(PayStates.wait_amount)


@router.message(PayStates.wait_amount)
async def pay_amount_received(msg: Message, state: FSMContext):
    if not msg.text or not msg.text.strip().isdigit():
        await msg.answer("❌ Iltimos, faqat son (raqam) kiriting. Masalan: 50000", reply_markup=cancel_kb)
        return

    amount = int(msg.text.strip())
    if not (1000 <= amount <= 10_000_000):
        await msg.answer(
            "❌ Kiritilgan summa chegaradan tashqarida.\n"
            "⬇️ Minimal: <b>1 000 so'm</b>\n"
            "⬆️ Maksimal: <b>10 000 000 so'm</b>\n\n"
            "Iltimos, to'g'ri summani qayta kiriting.",
            reply_markup=cancel_kb
        )
        return

    user_id  = msg.from_user.id
    fullname = msg.from_user.full_name

    pay_id = f"pay_{user_id}_{amount}_{msg.message_id}"
    await db.add_pending_payment(pay_id, user_id, amount, fullname)

    card  = await get_setting("card_number", CARD_NUMBER)
    owner = await get_setting("card_owner", CARD_OWNER)

    await state.update_data(pay_id=pay_id, pay_amount=amount)
    await state.set_state(PayStates.wait_check)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel_pay:{pay_id}")],
    ])

    await msg.answer(
        f"💸 To'lov summasi: <b>{amount:,} so'm</b>\n"
        f"💳 Karta raqami: <code>{card}</code>\n"
        f"👤 Karta egasi: <b>{owner}</b>\n\n"
        f"1️⃣ Yuqoridagi kartaga to'lovni amalga oshiring.\n"
        f"2️⃣ To'lov chekining (skrinshot) rasmini shu yerga yuboring.\n\n"
        f"⏰ <i>5 daqiqa ichida chek yuborilmasa, ushbu to'lov avtomatik bekor bo'ladi.</i>",
        reply_markup=kb
    )

    asyncio.create_task(_pay_timeout(pay_id, user_id, amount))


async def _pay_timeout(pay_id: str, user_id: int, amount: int):
    await asyncio.sleep(300)
    pay = await db.get_pending_payment(pay_id)
    if pay:
        await db.delete_pending_payment(pay_id)
        try:
            await bot.send_message(
                user_id,
                f"⏰ <b>{amount:,} so'm</b>lik to'lovingiz uchun berilgan vaqt tugadi, shuning uchun bekor qilindi.\n"
                f"Qayta urinish uchun «💳 Hisob to'ldirish» tugmasini bosing.",
                reply_markup=main_menu
            )
        except Exception:
            pass


@router.message(PayStates.wait_check, F.photo)
async def pay_check_received(msg: Message, state: FSMContext):
    data   = await state.get_data()
    pay_id = data.get("pay_id")
    pay    = await db.get_pending_payment(pay_id) if pay_id else None

    if not pay:
        await msg.answer(
            "❌ Bu to'lovning muddati tugagan yoki u bekor qilingan.\n"
            "Qaytadan «💳 Hisob to'ldirish» tugmasini bosing.",
            reply_markup=main_menu
        )
        await state.clear()
        return

    photo_id = msg.photo[-1].file_id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_ok:{pay_id}"),
        InlineKeyboardButton(text="❌ Rad etish",  callback_data=f"pay_no:{pay_id}"),
    ]])

    try:
        await bot.send_photo(
            ADMIN_ID,
            photo_id,
            caption=(
                f"🧾 <b>Yangi to'lov cheki!</b>\n\n"
                f"👤 {pay['fullname']} (<code>{pay['user_id']}</code>)\n"
                f"💰 Miqdor: <b>{pay['amount']:,} so'm</b>"
            ),
            reply_markup=kb
        )
    except Exception:
        await msg.answer("❌ Chekni yuborishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")
        return

    await msg.answer(
        "✅ Chekingiz adminga yuborildi. Tasdiqlanishini kuting — bu odatda uzoq davom etmaydi.",
        reply_markup=main_menu
    )
    await state.clear()


@router.message(PayStates.wait_check)
async def pay_check_wrong_type(msg: Message):
    await msg.answer("📸 Iltimos, to'lov chekining rasmini (skrinshotini) yuboring. Matn yoki fayl qabul qilinmaydi.")


@admin_router.callback_query(F.data.startswith("pay_ok:"))
async def pay_ok_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    pay_id = call.data.split(":", 1)[1]
    pay = await db.get_pending_payment(pay_id)
    if not pay:
        return await call.answer("❌ Bu to'lov allaqachon ko'rib chiqilgan!", show_alert=True)

    await confirm_payment(pay["pay_id"], pay["user_id"], pay["amount"], pay["fullname"])
    try:
        await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n✅ Tasdiqlandi!")
    except TelegramBadRequest:
        pass
    await call.answer("✅ Tasdiqlandi!")


@admin_router.callback_query(F.data.startswith("pay_no:"))
async def pay_no_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    pay_id = call.data.split(":", 1)[1]
    pay = await db.get_pending_payment(pay_id)
    if not pay:
        return await call.answer("❌ Bu to'lov allaqachon ko'rib chiqilgan!", show_alert=True)

    await db.delete_pending_payment(pay_id)
    try:
        await bot.send_message(
            pay["user_id"],
            f"❌ <b>{pay['amount']:,} so'm</b>lik to'lovingiz rad etildi.\n"
            f"Xato deb hisoblasangiz, «🆘 Qo'llab-quvvatlash» orqali admin bilan bog'laning."
        )
    except Exception:
        pass
    try:
        await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n❌ Rad etildi!")
    except TelegramBadRequest:
        pass
    await call.answer("❌ Rad etildi!")


@router.callback_query(F.data.startswith("cancel_pay:"))
async def cancel_pay_cb(call: CallbackQuery, state: FSMContext):
    pay_id = call.data.split(":", 1)[1]
    pay = await db.get_pending_payment(pay_id)
    if pay:
        await db.delete_pending_payment(pay_id)
    await state.clear()
    await call.message.edit_text("❌ To'lov bekor qilindi.")
    try:
        await bot.send_message(call.from_user.id, "Bosh menyuga qaytdingiz.", reply_markup=main_menu)
    except Exception:
        pass
    await call.answer()

# ══════════════════════════════════════════════════════════════
#  OBUNA, START, TELEFON
# ══════════════════════════════════════════════════════════════

async def check_subscription(user_id: int) -> bool:
    try:
        ch_id = await get_setting("required_channel_id", CHANNEL_ID)
        m = await bot.get_chat_member(int(ch_id), user_id)
        return m.status in ("creator", "administrator", "member")
    except TelegramBadRequest:
        return False
    except Exception:
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
    return (
        f"{hello}\n\n"
        f"Bu yerda siz Telegram uchun virtual raqam sotib olishingiz, "
        f"hisobingizni to'ldirishingiz va do'stlaringizni taklif qilib pul ishlashingiz mumkin.\n\n"
        f"Kerakli bo'limni pastdagi menyudan tanlang 👇"
    )

async def send_main(target, first_name: str = ""):
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

    if referrer_id:
        await db.add_pending_referrer(msg.from_user.id, referrer_id)

    if not await check_subscription(msg.from_user.id):
        await msg.answer(
            "⚠️ Botdan foydalanish uchun avval quyidagi kanalga obuna bo'ling, "
            "so'ng «✅ Obuna bo'ldim» tugmasini bosing:",
            reply_markup=await get_sub_kb()
        )
        return

    user = await db.get_user(msg.from_user.id)
    if not user or not user.get("phone"):
        await msg.answer(
            "📲 Botdan foydalanishni davom ettirish uchun quyidagi "
            "«Telefon raqamni yuborish» tugmasini bosing. Raqamingiz faqat "
            "hisobingizni tasdiqlash uchun ishlatiladi:",
            reply_markup=phone_request_kb()
        )
        await state.set_state(PhoneState.wait_phone)
        return

    await db.add_user(msg.from_user.id, msg.from_user.full_name, str(msg.from_user.username or ""), referrer_id)
    await send_main(msg, msg.from_user.first_name)

@router.callback_query(F.data == "check_sub")
async def check_sub_cb(call: CallbackQuery, state: FSMContext):
    if not await check_subscription(call.from_user.id):
        return await call.answer("❌ Siz hali kanalga obuna bo'lmadingiz. Avval obuna bo'lib, keyin qayta urinib ko'ring.", show_alert=True)
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass

    user = await db.get_user(call.from_user.id)
    if not user or not user.get("phone"):
        await bot.send_message(
            call.from_user.id,
            "✅ Obuna tasdiqlandi!\n\n"
            "📲 Endi botdan foydalanish uchun quyidagi "
            "«Telefon raqamni yuborish» tugmasini bosing:",
            reply_markup=phone_request_kb()
        )
        await state.set_state(PhoneState.wait_phone)
        await call.answer()
        return

    await bot.send_message(call.from_user.id, "✅ Obuna tasdiqlandi!", reply_markup=main_menu)
    await call.answer()

@router.message(F.content_type == "contact")
async def phone_received(msg: Message, state: FSMContext):
    if msg.contact.user_id and msg.contact.user_id != msg.from_user.id:
        await msg.answer("⚠️ Iltimos, faqat o'zingizga tegishli telefon raqamni yuboring.", reply_markup=phone_request_kb())
        return

    phone = msg.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    user_id  = msg.from_user.id
    fullname = msg.from_user.full_name
    username = str(msg.from_user.username or "")

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
            await db.log_transaction(referrer_id, "referral_bonus", ref_bonus, note=f"referred:{user_id}")
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
    await msg.answer("⚠️ Iltimos, pastdagi «📲 Telefon raqamni yuborish» tugmasini bosing — matn kiritish shart emas.", reply_markup=phone_request_kb())

# ─── HISOBIM ───────────────────────────────────────────────────
@router.message(F.text == "💰 Hisobim")
async def show_balance(msg: Message):
    user = await db.get_user(msg.from_user.id)
    if not user:
        await db.add_user(msg.from_user.id, msg.from_user.full_name, "")
        user = await db.get_user(msg.from_user.id)
    purchases = await db.get_purchases(msg.from_user.id)
    text = (
        f"👤 <b>Shaxsiy kabinetingiz</b>\n\n"
        f"🆔 Tartib raqami: <b>{user['tartib_id']}</b>\n"
        f"🆔 Telegram ID: <code>{user['user_id']}</code>\n"
        f"📱 Telefon: <code>{user['phone'] or 'Kiritilmagan'}</code>\n\n"
        f"💰 Balans: <b>{user['balance']:,.0f} so'm</b>\n"
        f"💵 Jami to'ldirilgan: <b>{user['total_deposited']:,.0f} so'm</b>\n"
        f"🛒 Xaridlar soni: <b>{len(purchases)} ta</b>\n\n"
        f"Hisobingizni to'ldirib, xizmatlardan bemalol foydalanishda davom eting ⚡"
    )
    orders_channel = await get_setting("orders_channel_username", "")
    buttons = [[InlineKeyboardButton(text="💳 Hisob to'ldirish", callback_data="goto_topup")]]
    if orders_channel:
        buttons.append([InlineKeyboardButton(text="📦 Buyurtmalar kanali", url=f"https://t.me/{orders_channel}")])
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "goto_topup")
async def goto_topup(call: CallbackQuery, state: FSMContext):
    await call.message.answer(TOPUP_INTRO, reply_markup=cancel_kb)
    await state.set_state(PayStates.wait_amount)
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
    total_pages      = (len(sorted_countries) - 1) // 10 + 1 if sorted_countries else 0
    start = page * 10
    end   = start + 10
    buttons = []
    for code, info in sorted_countries[start:end]:
        qty       = info.get("qty", 0)
        name      = info.get("name", code)
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or int(usd_price * 12500 * 1.3)
        flag      = country_flag(code)
        buttons.append([InlineKeyboardButton(
            text=f"{name} {flag} — {uzs_price:,} so'm -| {qty} dona",
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
        InlineKeyboardButton(text="📈 TOP 10 davlatlar", callback_data="top10_countries"),
        InlineKeyboardButton(text="🎊 Arzon raqamlar",   callback_data="cheap_countries"),
    ])
    return buttons, total_pages

@router.message(F.text == "📞 Nomer olish")
async def get_number_menu(msg: Message):
    loading_msg = await msg.answer("⏳ Mavjud davlatlar ro'yxati yuklanmoqda...")
    buttons, total_pages = await build_countries_page(0)
    try:
        await loading_msg.delete()
    except Exception:
        pass
    if not buttons:
        await msg.answer("❌ Hozircha davlatlar ro'yxatini olib bo'lmadi. Iltimos, birozdan so'ng qayta urinib ko'ring.")
        return
    await msg.answer(
        f"🌐 <b>Mavjud davlatlar ro'yxati:</b>\n<i>{page_label(0, total_pages)}</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

def page_label(page: int, total_pages: int) -> str:
    return f"{page+1}/{total_pages}" if total_pages else "0/0"

@router.callback_query(F.data.startswith("countries_page:"))
async def countries_page_cb(call: CallbackQuery):
    page = int(call.data.split(":")[1])
    buttons, total_pages = await build_countries_page(page)
    if not buttons:
        return await call.answer("❌ Ro'yxatni yuklab bo'lmadi, birozdan so'ng qayta urinib ko'ring.", show_alert=True)
    try:
        await call.message.edit_text(
            f"🌐 <b>Mavjud davlatlar ro'yxati:</b>\n<i>{page_label(page, total_pages)}</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except Exception:
        pass
    await call.answer()

@router.callback_query(F.data == "top10_countries")
async def top10_countries(call: CallbackQuery):
    data = await api_available_countries()
    if data.get("status") != "ok":
        return await call.answer("❌ Ro'yxatni yuklab bo'lmadi.", show_alert=True)
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
        flag      = country_flag(code)
        buttons.append([InlineKeyboardButton(
            text=f"{name} {flag} — {uzs_price:,} so'm -| {qty} dona",
            callback_data=f"buy:{code}:{uzs_price}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="countries_page:0")])
    await call.message.edit_text("📈 <b>TOP 10 davlat (raqamlar soni bo'yicha):</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data == "cheap_countries")
async def cheap_countries(call: CallbackQuery):
    data = await api_available_countries()
    if data.get("status") != "ok":
        return await call.answer("❌ Ro'yxatni yuklab bo'lmadi.", show_alert=True)
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
        flag      = country_flag(code)
        buttons.append([InlineKeyboardButton(
            text=f"{name} {flag} — {uzs_price:,} so'm -| {qty} dona",
            callback_data=f"buy:{code}:{uzs_price}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="countries_page:0")])
    await call.message.edit_text("🎊 <b>Eng arzon raqamlar:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()

# ─── NOMER SOTIB OLISH (2 bosqichli tasdiqlash) ────────────────
async def _country_name(country_code: str) -> str:
    data = await api_available_countries()
    countries = data.get("countries", {}) if data.get("status") == "ok" else {}
    info = countries.get(country_code, {})
    return info.get("name", country_code)

@router.callback_query(F.data.startswith("buy:"))
async def buy_number(call: CallbackQuery):
    """1-bosqich: tanlangan raqam haqida ma'lumot va ogohlantirish."""
    parts        = call.data.split(":")
    country_code = parts[1]
    uzs_price    = int(parts[2])
    user_id      = call.from_user.id
    if country_code.upper() in BLOCKED_COUNTRIES:
        return await call.answer("❌ Bu davlat uchun raqamlar mavjud emas.", show_alert=True)
    bal = await db.get_balance(user_id)
    if bal < uzs_price:
        return await call.answer(
            f"❌ Hisobingizda mablag' yetarli emas.\n"
            f"Raqam narxi: {uzs_price:,} so'm\nSizning balansingiz: {bal:,} so'm",
            show_alert=True
        )
    name = await _country_name(country_code)
    flag = country_flag(country_code)

    text = (
        f"🧾 <b>Siz sotib olmoqchi bo'layotgan raqam haqida ma'lumot:</b>\n\n"
        f"🌐 Davlat: <b>{name} {flag}</b>\n"
        f"💵 Narxi: <b>{uzs_price:,} so'm</b>\n\n"
        f"<blockquote>❗️ Diqqat: Botdan olingan raqamlar uchun hech qanday kafolat berilmaydi va pul qaytarilmaydi!</blockquote>\n\n"
        f"<blockquote>📌 Rasmiy Telegramdan olmang!!!\n"
        f"Agar siz rasmiy Telegram ilovasidan foydalansangiz, pulingiz 100% kuyadi va bu uchun bot va admin javobgar emas!\n"
        f"Faqat 🟢 Telegraph yoki Plus kabi ilovalardan foydalanish tavsiya etiladi!!!</blockquote>\n\n"
        f"👉 Shartlar bilan to'liq tanishib chiqing va \"🛒 Sotib olish\" tugmasini bosing!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Sotib olish", callback_data=f"terms:{country_code}:{uzs_price}")],
        [InlineKeyboardButton(text="🚫 Bekor qilish", callback_data="countries_page:0")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("terms:"))
async def buy_terms(call: CallbackQuery):
    """2-bosqich: xizmat ko'rsatish shartlari va yakuniy tasdiq."""
    parts        = call.data.split(":")
    country_code = parts[1]
    uzs_price    = int(parts[2])
    user_id      = call.from_user.id
    bal = await db.get_balance(user_id)
    if bal < uzs_price:
        return await call.answer("❌ Balans yetarli emas.", show_alert=True)

    text = (
        "🚀 Bizning bot orqali taqdim etilayotgan akkauntlar — tayyor ochilgan Telegram "
        "akkauntlar bazasidan olinadi va sotib olishdan oldin ma'lumotlarni tekshirish "
        "sizning vazifangizdir:\n\n"
        "<blockquote>⚠️ Kod faqat 🟢 <b>Telegraph</b> ilovasi orqali yuborilishi lozim!\n"
        "Agar Telegramning rasmiy ilovasidan kod yuborilsa, kod yetib bormasligi yoki "
        "xatoliklar bo'lishi mumkin — buning uchun adminlar javobgar emas!</blockquote>\n\n"
        "<blockquote>‼️ Sotib olingan akkauntlar uchun hech qanday kafolat yo'q. Muammo "
        "bo'lsa, pulni qaytarish yoki almashtirish kafolati berilmaydi. ❌</blockquote>\n\n"
        "✅ Raqamni to'g'ri ishlatish, spamdan saqlash va xavfsizlik choralariga rioya "
        "qilish — butunlay foydalanuvchi mas'uliyatidadir 🛡\n\n"
        "👍 Qoidalar bilan tanishib chiqing va \"🟢 Davom etish\" tugmasini bosing!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Davom etish", callback_data=f"confirm_buy:{country_code}:{uzs_price}")],
        [InlineKeyboardButton(text="🚫 Bekor qilish", callback_data="countries_page:0")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

_purchase_locks: dict[int, asyncio.Lock] = {}

def get_purchase_lock(user_id: int) -> asyncio.Lock:
    lock = _purchase_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _purchase_locks[user_id] = lock
    return lock

@router.callback_query(F.data.startswith("confirm_buy:"))
async def confirm_buy(call: CallbackQuery):
    parts        = call.data.split(":")
    country_code = parts[1]
    uzs_price    = int(parts[2])
    user_id      = call.from_user.id

    lock = get_purchase_lock(user_id)
    if lock.locked():
        return await call.answer("⏳ Oldingi buyurtmangiz hali qayta ishlanmoqda, biroz kuting.", show_alert=True)

    async with lock:
        bal = await db.get_balance(user_id)
        if bal < uzs_price:
            return await call.answer("❌ Balans yetarli emas.", show_alert=True)

        await call.message.edit_text("⏳ Raqam sotib olinmoqda... Iltimos, kuting.")
        result = await api_buy_number(country_code)
        if result.get("status") != "ok":
            err = result.get("message", "Noma'lum xato")
            await call.message.edit_text(
                f"❌ Raqam olishda xatolik yuz berdi: {err}\n\n"
                f"Mablag'ingiz hisobingizdan yechilmadi — istasangiz qayta urinib ko'rishingiz mumkin.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Davlatlar ro'yxatiga qaytish", callback_data="countries_page:0")]
                ])
            )
            await call.answer()
            return

        number   = result.get("Number", "")
        api_name = result.get("name", country_code)
        await db.update_balance(user_id, -uzs_price)
        await db.log_transaction(user_id, "purchase", -uzs_price, note=f"{country_code}:{number}")
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
        except Exception:
            pass
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 SMS kodni olish", callback_data=f"getcode:{number}")],
            [InlineKeyboardButton(text="🛒 Buyurtmalarim", callback_data="my_orders_inline")],
        ])
        await call.message.edit_text(
            f"✅ <b>Raqam muvaffaqiyatli olindi!</b>\n\n"
            f"📞 Raqamingiz: <code>{number}</code>\n"
            f"💰 Narxi: {uzs_price:,} so'm\n\n"
            f"⚠️ Faqat norasmiy (Telegraph kabi) ilovalardan foydalaning.\n\n"
            f"💡 Kirish kodini olish uchun pastdagi «🔑 SMS kodni olish» tugmasini bosing.",
            reply_markup=kb
        )
    await call.answer()

@router.callback_query(F.data == "my_orders_inline")
async def my_orders_inline(call: CallbackQuery):
    await my_orders(call.message, override_user_id=call.from_user.id)
    await call.answer()

@router.callback_query(F.data.startswith("getcode:"))
async def get_code(call: CallbackQuery):
    number  = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    purchase = await db.get_purchase_by_phone(user_id, number)
    if not purchase:
        return await call.answer("❌ Bu raqam sizga tegishli emas.", show_alert=True)
    await call.answer("⏳ Kod olinmoqda...")
    result = await api_get_code(number)
    if result.get("status") != "ok":
        err = result.get("message", "Noma'lum xato")
        kb  = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Qayta urinish", callback_data=f"getcode:{number}")]
        ])
        return await call.message.edit_text(f"❌ Kod olishda xatolik: {err}\n\nBiroz kutib, qayta urinib ko'ring.", reply_markup=kb)
    code     = result.get("code", "Topilmadi")
    password = result.get("pass", "")
    text = f"📨 <b>{number}</b> raqami uchun kod:\n\n🔑 Kirish kodi: <code>{code}</code>\n"
    if password:
        text += f"🔐 2-bosqichli parol: <code>{password}</code>\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Yangi kod olish", callback_data=f"getcode:{number}")]
    ])
    await call.message.edit_text(text, reply_markup=kb)

# ─── BUYURTMALARIM ─────────────────────────────────────────────
@router.message(F.text == "🛒 Buyurtmalarim")
async def my_orders(msg: Message, override_user_id: int | None = None):
    user_id = override_user_id or msg.from_user.id
    orders = await db.get_purchases(user_id)
    if not orders:
        await msg.answer("🛒 Siz hali raqam sotib olmagansiz.\n📞 Raqam olish uchun «📞 Nomer olish» tugmasini bosing.")
        return
    await msg.answer(f"🛒 <b>Sizning buyurtmalaringiz ({len(orders)} ta):</b>")
    for i, order in enumerate(orders[:20], 1):
        phone       = order['phone']
        country     = order['country_name']
        price       = order['price']
        bought_date = order['created_at']
        flag        = country_flag(order.get('country_code', ''))
        try:
            if isinstance(bought_date, str):
                d = datetime.strptime(bought_date[:19], '%Y-%m-%d %H:%M:%S')
            else:
                d = bought_date
            formatted = d.strftime('%d.%m.%Y %H:%M')
        except Exception:
            formatted = str(bought_date)
        text = (
            f"<b>{i}. 🌍 {country} {flag}</b>\n"
            f"📞 <code>{phone}</code>\n"
            f"💰 Narx: {price:,} so'm\n"
            f"📅 Sana: {formatted}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 SMS kodni olish", callback_data=f"getcode:{phone}")]
        ])
        await msg.answer(text, reply_markup=kb)

# ─── PUL ISHLASH ───────────────────────────────────────────────
@router.message(F.text == "💸 Pul ishlash")
async def earn_money(msg: Message, override_user_id: int | None = None):
    user_id = override_user_id or msg.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Referal orqali", callback_data=f"referral:{user_id}")],
        [InlineKeyboardButton(text="🎁 Kunlik bonus",   callback_data="daily_bonus")],
    ])
    await msg.answer("💸 <b>Pul ishlash uchun bo'limni tanlang:</b>", reply_markup=kb)

@router.callback_query(F.data.startswith("referral:"))
async def show_referral(call: CallbackQuery):
    user_id   = call.from_user.id
    me        = await bot.get_me()
    ref_link  = f"https://t.me/{me.username}?start=ref_{user_id}"
    ref_count = await db.get_referral_count(user_id)
    earnings  = await db.get_referral_earnings(user_id)
    ref_bonus = int(await get_setting("referral_bonus", 500))
    text = (
        f"👥 <b>Referal tizimi</b>\n\n"
        f"🔗 Sizning shaxsiy referal havolangiz:\n<code>{ref_link}</code>\n\n"
        f"👤 Taklif qilingan do'stlar: <b>{ref_count} ta</b>\n"
        f"💰 Referaldan daromad: <b>{earnings:,} so'm</b>\n\n"
        f"💡 Har bir taklif qilgan do'stingiz uchun <b>{ref_bonus:,} so'm</b> bonus olasiz!\n"
        f"⚠️ Bonus faqat <b>+998</b> (O'zbekiston) raqamli foydalanuvchilar uchun beriladi."
    )
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_earn")]
    ]))
    await call.answer()

@router.callback_query(F.data == "daily_bonus")
async def daily_bonus_cb(call: CallbackQuery):
    user_id     = call.from_user.id
    today       = now_tashkent().date().isoformat()
    last        = await db.get_last_bonus_date(user_id)
    daily_bonus = int(await get_setting("daily_bonus", 200))
    if last == today:
        await call.answer("❌ Bugungi bonusni allaqachon olib bo'lgansiz. Ertaga qayta urinib ko'ring!", show_alert=True)
        return
    await db.update_balance(user_id, daily_bonus)
    await db.set_last_bonus_date(user_id, today)
    await db.log_transaction(user_id, "daily_bonus", daily_bonus, note="kunlik_bonus")
    await call.answer(f"🎁 Kunlik bonus: {daily_bonus:,} so'm hisobingizga qo'shildi!", show_alert=True)

@router.callback_query(F.data == "back_earn")
async def back_earn(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await earn_money(call.message, override_user_id=call.from_user.id)
    await call.answer()

@router.message(F.text == "📕 Qo'llanma")
async def guide_menu(msg: Message):
    text = (
        "<b>📕 Botdan foydalanish qo'llanmasi</b>\n\n"
        "1️⃣ <b>📞 Nomer olish</b> — Telegram ro'yxatdan o'tish uchun virtual raqam sotib olasiz.\n"
        "2️⃣ <b>🛒 Buyurtmalarim</b> — sotib olgan raqamlaringiz va ulardan kod olish shu yerda.\n"
        "3️⃣ <b>💰 Hisobim</b> — joriy balansingiz va shaxsiy ma'lumotlaringiz.\n"
        "4️⃣ <b>💳 Hisob to'ldirish</b> — bot hisobingizni pul bilan to'ldirasiz.\n"
        "5️⃣ <b>💸 Pul ishlash</b> — do'stlaringizni taklif qilib yoki kunlik bonus orqali pul ishlaysiz.\n"
        "6️⃣ <b>🆘 Qo'llab-quvvatlash</b> — savol yoki muammo bo'lsa, admin bilan bog'lanasiz.\n\n"
        "Savollaringiz bo'lsa, «🆘 Qo'llab-quvvatlash» bo'limiga murojaat qiling."
    )
    await msg.answer(text)

@router.message(F.text == "🆘 Qo'llab-quvvatlash")
async def support_menu(msg: Message):
    await msg.answer(
        f"🆘 <b>Qo'llab-quvvatlash</b>\n\n"
        f"Savol, muammo yoki taklifingiz bo'lsa, quyidagi havola orqali "
        f"to'g'ridan-to'g'ri adminga yozing:\n"
        f"<a href='tg://user?id={ADMIN_ID}'>👤 Admin bilan bog'lanish</a>"
    )

# ══════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════════════════════

async def show_admin_panel(target):
    users_count     = await db.count_users()
    orders_count    = await db.count_orders()
    api_bal         = await api_get_balance_tglion()
    api_balance     = api_bal.get("balance", "N/A")
    daily_bonus_val = await get_setting("daily_bonus", 200)
    ref_bonus_val   = await get_setting("referral_bonus", 500)
    channel_un_val  = await get_setting("required_channel_username", CHANNEL_USERNAME)
    maintenance     = await is_maintenance_mode()
    text = (
        f"🔐 <b>Admin panel</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count}</b>\n"
        f"🛒 Jami buyurtmalar: <b>{orders_count}</b>\n"
        f"💰 TG-Lion balansi: <b>{api_balance}</b>\n\n"
        f"⚙️ <b>Joriy sozlamalar:</b>\n"
        f"🎁 Kunlik bonus: <b>{daily_bonus_val} so'm</b>\n"
        f"👥 Referal bonus: <b>{ref_bonus_val} so'm</b>\n"
        f"📢 Kanal: <b>@{channel_un_val}</b>\n"
        f"🔧 Ta'mirlash rejimi: <b>{'🔴 YOQILGAN' if maintenance else '🟢 O`CHIRILGAN'}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika",           callback_data="adm_stats"),
         InlineKeyboardButton(text="👥 Foydalanuvchilar",     callback_data="adm_users")],
        [InlineKeyboardButton(text="📅 Kunlik hisobot",       callback_data="adm_daily_report"),
         InlineKeyboardButton(text="🔍 Foydalanuvchi profili", callback_data="adm_user_profile")],
        [InlineKeyboardButton(text="➕➖ Balans o'zgartirish", callback_data="adm_balance"),
         InlineKeyboardButton(text="💵 Narx sozlash",         callback_data="adm_prices")],
        [InlineKeyboardButton(text="📞 Raqam qidirish",       callback_data="adm_search"),
         InlineKeyboardButton(text="📣 Xabar yuborish",       callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="⚙️ Bot sozlamalari",      callback_data="adm_settings"),
         InlineKeyboardButton(text="🔄 Yangilash",            callback_data="adm_refresh")],
        [InlineKeyboardButton(
            text=("🔧 Ta'mirlashni o'chirish" if maintenance else "🔧 Ta'mirlash rejimini yoqish"),
            callback_data="adm_toggle_maintenance"
        )],
    ])
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
    else:
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except Exception:
            await target.message.answer(text, reply_markup=kb)
        await target.answer()

@admin_router.message(F.text == "/admin")
async def admin_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await show_admin_panel(msg)

@admin_router.message(
    F.text.func(is_cancel_text),
    StateFilter(
        BalanceChangeState.wait_user_id, BalanceChangeState.wait_amount,
        AdminSearchState.wait_phone, AdminSearchState.wait_profile_id, BroadcastState.wait_message,
        AdminSettingsState.wait_daily_bonus, AdminSettingsState.wait_referral_bonus,
        AdminSettingsState.wait_bulk_percent, AdminSettingsState.wait_channel_id,
        AdminSettingsState.wait_channel_username, AdminSettingsState.wait_price_value,
        AdminSettingsState.wait_orders_channel_id, AdminSettingsState.wait_orders_channel_username,
        AdminSettingsState.wait_card_number, AdminSettingsState.wait_card_owner,
    )
)
async def admin_cancel_any(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await msg.answer("❌ Amal bekor qilindi.")
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_refresh")
async def adm_refresh(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    await show_admin_panel(call)

@admin_router.callback_query(F.data == "adm_toggle_maintenance")
async def adm_toggle_maintenance(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    current = await is_maintenance_mode()
    await db.set_setting("maintenance_mode", "0" if current else "1")
    await call.answer("🟢 Ta'mirlash rejimi o'chirildi" if current else "🔴 Ta'mirlash rejimi yoqildi", show_alert=True)
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
    if len(users) > 30:
        text += f"\n… va yana {len(users) - 30} ta foydalanuvchi.\nBirontasini batafsil ko'rish uchun «🔍 Foydalanuvchi profili» dan foydalaning."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_refresh")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

# ─── KUNLIK HISOBOT (foyda / zarar) ─────────────────────────────
@admin_router.callback_query(F.data == "adm_daily_report")
async def adm_daily_report(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    start_utc, end_utc = today_utc_bounds()
    report    = await db.get_daily_report(start_utc, end_utc)
    new_users = await db.get_new_users_count(start_utc, end_utc)

    topup       = report.get("topup",          {"count": 0, "total": 0})
    purchase    = report.get("purchase",        {"count": 0, "total": 0})   # manfiy summa
    ref_bonus   = report.get("referral_bonus",  {"count": 0, "total": 0})
    daily_bonus = report.get("daily_bonus",     {"count": 0, "total": 0})
    admin_adj   = report.get("admin_adjust",    {"count": 0, "total": 0})

    purchase_revenue = abs(purchase["total"])
    net_profit = purchase_revenue - ref_bonus["total"] - daily_bonus["total"] + admin_adj["total"]

    today_str = now_tashkent().strftime("%d.%m.%Y")
    text = (
        f"📅 <b>Kunlik hisobot — {today_str}</b>\n\n"
        f"🆕 Yangi foydalanuvchilar: <b>{new_users} ta</b>\n\n"
        f"💳 <b>Tasdiqlangan to'lovlar</b>\n"
        f"   ✅ Soni: <b>{topup['count']} ta</b>\n"
        f"   💰 Summasi: <b>{topup['total']:,} so'm</b>\n\n"
        f"🛒 <b>Sotilgan raqamlar</b>\n"
        f"   📦 Soni: <b>{purchase['count']} ta</b>\n"
        f"   💰 Daromad: <b>{purchase_revenue:,} so'm</b>\n\n"
        f"🎁 Kunlik bonuslar berildi: <b>{daily_bonus['count']} ta / {daily_bonus['total']:,} so'm</b>\n"
        f"👥 Referal bonuslar berildi: <b>{ref_bonus['count']} ta / {ref_bonus['total']:,} so'm</b>\n"
        f"➕➖ Admin tomonidan qo'lda o'zgartirilgan: <b>{admin_adj['count']} ta / {admin_adj['total']:+,} so'm</b>\n\n"
        f"📈 <b>Sof foyda (bugungi):</b> <b>{net_profit:,} so'm</b>\n"
        f"<i>(Sotuv daromadi − berilgan bonuslar ± admin tuzatishlari)</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_refresh")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

# ─── FOYDALANUVCHI PROFILI (to'liq ma'lumot) ───────────────────
@admin_router.callback_query(F.data == "adm_user_profile")
async def adm_user_profile_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("🔍 Foydalanuvchining Telegram ID sini kiriting:")
    await state.set_state(AdminSearchState.wait_profile_id)
    await call.answer()

@admin_router.message(AdminSearchState.wait_profile_id)
async def adm_user_profile_show(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if not msg.text or not msg.text.strip().lstrip("-").isdigit():
        return await msg.answer("❌ Faqat ID (son) kiriting, yoki bekor qilish uchun /cancel yozing.")
    uid = int(msg.text.strip())
    user = await db.get_user(uid)
    if not user:
        await msg.answer(f"❌ <code>{uid}</code> ID li foydalanuvchi topilmadi.")
        await state.clear()
        await show_admin_panel(msg)
        return

    tx_stats     = await db.get_user_tx_stats(uid)
    ref_count    = await db.get_referral_count(uid)
    ref_earnings = await db.get_referral_earnings(uid)
    referrer     = await db.get_referrer_info(uid)
    joined       = user["created_at"]
    try:
        joined_str = joined.strftime("%d.%m.%Y %H:%M") if not isinstance(joined, str) else joined
    except Exception:
        joined_str = str(joined)

    if referrer:
        referred_by_line = f"👤 Kim taklif qilgan: <b>{referrer['ref_fullname']}</b> (<code>{referrer['ref_id']}</code>)"
    else:
        referred_by_line = "👤 Kim taklif qilgan: <i>To'g'ridan-to'g'ri kirgan</i>"

    text = (
        f"🔍 <b>Foydalanuvchi profili</b>\n\n"
        f"🆔 Tartib raqami: <b>{user['tartib_id']}</b>\n"
        f"🆔 Telegram ID: <code>{user['user_id']}</code>\n"
        f"👤 Ism: <b>{user['fullname']}</b>\n"
        f"🔗 Username: @{user['username'] or '—'}\n"
        f"📱 Telefon: <code>{user['phone'] or 'Kiritilmagan'}</code>\n"
        f"📅 Qo'shilgan sana: <b>{joined_str}</b>\n"
        f"{referred_by_line}\n\n"
        f"💰 Joriy balans: <b>{user['balance']:,} so'm</b>\n"
        f"💵 Jami to'ldirilgan: <b>{user['total_deposited']:,} so'm</b>\n\n"
        f"💳 <b>To'lovlar</b>\n"
        f"   ✅ Tasdiqlangan: <b>{tx_stats['topup_count']} marta</b>\n"
        f"   💰 Jami summa: <b>{tx_stats['topup_total']:,} so'm</b>\n\n"
        f"🛒 <b>Xaridlar</b>\n"
        f"   📦 Soni: <b>{tx_stats['purchase_count']} ta</b>\n"
        f"   💰 Jami xarajat: <b>{tx_stats['purchase_total']:,} so'm</b>\n\n"
        f"👥 <b>Referal</b>\n"
        f"   👤 Taklif qilganlar: <b>{ref_count} ta</b>\n"
        f"   💰 Referaldan daromad: <b>{ref_earnings:,} so'm</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Boshqa profil qidirish", callback_data="adm_user_profile")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_refresh")],
    ])
    await state.clear()
    await msg.answer(text, reply_markup=kb)

@admin_router.callback_query(F.data == "adm_balance")
async def adm_balance_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("Foydalanuvchi ID sini kiriting (bekor qilish uchun /cancel yozing):")
    await state.set_state(BalanceChangeState.wait_user_id)
    await call.answer()

@admin_router.message(BalanceChangeState.wait_user_id)
async def adm_balance_uid(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if not msg.text or not msg.text.isdigit():
        return await msg.answer("❌ Faqat son (ID) kiriting, yoki bekor qilish uchun /cancel yozing.")
    await state.update_data(uid=int(msg.text))
    await msg.answer("Qancha so'm qo'shmoqchisiz? (Ayirish uchun: -5000)")
    await state.set_state(BalanceChangeState.wait_amount)

@admin_router.message(BalanceChangeState.wait_amount)
async def adm_balance_amount(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    try:
        amount = int(msg.text)
    except Exception:
        return await msg.answer("❌ Faqat son kiriting, yoki bekor qilish uchun /cancel yozing.")
    data = await state.get_data()
    uid  = data['uid']
    await db.update_balance(uid, amount)
    await db.log_transaction(uid, "admin_adjust", amount, note="adm_balance_panel")
    try:
        await bot.send_message(uid, f"ℹ️ Hisobingiz admin tomonidan <b>{amount:+,} so'm</b>ga o'zgartirildi.")
    except Exception:
        pass
    await msg.answer(f"✅ <code>{uid}</code> ID li foydalanuvchi balansi {amount:+,} so'mga o'zgartirildi.")
    await state.clear()
    await show_admin_panel(msg)

# ─── NARX SOZLASH ──────────────────────────────────────────────
@admin_router.callback_query(F.data == "adm_prices")
async def adm_prices(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Bitta davlat narxini o'zgartirish",    callback_data="price_single")],
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
        flag  = country_flag(code)
        cur   = markup_prices.get(code.upper(), None)
        label = f"{cur:,} so'm" if cur else "Standart"
        buttons.append([InlineKeyboardButton(text=f"{name} {flag}: {label}", callback_data=f"setprice:{code}")])
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
        flag      = country_flag(code)
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or int(usd_price * 12500 * 1.3)
        text += f"🌍 {name} {flag} (<code>{code}</code>): <b>{uzs_price:,} so'm</b>\n"
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
    except Exception:
        return await msg.answer("❌ Faqat son kiriting, yoki bekor qilish uchun /cancel yozing.")
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
        return await msg.answer("❌ Faqat son kiriting, yoki bekor qilish uchun /cancel yozing.")
    price = int(msg.text)
    data  = await state.get_data()
    code  = data['country']
    await db.set_markup_price(code.upper(), price)
    await msg.answer(f"✅ {code} uchun yangi narx: {price:,} so'm o'rnatildi.")
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
    except Exception: return await msg.answer("❌ Kanal ID raqam bo'lishi kerak!")
    await db.set_setting("required_channel_id", msg.text)
    await msg.answer("✅ Kanal ID saqlandi! Endi kanal username kiriting (@ belgisiz):")
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
    except Exception: return await msg.answer("❌ Kanal ID raqam bo'lishi kerak!")
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
        except Exception:
            failed += 1
        if i % 20 == 0:
            try:
                await status_msg.edit_text(f"📣 Yuborilmoqda... {i+1}/{len(users)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"📣 Xabar yuborildi:\n✅ Muvaffaqiyatli: {sent} ta\n❌ Yetib bormadi: {failed} ta")
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
