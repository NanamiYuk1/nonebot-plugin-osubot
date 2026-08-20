# nonebot-plugin-osubot（个人修改版）

> 本项目由 [yaowan233/nonebot-plugin-osubot](https://github.com/yaowan233/nonebot-plugin-osubot) 修改而来，
> 在原插件基础上新增了谱面预览（Rust 二进制渲染）、谱面试听、好友互关、成就查询/推荐等指令，
> 并重写了部分指令的渲染与发送方式。原项目全部指令与功能请查阅上方源项目仓库。
>
> 谱面预览渲染核心来自 [osu-beatmap-preview](https://github.com/2710165659/osu-beatmap-preview)，
> 二进制由 [astrbot_plugin_osu_beatmap_preview](https://github.com/2710165659/astrbot_plugin_osu_beatmap_preview) 提供（详见下文「预览渲染核心配置」）。

---

## 新增 / 改动的指令

### 谱面预览（/预览 系列，重写）

渲染优先调用 Rust 二进制 `osu-beatmap-preview`（速度快、带谱面音频），未配置或渲染失败时自动回退旧的浏览器渲染链路。

| 指令 | 说明 |
| --- | --- |
| `/预览 [mapid]:[模式]` | 生成预览图：std 输出 GIF，taiko / catch / mania 输出 PNG |
| `/预览 [mapid] +GIF` | 生成约 10 秒的动态 GIF 预览（任意模式） |
| `/完整预览 [mapid]` | 生成完整预览视频（MP4） |
| `/视频预览 [mapid]`、`/vpreview`、`/vp` | 同上，完整视频（MP4） |

- 先查询过谱面后可以省略 mapid，自动复用最近查询的谱面；`:模式` 用 `0/1/2/3` 或 `o/t/c/m`，非 std 谱面自动使用原生模式。
- std 预览会附带 [beatmap.try-z.net](https://beatmap.try-z.net) 的在线点击预览链接。
- 完整视频渲染耗时较长，中途会发送预计等待时间；首次渲染会写入缓存（`data/osu/<setid>/preview/`），下次秒出。
- 相关配置：`OSU_PREVIEW_BIN_PATH` 等，见下方「.env 配置方法」和「预览渲染核心配置」。

### 谱面猜歌（/谱面猜歌，改造）

- 渲染方式统一走 `render_preview`：std 出 GIF，taiko / mania 出 PNG，catch 出 PNG 且带上该成绩的真实 Mods。
- 其余游戏规则不变：抽选自群内绑定玩家的 BP，`/谱面提示` 提供提示，猜对或超时（5 分钟）结束并公布答案。

### 谱面试听（新增）

- `/au [mapid]`：发送该谱面的试听语音（🎵 谱面试听）。
- 不带 ID 时复用最近查询的谱面（先按 bid 转 sid 拉取，失败再按 sid 直接拉取）。

### 好友与互关（新增）

需要 osu! OAuth 用户级授权（`friends.read`），每个绑定用户各自授权、各自持有令牌。

| 指令 | 说明 |
| --- | --- |
| `/friend`、`/f` | 查看自己的全部好友列表（默认全部展示，无条数上限，图片输出） |
| `/f :pp` | 按 PP 排序；`:acc` / `:pc` / `:pt` / `:th` / `:t` / `:u` / `:c` / `:n` / `:o` / `:m` 同理，后缀 `+` 或 `2` 升序、`-` 降序 |
| `/f 1-30` | 查看第 1-30 位好友（范围） |
| `/f pp>=300 mutual=true country=JP` | 组合筛选（数值比较 + 布尔条件） |
| `/f <玩家名>` | 查询与对方是否互关（mutual，双方都授权时最准确） |

### OAuth 授权（新增）

- `/frbind`、`/fb`、`/好友授权`：生成 osu! OAuth 授权链接。
- 授权完成后自动回调绑定（FastAPI / Quart 驱动自动挂载 `/osubot/oauth/callback` 路由）。
- 回调地址不可达时，把授权后浏览器地址栏里的 `code=...`（或完整网址）发给机器人即可：`/frbind <code>`。
- `/bind` 绑定成功后，回复里也会附带授权链接，可直接点击授权。

### 成就（新增）

- `/myach [模式]`、`/我的成就`：以图片输出自己已获得的成就（按获得时间倒序）。模式：`o/t/c/m` 或 `osu/taiko/catch/mania`，默认 osu。
- `/achrec [模式]`、`/成就推荐`：根据已获得成就推荐 15 个未获得成就（图片 + 中文攻略）。
- `/md <成就名>`（原指令增强）：优先使用本地中文攻略目录（`osufile/medals/medals.json`），并附带相关谱面建议。

### 多人比赛（重写）

- `/mp <match id / room id>`、`/match`：多人比赛结果改为多页图片渲染；OB11 下优先合并转发（`send_group_forward_msg`），失败自动逐张重试发送。
- `/rt <match id / room id>`、`/rating`：多页 rating 图片顺序发送，补齐参数校验、异常捕获与友好错误提示。
- **支持 Lazer 多人房 room id 查询**（也兼容 stable mp 的 match id）：查询时自动尝试 `/matches/` 接口，失败后回退 `/rooms/` 接口。
- Lazer 房间支持 **head-to-head、team-vs（tag-team-vs）** 等模式：自动从房间 events 接口解析红蓝分队、剔除被强制关闭（abort）的对局；同一房间中途切换模式（如热身 head-to-head → 正赛 team-vs）时按模式分别渲染。

### 其他改动

- `/osuhelp`：帮助图直接展示详情页（`osufile/help.png` / `detail.png`）；修改 `osufile/help.html` 后可用 `osufile/gen_help_png.py` 重新生成。
- `/pr` `/pl` `/re` `/rl`：修复独立序号解析、Mods / 筛选条件下拉取窗口不足导致结果缺失的问题。
- `/nb N`：裸数字现在作为「N 天内新增 BP」的时间窗口（原为 BP 序号）。
- `/mu`：头像获取异常兜底；`/osudl`：下载失败友好提示；`/rank`：会话缺失时的兼容修复。
- API 层：凭据支持 OAuth 应用回退、401 自动刷新令牌重试、422 友好提示、过滤缺少 beatmap 信息的成绩。
- `fix_duplicated_files.py`：清理重复上传产生的「(1)」后缀文件的小工具（`python fix_duplicated_files.py --dry-run` 预览，`--dir` 指定目录）。

---

## .env 配置方法

NoneBot 项目的 `.env`（机器人根目录，不是插件目录）。新增配置项如下，原项目已有的 `OSU_CLIENT` / `OSU_KEY` / `OSU_PROXY` / `OSUTRACK_*` / `OSU_RECOMMEND_*` 照旧。

```ini
# ===== osu! API 凭据（必填，二选一）=====
OSU_CLIENT=你的osu客户端ID
OSU_KEY=你的osu客户端密钥
# —— 或使用 osu! OAuth 应用凭据（client_credentials 同样可用于 API 请求）——
OSU_OAUTH_CLIENT_ID=你的OAuth应用ID
OSU_OAUTH_CLIENT_SECRET=你的OAuth应用密钥

# ===== 好友功能 OAuth（/friend、/frbind）=====
# 必填：回调地址。必须为公网可访问的 HTTPS（osu! 仅允许 https 或 http://localhost），
# 且需在 osu! OAuth 应用设置里登记。回调路径固定为 /osubot/oauth/callback。
OSU_OAUTH_REDIRECT_URI=https://你的公网域名/osubot/oauth/callback

# ===== 谱面预览（/预览、/谱面猜歌）=====
# 必配：Rust 渲染二进制（osu-beatmap-preview）的绝对路径。
# config.py 默认值为 None（未配置）；不配置时 /预览 自动回退旧浏览器渲染链路。
OSU_PREVIEW_BIN_PATH=C:/path/to/osu-beatmap-preview-windows-amd64.exe
OSU_PREVIEW_USE_CORE=true      # 是否启用二进制渲染（默认 true）
OSU_PREVIEW_FALLBACK=true      # 二进制缺失/失败时是否回退旧浏览器链路（默认 true）
OSU_PREVIEW_TIMEOUT=120        # gif/png 单次渲染超时（秒，默认 120）
OSU_PREVIEW_VIDEO_TIMEOUT=300  # 完整 mp4 渲染超时（秒，默认 300）

# —— 以下为旧浏览器链路的可选参数（回退渲染时生效，不配则用默认值）——
# OSU_PREVIEW_FFMPEG_PATH=      # ffmpeg 绝对路径；不配则自动找 PATH 里的 ffmpeg
# OSU_PREVIEW_TAIKO_SKIN_PATH=  # taiko 皮肤目录（读取 taiko-roll-*.png 等素材）
# OSU_PREVIEW_FULL_SCALE=0.75   # 完整视频缩放（0.5-1.0）
# OSU_PREVIEW_FULL_FRAME_INTERVAL=30      # 完整视频帧间隔 ms（20-50）
# OSU_PREVIEW_TAIKO_FULL_SCALE=0.5
# OSU_PREVIEW_TAIKO_FULL_FRAME_INTERVAL=30
# OSU_PREVIEW_STD_CATCH_FULL_SCALE=0.5
# OSU_PREVIEW_STD_CATCH_FULL_FRAME_INTERVAL=30

# ===== 好友功能（预留，当前版本未使用）=====
# OSU_FRIEND_PAGE_SIZE=20
# OSU_FRIEND_MAX_PAGE=100
```

> 依赖说明：新增功能未引入新的硬依赖（Pillow、Jinja2、Playwright、expiringdict 均为原项目已有依赖）。
> 好友回调路由需要 FastAPI 或 Quart 驱动（`DRIVER=~fastapi+~httpx`）；其他驱动仍可用 `/frbind <code>` 手动完成授权。

---

## 预览渲染核心（astrbot_plugin_osu_beatmap_preview）配置与调用路径修改

### 原理

`/预览`、`/谱面猜歌` 的谱面渲染并不是通过那个 AstrBot 插件本身完成的，而是**直接调用 Rust 二进制 `osu-beatmap-preview`**：

```
osu-beatmap-preview --bid <bid> --fmt <png|gif|mp4> [--convert taiko|ctb|mania] [--mods hd+hr] ...
# stdout 输出 JSON，产物绝对路径在 "preview-img" 字段
```

`astrbot_plugin_osu_beatmap_preview` 仓库只是「AstrBot 平台」的插件封装，我们只用到它 `bin/` 目录里提供的四个平台二进制。调用路径由配置项 `osu_preview_bin_path`（`OSU_PREVIEW_BIN_PATH`）决定。

### 配置步骤

1. **获取二进制**（任选其一）：
   - 方式 A：`git clone https://github.com/2710165659/astrbot_plugin_osu_beatmap_preview.git`，使用其 `bin/` 目录下对应平台的二进制；
   - 方式 B：直接到 [osu-beatmap-preview Releases](https://github.com/2710165659/osu-beatmap-preview/releases) 下载：
     - Windows：`osu-beatmap-preview-windows-amd64.exe`
     - Linux：`osu-beatmap-preview-linux-amd64`
     - macOS Intel：`osu-beatmap-preview-macos-amd64`；Apple Silicon：`osu-beatmap-preview-macos-arm64`
   - 方式 C：在 AstrBot 里安装该插件后运行其 `update_core.bat`，会自动把四个平台的二进制下载到插件 `bin/` 目录。
2. **赋予执行权限**（仅 Linux / macOS）：`chmod +x osu-beatmap-preview-linux-amd64`
3. **配置调用路径**（二选一）：
   - **推荐：在 `.env` 里覆盖（不改代码）**
     ```ini
     OSU_PREVIEW_BIN_PATH=C:/absolute/path/to/osu-beatmap-preview-windows-amd64.exe
     ```
   - 或者修改 `config.py` 中 `osu_preview_bin_path` 的默认值（默认已为 `None`；注意改动会随代码提交到仓库）。
4. **验证**：`/预览 <mapid>` 正常出图即可。二进制缺失、超时或非零退出时，插件会按 `OSU_PREVIEW_FALLBACK` 配置回退旧浏览器链路，并在日志（logger：`nonebot_plugin_osubot.core_preview`）中输出原因。

### 二进制支持的参数（与 astrbot 插件文档一致）

| 参数 | 说明 |
| --- | --- |
| `--bid <id>` | 谱面 ID（必填） |
| `--fmt png\|gif\|mp4` | 输出格式 |
| `--convert taiko\|ctb\|mania` | 模式转换（std 不需要） |
| `--mods hd+hr` | Mods，小写、`+` 连接 |
| `--time t1+t2` | 片段起止时间（秒），完整 mp4 不要传 |
| `--preview-30s` | 渲染 PreviewTime 附近约 30 秒（视频默认行为） |
| `--gif-clip` / `--gif-clip-label` | 单屏连续 GIF / 带时间标签 |
| `--gap <秒>` | 时间间隔 |
| `--no-cache` | 跳过缓存 |
