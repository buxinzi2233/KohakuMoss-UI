# KohakuMoss-UI · 交互补全与「炼丹工作台」实施计划

- 日期：2026-10-22
- 范围：`KohakuMoss-UI`（本仓库）与 `../KohakuMossTrainer`（只读 + 拉取更新）
- 参考：`docs/teletype.md`、`docs/proposal-new-run-tabs.md`、`AGENTS.md`、训练器 `docs/zh-CN/`
- 设计路径：oil-ui 的「存量项目优化 UI / 改流程 / 加新功能」——沿用现有视觉与布局，先盘点再动手，不做视觉方向发散

---

## 0. 目标与验收标准

把用户 m00001 的 8 条任务翻译成可验收的交付物：

| # | 任务 | 验收证据 |
| --- | --- | --- |
| 7 | 先拉训练器后端更新并检查前端 | `../KohakuMossTrainer` 与 upstream 同步；产出 knob/section/metrics 差异清单；前端适配已合入 |
| 1 | 补全 UI 交互缺失 | NEW RUN 动作条常驻、页签深链、键盘操作、CHECK 结果定位、RUNS 筛选、日志跟随；无 JS 报错 |
| 2 | 炼丹热力图 / 个人主页 | 新 `#/profile`：左头像、右上最满意 LoRA、右下 53×7 热力图 + 炼丹历史；Civitai 凭据 + 发布标记 |
| 3 | 炼丹记事本 | 新 `#/notebook`：条目 CRUD、状态（想法/待炼/已入队/已炼成/放弃）、一键入队或预填 NEW RUN |
| 4 | 炼丹队列 | 新 `#/queue`：服务端串行调度、暂停/恢复、排序/取消、状态与进度回写 |
| 5 | 先 teletype 后两套主题 | teletype 下三视图全绿并截图；再用 `kohaku-gem-design` / `souldesign` 重写 `themes/kohaku.css` / `themes/soul.css`，三主题 × 两模式截图通过 |
| 6 | 中英界面 + 文档化参数解释 | 语言切换（AUTO/EN/ZH）；`web/data/knob-docs.json` 覆盖全部 knob 与 section，文案取自训练器文档 |
| 8 | review + 实际查看 | 测试全绿 + 截图矩阵 + 人工逐屏检查（含 1280/1440 两档宽度） |

全局硬约束（沿用 AGENTS.md）：只监听 127.0.0.1 + token；`contract/` 之外不出现训练器概念；不 import 训练器、不写 run 目录；无 Node 构建链、无第三方运行时依赖；Python 源码 ASCII-only；颜色只在 `themes/*.css`。

---

## 1. 现状基线（已核对）

**前端**：`views.js`(652) / `knobform.js`(≈900) / `settings.js`(273) / `app.js`(220) / `appearance.js`(15.9K) / `app.css`(33K) / `themes/{teletype,kohaku,soul}.css`。路由 `#/runs #/new #/datasets #/artifacts #/doctor #/settings #/run/<name>`，无 i18n、无子路径。

**三个公认的交互缺口**（实拍确认）：
1. `START RUN` 只存在于页头，滚动到第 8 行之后操作按钮离开视野；
2. knob 的 `?` 帮助只有训练器源码行尾英文注释，没有中文、没有文档出处；
3. RUNS 页无搜索/筛选，run 详情日志无限增长、无暂停跟随。

**teletype 门禁**：`tests/test_theme_teletype.py` 扫描 `views.js` / `index.html` 的 class token 与 `themes/*.css` 的禁用属性。新增视图必须同时满足 §5 标记预算、§6 禁令、两种模式。

**训练器侧事实**：`scripts/train/moss.py` 用 `# ==== SECTION ====` 分组，143 个 knob；`metrics.jsonl` 每行含 `time`(epoch) 与累计 `step` → 可按日聚合出热力图；`export/<name>_lora.safetensors` 是发布物。

**Civitai**：`GET /api/v1/models?username=&types=LORA` 无需 key 即可读公开模型；`GET /api/v1/me` 用 `Authorization: Bearer <key>` 验证凭据。只能标记发布状态，不自动上传。

---

## 2. 交付物地图

### 2.1 新增 Python 模块

