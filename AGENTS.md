# AGENTS.md — KohakuMoss UI

> English version: [AGENTS.en.md](AGENTS.en.md)

本仓库是 KohakuMoss 训练器的独立本地工作台。用户文档在 `README.md`，这里只记录改代码时必须知道的事实与边界。

## 边界（不要越线）

- 通过 **CLI + 文件契约** 对接 `../KohakuMossTrainer`：不 import 训练器、不改训练器源码、不写它的运行产物。
- `src/kohakumoss_ui/contract/` 是唯一知道训练器名字的层（`kogine`、`metrics.jsonl`、knob 名、run 目录布局、doctor JSON）。上游漂移只改这里 + 补测试，不要把训练器概念漏进 `server/` 或 `web/`。
- 默认只监听 `127.0.0.1` 并用一次性 token；服务能启停训练进程，不要绑公网。
- 当前目录不是 Git 仓库；即使以后初始化，也不要擅自 add/commit/push。

## 布局

```
src/kohakumoss_ui/
  cli.py                 参数解析、发现 root、队列调度线程、开浏览器或 pywebview 窗口
  paths.py               state 目录（KOHAKUMOSS_UI_STATE / XDG_STATE_HOME）
  shell/browser.py       webbrowser 打开
  shell/native.py        pywebview 窗口（可选 extra: native）
  server/http.py         stdlib ThreadingHTTPServer：token 校验、JSON API、SSE、静态资源、上传
  server/civitai.py      Civitai REST 客户端 + 发布状态快照（API key 只存 state 目录）
  contract/              root knobs configs metrics logs runs launch doctor datasets state appearance history
  store/                 jsonfile notebook queue profile：UI 自己的用户数据（原子写 + 0600）
  web/                   无构建链的原生 ES modules
    index.html base.css app.css
    api.js format.js app.js views.js settings.js appearance.js
    studio.js heatmap.js 训练主页 / 记事本 / 队列（事件走 data-act 委托）
    i18n.js lang/{en,zh}.json
    data/knob-docs.json  144 个参数的 en/zh 解释 + 训练器文档锚点
    themes/{kohaku,soul,teletype}.css
    fonts/IBMPlexMono-*.woff2
tests/                   契约 / 启动 / 外观 / teletype 门禁 / store / history / civitai / i18n / knob 文档
```

前端约定：

