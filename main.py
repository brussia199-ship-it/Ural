import asyncio
import re
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

BOT_TOKEN = "8737031061:AAFcp-LWdDfuUiI4VRTFBFrmaAr6k0H9-fY"
GETGEMS_API = "https://api.getgems.io/graphql"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---------- Парсинг ссылки ----------
def parse_gift_link(text: str) -> dict | None:
    """
    Возвращает {'type': 'address'|'shortname', 'value': str}
    """
    text = text.strip()

    # 1) Прямой адрес блокчейна TON (EQ... / UQ...) в getgems/tonviewer
    m = re.search(r"(E[Qq][A-Za-z0-9_\-]{46}|U[Qq][A-Za-z0-9_\-]{46})", text)
    if m:
        return {"type": "address", "value": m.group(1)}

    # 2) t.me/nft/PlushPepe-1234
    m = re.search(r"t\.me/nft/([A-Za-z0-9]+-\d+)", text)
    if m:
        return {"type": "shortname", "value": m.group(1)}

    # 3) fragment.com/gift/plushpepe-1234
    m = re.search(r"fragment\.com/gift/([a-z0-9]+-\d+)", text)
    if m:
        return {"type": "shortname", "value": m.group(1)}

    # 4) getgems.io/collection/<col>/<id>
    m = re.search(r"getgems\.io/[^/]+/[^/]+/([A-Za-z0-9]+-\d+)", text)
    if m:
        return {"type": "shortname", "value": m.group(1)}

    # 5) getgems.io/nft/<address>
    m = re.search(r"getgems\.io/nft/(E[Qq][A-Za-z0-9_\-]{46})", text)
    if m:
        return {"type": "address", "value": m.group(1)}

    return None


# ---------- Запросы к Getgems ----------
async def gql(query: str, variables: dict) -> dict | None:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            GETGEMS_API,
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("data")


async def get_nft_by_address(address: str) -> dict | None:
    query = """
    query NftItem($address: String!) {
        alphaNftItem(address: $address) {
            name
            address
            salePrice
            sale { price fullName }
            collection { name }
            owner { address name username }
            rarityRank
            totalSupply
        }
    }
    """
    data = await gql(query, {"address": address})
    return data.get("alphaNftItem") if data else None


async def get_nft_by_shortname(shortname: str) -> dict | None:
    """
    Getgems не принимает shortname напрямую — сначала ищем через поиск,
    затем берём адрес и запрашиваем полные данные.
    """
    query = """
    query SearchNft($text: String!) {
        alphaNftItemSearch(query: $text, first: 5) {
            items { name address }
        }
    }
    """
    data = await gql(query, {"text": shortname})
    if not data:
        return None
    items = (data.get("alphaNftItemSearch") or {}).get("items", [])
    if not items:
        return None
    # ищем точное совпадение по shortname
    exact = next((i for i in items if i["name"].lower() == shortname.lower()), items[0])
    return await get_nft_by_address(exact["address"])


async def get_floor_price(collection_name: str) -> float | None:
    query = """
    query Floor($name: String!) {
        alphaNftCollectionStats(collectionName: $name) { floorPrice }
    }
    """
    data = await gql(query, {"name": collection_name})
    if not data:
        return None
    stats = data.get("alphaNftCollectionStats") or {}
    floor = stats.get("floorPrice")
    return floor / 1e9 if floor else None


# ---------- Форматирование ответа ----------
def short_addr(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else a


def format_nft(nft: dict, floor: float | None) -> str:
    name = nft.get("name", "—")
    collection = (nft.get("collection") or {}).get("name", "—")
    owner = nft.get("owner") or {}
    sale_price_nano = nft.get("salePrice")
    rarity = nft.get("rarityRank")
    total = nft.get("totalSupply")

    # Владелец
    if owner.get("username"):
        owner_str = f"@{owner['username']}"
    elif owner.get("name"):
        owner_str = owner["name"]
    elif owner.get("address"):
        owner_str = short_addr(owner["address"])
    else:
        owner_str = "неизвестен (не в блокчейне / скрыт)"

    # Цена
    if sale_price_nano:
        price_str = f"🏷 Выставлен за *{sale_price_nano / 1e9:.3f} TON*"
    else:
        price_str = "🏷 *Не выставлен на продажу*"
        if floor:
            price_str += f"\n📉 Floor коллекции: *{floor:.3f} TON*"
        else:
            price_str = "🏷 Не выставлен на продажу\n📉 Floor: н/д"

    lines = [
        f"🎁 *{name}*",
        f"📦 Коллекция: {collection}",
        f"👤 Владелец: {owner_str}",
        price_str,
    ]
    if rarity and total:
        lines.append(f"⭐ Редкость: #{rarity} из {total}")
    return "\n".join(lines)


# ---------- Хендлеры ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Кинь мне ссылку на NFT-подарок Telegram, "
        "и я скажу владельца и цену.\n\n"
        "Подходят ссылки:\n"
        "• t.me/nft/PlushPepe-1234\n"
        "• getgems.io/nft/EQ…\n"
        "• fragment.com/gift/plushpepe-1234"
    )


@dp.message()
async def handle_link(message: Message):
    if not message.text:
        return

    parsed = parse_gift_link(message.text)
    if not parsed:
        await message.answer(
            "❓ Не похоже на ссылку NFT-подарка.\n"
            "Пример: `https://t.me/nft/PlushPepe-1234`",
            parse_mode="Markdown",
        )
        return

    wait = await message.answer("🔍 Ищу подарок в блокчейне...")

    try:
        if parsed["type"] == "address":
            nft = await get_nft_by_address(parsed["value"])
        else:
            nft = await get_nft_by_shortname(parsed["value"])
    except Exception as e:
        await wait.edit_text(f"⚠️ Ошибка запроса: `{e}`", parse_mode="Markdown")
        return

    if not nft:
        await wait.edit_text(
            "❌ Подарок не найден.\n\n"
            "Возможно:\n"
            "• он ещё не сконвертирован в NFT\n"
            "• ссылка неверная\n"
            "• Getgems временно недоступен"
        )
        return

    collection_name = (nft.get("collection") or {}).get("name")
    floor = await get_floor_price(collection_name) if collection_name else None

    await wait.edit_text(format_nft(nft, floor), parse_mode="Markdown")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