| 文件 | 职责 |
| --- | --- |
| `contract/history.py` | 唯一知道 `metrics.jsonl` 时间/步数字段的地方：`daily_activity(root, days)`、`run_history(root)`，带 mtime+尺寸指纹缓存 |
| `store/jsonfile.py` | `read_json(path, default)` / `write_json(path, data)`（tmp + `os.replace`，模块内 `threading.Lock`），`0600` 权限 |
| `store/notebook.py` | 记事本条目 CRUD；字段 `{id,title,base,dim,steps,lr,dataset,note,tags,priority,status,config,overrides,created_at,updated_at,run_name}` |
| `store/queue.py` | 队列 CRUD + 排序 + `paused` 状态 + `tick(root)` 串行调度 + 结果回写记事本 |
| `store/profile.py` | 主页资料：`displayName`、`favorite`（run + 备注）、头像文件名、Civitai 用户名/凭据标记、`publish` 映射 |
| `server/civitai.py` | `sync(username, api_key)`（urllib，15s 超时，最多 5 页）、`normalize(name)`、`match_local(...)`（纯函数）、`mask_key()` |

### 2.2 新增前端模块

| 文件 | 职责 |
| --- | --- |
| `web/studio.js` | `profileView` / `notebookView` / `queueView` 三个渲染器 + `mountStudio(ctx)`（事件委托） |
| `web/heatmap.js` | `renderHeatmap(days)`（53×7 grid、月份/星期标签、图例、`data-level` 0–4）、`renderHistory(rows)` |
| `web/i18n.js` | `loadLang()` / `t(key, vars)` / `setLang(lang)` / `applyStatic(root)` / `currentLang()` |
| `web/lang/en.json`、`web/lang/zh.json` | 词典（JSON，键集合必须一致，测试校验） |
| `web/data/knob-docs.json` | 143 knob + 13 section 的中英说明与文档锚点 |

### 2.3 修改文件

`index.html`（导航分组 / mast 语言按钮 / `data-i18n`）、`app.js`（路由、CRUMBS、ctx.t、boot 顺序）、`views.js`（i18n、sticky 动作条、RUNS 筛选、CHECK 定位）、`knobform.js`（页签深链、help 接文档、中文搜索、键盘）、`settings.js`（语言段、Civitai 段）、`app.css`（studio/heatmap/queue/notebook 样式，仅语义变量）、`themes/*.css`（新组件 + 主题重做）、`contract/appearance.py`（`lang` 字段）、`server/http.py`（新路由 + 队列 tick 线程 + SSE `queue` 事件）、`cli.py`（store 目录与后台线程）、`README.md` / `AGENTS.md` / `docs/teletype.md`（新增视图登记）。

---

## 3. 数据与契约设计

### 3.1 状态文件（`paths.state_dir()` 下）

```
appearance.json        + lang: "auto" | "en" | "zh"      （默认 auto）
profile.json           displayName, favorite, avatar, civitai{username,hasKey,lastSync}, publish{}
notebook.json          {items:[...]}
queue.json             {paused:false, pausedBy:null, items:[...], version:7}
avatars/avatar.<ext>   单张头像（PNG/JPEG/WebP ≤ 5 MB）
civitai.json           已同步模型快照 + API key（0600，永不回显）
runs.json, process/*   既有
```

所有写入都是原子替换；读取容错（坏 JSON → 默认值 + 一次 `.bak` 备份）。队列/记事本上限 200 / 500 条。

### 3.2 新 API