- 颜色只在 `web/themes/*.css` 定义；`base.css` / `app.css` 只用语义变量（`--canvas`、`--panel`、`--border-*`、`--text-*`、`--accent*`）。
- `web/appearance.js` 是主题 / 模式 / 每主题颜色覆盖 / 玻璃 / 壁纸的唯一状态源；`settings.js` 只渲染控件并调用它。
- 固定三个主题族：`kohaku`、`soul`、`teletype`，各有 `light` + `carbon`；不要新增第 4 个族。每族 5 个覆盖色：`canvas`、`panel`、`text`、`border`、`accent`，空字符串表示继承主题值，派生色用 `color-mix()`。
- 壁纸是画在 `<html>` 上的单个 `background-image`（veil 渐变 + 图片）。模糊用 canvas 烘焙进图片，**不要**加全屏 DOM 图层或全屏 CSS filter；`backdrop-filter` 只用在 `.aside` / `.mast` / `.foot` 这类单实例 chrome 上（MonadForge 方案，逐卡片采样会掉帧并在部分驱动上白屏），teletype 则完全不用。
- `teletype` 的材质 token `--perf` / `--perf-hole` / `--red` / `--tint` / `--log-text` 属主题专有，经 `MATERIAL_EXEMPT` 豁免；不要把它们加回 `DERIVED_VARS`，否则五个取色器会覆盖纸张材质。
- 分层固定：CUSTOM BACKGROUND 只管一件事 —— 有没有壁纸（`data-bg="on"` 时 `.app` 透明，壁纸只从控件之间的空隙和页边距透出来；关闭时整页用主题 canvas）。**GLASS 是唯一让表面变薄的开关**：玻璃关时 `.aside` / `.mast` / `.foot` / `.box` / `.runner` 一律 `background:var(--panel)` 实心；玻璃开时才走 `color-mix(in srgb, var(--panel) calc(100% * var(--surface-alpha)), transparent)`，并且只有 `.aside` / `.mast` / `.foot` 带 `backdrop-filter`。`.app` 从不读 `--surface-alpha`，任何开关都不得改变几何（不许桌垫 padding / `calc()` 高度）。「背景开 + 玻璃关 = 82%/92% 半透明」的旧说法是错的，已删除：玻璃关就是实心。
- `teletype` 的控件用薄纸（surface opacity）代替磨砂，但禁止 `backdrop-filter`：打孔带保持实色、控件透明度与滑杆 1:1（不设下限，可读性由 veil 与 MASK 保证），壁纸只在控件之间可见。改外观前先读 `docs/teletype.md`（§2、§6、§9）。
- `main` 的顶边距和 `.vhead-sticky` 的 `padding-top` 是 **1:1 对齐**的一对数（relaxed 16px / compact 10px）；改 `main` 的 padding 必须同步改它，否则粘性表头滚动时会跳一下。
- 侧边栏顺序固定在 `web/index.html` 的 nav 里，别随手调：工作台 = 01 新建训练 `#/new`、02 训练队列 `#/queue`、03 训练记录 `#/runs`；数据 = 04 数据集 `#/datasets`、05 产物 `#/artifacts`；工作室 = 06 训练主页 `#/profile`、07 训练记事本 `#/notebook`；系统 = 08 环境体检 `#/doctor`、09 设置 `#/settings`。产物没有独立分组了（`nav.group.artifacts` 已从 en/zh 两边同时删除），它挂在数据组下面。
- 界面文案统一叫「训练」：词典里 `nav.queue` / `nav.profile` / `nav.notebook` 分别是训练队列 / 训练主页 / 训练记事本，旧的叫法已从 en/zh 两边彻底清掉，别再引回来。
- 面板类控件点击即编辑：点字段本身进编辑态，不要在文字旁边再加 RENAME / UPLOAD 这类文字按钮（`#/profile` 的名字与头像已改成点击即编辑与点击即上传）。
- 数字列一律 `.led th.num{text-align:right}` 配 `.led td.num`，表头与单元格同侧右对齐；例外是数据集页的「图片」「标注」两列和训练队列的「耗时」列，按用户要求保持左对齐——队列那列用 `.q-table td.elapsed`（保留 `tabular-nums`、带 `min-inline-size`）而不挂 `num`，表头也要同步去掉 `num` 类，两边才同侧。
- 无 Node 构建链、无第三方运行时依赖（Pillow 可选，用于上传校验）。Python 源码保持 ASCII-only。
- 文案只在 `web/lang/{en,zh}.json`（键集合必须一致，`tests/test_i18n.py` 会查）；渲染函数从 `ctx.t(key, vars)` 取，不要硬编码可见文案；`data-i18n*` 属性在 `app.js` 的 `applyShell()` 里统一替换。
- 新工作台页写在 `web/studio.js`（run 相关留在 `web/views.js`），事件用 `data-act` 委托挂在视图根上，重渲染不会留旧监听；新页要在 `app.js` 的 `route()` 里登记，并在 `CRUMBS`/词典补面包屑。
- 参数解释来自 `web/data/knob-docs.json`（摘写自训练器 `docs/zh-CN/**`，`tests/test_knob_docs.py` 校验键与 `scripts/train/moss.py` 完全一致）；不要手写第二份。

## 命令

本机 shell 事实（先看这一段，能省掉一轮报错）：

- 用户的登录 shell 是 **fish 4.9.3**（`/usr/bin/fish`），命令按 fish 写；本机**没有** PowerShell（`pwsh`/`powershell` 都不存在）。
- fish 4.9 支持 `VAR=val cmd` 前缀赋值和 `$(...)` 命令替换，但**没有 heredoc**（`cat <<EOF` 会报 "预期 a string，但找到 a redirection"），循环/条件用 `; end` 收尾而不是 `done`/`fi`。
- 所以多行一次性脚本不要用 heredoc：写临时 `.fish` / `.py` 文件再执行，或者直接用 `python -c`。下面示例里的 `env VAR=val cmd` 写法 bash 与 fish 都认。

安装（训练器 venv 没有 pip）：

```bash
cd KohakuMoss-UI
uv pip install -e . --python ../KohakuMossTrainer/.venv/bin/python --no-deps
```

直接跑源码（不必安装）：

```bash
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m kohakumoss_ui \
  --root /tmp/kohakumoss-demo --runs /tmp/kohakumoss-demo --state /tmp/kmstate \
  --host 127.0.0.1 --port 8791 --token test --no-open
```

`/tmp/kohakumoss-demo` 是本地演示数据。测试时 `--state` 一定指向 `/tmp`，别把测试壁纸写进真实的 `~/.local/state/kohakumoss-ui/`。

测试（93 个，<1 s；主题门禁在 `tests/test_theme_teletype.py`，词典门禁在 `tests/test_i18n.py`，参数文档门禁在 `tests/test_knob_docs.py`）：

```bash
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m unittest discover -s tests
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m unittest discover -s tests -p 'test_appearance.py'
```

验证按风险取最小充分集：

- 改 `contract/`：跑对应测试；涉及真实进程/信号时再看 `test_launch.py`。
- 改 `server/http.py`：跑测试后做一次 curl 冒烟；**Python 不热载，必须重启进程**。
- 改前端样式/交互：静态文件直接刷新即可，但 `Cache-Control: no-store` 不总能盖过浏览器缓存，先硬刷新。
- 纯文案、注释、CSS 变量调色：看页面即可，不必跑测试。

