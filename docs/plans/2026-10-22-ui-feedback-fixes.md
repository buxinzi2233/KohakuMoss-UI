# UI 反馈七条修复计划（2026-10-22）

## 目标与验收
把用户 m02275 提出的 7 条反馈逐条落地，不改变三套主题的语言与既有几何。验收 = 每条有一个可复现的判据（DOM 断言 / 像素比对 / 截图），且 `93 tests` 与 `node --check` 全绿。

## 现状与根因（已核对代码）

| # | 反馈 | 根因位置 |
|---|---|---|
| 1 | RENAME / UPLOAD AVATAR 按钮多余 | `src/kohakumoss_ui/web/studio.js:412`（RENAME 按钮）、`:425-427`（demand label+button 上传） |
| 2 | 队列页"空闲"章与两个入口按钮位置散 | `studio.js:654-663`（scheduler 章独占一行）、`:717-722`（空态内 `.q-entry` 两个按钮） |
| 3 | 预检框上下间距与其它页不一致 | `src/kohakumoss_ui/web/app.css:550` `.vhead-sticky{padding:6px 0 7px;margin:-16px 0 12px}` 与 `main{padding:16px 20px 18px}`（`:43`）不匹配；compact 下 `main{padding:10px 14px 12px}`（`:395`）更是错位 6px |
| 4 | 数据集"图片/标注"表头与数值不对齐 | `src/kohakumoss_ui/web/views.js:512` 表头带 `class="num"`，但 `app.css:72` `.led th` 只有 `text-align:left`（无 `th.num` 规则），而 `app.css:74` `.led td.num{text-align:right}` → 表头左、数值右 |
| 5 | 侧边栏顺序与"炼丹"命名 | `src/kohakumoss_ui/web/index.html` nav 顺序（workbench → studio → data → artifacts → system）；`lang/zh.json` 的 `nav.queue=炼丹队列 / nav.profile=炼丹主页 / nav.notebook=炼丹记事本`（全库仅这 3 处含"炼丹"） |
| 6 | kohaku 关玻璃反而发透、开背景后反而实 | `app.css:446-450` 在 `data-bg="on"` 下把 chrome 混成 82%、控件混成 92%（**与 glass 开关无关**）；`teletype` 有 `:482-490` 的"玻璃关=实心纸"覆盖，**kohaku.css 与 soul.css 完全没有 data-bg/data-glass 规则** → 只有 teletype 语义正确 |

关于第 5 条中"数据集功能像复用过往训练配置"的追问：**这是契约决定的设计，不是写错。** `src/kohakumoss_ui/contract/datasets.py` 只有 `_count/_cache_info/scan` 三个函数，数据集来源一律是"配置里声明的路径"（`declared` 列 = 哪些 config 引用了它），cache 由训练器在训练时惰性构建，UI 不写配置也不建缓存（AGENTS.md 边界）。因此**不新增"预处理数据集"页**——后端没有独立的预处理入口，加页面等于造假功能。

## 变更分组

### A. 个人信息就地编辑（只改 `web/studio.js` + `web/app.css`）
- 名字：只读态由 `<div class="pf-name">名字 + RENAME 按钮</div>` 改为 `<button type="button" class="pf-name-edit" data-act="name-edit" title="点名字改名">名字</button>`；编辑态的 `input#pf-name-input` + SAVE/CANCEL 逻辑不动。
- 头像：`<div class="pf-upload"><label for="pf-file">UPLOAD AVATAR<input …></label></div>` 整行删除；改为把 `img.pf-face` / `div.pf-initial` 包进 `<label class="pf-face-pick" for="pf-file" role="button" tabindex="0" aria-label=今天的 studio.profile.upload>`，`input#pf-file` 保持视觉隐藏（`.pf-file{clip-path:inset(50%)}`），并在 label 上监听 `keydown`（Enter/Space）触发 `input.click()`。
- 词典：新增 `studio.profile.name.hint`（en "Click the name to rename" / zh "点击名字可改名"）与 `studio.profile.avatar.hint`（en "Click the portrait to upload" / zh "点击头像上传"）；`studio.profile.name.edit`(RENAME) 与 `studio.profile.upload`(UPLOAD AVATAR) 改为只做 title/aria 文本，不再渲染成按钮文字。
- CSS：`.pf-name-edit{background:none;border:0;padding:0;color:inherit;font:inherit;text-align:left;cursor:text;border-bottom:1px dashed transparent}` + hover/focus-visible 显示虚线；`.pf-face-pick{display:block;position:relative;cursor:pointer}` + focus-visible 2px accent 描边；删除 `.pf-upload` 与 `.pf-actions` 里对应的 spacing。

### B. 队列页头部收拢成一行（`web/studio.js` + `web/app.css`）
- `scheduler` 章从独立一行搬进 `.vhead .meta`：meta 文案变为 `队列 · {n} 项` + 状态章；`pausedBy` 仍跟在章后。
- `.vhead .acts` 增加两个常驻入口（放在控制按钮之前）：`<a class="btn" href="#/notebook">记事本</a>`、`<a class="btn pri" href="#/new">+ 新建训练</a>`。
- 空态 `.empty` 里删掉 `.q-entry` 两个按钮（它们已在头部），只留标题+说明；`.q-entry` 规则随之清理。

