"""TMDB 客户端:高清横版背景图(可选,无 API key 时整体跳过)。"""
import logging

from app.clients.http import make_client
from app.config import settings

log = logging.getLogger(__name__)
API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w1280"


class TmdbClient:
    @property
    def enabled(self) -> bool:
        return bool(settings.tmdb_api_key)

    def find_backdrop(self, query: str) -> tuple[int, str] | None:
        """按名称搜 TV,返回 (tmdb_id, backdrop完整URL);搜不到返回 None。"""
        if not self.enabled:
            return None
        with make_client("tmdb") as c:
            r = c.get(f"{API}/search/tv", params={
                "api_key": settings.tmdb_api_key, "query": query, "language": "zh-CN"})
            r.raise_for_status()
            results = r.json().get("results", [])
        for hit in results:
            if hit.get("backdrop_path"):
                return hit["id"], IMG + hit["backdrop_path"]
        return None

    def download_image(self, url: str) -> bytes:
        with make_client("tmdb") as c:
            r = c.get(url)
            r.raise_for_status()
            return r.content


tmdb_client = TmdbClient()
