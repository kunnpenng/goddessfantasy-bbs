"""纯美苹果园论坛查询插件。"""

from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from re import IGNORECASE, Pattern, compile, search, sub
from typing import Any, ClassVar, Dict, List, Optional
from urllib.parse import parse_qs, quote, urljoin, urlparse

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

import httpx
import json


PLUGIN_VERSION = "1.2.0"


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default=PLUGIN_VERSION, description="配置版本")


class SiteConfig(PluginConfigBase):
    """纯美苹果园站点访问配置。"""

    __ui_label__ = "站点"
    __ui_icon__ = "globe"
    __ui_order__ = 1

    base_url: str = Field(default="https://www.goddessfantasy.net/bbs", description="论坛基础地址")
    user_agent: str = Field(
        default="MaiBot GoddessFantasy Plugin/1.0",
        description="访问论坛时使用的 User-Agent",
    )
    cookie: str = Field(
        default="",
        description="访问需要登录的板块时使用的 Cookie 请求头；留空时只访问公开页面",
    )
    timeout_seconds: float = Field(default=12.0, description="HTTP 请求超时时间，单位秒", ge=1.0, le=60.0)


class BoardAliasConfig(PluginConfigBase):
    """可按代称查询的板块配置。"""

    name: str = Field(default="Unearthed Arcana", description="板块显示名称")
    url: str = Field(
        default="https://www.goddessfantasy.net/bbs/index.php?board=1888.0",
        description="板块 URL 或 board_id",
    )
    aliases: List[str] = Field(default_factory=lambda: ["UA", "Unearthed Arcana"], description="板块代称列表")


class QueryConfig(PluginConfigBase):
    """查询和消息发送限制。"""

    __ui_label__ = "查询"
    __ui_icon__ = "search"
    __ui_order__ = 2

    max_results: int = Field(default=5, description="搜索结果最多返回条数", ge=1, le=20)
    pages_per_board: int = Field(default=3, description="每个板块最多扫描页数", ge=1, le=10)
    search_recent_topics: int = Field(default=10, description="按板块代称搜索时扫描最近主题数", ge=1, le=100)
    render_first_match: bool = Field(default=True, description="搜索命中后是否发送第一条命中帖首楼图片")
    skip_sticky_topics: bool = Field(default=True, description="搜索和列出板块主题时是否跳过置顶帖")
    boards: List[BoardAliasConfig] = Field(default_factory=lambda: [BoardAliasConfig()], description="可按代称搜索的板块列表")


class RenderConfig(PluginConfigBase):
    """首楼图片渲染配置。"""

    __ui_label__ = "渲染"
    __ui_icon__ = "image"
    __ui_order__ = 3

    font_path: str = Field(default="", description="首楼图片字体文件路径；留空时使用系统中文字体")
    width: int = Field(default=1200, description="首楼图片宽度", ge=480, le=2400)
    max_chars: int = Field(default=2200, description="首楼图片最多渲染正文字符数", ge=200, le=8000)
    timeout_seconds: float = Field(default=30.0, description="HTML 渲染超时时间，单位秒", ge=3.0, le=120.0)


