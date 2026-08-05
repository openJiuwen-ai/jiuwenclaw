import asyncio
from typing import List, Dict, Any, Optional

import httpx
import logging

from jiuwenswarm.agents.harness.search.tools.tool_registry import tool

MAX_CONNECTIONS = 1200
Timeout = 120

# ---- 模块级共享 Semaphore，所有 WebSearcherAndFetchTool 实例共用 ----
_shared_search_sem: Optional[asyncio.Semaphore] = None
_shared_fetch_sem: Optional[asyncio.Semaphore] = None
_shared_fetch_summary_sem: Optional[asyncio.Semaphore] = None


class WebSearcherAndFetchTool:
    def __init__(self,
                 web_search_url: str,
                 web_fetch_url: str,
                 web_fetch_and_summary_url: str,
                 max_retry: int,
                 max_concurrency: int,
                 logger: logging.Logger,
                 use_cache: bool = False,
                 retry_interval: float = 3.0):
        global _shared_search_sem, _shared_fetch_sem, _shared_fetch_summary_sem
        if _shared_search_sem is None:
            _shared_search_sem = asyncio.Semaphore(max_concurrency)
            _shared_fetch_sem = asyncio.Semaphore(max_concurrency)
            _shared_fetch_summary_sem = asyncio.Semaphore(max_concurrency)
            logger.info(f"[ToolRateLimit] WebSearchTool 全局并发控制初始化: max_concurrency={max_concurrency}")
        self.use_cache = use_cache
        self.max_retry = max_retry
        self.retry_interval = retry_interval

        self._client: Optional[httpx.AsyncClient] = None
        self.web_search_url = web_search_url
        self.web_fetch_url = web_fetch_url
        self.web_fetch_and_summary_url = web_fetch_and_summary_url

        self.logger = logger

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或懒加载 httpx 异步客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=MAX_CONNECTIONS,
                                    max_keepalive_connections=min(MAX_CONNECTIONS, 200)),
                timeout=httpx.Timeout(timeout=Timeout, connect=10.0)
            )
        return self._client

    async def close(self):
        """关闭 httpx 客户端，释放连接池资源"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _post_with_retry(self, url: str, payload: dict) -> httpx.Response:
        client = await self._get_http_client()
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retry + 1):
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                # 仅对 5xx 重试；4xx（如参数错误）直接抛出
                if e.response is not None and e.response.status_code < 500:
                    raise
                last_exc = e
            except (httpx.TransportError, httpx.TimeoutException) as e:
                # 连接重置/断开、读写超时、连接池超时等
                last_exc = e
            if attempt < self.max_retry:
                self.logger.warning(
                    "POST %s failed (attempt %d/%d): %r; retrying in %.1fs",
                    url, attempt + 1, self.max_retry + 1, last_exc, self.retry_interval,
                )
                await asyncio.sleep(self.retry_interval)
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def format_search_results(results: list[dict]) -> str:
        lines = []

        for i, item in enumerate(results, start=1):
            title = item.get("title", "")
            url = item.get("url", "")
            snippet = item.get("snippet", "")
            query = item.get("query", "")
            date = item.get("date", "")

            lines.append(
                f"[{i}]\n"
                f"Title: {title}\n"
                f"URL: {url}\n"
                f"Snippet: {snippet}\n"
                f"Query: {query}\n"
                f"Date: {date}\n"
            )

        return "\n\n".join(lines)

    # @tool(name="web_search", description="根据关键词搜索网页，返回搜索结果摘要。", timeout=60.0)
    @tool(name="web_search",
          description="A web search tool that returns a list of web pages "
                      "with summaries based on input queries. "
                      "Queries should be concise and clear. "
                      "Break down complex questions into multiple steps. "
                      "If no useful results are found, adjust the query by reducing qualifiers or changing the search approach. "
                      "For better results, use Chinese queries for Chinese resources and English for non-Chinese resources.",
          timeout=60.0)
    async def web_search(self,
                         queries: List[str],
                         k: int = 10
                         ):
        # k = 10
        if isinstance(queries, str):
            queries = [queries]
        if not isinstance(queries, list):
            raise TypeError("queries must be a string or a list of strings, but got {}".format(type(queries)))

        search_results = await asyncio.gather(*[self._web_search_single_task(query, k) for query in queries],
                                              return_exceptions=False)
        search_results = [top_docs for top_docs in search_results if top_docs is not None]

        # 将多个query的结果交替拼接：A[0]、B[0]、A[1]、B[1]...
        max_len = max(len(r) for r in search_results) if search_results else 0
        search_results_all = []
        for i in range(max_len):
            for search_result in search_results:
                if i < len(search_result):
                    search_results_all.append(search_result[i])

        # 按 title-url-snippet 去重，只保留第一个出现的
        seen = set()
        deduped = []
        for item in search_results_all:
            key = (item.get("title", ""), item.get("url", ""), item.get("snippet", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        search_results_all = deduped
        if not search_results_all:
            return f"The web search tool does not find any related information for queries:{queries}.please try to adjust the queries"
        return search_results_all
        # return self.format_search_results(search_results_all)

    async def _web_search_single_task(self, query: str, k: int = 10):
        banned_sites = ["instagram.com", "instagr.am", "x.com", "tiktok.com", "twitter.com", "facebook.com", "xiaohongshu.com", "linkedin.com"]
        exclude_str = " ".join([f"-site:{site}" for site in banned_sites])
        query_without_ads = f"{query.strip()} {exclude_str}"
        payload = {
            "query": query_without_ads,
            "num": k,
            "use_cache": self.use_cache
        }
        url = self.web_search_url
        try:
            async with _shared_search_sem:
                response = await self._post_with_retry(url=url, payload=payload)
                data = response.json()

            results: List[Dict[str, Any]] = []
            for item in data.get("results", []) or []:
                url = item.get("url") or item.get("link")
                if not url:
                    continue
                entry = {
                    "title": item.get("title", ""),
                    "url": url,
                    "snippet": item.get("snippet", ""),
                    "query": query
                }
                if item.get("date"):
                    entry["date"] = item["date"]
                results.append(entry)
            return results[:k] if k and k > 0 else results
        except Exception as E:
            self.logger.error(f"web_search error: {E}")
            return [f"Failed to Search for query:{query} due to an error:{E}"]

    # 并行调用
    async def web_fetch(self, urls: List[str]):
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, list):
            raise TypeError("queries must be a string or a list of strings, but got {}".format(type(urls)))

        fetch_results = await asyncio.gather(*[self._web_fetch_single_task(url) for url in urls],
                                             return_exceptions=False)
        fetch_results = [result for result in fetch_results if result is not None]
        return fetch_results

    @tool(name="web_fetch", description="打开网页 URL，返回网页内容。", timeout=60.0)
    async def _web_fetch_single_task(self, url: str):
        payload = {
            "url": url,
            "use_cache": self.use_cache
        }
        try:
            async with _shared_fetch_sem:
                response = await self._post_with_retry(url=self.web_fetch_url, payload=payload)
                data = response.json()
                content = data.get("content", '')
                return {"url": url,
                        "content": content}
        except Exception as E:
            self.logger.error(f"web_fetch error: {E}")
            return {"url": url,
                    "content": f"Failed to fetch the webpage due to an error:{E}"}
    async def web_fetch_and_summary(self, urls: List[str], goal: str):
        if isinstance(urls, str):
            urls = [urls]

        summary_results = await asyncio.gather(*[self._web_fetch_and_summary_single_task(url, goal) for url in urls],
                                               return_exceptions=False)
        summary_results = [result for result in summary_results if result is not None]
        return summary_results

    @tool(name="web_fetch_and_summary",
          description="This is a web summary tool that can open links and summarize all relevant information on the page according to the goal. It is recommended to invoke this tool for valuable links to obtain information. Valuable links include but are not limited to the following types. 1. URLs explicitly provided in the task; 2. URLs with relevant abstracts returned by search results (source URLs of images returned by image searches are also included in this scope); 3. URLs contained in the content returned by previous calls of web_summary and judged to potentially contain useful information. Please try to avoid constructing links out of thin air.",
          timeout=60.0)
    async def _web_fetch_and_summary_single_task(self, url: str, goal: str):
        if not isinstance(url, str):
            self.logger.error(f"Failed to fetch and summarize the webpage due to an error: url must be a string, but got {type(url)}")
            return {
                "url": url,
                "summary": f"Failed to fetch and summarize the webpage due to an error: url must be a string, but got {type(url)}",
            }

        if not isinstance(goal, str):
            self.logger.error(f"Failed to fetch and summarize the webpage due to an error: goal must be a string, but got {type(goal)}")
            return {
                "url": url,
                "summary": f"Failed to fetch and summarize the webpage due to an error: goal must be a string, but got {type(goal)}",
            }

        payload = {
            "url": url,
            "use_cache": self.use_cache,
            "goal": goal
        }
        try:
            async with _shared_fetch_summary_sem:
                response = await self._post_with_retry(url=self.web_fetch_and_summary_url, payload=payload)
                data = response.json()
                summary = data.get("summary", '')
                return {
                    "url": url,
                    "summary": summary
                }
        except Exception as E:
            self.logger.error(f"web_fetch_and_summary failed | url:{url} | goal:{goal} | error:{E}")
            return {
                "url": url,
                "summary": f"Failed to fetch and summarize the webpage due to an error:{E}",
            }