| 方法 | 路径 | 入参 | 返回 |
| --- | --- | --- | --- |
| GET | `/api/profile` | – | `{displayName, favorite, avatarUrl, stats, civitai:{username,hasKey,lastSync}, publish}` |
| POST | `/api/profile` | `{displayName?, favorite?}` | 归一化后的 profile |
| POST | `/api/profile/avatar` | 原始图片字节（Content-Type 校验） | `{avatarUrl}` |
| DELETE | `/api/profile/avatar` | – | `{ok:true}` |
| GET | `/api/heatmap` | `?days=365` | `{days:[{date,steps,seconds,runs}], totals, streak, longest, years}` |
| GET | `/api/history` | – | `{runs:[{name,started_at,steps,seconds,status,exports}]}` |
| GET | `/api/notebook` | – | `{items}` |
| POST | `/api/notebook/add|update|remove` | `{item}` / `{id,patch}` / `{id}` | `{items}` |
| GET | `/api/queue` | – | `{paused, pausedBy, current, items}` |
| POST | `/api/queue/add|move|update|remove|control` | `{draft}` / `{id,direction}` / `{id,patch}` / `{id}` / `{action:pause\|resume\|retry\|clear-done}` | `{paused, current, items}` |
| POST | `/api/civitai/credentials` | `{username, apiKey?}` | `{username, hasKey, keyHint}` |
| DELETE | `/api/civitai/credentials` | – | `{hasKey:false}` |
| POST | `/api/civitai/sync` | `{refresh?}` | `{username, syncedAt, models, publish, error}` |

沿用现有 Handler 约定：POST body 必须是 JSON object（上传路由除外）；token 校验、`_json()` 输出、错误统一 `{error}` + 4xx/5xx。SSE 在既有 `/api/events` 上新增 `{"type":"queue", ...}`，只在队列 `version` 变化时推。

### 3.3 热力图聚合（`contract/history.py`）

- 遍历 runs 根下最多 200 个目录（mtime 倒序），每个 run 读 `metrics.jsonl` 尾部 ≤ 2 MB，解析 `(time, step)`。
- 对相邻样本 `(t0,s0) → (t1,s1)`：`Δstep = max(0, s1-s0)` 计入 `t1` 的本地日期；`Δsec = clamp(t1-t0, 0, 3600)` 同日期累加（每日封顶 86400）。
- 每日再记录活跃 run 名集合；`totals` 汇总活跃天数、总步数、总时长；`streak` / `longest` 按日期连续性计算；`years` 供年度切换。
- 缓存键 `(path, size, mtime)`，TTL 30 s；空数据返回 365 天全 0（前端走空态，不画假数据）。
- `run_history` 复用 `runs.list_runs` 的 status 判定，避免两套真相。

### 3.4 Civitai 同步与发布标记

- 凭据：`username` 必填；`apiKey` 可选（只用于 `/api/v1/me` 校验与更高配额）。key 存 `civitai.json`（0600），API 只返回 `hasKey` 与末 4 位提示。
- 拉取：`/api/v1/models?username=<u>&types=LORA&limit=100&page=N`，最多 5 页；快照缓存 24 h，`refresh` 强制重取；网络失败保留旧快照并回 `error`。
- 匹配：`normalize(name)` = 小写、去扩展名、非字母数字折叠为 `-`、去首尾；本地名取 `run 名` + `export/*_lora.safetensors` 的 stem（去 `_lora` 后缀）。命中多个 → 取第一个并标 `ambiguous`；未命中 → `draft`。
- 合并优先级：用户手动标记 `manual:true` > Civitai 命中 > 未知。`publish` 存规范化名 → `{state, modelId, url, manual, checkedAt}`。
- 安全：URL 只接受 `https://civitai.com/` 前缀；不把 key 写进日志/错误串。

### 3.5 队列调度状态机

```
waiting --(轮到 & !paused & 无 alive run)--> starting --(pid alive)--> running
running --(step>=steps 或 status=done)--> done
running --(pid 消失 & step<steps)--> failed(error)
waiting/running --(用户 cancel)--> canceled
failed/canceled --(retry)--> waiting
```

- `tick(root)` 每 5 s（daemon 线程，挂在 `cli` 启动后）：先结算 `starting/running` 项，再决定是否启动第一项 `waiting`。
- 三道保险：`paused` 为真不启动；任何 alive 的 managed run 存在不启动；`launch.start` 前再次检查 run 目录不存在（重名 → `failed: run name exists`）。
- 手动 `START RUN` 时若队列有 running 项 → 队列自动 `pausedBy:"manual"`，UI 提示；手动 `STOP` 不自动恢复队列（安全优先，页面明示 RESUME）。
- 完成后回写记事本：队列项若来自 notebook（`notebookId`），对应条目 `status=trained|failed`。
- 重启恢复：`queue.json` 里 `starting/running` 项按 run 名重新对账（pid 存活 → running；否则结算）。

