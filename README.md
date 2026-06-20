# 纯美苹果园论坛查询

MaiBot 第三方插件，用来在聊天中查询纯美苹果园论坛主题，并把帖子首楼渲染为图片发送到当前群聊或私聊。

## 功能

- 按板块代称搜索最近主题标题。
- 用 topic 编号直接发送帖子首楼截图。
- 在聊天中添加或删除自定义板块代称。
- 列出指定板块的最近主题。
- 按群聊或私聊单独关闭果园功能，避免在不需要的聊天里打扰其他机器人。
- 提供 `goddessfantasy_search` Tool，MaiBot 可在对话中主动调用。

## 安装

1. 将本仓库放到 MaiBot 的插件目录：

```text
plugins/goddessfantasy-bbs/
```

2. 确认目录内至少包含：

```text
plugin.py
_manifest.json
config.toml
README.md
```

3. 重启 MaiBot。
4. 在 MaiBot WebUI 的插件管理中确认 `纯美苹果园论坛查询` 已加载并启用。

## 配置

主要配置在 `config.toml`：

```toml
[plugin]
enabled = true

[site]
base_url = "https://www.goddessfantasy.net/bbs"
user_agent = "Mozilla/5.0 ..."
cookie = ""
timeout_seconds = 12.0
```

常用项：

- `plugin.enabled`：是否启用插件。
- `site.cookie`：访问需要登录的板块时使用的 Cookie。留空时只能访问公开页面。
- `query.search_recent_topics`：`/果园搜索` 扫描的最近主题数量。
- `query.skip_sticky_topics`：搜索和列板块主题时是否跳过置顶帖。
- `query.boards`：内置板块代称配置。
- `render.width`：首楼 HTML 截图宽度。
- `render.timeout_seconds`：渲染超时时间。

板块配置示例：

```toml
[[query.boards]]
name = "Unearthed Arcana"
url = "https://www.goddessfantasy.net/bbs/index.php?board=1888.0"
aliases = ["UA", "Unearthed Arcana"]
```

## 配置登录 Cookie

纯美苹果园的部分板块需要登录才能访问。如果查询时提示需要登录：

1. 在浏览器登录纯美苹果园。
2. 打开浏览器开发者工具，复制访问 `www.goddessfantasy.net` 时的请求 Cookie。
3. 将 Cookie 填入 `config.toml` 的 `site.cookie`。
4. 重启 MaiBot 或重新加载插件。

不要把包含 Cookie 的 `config.toml` 提交到公开仓库。建议只在本地保存 Cookie。

## 命令

```text
/果园搜索 <板块代称> <关键词>
/果园搜索 <topic编号>
/果园板块 <板块URL或board_id> [数量]
/果园添加 <板块URL或board_id> <代称1|代称2>
/果园删除 <板块URL或board_id|代称> [代称1|代称2]
/果园帮助
```

示例：

```text
/果园搜索 UA 邪恶子职
/果园搜索 167693
/果园板块 1888 5
/果园添加 2318 鸦阁书|鸦阁|鸦阁魔域：魔障深藏
/果园删除 鸦阁
```

搜索结果规则：

- 找到唯一帖子时，插件会直接发送该帖首楼截图。
- 找到多个帖子时，插件会发送标题和 topic 编号。
- 可以再用 `/果园搜索 <topic编号>` 获取指定帖子的首楼截图。

## 按聊天关闭或开启

关闭和开启命令必须明确 `@机器人`，防止误伤其他机器人：

```text
@机器人 /关闭果园
@机器人 /果园关闭
@机器人 /开启果园
@机器人 /果园开启
```

开关只影响当前聊天流：

- 在某个群聊关闭，只会静默这个群聊。
- 在私聊关闭，只会静默这个私聊。
- 其他群聊和私聊不受影响。

关闭后，当前聊天里除了 `@机器人 /开启果园` 和 `/果园帮助`，其他果园聊天指令都不会发送回复。Tool 调用会返回当前聊天已关闭的状态。

开关状态保存在插件目录的 `runtime_state.json`，不会改写 `config.toml`。

## 用户侧板块

通过 `/果园添加` 添加的板块会保存到 `user_boards.json`。

`config.toml` 内置板块不能通过聊天命令删除，但可以通过 `/果园添加` 为已有板块追加用户侧代称。

## 测试

安装后在群聊或私聊发送：

```text
/果园帮助
/果园搜索 UA 邪恶子职
/果园板块 1888 5
@机器人 /关闭果园
/果园搜索 UA 邪恶子职
/果园帮助
@机器人 /开启果园
/果园搜索 UA 邪恶子职
```

预期结果：

- `/果园帮助` 会显示当前聊天的果园状态。
- 关闭后，普通果园查询指令静默。
- 开启后，当前聊天恢复果园查询。
- 在其他群聊或私聊中，开关状态互不影响。

## 排障

- 提示需要登录：检查 `site.cookie` 是否有效，或重新登录论坛后更新 Cookie。
- 没有搜索到帖子：确认板块代称正确，并调整 `query.search_recent_topics`。
- 截图失败：确认运行环境可用浏览器或 Playwright，必要时增大 `render.timeout_seconds`。
- 论坛页面结构变化：插件可能无法解析主题列表或首楼内容，需要更新插件。