### C. 页头间距统一（`web/app.css`）
- `.vhead-sticky` → `padding:16px 0 7px;margin:-16px 0 12px`（默认与 `main` 的 16px 顶距严丝合缝，吸顶时留 16px 纸白）。
- 新增 `html[data-density="compact"] .vhead-sticky{padding-top:10px;margin-top:-10px}`（compact 的 `main` 是 10px）。
- 判据：NEW RUN 与 RUNS 页里 `.vhead` 底边到 `.alert` 顶边的像素距离差 ≤ 1px。

### D. 数据集表对齐（`web/views.js` + `web/app.css`）
- `views.js` 数据集表：`images`/`captions` 的 `<th class="num">` 与 `<td class="num">` 去掉 `num` 类 → 两列双向左对齐（用户明确要左对齐）。
- `app.css` 补 `.led th.num{text-align:right}`：其它页（runs 的 STEPS/LOSS、队列的 PROGRESS）表头与数值从此同侧。
- 判据：`getComputedStyle` 抽样 + 截图；表格不出现横向滚动。

### E. 侧边栏重排与"训练"化（`web/index.html` + `web/lang/zh.json`）
新顺序（编号全局连续，删掉独立的"产物"分组）：

| 编号 | 视图 | hash | 分组 |
|---|---|---|---|
| 01 | 新建训练 | `#/new` | 工作台 |
| 02 | 训练队列 | `#/queue` | 工作台 |
| 03 | 训练记录 | `#/runs` | 工作台 |
| 04 | 数据集 | `#/datasets` | 数据 |
| 05 | 产物 | `#/artifacts` | 数据 |
| 06 | 训练主页 | `#/profile` | 工作室 |
| 07 | 训练记事本 | `#/notebook` | 工作室 |
| 08 | 环境体检 | `#/doctor` | 系统 |
| 09 | 设置 | `#/settings` | 系统 |

- `index.html`：只动 `<nav>` 内的分组与 `a` 顺序、`.idx` 数字；`data-view` 与 `data-i18n` 键不变（除新增/删除的分组标题）。
- `zh.json`：`nav.queue`=训练队列、`nav.profile`=训练主页、`nav.notebook`=训练记事本；`nav.group.artifacts` 键删除（en/zh 同步删，保持键集合相等）。
- 不新增"预处理数据集"页（理由见上）。

### F. 玻璃语义修正（`web/app.css`）
- `:446-450` 改为一条规则：`html[data-bg="on"] :is(.aside,.mast,.foot,.box,.runner){background:var(--panel)}` —— **玻璃关 = 实心面板**，壁纸只在控件间隙与页边距可见。
- 玻璃开（`:459-469`）保持 `color-mix(--panel calc(100% * --surface-alpha))` + chrome 的 `backdrop-filter`；teletype 的 `:482-490` 覆盖保持不动（更具体，5% ink wash 是它的纸感）。
- 重写 `:429-438` 的层模型注释：GLASS 是唯一让表面变薄的开关，CUSTOM BACKGROUND 只决定"有没有壁纸"，两者不再互相串味。

## 测试与验收
1. `env PYTHONPATH=src ../KohakuMossTrainer/.venv/bin/python -m unittest discover -s tests` → 93 OK（`test_i18n.py` 会用到新键；删键要同步 en/zh）。
2. `cp web/*.js /tmp/kmcheck/*.mjs && node --check`（studio/views/app）。
3. ego 实拍（服务照旧 8791 / token test / teletype·light·zh 为主，抽查 kohaku·light 与 soul·carbon）：
   - `#/profile`：名字可点、无 RENAME 按钮；头像区域可点、无 UPLOAD AVATAR 按钮；点击头像触发 `#pf-file.click()`（用 `ego_js` 挂一次性监听断言）。
   - `#/queue`：头部只有一行（项数+状态章 + 记事本/新建训练/四个控制按钮），空态框里没有按钮。
   - `#/new` vs `#/runs`：量 `.vhead` 与 `.alert` 的 `getBoundingClientRect()` 差值。
   - `#/datasets`：图片/标注两列 `text-align:left`，无横向滚动条。
   - 侧边栏：9 项顺序与编号、无"炼丹"字样、产物在"数据"组下。
   - kohaku/light：壁纸开+玻璃关=面板实心；玻璃开=按滑块变薄且 chrome 有模糊。teletype/soul 各拍一张确认未回归。

## 风险与假设
- **F 会改变 kohaku/soul 在开启壁纸时的观感**（面板从半透明变实心）——这正是反馈 6 的修复目标，且与 teletype 一致；如有异议可回退到"仅 kohaku/soul 补覆盖"的保守变体。
- 头像用 `label` 包 `img` 后，`studio.js` 的 `data-act` 委托不受影响（label 的默认行为就是触发 file input），但需要给 `tabindex` 补键盘路径。
- 删 `nav.group.artifacts` 键会牵动 `test_i18n.py` 的键集合断言 —— en/zh 必须同改。
- 假定服务仍是 `--root ../KohakuMossTrainer --runs /tmp/km-runs`（真实训练器 + 空 runs）；数据集页 7 条"未找到"是模板路径，属预期。

## 执行顺序
C → F（纯 CSS，风险低，先验） → D → E（索引与词典） → A → B（studio.js 两处交互） → 全量测试 → 实拍 review。
