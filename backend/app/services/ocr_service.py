import base64
import httpx
from app.config import settings


async def get_baidu_access_token() -> str:
    url = "https://aip.baidubce.com/oauth/2.0/token"

    params = {
        "grant_type": "client_credentials",
        "client_id": settings.baidu_ocr_api_key,
        "client_secret": settings.baidu_ocr_secret_key,
    }

    timeout = httpx.Timeout(20.0, connect=8.0, read=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        message = data.get("error_description") or data.get("error") or "百度 OCR 鉴权失败"
        raise RuntimeError(message)

    return data["access_token"]


async def baidu_ocr_image(image_bytes: bytes) -> str:
    token = await get_baidu_access_token()

    url = (
        "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"
        f"?access_token={token}"
    )

    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "image": image_base64,
        "detect_direction": "true",
        "paragraph": "true",
    }

    timeout = httpx.Timeout(65.0, connect=10.0, read=65.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, data=data)
        resp.raise_for_status()
        result = resp.json()

    if "error_code" in result:
        message = result.get("error_msg") or f"百度 OCR 返回错误码 {result['error_code']}"
        raise RuntimeError(message)

    words = []
    for item in result.get("words_result", []):
        words.append(item.get("words", ""))

    return "\n".join(words)