### 3.6 语言解析

优先级：`appearance.lang`（`en`/`zh`）→ `navigator.language` 前缀 `zh` → `en`。`auto` 时随浏览器。切换来源：mast `[ ZH ]` 按钮 + 设置页 LANGUAGE 段；写入 `appearance.lang` 后 `rerender()`。词典 JSON 首次 `fetch`，失败回退内置英文表（保证离线不白屏）。

**不翻译**：run 名、config 路径、日志原文、knob 原始名、源码注释、指标缩写、Civitai 返回的模型名。

---

## 4. 分阶段实施

### Phase 0 · 训练器对齐（先做，用户第 7 条）

1. `git -C ../KohakuMossTrainer fetch upstream --prune`；`git log --oneline HEAD..upstream/main`。
2. 有新提交 → `git merge --no-edit upstream/main`。**冲突则停止**，把冲突文件与
   `git diff --name-only --diff-filter=U` 报给用户，不擅自解决。
3. 对齐检查（一次性脚本，只读）：section 列表 vs `knobform.js` 的 `GROUP_META`；knob 名集合 vs `BASE_NAMES`/`LABELS`；`metrics.jsonl` 字段 vs `contract/metrics.py`；demo 导出布局 vs `contract/runs.py`。
4. 把差异落成两件事：前端缺的 label/分组/basic 归类补齐；`web/data/knob-docs.json` 的 knob 集合补齐。上游若新增 section，先给默认英文标题与 `OTHER` 子组，再补中文。
5. 顺手修正 README 的演示命令（`--runs /tmp/kohakumoss-demo/runs/moss`，当前会只扫到空目录）。

验收：训练器 `git status` 干净（除既有未跟踪目录）；`GET /api/knobs` 的 `count` 与前端 footer 一致；差异清单入 commit 说明（不提交，仅记录）。

### Phase 1 · 交互补全（仅 teletype 视角验收）

1. **NEW RUN 动作条常驻**：`.vhead` 内动作组改为 `position:sticky; top:0`，纸色底 + `border-bottom`，无阴影；滚动时 `START RUN` 始终可达。
2. **页签深链**：`#/new/<slug>`（`checkpoint-export`、`optimize` 等由 section 小写化生成）；切页 `history.replaceState`；刷新恢复；非法 slug → 首页。**不重建表单**，只切 `renderPage`，overrides 不丢。
3. **键盘**：`Ctrl/Cmd+Enter` 启动、`Alt+[`/`Alt+]` 切页、`Esc` 关帮助面板、`/` 聚焦搜索（输入框内不触发）。
4. **CHECK 定位**：preflight 失败信息里解析 knob 名 → 自动切到所属页、高亮该行、滚动到可视区；顶部 toast 汇总。
5. **RUNS 页**：搜索（名称子串）、状态筛选（ALL/RUNNING/DONE/FAILED）、排序（LAST ACTIVE / NAME）、行内 `OPEN`/`STOP`；SSE 刷新时保留筛选与滚动位置。
6. **run 详情**：日志「跟随 / 暂停」开关（暂停时停止自动滚动）、`COPY PATH`（artifact 与 export）。
7. **可访问性（不改视觉）**：页签 `role="tablist"/"tab"/"aria-selected"`，帮助按钮 `aria-expanded`，热力图格子 `title` + 文字统计。
8. 所有新文案先走英文 key，Phase 4 再接中文，避免二次返工。

边界：切换级别（BASIC/ADVANCED/ALL）保留搜索词；深链切换保留已改值；SSE 重渲染不得抢焦点（输入中不重绘当前页签内容）。

### Phase 2 · 数据层

`contract/history.py` → `store/{jsonfile,notebook,queue,profile}.py` → `server/civitai.py` → `server/http.py` 路由 → `cli.py` 后台 `tick` 线程。每层完成即补测试（见 §6），再进 UI。

### Phase 3 · Studio 三视图（teletype 语法）

