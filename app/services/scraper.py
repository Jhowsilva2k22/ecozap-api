import httpx
import re
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
        """Detecta o tipo de link e usa a estratégia correta."""
        url = url.strip()

        # ── YouTube ──────────────────────────────────────────────────────────
        if "youtube.com" in url or "youtu.be" in url:
            content = await self._read_youtube(url)
            if content:
                return content
            logger.warning(f"[Scraper] YouTube sem legenda: {url}")
            return f"[Vídeo YouTube sem transcrição disponível: {url}]"

        # ── Instagram ────────────────────────────────────────────────────────
        if "instagram.com" in url:
            return await self._read_instagram(url)

        # ── Sites / PDFs / outros ────────────────────────────────────────────
        if self.settings.firecrawl_api_key:
            return await self._read_with_firecrawl(url)
        return await self._read_simple(url)

    # ── YouTube: extrai transcrição via youtube-transcript-api ───────────────

    async def _read_youtube(self, url: str) -> str:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            video_id = self._extract_youtube_id(url)
            if not video_id:
                return ""
            # Tenta PT-BR primeiro, depois PT, depois qualquer idioma
            for lang in [["pt-BR", "pt"], ["pt"], None]:
                try:
                    if lang:
                        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=lang)
                    else:
                        transcript = YouTubeTranscriptApi.get_transcript(video_id)
                    text = " ".join(t["text"] for t in transcript)
                    logger.info(f"[Scraper] YouTube transcrição OK: {video_id} ({len(text)} chars)")
                    return text[:6000]
                except Exception:
                    continue
        except ImportError:
            logger.error("[Scraper] youtube-transcript-api não instalado")
        except Exception as e:
            logger.error(f"[Scraper] Erro YouTube {url}: {e}")
        return ""

    def _extract_youtube_id(self, url: str) -> str:
        patterns = [
            r"youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})",
            r"youtu\.be/([a-zA-Z0-9_-]{11})",
            r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
            r"youtube\.com/embed/([a-zA-Z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    # ── Instagram: usa Firecrawl se disponível, senão tenta simples ──────────

    async def _read_instagram(self, url: str) -> str:
        if self.settings.firecrawl_api_key:
            content = await self._read_with_firecrawl(url)
            if content:
                return content
        # Fallback: scraping simples (funciona para perfis públicos com bio)
        return await self._read_simple(url)

    # ── Firecrawl (melhor para sites) ────────────────────────────────────────

    async def _read_with_firecrawl(self, url: str) -> str:
        api_url = "https://api.firecrawl.dev/v1/scrape"
        headers = {
            "Authorization": f"Bearer {self.settings.firecrawl_api_key}",
            "Content-Type": "application/json"
        }
        payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(api_url, json=payload, headers=headers)
                data = response.json()
                return data.get("data", {}).get("markdown", "")[:6000]
            except Exception as e:
                logger.error(f"[Scraper] Firecrawl erro {url}: {e}")
                return await self._read_simple(url)

    # ── Leitura HTTP simples (fallback universal) ─────────────────────────────

    async def _read_simple(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            try:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                text = response.text
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                logger.info(f"[Scraper] HTTP simples OK: {url} ({len(text)} chars)")
                return text[:6000]
            except Exception as e:
                logger.error(f"[Scraper] HTTP simples erro {url}: {e}")
                return ""
