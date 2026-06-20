# 纯美苹果园论坛查询插件

从纯美苹果园论坛按配置板块搜索最近主题、获取帖子首楼 HTML 截图，并把结果发送到当前聊天流。

## 功能

- `/果园搜索 <板块代称> <关键词>`：搜索指定配置板块最近主题标题。多条命中时只发送帖子标题和 topic 编号；唯一命中时只发送首楼截图。
- `/果园搜索 <topic编号>`：直接获取指定 topic 的帖子首楼截图。
- `/果园添加 <板块URL或board_id> <代称1|代称2>`：新增用户侧搜索板块；如果板块已存在，则只追加代称。
- `/果园删除 <板块URL或board_id|代称> [代称1|代称2]`：删除用户侧板块或指定代称。
- `/果园帮助`：发送插件命令帮助和已配置板块代称。
- `/果园板块 <板块URL或board_id> [数量]`：列出指定板块主题。
- `goddessfantasy_search` Tool：供 MaiBot 在对话中主动调用。

## 配置

配置文件为 `config.toml`：

- `plugin.enabled`：是否启用插件。
- `site.base_url`：纯美苹果园论坛基础地址。
- `site.user_agent`：HTTP 请求使用的 User-Agent。
- `site.cookie`：访问需要登录的板块时使用的 Cookie 请求头；留空时只能访问公开页面。
- `site.timeout_seconds`：请求超时时间。
- `query.max_results`：板块列表等旧接口最多返回条数。
- `query.pages_per_board`：列出板块主题时最多扫描页数。
- `query.search_recent_topics`：`/果园搜索` 扫描的最近主题数量。
- `query.render_first_match`：保留配置项；当前搜索仅在唯一命中时发送首楼截图。
- `query.skip_sticky_topics`：搜索和列出板块主题时是否跳过置顶帖；直接 `/果园搜索 <topic编号>` 不受影响。
- `query.boards`：可通过代称搜索的板块列表，每个板块包含 `name`、`url` 和 `aliases`。
- `render.font_path`：保留配置项；当前使用论坛原始 HTML 截图，不再使用文字卡片渲染。
- `render.width`：首楼 HTML 截图的浏览器视口宽度。
- `render.max_chars`：保留配置项；当前使用论坛原始 HTML 截图，不截断正文。
- `render.timeout_seconds`：首楼 HTML 截图超时时间。

板块配置示例：

```toml
[[query.boards]]
name = "Unearthed Arcana"
url = "https://www.goddessfantasy.net/bbs/index.php?board=1888.0"
aliases = ["UA", "Unearthed Arcana"]
```

用户通过 `/果园添加` 新增的板块会保存到插件目录的 `user_boards.json`，不会改写 `config.toml`。配置文件内置板块不能通过聊天命令删除，但可以通过 `/果园添加` 追加用户侧代称。

纯美苹果园的部分板块禁止访客访问。若访问 `https://www.goddessfantasy.net/bbs/index.php?board=1888.0` 这类页面时返回登录提示，需要先在浏览器登录果园，再把该站点请求中的 Cookie 值填入 `site.cookie`。

## 启用方式

1. 确认目录位于 `plugins/goddessfantasy-bbs/`。
2. 启动或重启 MaiBot。
3. 在 WebUI 插件管理中确认 `纯美苹果园论坛查询` 已加载并启用。

## 测试方式

在群聊中发送：

```text
/果园搜索 UA 邪恶子职
/果园搜索 167693
/果园添加 2318 鸦阁书|鸦阁|鸦阁魔域：魔障深藏
/果园添加 https://www.goddessfantasy.net/bbs/index.php?board=2318.0 鸦阁魔域：魔障深藏
/果园删除 鸦阁
/果园删除 2318 鸦阁书|鸦阁
/果园帮助
/果园板块 1888 5
```

如果 `/果园搜索 <板块代称> <关键词>` 返回多个帖子，可以再用 `/果园搜索 <topic编号>` 直接获取指定帖子的首楼截图。

如果站点要求登录、出现验证码、页面结构变化或图片过大，插件会返回明确的中文错误信息。