`#/profile`（参考 GitHub 个人主页）：
- 左列 ≈220px：正方形头像（无头像时打孔纸占位 + 首字母）、显示名、一句 bio、四个统计（RUNS / STEPS / ACTIVE DAYS / PUBLISHED）。
- 右列：上「BEST LORA」卡（run 名 + 备注 + STEPS/LR/DIM/导出 + OPEN + PIN）；下「ACTIVITY」53×7 热力图（`data-level` 0–4，`--tint`/ink 梯度，标题栏放年度与总计）；再下「HISTORY」行表（日期/名称/步数/时长/状态/导出）。
- 空态：无 run → 引导 NEW RUN；无头像 → UPLOAD；无 Civitai → CONNECT。
- `<1100px` 单列堆叠。

`#/notebook`：顶部筛选（ALL/IDEA/TODO/QUEUED/TRAINED/DROPPED）+ 搜索 + `[ NEW ENTRY ]`；行内展开表单（不引入 modal）；每行 `[ QUEUE ]`/`[ PREFILL ]`/`[ EDIT ]`/`[ DELETE ]`；有 `config` 才可直接入队，否则跳 NEW RUN 预填 `overrides`。

`#/queue`：调度状态行（IDLE / RUNNING name / PAUSED by whom）+ `[ PAUSE ][ RESUME ][ CLEAR DONE ]`；列表列序号/名字/config/状态章/进度/耗时/操作（UP/DOWN/CANCEL/RETRY）；空态两条入口。SSE `queue` 事件驱动刷新，无轮询。

导航（`index.html` 与 `CRUMBS` 同步重编号）：`WORKBENCH 01 RUNS · 02 NEW RUN · 03 QUEUE` / `STUDIO 04 PROFILE · 05 NOTEBOOK` / `DATA 06 DATASETS` / `ARTIFACTS 07 ARTIFACTS` / `SYSTEM 08 DOCTOR · 09 SETTINGS`。

### Phase 4 · 中英界面 + 参数解释

1. 抽 key：`index.html` / `views.js` / `knobform.js` / `settings.js` / `studio.js` 全量替换为 `t("...", {...})`；`en.json` 与 `zh.json` 同步写。
2. `web/data/knob-docs.json`：13 个 section + 143 个 knob，每条 `{en, zh, doc}`；依据训练器 `docs/zh-CN/guides/configs.md`、`concepts/*.md`、`guides/{datasets,logging,multi-gpu,weights}.md` 与源码行尾注释撰写；`doc` 指向训练器相对路径 + 锚点。
3. knob 帮助面板：标题（友好名）→ 中文/英文说明（按当前语言）→ 源码注释原文（`SOURCE script:line`）→ 文档链接（`DOCS docs/zh-CN/...`）→ DEFAULT/TYPE/RELATION。
4. 搜索匹配扩展：中文 label、英文 label、原始名、说明文本四路匹配。
5. 设置页新增 LANGUAGE 段（AUTO / ENGLISH / 中文）与 CIVITAI 段（USERNAME / API KEY / SYNC / STATUS），两者都复用现有 `field`/`seg` 组件。

### Phase 5 · 两套主题收尾

前置：Phase 1–4 完成、`unittest` 全绿、teletype 三视图 × light/carbon 截图通过。

1. `skill kohaku-gem-design`：提取 gem 五色三档 + warm-gray 阶梯 + 边框代阴影 + 关键帧预算 → 映射到既有语义变量（`--canvas/--panel/--ink/--rule/--accent/--warn/--ok/--bad/--radius-*/--shadow-*`）→ 只重写 `themes/kohaku.css`，覆盖新组件（热力图色阶、队列状态章、记事本行）。
2. `skill souldesign`：只采纳 token、布局语法、材质与节奏；**不引入 GSAP 或任何 JS 动画库**（项目无构建链、无第三方运行时依赖），过渡统一 ≤ `--dur-fast` 或主题自有的 `--dur-base`，尊重 `prefers-reduced-motion`（`base.css` 已有兜底）→ 只重写 `themes/soul.css`。
3. 两族都必须让 5 个取色器覆盖继续生效（`appearance.js` 的 `applyColors` 派生）；不得改 DOM 与几何。
4. 每族完成后各拍 6 张（三视图 × 两模式），与 teletype 基线并排检查层级、对比度、状态可辨性。

### Phase 6 · Review 与文档

