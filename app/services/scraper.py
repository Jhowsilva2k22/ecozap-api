import httpx
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)

class ScraperService:
    def __init__(self):
        self.settings = get_settings()

    async def read_links(self, links: list) -> str:
        all_content = []
        for link in links:
            content = await self.read_link(link)
            if content:
                all_content.append(f"=== {link} ===\n{content}\n")
        return "\n".join(all_content)

    async def read_link(self, url: str) -> str:
        if self.settings.firecrawl_api_key:
            return await self._read_with_firecrawl(url)
        return await self._read_simple(url)

    async def _read_with_firecrawl(self, url: str) -> str:
        api_url = "https://api.firecrawl.dev/v1/scrape"
        headers = {"Authorization": f"Bearer {self.settings.firecrawl_api_key}", "Content-Type": "application/json"}
        payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(api_url, json=payload, headers=headers)
                data = response.json()
                return data.get("data", {}).get("markdown", "")[:5000]
            except Exception as e:
                logger.error(f"Erro Firecrawl para {url}: {e}")
                return await self._read_simple(url)

    async def _read_simple(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            try:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                import re
                text = response.text
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:5000]
            except Exception as e:
                logger.error(f"Erro leitura simples para {url}: {e}")
                return ""