常用冒烟与截图：

```bash
curl -s -H 'X-UI-Token: test' http://127.0.0.1:8791/api/state
curl -X POST -H 'X-UI-Token: test' -H 'Content-Type: image/png' \
  --data-binary @shot.png http://127.0.0.1:8791/api/appearance/background
# 本机的 chromium --headless 会挂死（见下），这条只在大屏机器上可用
chromium --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
  --window-size=1440,900 --virtual-time-budget=9000 \
  --screenshot=/tmp/km.png 'http://127.0.0.1:8791/?token=test#/settings'
fuser -k 8791/tcp    # 停服务；不要用 pkill -f，会连带杀掉调用它的 shell
```

本机的 `chromium --headless` 会卡在 GCM 注册（`registration_request.cc`）不退出，`--virtual-time-budget` 不收敛，截图为空；要截图就用 ego 浏览器（`ego_navigate` → `ego_wait` → `ego_screenshot`），别在 headless 上耗时间。切主题也可以直接打 API：`GET /api/appearance` 拿全量 → 改字段 → `POST /api/appearance` 同一对象。

`web/*.js` 是 ESM，`node --check` 需要先复制成 `.mjs`：

```bash
cp src/kohakumoss_ui/web/appearance.js /tmp/appearance.mjs && node --check /tmp/appearance.mjs
```

## 环境变量

`KOHAKUMOSS_ROOT`（训练器路径）、`KOHAKUMOSS_RUNS`（输出根）、`KOHAKUMOSS_PYTHON`（解释器）、`KOHAKUMOSS_UI_STATE`（UI state 目录）。CLI 的 `--runs` / `--state` 会写进对应环境变量。

## 接口与数据须知

- API：`GET /api/{state,knobs,configs,config/load,config/check,runs,run,run/log,doctor,datasets,file,appearance,appearance/background,profile,profile/avatar,heatmap,history,notebook,queue,publish,events}`；`POST /api/run/{start,stop}`、`POST /api/appearance`、`POST /api/appearance/background`、`POST /api/profile`、`POST /api/profile/avatar`（原始字节 ≤5 MB）、`POST /api/notebook/{add,update,remove}`、`POST /api/queue/{add,move,update,remove,control}`、`POST /api/publish`、`POST /api/civitai/{credentials,sync}`；`DELETE /api/profile/avatar`、`DELETE /api/civitai/credentials`。`/api/events` 是 SSE（队列版本变化时推 `{"type":"queue"}`）。
- 队列是串行的：`cli.py` 起 5 s 调度线程，只要队列被暂停、或任何 managed run 还活着、或目标 run 目录已存在，就不会启动下一项；手动 START 会把队列置为 `pausedBy="manual"`，手动 STOP 不会自动恢复。
- 热力图按 `metrics.jsonl` 相邻两点的 `time` 差分聚合到本地日期（`Δsec` 上限 3600、每天封顶 86400），缓存键是 (path,size,mtime) + 30 s TTL。
- 刻意没有「预处理数据集」页：`contract/datasets.py` 只有 `scan` / `_count` / `_cache_info`，`cache/` 由训练器在训练时惰性构建，UI 侧根本没有可触发的预处理动作，所以不给这个页面，也不假装能给。
- Civitai 凭据存在 state 目录的 `civitai.json`（0600），API 只回 `{username,hasKey,keyHint,lastSync}`，**没有** GET 凭据路由；发布标记存在 `profile.json` 的 `publish` 段，`state` 只允许 `published`/`draft`/`unknown`。
- 除上传路由外，POST body 必须是 JSON object，否则 400；上传路由直接读原始字节，自己检查 ≤25 MB。
- UI state：`appearance.json` + `backgrounds/custom.<ext>`（单张背景图）+ `runs.json`（UI 启动的子进程）+ `process/*.out` + `profile.json` / `notebook.json` / `queue.json` / `civitai.json` + `avatar.<ext>`。全部经 `store/jsonfile.py` 原子写，永远不要写进 run 目录。
- 静态资源按文件名从 `web/` 提供，`/assets/...` 前缀会被剥掉；`_static()` 会 `resolve()` 后校验仍在 `web/` 内，改这里时保留该防护。

## 当前状态 / 不要顺手改

- 三套主题都已按各自设计语言重做过：`teletype.css` 依 `docs/teletype.md`，`kohaku.css` 依 `kohaku-gem-design`（暖灰 + 五色宝石三档），`soul.css` 依 `souldesign`（五级阶梯 + ember）。改主题只动 `web/themes/*.css`，不要动几何（`padding`/`height`/`font-size`/`letter-spacing`），三套主题共用同一份 `app.css` 布局。
- `themes/teletype.css` 的镂空条参数 `--perf` / `--perf-hole` 仍在等用户确认，不要顺手调整。
- 没有真实训练运行过（无权重/数据集）；启动链路只验证到命令构造与信号。