- 逐屏检查：RUNS / NEW RUN（3 页签）/ RUN 详情 / DATASETS / ARTIFACTS / DOCTOR / SETTINGS / PROFILE / NOTEBOOK / QUEUE × 三主题 × 两模式（重点 1440 与 1280）。
- 交互回归：深链刷新、键盘、CHECK 定位、队列启动/取消、Civitai 无网/无 key/命中三态、记事本入队。
- 文档：`README.md` 新功能与截图、`AGENTS.md` 布局与 API 清单、`docs/teletype.md` §8 登记新视图标记点、新增 `docs/studio.md`（数据文件与队列语义）。
- 收尾：停服务（`fuser -k 8791/tcp`），清理 `/tmp` 测试 state，确认没有写入 run 目录。

---

## 5. teletype 合规要点（新组件）

| 组件 | 允许的标记 | 禁用 |
| --- | --- | --- |
| 头像框 | 1 个 `.box` 规则角 | 圆角、投影、滤镜 |
| 热力图 | 纸纹之外无新纹理；`data-level` 用 `color-mix(--ink, --paper)` 四档实色 | 渐变、色相叠加、循环动画 |
| 队列状态章 | 每行 1 枚章（复用 `.st` 类族） | 新色相（RUNNING 用 `--accent`，FAILED 用 `--bad`，DONE 用 `--ink`） |
| 记事本行 | 收据规则 + 行号 | 卡片阴影、>2 字号档 |
| 动作条 | `border-bottom` + 纸色底 | `box-shadow`、`backdrop-filter` |

新样式只用 `base.css` 已有语义变量；`--perf/--tint/--red` 等材质段不得被 `applyColors` 覆盖。

---

## 6. 测试与门禁

新增：
- `tests/test_store.py`：jsonfile 原子写 + 坏 JSON 容错；notebook CRUD 与上限；queue 状态机（用假 `launch.start`/`state` 的注入接口）、重名保护、暂停语义、重启对账。
- `tests/test_history.py`：用 `tools/make_demo_run.py` 造 3 个跨日期 run → 断言日聚合、streak、缓存命中、空目录返回 365 天全 0。
- `tests/test_civitai.py`：`normalize` 与 `match_local`（命中/未命中/歧义/后缀剥离）纯函数；`sync` 用 `unittest.mock` 替换 URL 打开，覆盖 401、分页、超时。
- `tests/test_i18n.py`：`en.json`/`zh.json` 键集合完全一致；扫描 `web/*.js` 的 `t("...")` 字面量全部存在于词典；`data-i18n` 属性同源。
- `tests/test_knob_docs.py`：JSON 合法；每条 `en`/`zh` 非空且 ≥ 8 字符；`BASE_NAMES` 全覆盖；键名必须是 `/api/knobs` 里存在的 knob（防拼错）；缺说明时只报告覆盖率、不硬失败（上游新增 knob 不应红）。
- `tests/test_theme_contract.py`：三套主题都声明了 studio 新组件所需的语义变量；`themes/*.css` 不含 `border-radius:[1-9]`、全屏 `filter`、`backdrop-filter` 于 teletype。

扩展：
- `tests/test_theme_teletype.py`：`VIEWS_JS` 常量加入 `studio.js`/`heatmap.js`，让新视图同样受 §5 标记与落地选择器门禁约束。
- `tests/test_appearance.py`：`lang` 归一化与非法值回退。

基线：现有 32 个测试不得回归；总时长仍 < 2 s。

---

## 7. 团队分工（Agent Teams，写范围不重叠）

| 角色 | 写范围 | 依赖 |
| --- | --- | --- |
| Lead | Phase 0、`index.html`/`app.js`/`app.css` 收口、`themes/teletype.css`、Phase 4 数据表撰写、最终 review 与截图 | 全程 |
| teammate `backend` | `contract/history.py`、`store/*`、`server/civitai.py`、`server/http.py`（路由与 tick）、`cli.py`、`tests/test_{store,history,civitai}.py` | Phase 0 完成 |
| teammate `studio` | `web/studio.js`、`web/heatmap.js` | backend 的 API 契约冻结后 |
| teammate `i18n` | `web/i18n.js`、`web/lang/*.json`、`web/data/knob-docs.json`、`settings.js` 文案 | Phase 1 文案冻结后 |
| teammate `themes` | `themes/kohaku.css`、`themes/soul.css` | Phase 3 完成且 teletype 截图通过 |