class GoddessFantasyConfig(PluginConfigBase):
    """纯美苹果园插件配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    site: SiteConfig = Field(default_factory=SiteConfig)
    query: QueryConfig = Field(default_factory=QueryConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)


@dataclass
class ForumSearchResult:
    """论坛搜索结果。"""

    title: str
    url: str
    tid: str
    matched_text: str = ""
    is_sticky: bool = False


class ForumHTMLParser(HTMLParser):
    """提取 Discuz 页面中的链接、正文和图片。"""

    _thread_link_pattern: ClassVar[Pattern[str]] = compile(r"(?:tid=|thread-|topic=)(\d+)", IGNORECASE)

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: List[Dict[str, Any]] = []
        self.images: List[str] = []
        self.title_parts: List[str] = []
        self.post_text_parts: List[str] = []
        self.post_texts: List[str] = []
        self.visible_text_parts: List[str] = []
        self._current_anchor: Optional[Dict[str, Any]] = None
        self._current_post_parts: List[str] = []
        self._ignore_depth = 0
        self._message_depth = 0
        self._title_depth = 0
        self._topic_row_depth = 0
        self._topic_row_sticky = False

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        normalized_tag = tag.lower()

        if normalized_tag in {"script", "style", "noscript"}:
            self._ignore_depth += 1
            return

        class_name = attr_map.get("class", "")
        classes = class_name.split()
        if normalized_tag == "div" and self._topic_row_depth == 0 and "windowbg" in classes:
            self._topic_row_depth = 1
            self._topic_row_sticky = "sticky" in classes
        elif normalized_tag == "div" and self._topic_row_depth > 0:
            self._topic_row_depth += 1

        if self._message_depth == 0 and self._is_post_message_node(attr_map):
            self._message_depth = 1
            self._current_post_parts = []
        elif self._message_depth > 0:
            self._message_depth += 1

        if normalized_tag == "title":
            self._title_depth += 1

        if normalized_tag == "a":
            self._current_anchor = {
                "href": attr_map.get("href", ""),
                "class": class_name,
                "id": attr_map.get("id", ""),
                "is_sticky": self._topic_row_sticky,
                "text_parts": [],
            }
            return

        if normalized_tag == "img":
            image_url = self._extract_image_url(attr_map)
            if image_url:
                self.images.append(image_url)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style", "noscript"} and self._ignore_depth > 0:
            self._ignore_depth -= 1
            return

        if normalized_tag == "title" and self._title_depth > 0:
            self._title_depth -= 1

        if normalized_tag == "a" and self._current_anchor is not None:
            text = self._normalize_text("".join(self._current_anchor["text_parts"]))
            href = str(self._current_anchor.get("href", "")).strip()
            if href and text:
                self.anchors.append(
                    {
                        "href": href,
                        "class": self._current_anchor.get("class", ""),
                        "id": self._current_anchor.get("id", ""),
                        "is_sticky": self._current_anchor.get("is_sticky", False),
                        "text": text,
                    }
                )
            self._current_anchor = None

        if self._message_depth == 1:
            text = self._normalize_text(" ".join(self._current_post_parts))
            if text:
                self.post_texts.append(text)
        if self._message_depth > 0:
            self._message_depth -= 1

        if normalized_tag == "div" and self._topic_row_depth > 0:
            self._topic_row_depth -= 1
            if self._topic_row_depth == 0:
                self._topic_row_sticky = False

    def handle_data(self, data: str) -> None:
        if self._ignore_depth > 0:
            return

        text = self._normalize_text(data)
        if not text:
            return

        if self._current_anchor is not None:
            self._current_anchor["text_parts"].append(text)

        if self._title_depth > 0:
            self.title_parts.append(text)
        elif self._message_depth > 0:
            self.post_text_parts.append(text)
            self._current_post_parts.append(text)
        else:
            self.visible_text_parts.append(text)

    @classmethod
    def is_thread_link(cls, href: str) -> bool:
        return cls._thread_link_pattern.search(href) is not None

    @staticmethod
    def _normalize_text(text: str) -> str:
        return sub(r"\s+", " ", text).strip()

    @staticmethod
    def _extract_image_url(attrs: Dict[str, str]) -> str:
        for key in ("file", "zoomfile", "data-original", "data-src", "src"):
            value = attrs.get(key, "").strip()
            if value and not value.startswith("data:"):
                return value
        return ""

    @staticmethod
    def _is_post_message_node(attrs: Dict[str, str]) -> bool:
        node_id = attrs.get("id", "")
        class_name = attrs.get("class", "")
        classes = class_name.split()
        return (
            node_id.startswith("postmessage_")
            or node_id.startswith("msg_")
            or "t_f" in classes
            or "inner" in classes
            or "post" in classes
        )


class GoddessFantasyError(RuntimeError):
    """纯美苹果园查询失败。"""


class GoddessFantasyPlugin(MaiBotPlugin):
    """从纯美苹果园论坛获取信息并发送到聊天流。"""

    config_model = GoddessFantasyConfig

    async def on_load(self) -> None:
        """插件加载完成。"""

        self.ctx.logger.info("纯美苹果园论坛查询插件已加载")

    async def on_unload(self) -> None:
        """插件卸载前清理。"""

        self.ctx.logger.info("纯美苹果园论坛查询插件已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        """处理配置热更新。"""

        del config_data
        self.ctx.logger.info("纯美苹果园论坛查询插件配置已更新：scope=%s version=%s", scope, version)

    @Command(
        "goddessfantasy_search_command",
        description="按配置板块代称搜索纯美苹果园论坛最近主题",
        pattern=r"^/果园搜索\s+(?P<board_alias>\S+)\s+(?P<query>.+?)$",
    )
    async def handle_search_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """处理 /果园搜索 命令。"""

        board_alias = self._get_matched_value(kwargs, "board_alias").strip()
        query = self._get_matched_value(kwargs, "query").strip()
        if not board_alias or not query:
            return await self._send_usage(stream_id, "用法：/果园搜索 <板块代称> <关键词>")

        return await self._send_search_results(stream_id, board_alias, query)

    @Command(
        "goddessfantasy_search_thread_command",
        description="按 topic 编号获取纯美苹果园论坛帖子首楼图片",
        pattern=r"^/果园搜索\s+(?P<thread>\d+)$",
    )
    async def handle_search_thread_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """处理 /果园搜索 <topic编号> 命令。"""

        thread = self._get_matched_value(kwargs, "thread").strip()
        if not thread:
            return await self._send_usage(stream_id, "用法：/果园搜索 <topic编号>")

        return await self._send_thread_render_image(stream_id, thread)

    @Command(
        "goddessfantasy_help_command",
        description="发送纯美苹果园论坛查询插件帮助",
        pattern=r"^/果园(?:帮助|help)$",
    )
    async def handle_help_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """处理 /果园帮助 命令。"""

        del kwargs
        return await self._send_usage(stream_id, self._build_help_text())

    @Command(
        "goddessfantasy_add_board_command",
        description="新增或更新纯美苹果园论坛搜索板块代称",
        pattern=r"^/果园添加\s+(?P<board>\S+)\s+(?P<aliases>.+?)$",
    )
    async def handle_add_board_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """处理 /果园添加 命令。"""

        board = self._get_matched_value(kwargs, "board").strip()
        aliases = self._get_matched_value(kwargs, "aliases").strip()
        if not board or not aliases:
            return await self._send_usage(stream_id, "用法：/果园添加 <板块URL或board_id> <代称1|代称2>")

        try:
            message = self._add_user_board_aliases(board, aliases)
            await self.ctx.send.text(message, stream_id)
            return True, message, True
        except Exception as exc:
            message = f"添加果园板块失败：{exc}"
            await self.ctx.send.text(message, stream_id)
            return False, message, True

    @Command(
        "goddessfantasy_delete_board_command",
        description="删除用户侧纯美苹果园论坛搜索板块或代称",
        pattern=r"^/果园删除\s+(?P<target>\S+)(?:\s+(?P<aliases>.+?))?$",
    )
    async def handle_delete_board_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """处理 /果园删除 命令。"""

        target = self._get_matched_value(kwargs, "target").strip()
        aliases = self._get_matched_value(kwargs, "aliases").strip()
        if not target:
            return await self._send_usage(stream_id, "用法：/果园删除 <板块URL或board_id|代称> [代称1|代称2]")

        try:
            message = self._delete_user_board(target, aliases)
            await self.ctx.send.text(message, stream_id)
            return True, message, True
        except Exception as exc:
            message = f"删除果园板块失败：{exc}"
            await self.ctx.send.text(message, stream_id)
            return False, message, True

    @Command(
        "goddessfantasy_board_command",
        description="列出纯美苹果园论坛指定板块主题",
        pattern=r"^/果园板块\s+(?P<board>\S+)(?:\s+(?P<limit>\d+))?$",
    )
    async def handle_board_command(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, bool]:
        """处理 /果园板块 命令。"""

        board = self._get_matched_value(kwargs, "board").strip()
        limit = self._parse_optional_int(self._get_matched_value(kwargs, "limit"), self.config.query.max_results)
        if not board:
            return await self._send_usage(stream_id, "用法：/果园板块 <板块URL或board_id> [数量]")

        return await self._send_board_topics(stream_id, board, limit)

    @Tool(
        "goddessfantasy_search",
        brief_description="搜索纯美苹果园论坛主题并发送结果",
        parameters=[
            ToolParameterInfo(name="query", param_type=ToolParamType.STRING, description="搜索关键词", required=True),
            ToolParameterInfo(name="stream_id", param_type=ToolParamType.STRING, description="当前聊天流 ID", required=True),
            ToolParameterInfo(name="board_alias", param_type=ToolParamType.STRING, description="板块代称", required=True),
            ToolParameterInfo(
                name="include_images",
                param_type=ToolParamType.BOOLEAN,
                description="是否发送第一条命中帖首楼图片",
                required=False,
                default=True,
            ),
        ],
    )
    async def handle_search_tool(
        self,
        query: str,
        stream_id: str,
        board_alias: str,
        include_images: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """供 LLM 调用的论坛搜索工具。"""

        del kwargs
        success, message, _ = await self._send_search_results(stream_id, board_alias, query, render_first_match=include_images)
        return {"success": success, "message": message}


    async def _send_usage(self, stream_id: str, text: str) -> tuple[bool, str, bool]:
        if stream_id:
            await self.ctx.send.text(text, stream_id)
        return False, text, True

    async def _send_search_results(
        self,
        stream_id: str,
        board_alias: str,
        query: str,
        *,
        render_first_match: bool | None = None,
    ) -> tuple[bool, str, bool]:
        if not self.config.plugin.enabled:
            return await self._send_usage(stream_id, "纯美苹果园论坛查询插件未启用")

        try:
            board = self._resolve_board_alias(board_alias)
            results = await self._search_forum(board, query)
            if not results:
                message = f"没有在“{board.name}”最近 {self.config.query.search_recent_topics} 个主题中找到与“{query}”相关的帖子。"
                await self.ctx.send.text(message, stream_id)
            elif len(results) == 1:
                await self._send_thread_render_image(stream_id, results[0].url)
                message = "搜索到唯一帖子，已发送首楼图片"
            else:
                lines = [f"纯美苹果园搜索：{board.name} / {query}"]
                for index, item in enumerate(results, start=1):
                    tid_text = f" tid={item.tid}" if item.tid else ""
                    lines.append(f"{index}. {item.title}{tid_text}")
                message = "\n".join(lines)
                await self.ctx.send.text(message, stream_id)
            del render_first_match
            return True, "搜索完成", True
        except Exception as exc:
            message = f"搜索纯美苹果园失败：{exc}"
            await self.ctx.send.text(message, stream_id)
            return False, message, True

    async def _send_board_topics(self, stream_id: str, board: str, limit: int) -> tuple[bool, str, bool]:
        if not self.config.plugin.enabled:
            return await self._send_usage(stream_id, "纯美苹果园论坛查询插件未启用")

        try:
            results = await self._fetch_board_topics(board, limit)
            if not results:
                message = "该板块没有解析到主题。"
            else:
                lines = [f"纯美苹果园板块主题：{board}"]
                for index, item in enumerate(results, start=1):
                    tid_text = f" topic={item.tid}" if item.tid else ""
                    lines.append(f"{index}. {item.title}{tid_text}\n{item.url}")
                message = "\n".join(lines)
            await self.ctx.send.text(message, stream_id)
            return True, "板块主题获取完成", True
        except Exception as exc:
            message = f"获取纯美苹果园板块失败：{exc}"
            await self.ctx.send.text(message, stream_id)
            return False, message, True

    async def _search_forum(self, board: BoardAliasConfig, query: str) -> List[ForumSearchResult]:
        normalized_query = query.strip()
        if not normalized_query:
            raise GoddessFantasyError("搜索关键词不能为空")

        results: List[ForumSearchResult] = []
        seen_keys: set[str] = set()
        board_results = await self._fetch_board_topics(board.url, self.config.query.search_recent_topics)
        for item in board_results:
            if normalized_query.casefold() not in item.title.casefold():
                continue
            dedupe_key = item.tid or item.url
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            results.append(ForumSearchResult(title=item.title, url=item.url, tid=item.tid, is_sticky=item.is_sticky))
        return results

    async def _fetch_board_topics(self, board: str, limit: int) -> List[ForumSearchResult]:
        max_results = self._clamp(limit, 1, 100)
        results: List[ForumSearchResult] = []
        seen_keys: set[str] = set()

        async with self._build_client() as client:
            for page_index in range(self.config.query.pages_per_board):
                board_url = self._normalize_board_url(board, page_index)
                html = await self._fetch_text(board_url, client=client)
                self._ensure_not_login_page(html, board_url)
                page_results = self._parse_topic_links(html, board_url)
                for item in page_results:
                    dedupe_key = item.tid or item.url
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)
                    results.append(item)
                    if len(results) >= max_results:
                        return results
        return results

    def _parse_topic_links(self, html: str, page_url: str) -> List[ForumSearchResult]:
        parser = ForumHTMLParser()
        parser.feed(html)

        results: List[ForumSearchResult] = []
        seen_keys: set[str] = set()
        for anchor in parser.anchors:
            href = str(anchor.get("href", "")).strip()
            class_name = str(anchor.get("class", "")).strip()
            title = str(anchor.get("text", "")).strip()
            is_sticky = bool(anchor.get("is_sticky", False))
            if not href or not title or not self._is_board_topic_anchor(href, title, class_name):
                continue
            if self.config.query.skip_sticky_topics and is_sticky:
                continue

            absolute_url = self._absolute_url(href)
            tid = self._extract_tid(absolute_url)
            dedupe_key = tid or absolute_url
            if dedupe_key in seen_keys:
                continue

            seen_keys.add(dedupe_key)
            results.append(ForumSearchResult(title=title, url=absolute_url, tid=tid, is_sticky=is_sticky))
        return results

    async def _send_thread_render_image(self, stream_id: str, thread: str) -> tuple[bool, str, bool]:
        image_path = await self._render_thread_to_image_file(thread)
        try:
            image_base64 = b64encode(image_path.read_bytes()).decode("ascii")
            sent = await self.ctx.send.image(image_base64, stream_id)
        finally:
            if image_path.exists():
                image_path.unlink()
        if not sent:
            raise GoddessFantasyError("首楼图片发送失败")
        return True, "首楼图片发送完成", True

    async def _fetch_text(self, url: str, *, client: httpx.AsyncClient | None = None) -> str:
        if client is not None:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

        async with self._build_client() as local_client:
            response = await local_client.get(url)
            response.raise_for_status()
            return response.text

    async def _render_thread_to_image_file(self, thread: str) -> Path:
        url = self._normalize_thread_url(thread)
        image_path = self._build_render_image_path(url)
        timeout_ms = int(self.config.render.timeout_seconds * 1000)

        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise GoddessFantasyError("当前环境未安装 Playwright，无法渲染论坛 HTML") from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(**self._build_browser_launch_options(timeout_ms))
            try:
                context = await browser.new_context(
                    viewport={"width": self.config.render.width, "height": 1200},
                    device_scale_factor=1,
                    extra_http_headers=self._build_browser_headers(),
                )
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                self._ensure_not_login_page(await page.content(), url)
                await self._prepare_first_post_screenshot(page)
                locator = page.locator("#forumposts .windowbg").first
                try:
                    await locator.wait_for(state="visible", timeout=timeout_ms)
                except PlaywrightTimeoutError as exc:
                    raise GoddessFantasyError("页面中没有找到可渲染的首楼节点") from exc
                await locator.screenshot(path=str(image_path), timeout=timeout_ms)
                await context.close()
            finally:
                await browser.close()

        return image_path

    async def _prepare_first_post_screenshot(self, page: Any) -> None:
        await page.add_style_tag(
            content="""
                body {
                    background: #f4f1e8 !important;
                }
                #forumposts .windowbg {
                    max-width: 100% !important;
                    margin: 0 !important;
                    box-shadow: none !important;
                }
                #forumposts .windowbg:not(:first-of-type) {
                    display: none !important;
                }
                #forumposts .moderatorbar,
                #forumposts .quickbuttons,
                #forumposts .keyinfo .smalltext,
                #forumposts .signature,
                #forumposts .under_message,
                #forumposts .post_options {
                    display: none !important;
                }
            """,
        )

    def _build_render_image_path(self, url: str) -> Path:
        render_dir = Path(__file__).resolve().parent / ".render_cache"
        render_dir.mkdir(parents=True, exist_ok=True)
        tid = self._extract_tid(url) or "thread"
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return render_dir / f"goddessfantasy_{escape(tid, quote=False)}_{timestamp}.png"

    def _build_browser_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": self.config.site.user_agent,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        cookie = self.config.site.cookie.strip()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _build_help_text(self) -> str:
        board_lines = []
        for board in self._get_all_boards():
            aliases = " / ".join(alias for alias in board.aliases if alias.strip())
            board_lines.append(f"- {board.name}: {aliases or '未配置代称'}")
        configured_boards = "\n".join(board_lines) if board_lines else "- 未配置"
        sticky_text = "跳过" if self.config.query.skip_sticky_topics else "包含"
        return (
            "纯美苹果园论坛查询插件帮助\n"
            "\n"
            "命令：\n"
            "/果园搜索 <板块代称> <标题关键词>：搜索指定板块最近主题，只匹配标题。\n"
            "/果园搜索 <topic编号>：直接发送指定帖子的首楼截图。\n"
            "/果园添加 <板块URL或board_id> <代称1|代称2>：新增板块或给已有板块追加代称。\n"
            "/果园删除 <板块URL或board_id|代称> [代称1|代称2]：删除用户侧板块或代称。\n"
            "/果园板块 <板块URL或board_id> [数量]：列出指定板块主题。\n"
            "/果园帮助：发送本帮助。\n"
            "\n"
            f"当前搜索扫描最近 {self.config.query.search_recent_topics} 个非置顶主题，置顶帖处理：{sticky_text}。\n"
            "已配置板块代称：\n"
            f"{configured_boards}"
        )

    def _add_user_board_aliases(self, board: str, aliases_text: str) -> str:
        aliases = self._parse_aliases(aliases_text)
        identity = self._try_board_identity(board)
        if not identity:
            raise GoddessFantasyError("板块必须是 board_id、完整板块 URL 或 board=... 参数")
        normalized_url = self._normalize_board_url(board, 0)
        user_boards = self._load_user_boards()
        existing_config_board = self._find_config_board_by_identity(identity)
        existing_user_board = self._find_user_board_by_identity(user_boards, identity)

        if existing_user_board is None:
            board_name = existing_config_board.name if existing_config_board is not None else aliases[0]
            existing_user_board = {"name": board_name, "url": normalized_url, "aliases": []}
            user_boards.append(existing_user_board)

        old_aliases = self._merge_aliases([], [str(alias) for alias in existing_user_board.get("aliases", [])])
        merged_aliases = self._merge_aliases(old_aliases, aliases)
        existing_user_board["url"] = normalized_url
        existing_user_board["aliases"] = merged_aliases
        if not str(existing_user_board.get("name", "")).strip():
            existing_user_board["name"] = aliases[0]
        self._save_user_boards(user_boards)
        added_aliases = [alias for alias in aliases if alias not in old_aliases]
        added_text = "、".join(added_aliases) if added_aliases else "无新增代称"
        return f"已更新果园板块：{existing_user_board['name']} ({normalized_url})，新增代称：{added_text}"

    def _delete_user_board(self, target: str, aliases_text: str) -> str:
        user_boards = self._load_user_boards()
        if aliases_text:
            aliases = self._parse_aliases(aliases_text)
            identity = self._resolve_board_identity(target)
            user_board = self._find_user_board_by_identity(user_boards, identity)
            if user_board is None:
                raise GoddessFantasyError("该板块没有用户侧可删除的代称")
            old_aliases = self._merge_aliases([], [str(alias) for alias in user_board.get("aliases", [])])
            removed_aliases = [alias for alias in aliases if alias in old_aliases]
            user_board["aliases"] = [alias for alias in old_aliases if alias not in aliases]
            if not user_board["aliases"]:
                user_boards.remove(user_board)
            self._save_user_boards(user_boards)
            removed_text = "、".join(removed_aliases) if removed_aliases else "无匹配代称"
            return f"已更新果园板块代称，删除：{removed_text}"

        identity = self._try_board_identity(target)
        if identity:
            user_board = self._find_user_board_by_identity(user_boards, identity)
            if user_board is None:
                raise GoddessFantasyError("没有找到可删除的用户侧板块；配置文件内置板块不能通过聊天命令删除")
            user_boards.remove(user_board)
            self._save_user_boards(user_boards)
            return f"已删除用户侧果园板块：{user_board.get('name', '')}"

        normalized_target = target.casefold()
        removed_from: List[str] = []
        for user_board in list(user_boards):
            old_aliases = self._merge_aliases([], [str(alias) for alias in user_board.get("aliases", [])])
            kept_aliases = [alias for alias in old_aliases if alias.casefold() != normalized_target]
            if len(kept_aliases) == len(old_aliases):
                continue
            user_board["aliases"] = kept_aliases
            removed_from.append(str(user_board.get("name", "") or user_board.get("url", "")))
            if not kept_aliases:
                user_boards.remove(user_board)
        if not removed_from:
            raise GoddessFantasyError("没有找到可删除的用户侧代称")
        self._save_user_boards(user_boards)
        return f"已删除用户侧代称“{target}”：{', '.join(removed_from)}"

    def _get_all_boards(self) -> List[BoardAliasConfig]:
        merged_boards: Dict[str, BoardAliasConfig] = {}
        for board in self.config.query.boards:
            identity = self._board_identity(board.url)
            merged_boards[identity] = BoardAliasConfig(
                name=board.name,
                url=self._normalize_board_url(board.url, 0),
                aliases=self._merge_aliases([], board.aliases),
            )

        for user_board in self._load_user_boards():
            url = str(user_board.get("url", "")).strip()
            if not url:
                continue
            identity = self._board_identity(url)
            aliases = self._parse_aliases("|".join(str(alias) for alias in user_board.get("aliases", [])))
            if identity in merged_boards:
                existing_board = merged_boards[identity]
                existing_board.aliases = self._merge_aliases(existing_board.aliases, aliases)
                continue
            name = str(user_board.get("name", "")).strip() or (aliases[0] if aliases else url)
            merged_boards[identity] = BoardAliasConfig(name=name, url=url, aliases=aliases)
        return list(merged_boards.values())

    def _load_user_boards(self) -> List[Dict[str, Any]]:
        path = self._user_boards_path()
        if not path.exists():
            return []
        raw_data = json.loads(path.read_text(encoding="utf-8"))
        raw_boards = raw_data.get("boards", []) if isinstance(raw_data, dict) else []
        if not isinstance(raw_boards, list):
            raise GoddessFantasyError("用户侧板块数据格式错误：boards 必须是列表")
        boards: List[Dict[str, Any]] = []
        for raw_board in raw_boards:
            if not isinstance(raw_board, dict):
                continue
            url = str(raw_board.get("url", "")).strip()
            aliases = raw_board.get("aliases", [])
            if not url or not isinstance(aliases, list):
                continue
            boards.append(
                {
                    "name": str(raw_board.get("name", "")).strip(),
                    "url": url,
                    "aliases": self._merge_aliases([], [str(alias) for alias in aliases]),
                }
            )
        return boards

    def _save_user_boards(self, boards: List[Dict[str, Any]]) -> None:
        path = self._user_boards_path()
        path.write_text(
            json.dumps({"boards": boards}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _user_boards_path() -> Path:
        return Path(__file__).resolve().parent / "user_boards.json"

    def _resolve_board_identity(self, target: str) -> str:
        identity = self._try_board_identity(target)
        if identity:
            return identity
        board = self._resolve_board_alias(target)
        return self._board_identity(board.url)

    def _try_board_identity(self, target: str) -> str:
        normalized_target = target.strip()
        if not normalized_target:
            return ""
        if normalized_target.isdigit() or normalized_target.startswith(("http://", "https://", "board=")):
            return self._board_identity(self._normalize_board_url(normalized_target, 0))
        return ""

    def _board_identity(self, board: str) -> str:
        normalized_url = self._normalize_board_url(board, 0)
        parsed = urlparse(normalized_url)
        board_values = parse_qs(parsed.query).get("board", [])
        if board_values:
            board_id = board_values[0].split(".", maxsplit=1)[0]
            if board_id:
                return f"board:{board_id}"
        return normalized_url.casefold()

    def _find_config_board_by_identity(self, identity: str) -> BoardAliasConfig | None:
        for board in self.config.query.boards:
            if self._board_identity(board.url) == identity:
                return board
        return None

    def _find_user_board_by_identity(self, boards: List[Dict[str, Any]], identity: str) -> Dict[str, Any] | None:
        for board in boards:
            if self._board_identity(str(board.get("url", ""))) == identity:
                return board
        return None

    @staticmethod
    def _parse_aliases(aliases_text: str) -> List[str]:
        aliases = []
        seen_aliases: set[str] = set()
        for alias in aliases_text.split("|"):
            normalized_alias = alias.strip()
            if not normalized_alias:
                continue
            alias_key = normalized_alias.casefold()
            if alias_key in seen_aliases:
                continue
            seen_aliases.add(alias_key)
            aliases.append(normalized_alias)
        if not aliases:
            raise GoddessFantasyError("代称不能为空")
        return aliases

    @staticmethod
    def _merge_aliases(existing_aliases: List[str], new_aliases: List[str]) -> List[str]:
        merged_aliases: List[str] = []
        seen_aliases: set[str] = set()
        for alias in [*existing_aliases, *new_aliases]:
            normalized_alias = str(alias).strip()
            if not normalized_alias:
                continue
            alias_key = normalized_alias.casefold()
            if alias_key in seen_aliases:
                continue
            seen_aliases.add(alias_key)
            merged_aliases.append(normalized_alias)
        return merged_aliases

    def _build_browser_launch_options(self, timeout_ms: int) -> Dict[str, Any]:
        launch_options: Dict[str, Any] = {
            "headless": True,
            "timeout": timeout_ms,
            "args": ["--disable-dev-shm-usage"],
        }
        browser_path = self._detect_local_browser_executable()
        if browser_path:
            launch_options["executable_path"] = browser_path
        return launch_options

    @staticmethod
    def _detect_local_browser_executable() -> str:
        browser_paths = [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ]
        for browser_path in browser_paths:
            if browser_path.exists():
                return str(browser_path)
        return ""

    def _build_client(self) -> httpx.AsyncClient:
        headers = {
            "User-Agent": self.config.site.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        cookie = self.config.site.cookie.strip()
        if cookie:
            headers["Cookie"] = cookie
        return httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self.config.site.timeout_seconds),
            follow_redirects=True,
        )

    def _resolve_board_alias(self, board_alias: str) -> BoardAliasConfig:
        normalized_alias = board_alias.casefold()
        for board in self._get_all_boards():
            names = [board.name, *board.aliases]
            if any(normalized_alias == name.strip().casefold() for name in names if name.strip()):
                if not board.url.strip():
                    raise GoddessFantasyError(f"板块“{board.name}”没有配置 URL")
                return board
        configured_aliases = sorted(
            alias
            for board in self._get_all_boards()
            for alias in [board.name, *board.aliases]
            if alias.strip()
        )
        raise GoddessFantasyError(f"未找到板块代称“{board_alias}”，已配置代称：{', '.join(configured_aliases)}")

    def _normalize_thread_url(self, thread: str) -> str:
        normalized_thread = thread.strip()
        if not normalized_thread:
            raise GoddessFantasyError("帖子 URL 或 tid 不能为空")

        if normalized_thread.isdigit():
            return f"{self._base_url()}/index.php?topic={quote(normalized_thread)}.0"

        if normalized_thread.startswith(("http://", "https://")):
            return normalized_thread

        if normalized_thread.startswith("tid="):
            tid_values = parse_qs(normalized_thread).get("tid", [])
            if tid_values and tid_values[0].isdigit():
                return f"{self._base_url()}/forum.php?mod=viewthread&tid={quote(tid_values[0])}"

        if normalized_thread.startswith("topic="):
            topic_values = parse_qs(normalized_thread).get("topic", [])
            if topic_values:
                topic_id = topic_values[0].split(".", maxsplit=1)[0]
                if topic_id.isdigit():
                    return f"{self._base_url()}/index.php?topic={quote(topic_id)}.0"

        tid = self._extract_tid(normalized_thread)
        if tid:
            return f"{self._base_url()}/forum.php?mod=viewthread&tid={quote(tid)}"

        return self._absolute_url(normalized_thread)

    def _normalize_board_url(self, board: str, page_index: int = 0) -> str:
        normalized_board = board.strip()
        if not normalized_board:
            raise GoddessFantasyError("板块 URL 或 board_id 不能为空")

        start = page_index * 50
        if normalized_board.isdigit():
            return f"{self._base_url()}/index.php?board={quote(normalized_board)}.{start}"

        if normalized_board.startswith(("http://", "https://")):
            parsed = urlparse(normalized_board)
            query = parse_qs(parsed.query)
            board_values = query.get("board", [])
            if board_values:
                board_id = board_values[0].split(".", maxsplit=1)[0]
                if board_id.isdigit():
                    return f"{self._base_url()}/index.php?board={quote(board_id)}.{start}"
            return normalized_board

        if normalized_board.startswith("board="):
            board_values = parse_qs(normalized_board).get("board", [])
            if board_values:
                board_id = board_values[0].split(".", maxsplit=1)[0]
                if board_id.isdigit():
                    return f"{self._base_url()}/index.php?board={quote(board_id)}.{start}"

        return self._absolute_url(normalized_board)

    def _absolute_url(self, url: str) -> str:
        return urljoin(f"{self._base_url()}/", url.strip())

    def _base_url(self) -> str:
        return self.config.site.base_url.rstrip("/")

    @staticmethod
    def _is_board_topic_anchor(href: str, title: str, class_name: str) -> bool:
        classes = class_name.split()
        if "nav_page" in classes or "new_posts" in classes:
            return False
        if title in {"新", "1", "2", "3", "4", "5"}:
            return False
        if search(r"^(?:\d{4}-\d{2}-\d{2}.+|昨天.+|今天.+)$", title):
            return False
        if ".msg" in href or "#new" in href:
            return False
        if "index.php?topic=" not in href:
            return False
        return ForumHTMLParser.is_thread_link(href)

    @staticmethod
    def _extract_tid(url: str) -> str:
        parsed = urlparse(url)
        tid_values = parse_qs(parsed.query).get("tid", [])
        if tid_values:
            return tid_values[0]

        topic_values = parse_qs(parsed.query).get("topic", [])
        if topic_values:
            return topic_values[0].split(".", maxsplit=1)[0]

        match = search(r"thread-(\d+)", url, IGNORECASE)
        if match is not None:
            return match.group(1)
        return ""

    @staticmethod
    def _ensure_not_login_page(html: str, url: str) -> None:
        title_match = search(r"<title>\s*(.*?)\s*</title>", html, IGNORECASE)
        title = ForumHTMLParser._normalize_text(title_match.group(1)) if title_match is not None else ""
        if title in {"登录", "Login"}:
            raise GoddessFantasyError(
                f"页面需要登录或访客无权访问：{url}。请在 config.toml 的 site.cookie 中配置已登录浏览器 Cookie。"
            )

    @staticmethod
    def _get_matched_value(kwargs: Dict[str, Any], name: str) -> str:
        matched_groups = kwargs.get("matched_groups")
        if isinstance(matched_groups, dict):
            return str(matched_groups.get(name) or "")
        return ""

    @staticmethod
    def _parse_optional_int(raw_value: str, default: int) -> int:
        normalized_value = raw_value.strip()
        if not normalized_value:
            return default
        return int(normalized_value)

    @staticmethod
    def _clamp(value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(value, maximum))


def create_plugin() -> GoddessFantasyPlugin:
    """创建纯美苹果园论坛查询插件实例。"""

    return GoddessFantasyPlugin()