冲突控制：`app.js`/`index.html`/`app.css`/`views.js` 只由 Lead 改；`views.js` 的 i18n 改造由 Lead 与 `i18n` 分文件进行（`i18n` 只写词典与 `i18n.js`，替换由 Lead 批量执行）。任务板用 `team_task_create` + 依赖表达顺序。

---

## 8. 风险与边界情况

| 风险 | 处理 |
| --- | --- |
| 训练器 merge 冲突 | 停下上报，不擅自解决；前端适配可先行（knob 扫描是运行时的） |
| 队列误启动第二个 run | 三重检查（paused / 任何 alive managed run / run 目录已存在），失败写 `failed` 并留 error 文本 |
| 服务重启时队列项悬挂 | 启动时按 run 名与 pid 对账，超 30 s 无 pid → `failed: lost` |
| `metrics.jsonl` 巨大 | 只读尾部 2 MB（复用 `metrics.read_tail_text`），缓存 30 s，最多 200 个 run |
| 无网络 / C 站 5xx | 保留旧快照，返回 `error`，UI 显示「SYNC FAILED」而不清空已标记状态 |
| API key 泄漏 | 只存 state 目录 0600，返回 `hasKey`+末 4 位，不打日志，不写入 URL |
| i18n 漏翻 | 测试断言键集合一致 + `t()` 字面量存在；缺失时显示 key（可见即修） |
| 主题重做破坏 teletype 门禁 | `test_theme_teletype.py` 与 `test_theme_contract.py` 双门禁，任何主题漏变量即红 |
| 深链/SSE 抢焦点 | 输入中不重绘当前页；切页只换局部 DOM |
| `_static` 路径穿越 | 继续复用现有 `resolve()` 校验；`data/*.json` 走同一路径 |

**不做（YAGNI）**：run 对比视图、图表库、WebSocket、多用户/账户、Civitai 自动上传、训练器源码改动、第 4 套主题、npm/构建链/GSAP 运行时、复数规则引擎、run 目录写入。

---

## 9. 已采纳的假设

1. 「C 站 API」= 用户名（读公开模型）+ 可选 API key（校验与私有配额），两者都支持。
2. 「最满意 LoRA」= 用户在 PROFILE 手动 PIN 一个 run（默认最近完成），不做算法评分。
3. 「炼丹历史」= run 列表（日期/步数/时长/状态/导出），与热力图同页。
4. 「记事本」记录想法与待办，不等同队列；条目可一键入队或预填 NEW RUN。
5. 队列是串行单卡调度（一次一个 run），与手动启动互斥且手动优先。
6. 头像为用户上传图片（≤5 MB）；未上传时用纸面占位，不影响布局。
7. 语言默认 AUTO（跟随浏览器），可在 mast 与设置页切换。
8. 主题重做只改 `themes/{kohaku,soul}.css`，不改 DOM 与几何；souldesign 的 GSAP 依赖不引入。

---

## 10. 验收命令

```bash
# 测试
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m unittest discover -s tests

# 启动演示（注意 runs 根）
PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m kohakumoss_ui \
  --root /tmp/kohakumoss-demo --runs /tmp/kohakumoss-demo/runs/moss \
  --state /tmp/kmstate --host 127.0.0.1 --port 8791 --token test --no-open

# API 冒烟
curl -s -H 'X-UI-Token: test' 'http://127.0.0.1:8791/api/heatmap?days=365' | head -c 400
curl -s -H 'X-UI-Token: test' http://127.0.0.1:8791/api/queue
curl -s -H 'X-UI-Token: test' http://127.0.0.1:8791/api/profile

# 截图（三视图 × 三主题 × 两模式）
chromium --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
  --window-size=1440,1000 --virtual-time-budget=9000 \
  --screenshot=/tmp/km-profile.png 'http://127.0.0.1:8791/?token=test#/profile'
fuser -k 8791/tcp
```

完成定义：8 条任务各有可指认的界面与测试证据；无回归；三套主题在两种模式下都可用；文档已更新。
