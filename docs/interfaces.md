# KohakuMoss UI 接口参考

本页按当前源码（2026-10-24）整理 UI 对外接口：HTTP、SSE、命令行及训练器 CLI/文件契约。用于本机客户端集成，不是训练器内部函数百科，也不承诺版本化公共 SDK。标注工作台的跨层契约、实现状态与验收记录见 [标注工作台](annotation-workbench.md)。历史设计见 [工作室实施计划](plans/2026-10-22-studio-heatmap-queue-i18n.md)，使用说明见 [README](../README.md)、[训练主页](profile.md)；计划中的字段或行为与本页不同时，以当前实现为准。

## 1. 通用 HTTP 约定与安全边界

实现：[server/http.py](../src/kohakumoss_ui/server/http.py) 的 `Api`、`Handler`、`make_server`；客户端封装见 [web/api.js](../src/kohakumoss_ui/web/api.js)。

- 默认地址 `http://127.0.0.1:<动态端口>`，没有 `/v1` 前缀。共 **62 个旧 method/path 分支：30 GET、29 POST、3 DELETE**（含下文采集适配器的 21 个路由）；另有 `/api/grabber/annotations/` 的 14 组与 `/api/translation/` 的 16 组 method/后缀，由 schema 表分派且不计入上述显式分支数。其中 `POST /api/publish` 是保留的拒绝分支（405），不是发布能力。未实现 WebSocket、配置写入、数据集预处理或模型上传 API。
- `/api/` 下所有 GET，以及所有 POST/DELETE，要求 token。认证优先级为查询参数 `token` → `X-UI-Token` 请求头 → `km_token` cookie；优先项非空但错误时不会继续尝试下一项。token 由启动时随机生成，也可指定；它在服务器生命周期内可以反复使用，并非一次请求后失效。
- `GET /` 也验证 token；成功设置会话 cookie `km_token`（`Path=/; HttpOnly; SameSite=Strict`，无 Max-Age、无 Secure）。其他静态资源直接从 `web/` 提供，无 token 校验；`/assets/` 前缀会被剥掉，路径 resolve 后不能逃出 `web/`。`GET /favicon.ico` 返回 204。不要在静态目录放秘密。
- JSON 成功响应通常 HTTP 200，直接返回对象，无统一 `data` 包装；**例外**是下文标注/翻译工作台前缀，固定 `{ok,data}` envelope。`Content-Type: application/json`、UTF-8 编码、`Cache-Control: no-store`。时间戳通常是 Unix 秒（浮点）；日期是服务器本地日期 `YYYY-MM-DD`。没有统一分页协议；只有 grabber preview 支持 private/no-cache 与 ETag/304。
- 非上传 POST 必须是 JSON object，空 body 按 `{}` 处理；上限 **1,000,000 字节**，依据 `Content-Length` 读取，非 multipart、非 chunked 上传协议。建议始终发正确的长度与 `Content-Type: application/json`；服务并不以 Content-Type 判断 JSON body。
- 布尔字段请传 JSON `true/false`：`force`、同步 `refresh` 等使用 Python truthiness，字符串 `"false"` 仍被视为真。查询参数多值仅取首值，空查询值经 `parse_qs` 被忽略。

### 错误与业务状态

| HTTP 状态 | 含义与响应 |
| --- | --- |
| 400 | `ValueError` / `KeyError`、非法 JSON、对象类型错误、非法路径/字段/条目状态；`{"error":"具体原因"}` |
| 403 | token 缺失或不匹配：`{"error":"bad token"}`；根页面失败返回纯文本而非 JSON |
| 404 | 未知接口或 GET/普通 POST 的 `FileNotFoundError`；`{"error":"not found: ..."}` 或 `unknown endpoint ...` |
| 405 | 合法认证及 JSON body 下，`POST /api/publish` 返回 `{"error":"publish status is read-only; query Civitai"}` |
| 413 | JSON body、头像或背景上传超限 |
| 500 | 其他异常，`{"error":"异常类型: 原因"}`；DELETE 未专门捕获 `FileNotFoundError`，此类异常也进入 500 |

未实现的方法（如 PUT/PATCH/HEAD/OPTIONS）继承标准库默认 501/HTML 行为，不是统一 405/JSON。`ok:false`、`stopped:false`、Civitai 的非空 `error` 或 doctor payload 中的 `error` 仍可能以 **HTTP 200** 返回，客户端必须检查业务字段。

这是具备本机文件读取与进程启停权限的可信桌面服务，**不要绑定公网**。没有 TLS、用户角色、速率限制或跨域 API 配置；token 不是沙箱。配置加载在训练器解释器的子进程内执行 Python 配置，check/doctor 可能导入 GPU 相关库。只加载可信配置。路径护栏不是所有入口统一施加的：手动启动与 config/load/check 限制 `configs/`，但队列草稿只要求非空 config，调度器直接交给 `launch.start`，不经过 HTTP 的 `_guard_config`。API 也会返回本地绝对路径。

## ArtistSourceGrabber 采集接口（新增）

完整字段、服务生命周期、凭据白名单与限制见 [采集接入](grabber.md)。适配器只使用 ASG HTTP/文件契约，不嵌旧页面，也不导入采集器 Python 模块。以下均继承同一 UI token 保护，缺失或错误 token 为 403。

| 方法 | `/api/grabber/` 后缀 | 约定 |
| --- | --- | --- |
| GET | `status` | `{available,running,managed,root,download_dir,error,pid,port}`；缺失/离线仍为 200，不自动启动 |
| POST | `service/start` | `{}` → 同一 status；从受管子进程 stdout 获取 8710..8719 端口并实际探测，不接管同端口外部服务 |
| GET | `sources` | 原 `{sources:[元数据],capabilities:{images_only:true}}`；保留来源 `query_types/search_query_types` 字符串数组，旧服务可能无能力字段 |
| POST | `source/test`、`artists/search`、`tagger/test` | 单来源 / 嵌套 tagger_config 原业务形状；合并保存配置，保持 HTTP 200 `ok:false` |
| POST | `task/start`、`task/stop` | 映射 `/api/start`、`/api/stop`，`{ok:true}` 仅代表接受/请求停止；无历史队列 |
| GET | `progress?logs=0&items=0` | 原 snapshot 与计数；本 UI 只接受非负整数 offset |
| POST | `session/open`、`session/check` | `{source:'twitter'|'pixiv',artist?}`，映射 `/api/x/session/...` 或 `/api/pixiv/session/...` |
| GET | `file?path=<ASG /files/...>` | downloads 内安全 raster/txt 原始字节，最多 64 MiB，no-store；禁止 URL 代理、穿越、symlink、SVG/HTML；带 nosniff + sandbox |
| GET | `preview?path=<ASG /files/...>&size=thumb` | thumb/detail 最长边 384/1600，不放大，EXIF 方向/透明/动画首帧；成功 private,no-cache + ETag，鉴权且安全打开源文件后可 304；错误 no-store，400 参数/413 超过 32 MiB 或 6400 万像素/422 解码/503 无 Pillow；不自动传原图 |
| GET / POST | `credentials` | GET query source；POST `{source,values,clear}`；安全结果 `{source,values:非秘密配置,configured:秘密字段名数组}` |

服务显式启动；UI 退出不停止 ASG，重启不接管旧服务，避免意外中断采集。status 的 pid/port 是诊断扩展，不是可靠任务 ID。来源仅限 ASG 已注册的 8 项；凭据另支持 `tagger:openai_compatible` / `tagger:local_onnx`。凭据原子写 0600，空秘密保留，clear 显式删除；不会加入前端本次配置或错误对象。响应保持原业务结构但脱敏敏感字段、配置对象和已知秘密值。

上游 400/401/403/404/409/422/429 保留，其余 HTTP 失败转 502；不可达/超时 503。回环请求禁用系统代理且拒绝所有重定向。不支持旧 tagger 对象选择器/别名及带用户名密码/query 的 endpoint/proxy。源站访问、登录、下载、模型测试只由用户明确操作触发。

多来源 `task/start` 接受 `sources:[ID,...]`（1–8 项，白名单、顺序去重）、`source_configs:{ID:{artist,query_type,rating,...来源专有字段}}`，保留原单源 source 契约。每源独立合并已保存凭据/选项，拒绝顶层来源认证、未选中来源配置、来源内 tagger/images_only 等公共覆盖。每源 artist 映射必须显式提供，不猜 X/Pixiv 身份、不跳过选中来源。tagger 仅全局合并；test/search 仍单来源。运行中 ASG 明确声明 multi_source:false 则拒绝，未声明时兼容旧多源实现。

采集 start 新增 `images_only:boolean=false`（亦接受明确布尔字符串，非法值 400）。true 时不合并/发送已保存 AI 配置，强制关闭打标；ASG 不读取或改动 `.txt`，结果标签数组为空、`caption_url:""`、`tag_status:"skipped"`，重复只清理新下载的冗余图。适配器 start 前以回环 sources 检查 `capabilities.images_only===true`，旧 ASG 缺少能力时返回 409 并要求更新重启，而非静默忽略。来源查询与候选能力的区别及完整表格见 [采集接入](grabber.md#仅下载图片与来源能力)。UI 后端与 ASG 都需用户自行重启以加载 Python 修改，不自动重启正在运行的服务。

### 本地模型与下载图库

| 方法 | `/api/grabber/` 后缀 | 请求 / 响应 |
| --- | --- | --- |
| POST | `models/scan` | `{directory?:string}` → `{directory,models:[{name,model_path,tags_path:null或string}],truncated}`；本地有界扫描，不依赖 ASG 在线 |
| GET | `library/groups` | → `{groups:[{name,image_count,tagged_count,bytes,mtime}],truncated}` |
| GET | `library/items?group=...&offset=0&limit=60` | group 为单级非隐藏目录名，offset 非负整数、limit 1–100 → `{group,items:[{filename,path,image_url,caption_url,tags,has_tags,size,mtime}],total,offset,limit,truncated}` |
| POST | `library/retag/start` | `{group,mode:'merge'或'replace'或'missing',tag_format:'comma'或'space',tagger_id,tagger_config,confirm_replace,confirm_remote}` → `{ok:true,job}` |
| GET | `library/retag/progress` | 当前 job 对象，未创建为 `{status:'idle'}` |
| POST | `library/retag/stop` | `{id?:string}` → `{ok:true}`；协作停止，仍需轮询终态 |

模型扫描默认使用保存 ONNX 路径的现存父目录，否则 `<ASG>/models`；显式目录经 expanduser/resolve 检查，不存在/非目录 404、非法路径 400、不可读 403。最多两层子目录、4096 条目录项、256 个 ONNX、2 秒遍历预算，隐藏项/符号链接不跟随；截断返回 truncated。只配同目录 selected_tags.csv（优先）或同 stem CSV，否则 null；不创建目录、不读模型内容、不下载/加载推理。这不是文件上传/原生文件选择对话框，仍可手输 model_path/tags_path 并保存。

图库固定代理至 ASG `/api/library/...`，只用 urlencode 构造分页 query，媒体仍通过受 token 保护的 UI file 路由。先要求 `capabilities.library===true`；retag 另要求 `retag===true`，缺失或不为 true 返回 409 更新重启提示。retag 仅合并所选打标器的保存配置，不发送来源凭据、不继承采集 images_only；none 拒绝，replace 必须 confirm_replace=true，OpenAI-compatible 必须 confirm_remote=true（包括用户配置的本地兼容服务），确认必须为 JSON boolean。job 字段为 `id,status,group,mode,total,processed,written,skipped,failed,current,error,backup_dir,logs`；备份和文件写入以及与采集互斥由 ASG 实现，UI 不重复写文件。

**当前任务结果不是历史图库**：progress 是最近采集任务的内存快照；library 是 downloads 下已有分组的磁盘索引，不是任务历史或队列。

## 标注工作台与翻译接口（新增）

跨层契约、实现状态与验收记录见 [标注工作台](annotation-workbench.md)，采集侧路由说明见 [采集接入](grabber.md#标注工作台与翻译annotations_v1)。两组前缀由 `Handler._workbench` 在进入旧 `/api/*` 分支前独立分派，使用比旧接口更严格的框架：

- 认证失败为 **403 `AUTH_REQUIRED`** 结构化错误（旧接口是 `{"error":"bad token"}`）；GET 只允许既有 UI token（query 参数、`X-UI-Token` 或 cookie），且 `token` 不转发上游；POST 除认证 token 外不接受 query 字段。
- POST 必须 `Content-Type: application/json`（可带 UTF-8 charset）、单一十进制 `Content-Length`，body 上限 1,000,000 字节；响应上限 16 MiB。POST 省略/重复 `Content-Length`、任一方法带 `Transfer-Encoding`、fragment、重复或畸形 query 参数直接 400。
- 只支持 GET/POST；其他方法（含 DELETE/PATCH/PUT/OPTIONS/HEAD）经 `Handler.send_error` 返回结构化 405 `METHOD_NOT_ALLOWED`，不是 HTML 501。
- 成功 `{"ok":true,"data":...}`；失败 `{"ok":false,"error":{"code","message","details","retryable"}}`。`message` 只作诊断，逻辑以 `code` 为准；`details` 仅含 ID、计数、相对路径、字段名与白名单上限。

| 前缀 | 实现层 | method/后缀 | 说明 |
| --- | --- | --- | --- |
| `/api/grabber/annotations/` | [contract/annotations.py](../src/kohakumoss_ui/contract/annotations.py) `AnnotationContract` → [contract/grabber.py](../src/kohakumoss_ui/contract/grabber.py) `Grabber.annotation_request` | 7 GET + 7 POST | 先做完整 schema 校验，再读受管 ASG `/api/sources` 要求 `capabilities.annotations_v1 === true`，然后转发 ASG `/api/annotations/<后缀>`；固定 30 秒超时，不自动重试 mutation |
| `/api/translation/` | [contract/translation.py](../src/kohakumoss_ui/contract/translation.py) `TranslationService` | 7 GET + 9 POST | UI 本地实现；词典 probe、分段、费用上限与 jobs 由 UI 服务负责，ASG 没有翻译路由 |

| 方法 | `/api/grabber/annotations/` 后缀 | 成功 status | 说明 |
| --- | --- | --- | --- |
| GET | `snapshots` | 200 | 冻结快照分页；limit 必须等于创建值 |
| GET | `candidates` | 200 | `{itemId,candidates}`；可选 `sharedPath` |
| GET | `documents` | 200 | 完整 Document；可选 `expectedVersion` |
| GET | `plans`、`plans/rows` | 200 | Plan 元数据 / 分页行；`includeDiff` 只接受字符串 `true` 或 `false` |
| GET | `jobs`、`jobs/rows` | 200 | Job / 分页结果行 |
| POST | `snapshots` | 201 | 创建快照，首 offset=0 |
| POST | `bindings/confirm`、`bindings/select` | 200 | 版本化 metadata mutation |
| POST | `plans`、`undo/plans` | 201 | 编辑计划 / 撤销计划 |
| POST | `plans/apply` | 202 | `{job}`，后台执行 |
| POST | `jobs/stop` | 200 | 合作停止指定作业 |

| 方法 | `/api/translation/` 后缀 | 成功 status | 说明 |
| --- | --- | --- | --- |
| GET | `settings`、`dictionary`、`view` | 200 | masked settings / 本地词典状态 / 文档译文视图 |
| GET | `plans`、`plans/segments`、`jobs`、`jobs/segments` | 200 | 翻译计划与作业分页 |
| POST | `settings`、`credentials` | 200 | 平铺字段 settings；apiKey 只回 `hasKey/keyHint` |
| POST | `dictionary/import`、`dictionary/install`、`dictionary/update` | 202 | `{job}`；`dictionary/update` 需 `expectedDictionaryVersion` |
| POST | `plans`、`jobs/start`、`jobs/stop`、`manual` | 201 / 202 / 200 / 200 | 计划、启动、停止与人工译文保存 |

ASG 端同一组 14 个后缀挂在 `/api/annotations/`，并要求 `ANNOTATIONS` 已初始化；未初始化时该前缀返回结构化 409 `UNSUPPORTED_CAPABILITY`，方法不允许时 405 `METHOD_NOT_ALLOWED`。旧 ASG 缺 `annotations_v1` 时 UI 返回 409 并提示更新、重启受管服务，不静默降级；UI `GET /api/grabber/sources` 透明保留 ASG 的 `capabilities` 字段，客户端可据此判断。

翻译状态位于 UI 私有 `KOHAKUMOSS_UI_STATE/annotations/`（目录 0700、文件 0600）：`translations/`、`translation-plans/`、`translation-jobs/`、`requests/`、`translation-cache.json`、`translation-settings.json`、`dictionary/`、`translation-credentials.json` 与 `owner.lock`。译文不写图旁 sidecar、不写 ASG 源文件；API key 不进入响应、日志、ASG 请求或 provider prompt。

### 工作台错误码

下表是 `contract/annotations.py` 的 `ERROR_STATUSES`（UI 桥与翻译服务共用的错误白名单），逐个 code 对应实际状态码。

| status | code |
| --- | --- |
| 400 | `INVALID_REQUEST`、`INVALID_JSON`、`UNSAFE_PATH` |
| 403 | `AUTH_REQUIRED`、`UNSAFE_STORAGE` |
| 404 | `NOT_FOUND` |
| 405 | `METHOD_NOT_ALLOWED` |
| 409 | `BUSY`、`UNSUPPORTED_CAPABILITY`、`SNAPSHOT_TRUNCATED`、`SNAPSHOT_CHANGED`、`VERSION_CONFLICT`、`BINDING_STALE`、`AMBIGUOUS_SIDECAR`、`IDEMPOTENCY_CONFLICT`、`PLAN_CONSUMED`、`RETAG_PROTECTED`、`RECOVERY_REQUIRED`、`DICTIONARY_MISSING`、`SETTINGS_CHANGED`、`TRANSLATION_STALE` |
| 410 | `SNAPSHOT_EXPIRED`、`PLAN_EXPIRED` |
| 413 | `BODY_TOO_LARGE`、`LIMIT_EXCEEDED`、`DOCUMENT_TOO_LARGE` |
| 422 | `INVALID_ENCODING`、`INVALID_DOCUMENT`、`DUPLICATE_KEY`、`SCHEMA_REQUIRED`、`SCHEMA_MISMATCH`、`INVALID_OPERATION`、`DICTIONARY_INVALID`、`DICTIONARY_TABLE_REQUIRED` |
| 429 | `JOB_LIMIT` |
| 500 | `INTERNAL` |
| 502 | `UPSTREAM_PROTOCOL`、`TRANSLATION_PROTOCOL`、`REMOTE_FAILED` |
| 503 | `UPSTREAM_UNAVAILABLE` |
| 504 | `UPSTREAM_TIMEOUT`、`REMOTE_TIMEOUT` |
| 507 | `STORAGE_FULL` |

`LIMIT_EXCEEDED` 的计划预算 details 为 `{"maximum": 25}`（秒）。UI 桥对上游非法/超大/HTML 响应统一 502 `UPSTREAM_PROTOCOL`，受管 ASG 不在或连接失败 503 `UPSTREAM_UNAVAILABLE`，超时 504 `UPSTREAM_TIMEOUT`；超时的 POST 结果未知，必须用同一 `requestId` 对账，客户端不得自动换 key 重发。`retryable:true` 的实现范围：ASG 只有 `BUSY`；UI 桥为 `UPSTREAM_UNAVAILABLE`、`UPSTREAM_TIMEOUT`；翻译服务为 `REMOTE_TIMEOUT`、`BUSY`、`JOB_LIMIT`（`UNSUPPORTED_CAPABILITY` 明确为 false）。

## 2. GET 接口清单

表中的 query 均不包含通用认证参数；`—` 表示没有业务 query。

| 方法与路径 | query / 默认 / 限制 | 200 响应与语义 |
| --- | --- | --- |
| `GET /api/state` | — | `{trainer, server_time, knob_script}`；trainer 字段见下文 |
| `GET /api/knobs` | `script=train`；任何非 `train` 值都选择 sample 脚本，不是严格枚举校验 | `{script,sections,count,names}`，只做 AST 扫描，不 import 脚本；script 是文件名 |
| `GET /api/configs` | — | `{configs:[ConfigEntry],user:[ConfigEntry],recipes:[ConfigEntry]}`；扫描 `configs/**/*.py`，排除 `__pycache__`；后两项按 dir 前缀 `user` / `moss/recipes` 筛选 |
| `GET /api/config/load` | `path` 必填，resolve 后在 trainer 的 `configs/` 内且必须存在 | `{path,values}`；values 为解析后 globals 的 JSON 化结果，不保证只有 knob；非 JSON 值转 repr |
| `GET /api/config/check` | 同 load 的 path | `{ok:boolean,output:string}`；配置工具缺失也返回 200/ok:false，输出最多末尾 4000 字符 |
| `GET /api/runs` | — | `{runs:[RunSummary],runs_dir,server_time}`；最近活动优先，包含目录尚未创建的 managed run |
| `GET /api/run` | `name` 为输出根下一个存在的直接子目录名 | `{summary,metrics,config,logs,checkpoints,samples,exports,grid,server_time}`；metrics 最多 800 点，logs 最多 400 行；不支持通过 query 改 limit |
| `GET /api/run/log` | `name` 同上；`offset=0` 为字节偏移，须可转 int | `{offset,lines:[string]}`；返回新的完整行边界偏移，详见文件契约 |
| `GET /api/doctor` | `refresh=0`，仅 `1/true/yes` 为真 | 训练器原始 JSON 对象或 `{error}`，附加 `{preflight:{blocking:[{what,fix}],warnings:[{what,fix}],configs},checked_at}`；60 秒缓存 |
| `GET /api/datasets` | `refresh` 同 doctor | `{datasets:[Dataset],scanned_configs:[path],failures:[{config,error}],scanned_at,root}`；30 秒缓存，仅扫描不预处理 |
| `GET /api/file` | `name` 同 run；`path` 为该 run 内文件路径，resolve 不得逃出该目录 | 原始文件字节，MIME 按文件名推断，未知用 `application/octet-stream`；不限于图片/模型，无 Range/分块下载实现，整文件读入内存 |
| `GET /api/appearance` | — | 完整 Appearance 对象，含计算得到的 backgroundUrl |
| `GET /api/appearance/background` | `v` 仅客户端 cache-buster，服务端忽略 | 已上传图片字节（PNG/JPEG/WebP）；缺失 404，不代理外部背景 URL |
| `GET /api/profile` | — | 完整 Profile 对象，含 stats、公开 civitai 状态 |
| `GET /api/profile/avatar` | `v` 同背景 | 已上传头像字节；缺失 404 |
| `GET /api/heatmap` | `days=365`，非法整数回退 365，钳制 1–3660；可选 `year` 整数 1970–当前年，优先于 days，非法 400 | `{days:[{date,steps,seconds,runs}],totals:{days,steps,seconds,runs},streak,longest,years,start,end,today,generated_at}`；按年返回完整日历，未来日为零 |
| `GET /api/history` | — | `{runs:[{name,started_at,steps,seconds,status,exports,datasets:[{path,source,name}],algo,dim,lr}],server_time}`；datasets 来自保存的 config，不从 run 名猜测 |
| `GET /api/notebook` | — | `{items:[NotebookItem]}` |
| `GET /api/queue` | — | QueueView（见下文） |
| `GET /api/publish` | 仅 `refresh=1` 强制刷新 | `{publish,civitai,error,cached,count}`；已设置 username 时可能主动联网；count 未同步时 null，否则按 modelId 去重的匹配数 |
| `GET /api/events` | 可选 `name`；不提供时用于队列通知 | SSE 流，不是 JSON 文档，见第 5 节 |

### 基础读取模型

源码：[root.py](../src/kohakumoss_ui/contract/root.py) `TrainerRoot.info`、[knobs.py](../src/kohakumoss_ui/contract/knobs.py) `scan_script`、[configs.py](../src/kohakumoss_ui/contract/configs.py) `list_configs/load_config/check_config`、[runs.py](../src/kohakumoss_ui/contract/runs.py) `summarize/detail`、[datasets.py](../src/kohakumoss_ui/contract/datasets.py) `scan`。

- `trainer`：`{root,name,version,python,kogine,config_tool,doctor_tool,runs_dir,train_script}`；不可用的工具为 null，未知 version 为 `"unknown"`，`train_script` / `knob_script` 为 `scripts/train/moss.py`。
- `ConfigEntry`：`{path,name,group,dir,doc,mtime}`，path 相对 trainer root、dir 相对 configs，doc 为模块文档字符串首行。
- knob 响应：`sections:[{title,knobs:[{name,default,value,literal,type,comment,line}]}]`；default 是源码表达式文本，value 是可 JSON 化的字面值或 null，type 为注解文本或 null，line 为源码行号。count/names 动态取自脚本，不将历史 143/144 数量当作固定契约。sample 路径为 `scripts/sample/sample.py`。
- `RunSummary`：`{name,path,status,step,steps,progress,loss,lr,grad_norm,tokens_per_s,vram,step_time,started_at,last_activity,checkpoints,samples,exports,config,managed}`。checkpoints/samples 是目录数量，exports 是文件名数组；`progress=step/steps`，steps 为零时为 0，不强制钳制到 1。指标缺失为 null。config 摘要含 `ALGO,DIM,LR,STEPS,PRECISION,MEMORY_PROFILE,OUTPUT_DIR,RUN_NAME`；managed 为 null 或 `{pid,config,command,log_path,started_at,alive}`。
- run 状态不是队列状态：有 managed 记录时只按 alive 得到 `running/stopped`；未管理时达到 STEPS 为 `done`，有指标但未完成为 `stopped`，仅 log/config 为 `unknown`，否则 `empty`。因此 managed 进程退出后即使完成 STEPS，也可显示 stopped，而队列显示 done。
- run detail 的 config 为完整保存配置；checkpoints 为 `[{name,step,size,mtime,exports}]`；samples 为 `[{name,step,files:[{name,size}]}]`；exports 为根 export 文件名数组；grid 为 `samples/grid.png` 是否存在。
- `Dataset`：`{source,path,configs,options,template,absolute,exists,images,captions,files,truncated,cache:{present,files,bytes}}`。从 `user*`、`moss/data`、`moss/recipes`、`moss/_base` 配置的对象式 DATASETS 获取，按 `(source,path)` 去重；每个目录统计最多 50,000 个文件（truncated 表示截止），cache 目录统计独立。字符串式 DATASETS 仅 history 兼容，不由此扫描器识别。

## 3. POST / DELETE 接口清单

| 方法与路径 | 请求 | 响应 / 限制 |
| --- | --- | --- |
| `POST /api/run/start` | `{config,overrides?,name?}`；config 必填且在 configs 内；overrides 默认 `{}`，非空非对象为 400；键值转字符串；name 可省略 | `{name,pid,alive,command,log}`，只表示启动后的短时存活；name 优先覆盖 RUN_NAME，否则取 overrides/配置 RUN_NAME，再回退 `run`；禁止空名、`.`、`..`、斜杠或反斜杠 |
| `POST /api/run/stop` | `{name,force?:false}`；name 必填 | `{stopped,pid?,signal?,reason?}`，不管理/已退出也返回 200；不是训练暂停 |
| `POST /api/appearance` | Appearance 的部分字段，见下文 | 合并归一化后的完整 Appearance |
| `POST /api/appearance/background` | **原始字节**，Content-Type 为 `image/png` / `image/jpeg` / `image/webp`，长度 >0 且 ≤25×1024×1024 | 完整 Appearance；替换单张文件，自动启用背景并改为本地图片 URL；MIME + magic 校验，可选 Pillow verify |
| `DELETE /api/appearance/background` | 无 body | `{"ok":true}`；移除本地图，清空背景 URL 并禁用背景，不是返回 Appearance |
| `POST /api/profile` | `{displayName?,favorite?,pins?}` | 完整 Profile；忽略其他写入字段（包括 publish/stats/civitai） |
| `POST /api/profile/avatar` | 原始 PNG/JPEG/WebP 字节，长度 >0 且 ≤5×1024×1024 | 基础 Profile（含 avatarUrl，但没有 HTTP get_profile 附加的 stats/civitai）；MIME + magic 校验，无 Pillow verify |
| `DELETE /api/profile/avatar` | 无 body | 完整 Profile，avatarUrl 变为空字符串；不是 `{ok:true}` |
| `POST /api/notebook/add` | `{item:NotebookDraft}`，也支持直接提交 draft 对象；title 必填 | 返回全部 `{items}`，新条目插到首位，上限 500 |
| `POST /api/notebook/update` | `{id,patch:object}` | 全部 `{items}`；找不到 id、空 title、未知 status 为 400 |
| `POST /api/notebook/remove` | `{id}` | 全部 `{items}`；找不到 id 为 400 |
| `POST /api/queue/add` | `{draft:{config,name?,overrides?,notebookId?,steps?,title?}}` | QueueView；config 非空，上限 200；无 name 时由 title 生成 slug，否则启动时解析配置名 |
| `POST /api/queue/move` | `{id,direction?:"up"}`，up/down | QueueView；与相邻项交换，边缘无操作，不仅限 waiting |
| `POST /api/queue/update` | `{id,patch:{name?,config?,overrides?,steps?}}` | QueueView；仅 waiting 可编辑，config 不能清空 |
| `POST /api/queue/remove` | `{id}` | QueueView；starting/running 不可删，需先 cancel |
| `POST /api/queue/control` | `{action,id?}`；action 为 pause/resume/retry/cancel/clear-done | QueueView；retry/cancel 要 id，具体语义见队列模型 |
| `POST /api/publish` | JSON object | 固定 405，只读发布查询，不支持人工标记或上传 |
| `POST /api/civitai/credentials` | `{username,apiKey?}`；username 去空白、截到 100 字符且非空；key 截到 200 | `{username,hasKey,keyHint,lastSync}`；仅存储，不远程验证。省略/null key 且帐号不变则保留，换帐号则清空；显式空字符串清 key；换帐号清旧快照 |
| `DELETE /api/civitai/credentials` | 无 body | `{username:"",hasKey:false,keyHint:"",lastSync:0.0}`；重置整个帐号、key 和缓存 |
| `POST /api/civitai/sync` | `{refresh?:false}`；必须先设置帐号，body 不支持选择 username | `{username,syncedAt,models,publish,cached,error}`；可能联网，错误可为 200 的非空 error |

### 3.1 Appearance

源码：[contract/appearance.py](../src/kohakumoss_ui/contract/appearance.py) `DEFAULT/save/_normalize/_decorate`。

| 可写字段 | 默认 / 合法值 |
| --- | --- |
| theme | `teletype`；kohaku/soul/teletype，非法回默认 |
| mode | `light`；light/carbon，非法回默认 |
| lang | `auto`；auto/en/zh，保存未知值时保留现值 |
| glass | `false`；只接受 JSON boolean，否则归一化为默认 |
| surfaceOpacity | `0.82`，数值钳制 0–1 |
| background.enabled | `false`，boolean |
| background.url | `""`，字符串截到 1,000,000 字符；http://、https://、data:image/ 会直接成为 backgroundUrl，否则使用已上传图片（如有） |
| background.blur | `5`，数值钳制 0–30 |
| background.mask | `0.5`，数值钳制 0–1 |
| colors | 每主题各 `{canvas,panel,text,border,accent}`；值为 `#rrggbb` 或 `""`，非法色归空；空表示继承主题色 |

保存是字段合并，background 子字段及 colors 每主题字段也合并；未知字段忽略。响应另含只读 `backgroundUrl`（可能为 `/api/appearance/background?v=<mtime整数>`）。外部 URL 由浏览器加载，不是服务端抓取或本地文件读取。UI 缩放/密度不是此 HTTP 模型的字段。

### 3.2 Profile

源码：[store/profile.py](../src/kohakumoss_ui/store/profile.py) `read/save/save_avatar`、`Api.get_profile/profile_stats`。

基础 Profile 为 `{displayName,favorite:{run,note},pins:[{run,note}],publish,avatarUrl}`。displayName/run 去空白截到 120 字符，note 到 2000；pins 最多 6 项，各 run 非空且唯一，非法列表为 400。不验证 pin 对应的 run 是否存在。未存 pins 的旧 favorite 会读取为单个 pin；保存 pins 同步第一项到 favorite，空 pins 清空 favorite；同时提交 favorite 与 pins 时 pins 优先。旧 publish 元数据仍可读取，但不能通过 profile 保存更改，也不用于 Civitai 的发布判断。

HTTP 完整 Profile 另加 `civitai:{username,hasKey,keyHint,lastSync}`、`stats:{runs,steps,activeDays,published}`。前三个统计取默认 365 日热力图的 totals，不保证是全历史累计；published 未有同步记录为 null。头像 URL 为 `""` 或 `/api/profile/avatar?v=<mtime整数>`。

### 3.3 NotebookItem

源码：[store/notebook.py](../src/kohakumoss_ui/store/notebook.py) `_blank/_apply/add/update/remove`。

字段：`{id,title,base,dim,steps,lr,dataset,note,tags,priority,status,config,overrides,created_at,updated_at,run_name}`。

- title 去空白最多 200 且非空；base/dataset/note/config 最多 4000 字符；run_name 最多 120。数值 dim/steps 非负整数、lr 非负浮点，转换失败回零；priority 任意整数默认 0。
- tags 接受数组或逗号分隔字符串，去重去空白，最多 20 项、每项 40 字符。overrides 非对象归 `{}`，最多取前 200 项，key/value 文本最多 80/200 字符。
- status 默认 idea，合法为 idea/todo/queued/trained/failed/dropped，未知值 400。文本默认空，列表/对象默认空。
- add 可传 id（截到 64），未传生成 12 位 UUID hex；实现不拒绝重复自定义 id，客户端应让服务端生成。created_at 可指定正时间戳，默认当前时间；updated_at 由服务刷新；update 不允许改 id。
- notebook 仅保存草稿与笔记，不会自动启动训练；队列用 notebookId 建立关联，入队写 queued，完成写 trained，失败或 cancel 写 failed；关联失效不阻止队列执行。

### 3.4 QueueView 与调度

源码：[store/queue.py](../src/kohakumoss_ui/store/queue.py) `view/add/control/tick`、[cli.py](../src/kohakumoss_ui/cli.py) `start_scheduler`。

QueueView 为 `{paused,pausedBy,current,items,version}`：初始 false/null/null/[]/0；current 是第一个 starting/running 条目副本或 null；pausedBy 为 null/user/manual；version 为状态变更计数，无条件更新/CAS 接口。

条目字段为 `{id,name,config,overrides,status,notebookId,steps,step,created_at,updated_at,started_at,finished_at,pid,error}`。name 最多 120、config 最多 500、notebookId 最多 64、error 最多 400 字符；overrides 限制同 notebook；steps/step/pid 为非负整数，未启动时间为 0。add 兼容 runName/run_name、notebook_id；自动名称为 title 的小写字母数字 slug，最多 60 字符。id 由服务生成。

- 串行流转 `waiting → starting → running → done/failed`，另有 canceled。调度先对账，再尝试第一项；服务启动时立即 tick，后台间隔 5 秒。
- paused、有 starting/running 项或任意仍存活 managed 进程时不启动下一项。显式 name 的目标目录已存在则标 failed；name 为空时，启动前没有按最终解析名称做同样的目录检查。不要将此视为通用去重/续训保护。
- pause 只暂停后续调度（pausedBy=user），**不暂停训练进程**；resume 清 paused；retry 只允许 failed/canceled，重置 pid/进度/错误/起止时间到 waiting，不会清理既有训练目录；clear-done 只清 done，保留 failed/canceled。
- cancel 只接受 waiting/active；active 会尝试 stop，但无论停止结果如何都会标 canceled，不能据此断言进程已退出。后续调度仍有 managed-alive 护栏。
- 手动 start 成功后调用 pause_for_manual，**仅队列已有 active 项且尚未 paused 时**置 pausedBy=manual；不是所有手动启动都无条件暂停队列。手动 stop 不自动恢复调度。手动启动本身不提供互斥或同名保护。
- 进程退出后由记录步数判断 done（steps>0 且 step>=steps），否则 failed，不以真实退出码判断；starting 且没有 pid 的失联宽限 30 秒。不要把 UI 关闭当成训练停止。

### 3.5 Civitai 查询结果

源码：[server/civitai.py](../src/kohakumoss_ui/server/civitai.py) `save_credentials/sync/match_local/publish_map`。

`models` 为 `[{id,name,normalized,url,type,createdAt}]`，`publish` 以本地规范化名称为键：`{state,modelId,url,name,ambiguous,manual:false,runs:[runName]}`。state 为 published/draft/unknown。本地候选来自 run 名与根 export 文件名，去扩展名、末尾 `_lora` 后小写折叠；唯一模型匹配为 published，歧义为 unknown，成功快照无匹配为 draft。draft 只意味着该帐号查询结果未匹配，不能证明全站未发布。

缓存 24 小时；网络调用为 HTTPS Civitai models 列表（types=LORA、limit=100、page，从 1 开始），页码循环上界 5、最多 500 个模型，单请求超时 15 秒；没有本地 `/me` 路由，也没有实现远程 `/me` 验证。key 只在出站 Authorization Bearer 使用，响应只回提示末四位（短 key 全遮蔽）。失败返回旧 models/时间及 error，旧 publish 可能仍来自旧快照，客户端应优先处理 error，不把旧标签当成重新确认。同步期间帐号变化会返回 `civitai account changed during query`，不覆盖新帐号。

## 4. 最小调用示例

以下只展示协议，**不会自动执行**。把 `BASE_URL` 设为实际本机地址，把 `UI_TOKEN` 设为启动时给出的 token；配置 path、run name 和 id 均应从 GET 清单中取得，而非照抄不存在的示例。不要公开 token 或带 token 的 URL。

```sh
curl -sS -H "X-UI-Token: $UI_TOKEN" "$BASE_URL/api/state"
curl -sS -H "X-UI-Token: $UI_TOKEN" "$BASE_URL/api/queue"
curl -sS -H "X-UI-Token: $UI_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"item":{"title":"记录一次参数想法","status":"idea","tags":["experiment"]}}' \
  "$BASE_URL/api/notebook/add"
curl -N -H "X-UI-Token: $UI_TOKEN" "$BASE_URL/api/events"
```

配置检查应 URL 编码路径；使用 `/api/configs` 返回的实际路径：

```sh
curl -sS -G -H "X-UI-Token: $UI_TOKEN" \
  --data-urlencode "path=$CONFIG_PATH" "$BASE_URL/api/config/check"
```

此 GET **会运行训练器配置检查子进程**，不是纯静态查询。启动请求结构如下（config/name 为示意，执行会真实启动训练，不是 dry-run）：

```json
{"config":"configs/user/train.py","name":"experiment-01","overrides":{"STEPS":"1000","LR":"0.0001"}}
```

## 5. SSE 事件、重连与关闭

实现：`Handler._sse`，客户端 `subscribeQueue` 见 [web/app.js](../src/kohakumoss_ui/web/app.js)。

`GET /api/events?name=<run>` 返回 `Content-Type: text/event-stream; charset=utf-8`、`Cache-Control: no-store`、`Connection: keep-alive`。每帧是 `data: <JSON>\n\n`，没有 SSE `event:` 命名字段，客户端监听通用 `message` 后按 JSON `type` 分发。

| JSON type | 数据与时机 |
| --- | --- |
| `hello` | `{type:"hello",server_time}`，每次连接首先发送 |
| `tick` | `{type:"tick",server_time,summary,lines?}`，循环每约 1 秒发送（扫描耗时另计）；summary 是 RunSummary 或 null，lines 仅有新增完整日志行时存在 |
| `queue` | `{type:"queue",server_time,paused,pausedBy,current,items,version}`，与本连接记住的 version 不同才发送；是完整快照，不是 patch |

每约超过 15 秒还有注释心跳 `: keep-alive\n\n`，不作为 message 数据。无 name、非法或不存在 name 仍可建立流，summary=null；日志文件在建连时不存在则该连接不再自动开始追踪后来新建的日志。有效日志从**连接当时文件末尾**开始追踪，不重放历史，初始内容应由 `/api/run` 或 `/api/run/log` 获取。

不提供 `id:`、`retry:`、Last-Event-ID 续传或服务端事件存储。浏览器 EventSource 自行执行标准重连；每次重连都是新会话，重新 hello、重新从日志末尾起读，并以当前队列 version 初始化，所以断线期间的日志与队列变动不保证补发；重连后应 GET queue/run 刷新快照。JSON tick.summary 调用 `runs.summarize` 时**未传 managed 状态**，其状态/managed 字段可能与 GET run/runs 不同，应以显式 GET 为准。

训练结束不会自动关闭 SSE；客户端可用 `EventSource.close()`，连接断开/写入失败时服务器返回并结束处理。当前壳级 subscribeQueue 使用 EventSource 的默认重连，没有自定义 error/retry/close 逻辑；它只响应 queue 消息并在队列页延迟 300 ms 重绘。

## 6. 命令行与环境变量

安装入口由 [pyproject.toml](../pyproject.toml) 的 `project.scripts` 注册：`kohakumoss-ui → kohakumoss_ui.cli:main`；`python -m kohakumoss_ui` 同入口。参数以 [cli.py](../src/kohakumoss_ui/cli.py) `build_parser/main` 为准（8 个业务选项及 argparse 自带 help）。

| 参数 | 默认与作用 |
| --- | --- |
| `--root` | 空字符串，自动发现训练器根 |
| `--runs` | 空字符串，不覆盖已有环境；非空写入 KOHAKUMOSS_RUNS，默认根下 runs/moss |
| `--state` | 空字符串，不覆盖已有环境；非空写入 KOHAKUMOSS_UI_STATE |
| `--host` | `127.0.0.1`；支持传其他地址但不推荐，未强制本机绑定 |
| `--port` | 整数 0，由 OS 选空闲端口 |
| `--token` | 空时随机 token；非空固定 token，不要将生产 token 写进脚本/仓库 |
| `--no-open` | false；不打开浏览器，但服务和队列调度仍启动 |
| `--native` | false；pywebview 原生窗口，优先于 no-open；需 native extra |
| `-h` / `--help` | 打印帮助并退出，不启动服务 |

根发现按候选顺序选择首个包含 `scripts/train/moss.py` 和 `configs/` 的目录：显式 root → `KOHAKUMOSS_ROOT` → cwd 及其父目录 → 当前模块的父目录 → cwd 的兄弟 `KohakuMossTrainer`；无效显式 root 不会立即终止，而是继续候选。全部失败 main 返回 2；正常关闭/Ctrl+C 返回 0；其他运行异常未统一转成返回码。native 窗口关闭会 shutdown HTTP 服务；finally 关闭调度与监听，不自动 stop 已启动训练。

| 环境变量 | 解析 |
| --- | --- |
| KOHAKUMOSS_ROOT | 根发现候选，优先级低于有效 --root |
| KOHAKUMOSS_RUNS | 浏览的 run 输出根，expanduser/resolve；不自动改训练配置 OUTPUT_DIR |
| KOHAKUMOSS_PYTHON | 配置加载解释器；否则 `<root>/.venv/bin/python`，不存在则当前 sys.executable |
| KOHAKUMOSS_UI_STATE | UI state 目录，expanduser；--state 可覆盖 |
| XDG_STATE_HOME | 未指定 UI_STATE 时用 `<XDG_STATE_HOME>/kohakumoss-ui`；未设则 `~/.local/state/kohakumoss-ui` |

kogine/config/doctor 工具独立从 `<root>/.venv/bin/` 查找，再回退 PATH；改变 KOHAKUMOSS_PYTHON **不会**同时改变这些 CLI 的选择。发布元数据声明 Python ≥3.10，但 root.py 直接 import `tomllib`；该模块在标准库 Python 3.11 起提供，未见 3.10 backport，运行时请留意这个现存边界。

演示工具 [tools/make_demo_run.py](../tools/make_demo_run.py) `main` 非安装入口：`python tools/make_demo_run.py`，选项 `--out=/tmp/kohakumoss-demo/runs/moss`、`--name=demo-run`、`--steps=2000`（至少 100）、`--at-step=0`（≤0 表示完整步数，否则不超过 steps，再按工具最小采样间隔下限处理）、`--ended-ago=0.0`（秒）、`--sample-size=256`（至少 64），以及 help。它会写假 run 文件，不启动训练；只用于明确指定的临时演示目录，不要对真实产物目录运行。

## 7. UI—训练器进程与文件契约

### 7.1 子进程调用

源码：[launch.py](../src/kohakumoss_ui/contract/launch.py) `command/start/stop`、[configs.py](../src/kohakumoss_ui/contract/configs.py)、[doctor.py](../src/kohakumoss_ui/contract/doctor.py)。以下是构造模板，不是已执行结果。

```text
<kogine> run scripts/train/moss.py --config <config_rel> [--set KEY=VALUE ...]
<config_tool> check <absolute_config_path>
<doctor_tool> --json <temporary_json_path>
<python> -c <LOAD_CODE> <absolute_config_path>
```

全部 cwd 为 trainer root；启动无 shell=True，overrides 逐个作为 `--set` 参数，`PYTHONUNBUFFERED=1`；POSIX 独立 session/process group，Windows 创建 process group。stdout/stderr 合并追加到 UI state 的 process 日志，0.15 秒后探测存活并返回。配置加载子进程才 import `kohakuengine.load_config_file`（不在 UI 进程 import 训练器），超时 60 秒；check 超时 300 秒；doctor 超时 240 秒并在 finally 清理临时报告。doctor 额外检查最多前 8 个 user 配置的权重/数据集路径。

停止只针对 UI 注册的名字；POSIX SIGTERM，force=true 为 SIGKILL，优先进程组再回退 pid；Windows 使用 `taskkill /PID <pid> /T`（force 加 `/F`）。最多等待 15 秒，force 时 2 秒；仍存活返回 stopped:false。没有训练 pause；续训只是启动 overrides 中 `RESUME=latest` 的约定，不是独立 resume endpoint。

### 7.2 训练器拥有的输出（UI 读取，不改写）

| run 内路径 | UI 消费格式 |
| --- | --- |
| `config.json` | JSON object，完整保存配置，摘要读取 STEPS/ALGO/DIM/LR 等，history 读取 DATASETS；不可读/非法 JSON 归 `{}` |
| `metrics.jsonl` | 每行 JSON object；UI 保留行的任意额外字段。摘要读 step/loss/lr/grad_norm/tokens_per_s/vram_peak_alloc/step_time；历史统计依赖 time（Unix 秒）和 step（累计非负整数） |
| `log.txt` | UTF-8（错误字节替换），详情取最多尾部 1,000,000 字节中的最后 400 行；增量每次最多 512,000 字节，只返回换行完整行 |
| `checkpoints/step_*` | 目录名后缀转 step；统计目录尺寸/mtime，读取其中 export 文件名 |
| `samples/step_*/*` | 返回文件名及字节数，step 来自目录名；`samples/grid.png` 提供 grid 标记 |
| `export/*` | 根导出文件名，亦用于 Civitai 名称匹配 |

指标解析见 [metrics.py](../src/kohakumoss_ui/contract/metrics.py)：跳过空行、坏 JSON、非对象；摘要读尾部 262,144 字节，曲线读尾部 2,000,000 字节并降采样到最多 800 点（保留首尾）。因此不是无损全历史接口。

日志偏移见 [logs.py](../src/kohakumoss_ui/contract/logs.py) `read_from`：offset 是**字节**而非行号；文件不存在回 offset=0/lines=[]。负数或超过大小时重定位到最多末尾 512,000 字节；积压超限时丢弃更旧内容并跳过起始残行；未写完的末行不推进返回 offset。客户端保存响应 offset 再请求，但不能将此当作日志轮转无损续传保证。

热力图/历史见 [history.py](../src/kohakumoss_ui/contract/history.py)：heatmap 扫描最近 200 个有指标文件的 run；每 run 只读尾部 2,000,000 字节；以 path/size/mtime 缓存 30 秒。相邻样本的 step 差取非负，time 差每对最多 3600 秒，归入较晚样本的本地日；日活动最多 86400 秒。历史的 steps 是尾部末步数，不是热力图的步数差总和；不是 GPU 用量精确账单。

### 7.3 UI 自有状态

目录解析见 [paths.py](../src/kohakumoss_ui/paths.py) `state_dir/process_log`；进程注册见 [contract/state.py](../src/kohakumoss_ui/contract/state.py) `register/status`。目录可配置但实现没有强制禁止放入训练输出，操作者应保持隔离。

| UI state 相对路径 | 内容与权限 |
| --- | --- |
| `runs.json` | `{runs:{<name>:{pid,config,command,log_path,started_at}}}`；重启后用 pid 探测 alive（Linux 排除 zombie），不是可靠进程身份令牌，pid 复用不另行验证 |
| `process/<sanitized-name>.out` | UI 启动子进程 stdout/stderr 与命令记录，不等于 run/log.txt，当前无专用读取 API |
| `appearance.json` | Appearance 可持久化部分；tmp + replace |
| `backgrounds/custom.png/.jpg/.webp` | 单张背景，替换旧图；tmp + replace |
| `profile.json` | displayName/favorite/pins/旧 publish 元数据 |
| `avatars/avatar.png/.jpg/.webp` | 单张头像，0600，原子替换 |
| `notebook.json` | `{items}` |
| `queue.json` | `{paused,pausedBy,items,version}`，current 为响应派生字段 |
| `civitai.json` | `{username,apiKey,syncedAt,models}`；敏感 key 仅在此，API 不原样返回 |

profile/notebook/queue/civitai 使用 [store/jsonfile.py](../src/kohakumoss_ui/store/jsonfile.py) 的临时文件 + fsync + replace，目标文件 0600，坏 JSON 回默认并尝试 `.bak`。**并非所有 state 文件都由此助手写入**：appearance、runs 及背景各自替换，没有同样显式 chmod 0600；也不要把 `.bak` 的权限与目标文件等同。请按整个 state 目录的敏感级别管理备份、目录权限与分享。持久化不是多 UI 实例的跨进程事务协议，应避免多个服务共用一套 state。

## 8. 关键扩展入口与文档维护

- 训练器协议变更集中在 `contract/`：root 路径/工具发现、configs、knobs、runs/metrics/logs、launch、doctor/datasets/history；不要把 trainer 字段或进程命令散入 server/web。
- 增加对外 HTTP 能力时在 `Api` 实现 JSON 模型，在 `Handler._api_get/do_POST/do_DELETE` 明确登记；SSE 改 `Handler._sse`。本页以 **方法 + 路径** 计数，不以内部函数计数；新增时同步错误/状态与字段。
- 替换桌面壳可复用 `make_server(root,host,port,token)` 的本地 HTTP URL；浏览器壳与 [shell/native.py](../src/kohakumoss_ui/shell/native.py) `open_window(url,title)` 无独立 IPC 协议，native 要在主线程运行。它们是源码扩展点，不是已版本化插件注册 API。
- 前端客户端集中在 web/api.js，静态 ES modules，无独立构建链。参数说明数据在 `web/data/knob-docs.json`；不要复制成第二套固定 knob 表。

本页验证范围：对照上述真实源码的路由、CLI 解析器、SSE emit 与数据模型，检查文档本地链接及最小请求示例；没有启动服务器、训练器、GPU 检查或 Civitai 网络请求。2026-10-24 增补标注工作台/翻译接口时，逐个核对了 `contract/annotations.py`、`contract/translation.py`、`contract/translation_schema.py`、`server/http.py` 与 ASG `app.py`/`annotations.py` 的路由字符串与 schema 表，没有调用真实 provider 或写入真实图库。计划文档保留历史意义，不作为当前接口实现的证明。

## GBC 工作区 / GBC workspace (v1)

### 页面和操作边界 / Routes and action boundaries

`#/datasets` 的 **GBC 工作区**入口打开 `#/datasets/gbc`，不改变侧栏编号。环境、生成任务、数据包、Caption 与草稿使用同一工作台语义主题；不增加新主题、壁纸层或 blur。

The **GBC workspace** link on `#/datasets` opens `#/datasets/gbc`. It reuses the existing shell and themes. Its four tabs are Environment, Generation tasks, Dataset packages, and Caption & draft. No process, configuration execution, model download or remote call occurs merely by opening the page.

1. 显式登记现有 `input/output/model` 根目录、`generator/data` Python 环境，并勾选信任确认。登记不是执行。版本与 doctor 是独立的显式生成器探测；数据环境通过导入/验证检查。
2. 选择 `replay`（已有轨迹）或有限 `qwen35-local-v1`（本地新图）。后者要求已登记本地 `qwen3_5`、`owlv2`、`gemma3_text` 模型目录，只接受受限安全权重；不接受 Python/任意 engine/CLI 参数/远程模型 ID。能力支持不代表环境已安装，官方配置不保证 8 GiB 可运行。
3. 创建冻结任务 → 显式配置检查 → 读取检查后的 task revision / validationId → 显式启动生成。停止是请求，必须等待作业终态；强停另行确认。退出码 0 仍可能为 partial/unchecked，不等于完整语义验证。
4. 导入标准数据包会复制到新的已授权输出目录并验证；导出必须显式从当前作业 artifact 打包。源文件不覆盖、不修复。查看样本页不代表完整验证。
5. 选择数据集、非空分片集合、可信 base config 与完整 caption options，明确修正居中裁剪/不翻转冲突后创建不可变草稿。不会借用多卡示例配置；基础配置必须用户选择。高级 JSON 错误保留输入，服务执行权威字段/类型校验。
6. `#/new?draftId=<id>&revision=<revision>&effectiveHash=<hash>` 只读展示完整 typed values，独立执行精确草稿 check。引用由 `{draftId,revision,effectiveHash}` 组成并与服务响应严格核对。这里**不启动训练、不入队、不写记事本**，绝不转为旧 200 字符 override。编辑通过创建新 ID，不修改旧草稿。

Explicit registration precedes execution. Create a frozen task, check it, then explicitly start it using the returned revision/hash/validation ID. Import/export writes new directories and validates the native package. Select a registered dataset and shards, a trusted base configuration and caption settings to create an immutable draft. The dedicated New training query route preserves the exact typed reference and offers only a separate check, not training/queue/notebook actions. Every edit creates a new draft ID; full values never pass through legacy string overrides.

### HTTP 协议 / HTTP protocol

除读取基础配置选项的既有 `GET /api/configs` 外，工作区所有 GBC 请求均使用 `/api/gbc/v1/`。每个请求只发送一个 `X-UI-Token`，不使用 query token 或 cookie。所有写操作使用 JSON object，并由客户端加入 `apiVersion:"gbc-ui-v1"` 和新 `requestId`；不自动重试 POST。网络中断的写操作结果未知，必须刷新任务/资源并使用 requestId 对账，不能盲目重新提交。

All GBC operations use `/api/gbc/v1/`, with exactly one `X-UI-Token` header and credentials omitted. The sole legacy metadata read is `GET /api/configs` for available base configs. POSTs carry `apiVersion` and a unique `requestId`, with no automatic retries. An interrupted mutation has an unknown outcome. JSON envelopes are version-checked: `{apiVersion,ok:true,data}` or `{apiVersion,ok:false,error:{code,message,details,retryable}}`. **HTTP 200 is not inner operation success**; draft checks inspect `check.ok`, `validation.status`, and matching hash/ref.

| Method | Suffix | Request / response summary |
| --- | --- | --- |
| GET | `capabilities`, `settings` | `features/localProfiles/limits`; `{settings:{revision,roots,runtimes}}` |
| POST | `settings` | `expectedRevision`, roots `{path,purpose,confirmTrust:true}` / runtimes `{python,role,confirmExecute:true}`; append trusted registrations |
| POST | `runtimes/check` | `{runtimeId,checkMode:'version'|'doctor',confirmExecution:true}` → `{job}` |
| POST | `tasks` | `{settingsRevision,inputRootId,relativePath,outputRootId,runtimeId,mode,timeoutSeconds?}`; local v1 adds `sourceKind` and `models:{vlm,detect,embed}:{rootId,relativePath}` → `{task}` |
| POST | `tasks/check` | `{taskId,expectedRevision,confirmExecution:true}` → `{job}`; re-read task after completion |
| POST | `jobs/start` | `{taskId,expectedRevision,manifestHash,validationId,confirmRun:true}` → `{job}` |
| POST | `jobs/stop` | `{jobId,expectedRevision,force:boolean}` → `{job}`; stopping is not terminal |
| GET | `tasks`, `jobs`, `datasets` | Optional respective resource ID; collections bounded to latest 100 |
| GET | `jobs/logs` | `{jobId,cursor,maxBytes}` → `{text,nextCursor,dropped,terminal}`; byte cursor, UI reads 32 KiB and retains last 128 KiB display text |
| GET | `requests` | `{requestId}` → receipt / reconciliation record |
| POST | `imports` | `{sourceRootId,relativePath,outputRootId,dataRuntimeId,confirm:true}` → `{dataset}` |
| POST | `exports` | `{artifactId,outputRootId,dataRuntimeId,confirm:true}` → `{dataset}` |
| POST | `datasets/validate` | `{datasetId,expectedRevision,dataRuntimeId,confirm:true}` → updated `{dataset}` |
| GET | `datasets/shards` | `{datasetId,offset,limit}` → `{shards,nextOffset}`; UI pages at most 100 shards |
| GET | `datasets/page` | `{datasetId,dataRuntimeId,offset,limit}` → `{items,offset,nextOffset,total,shard}`; **row** offsets, UI pages at most 20 |
| GET | `graphs` | `{artifactId,offset,limit}` → `{items,offset,nextOffset}`; **JSONL byte** offsets, UI passes server `nextOffset`, never increments by row count |
| GET | `datasets/media` | `{datasetId,dataRuntimeId,shard,key,size?}` → `{media}`; size is `thumb` or `detail`; exact shard/key from dataset page |
| GET | `graphs/media` | `{artifactId,sampleId,size?}` → `{media}`; size is `thumb` or `detail`; exact IDs from graph page, registered data runtime required |
| POST | `training-drafts` | `{datasetId,datasetRevision,shards,baseConfigId,captionOptions,knobPatch?,confirmExecution:true}` → `{draft}` |
| GET | `training-drafts` | `{draftId,revision}` → `{draft}`; no internal dataset path in wire values |
| POST | `training-drafts/check` | `{draftRef,confirmExecution:true}` → `{check}`; real bounded trainer checker, missing weights can return HTTP 200 with `ok:false` |
| POST | `captions/preview` | `{draftRef,sampleId,seed,variant,target?:{width,height}}` → `{preview}`; dataset sampleId is `shard/key` |

`declaredSamples`, `manifestRows`, `validatedSamples`, `actualBytes` 分别展示；null 是未知横线，不是 0。图节点 ID 空字符串是有效根节点；显示叠层仅用 `displayBbox`，不改原始 bbox。节点、边和描述文本均转义，JSON 从不作为 HTML 执行。图像媒体须采用已实现的受鉴权端点，不从图中的任意路径或 URL 取图。

Counts are distinct, and null is never rendered as validated zero. Graph root ID `""` is valid. Overlay geometry uses `displayBbox` without mutating original `bbox`. Graph labels/descriptions are escaped text. Graph inspection supports node/edge-linked selection, zoom and arrow/Home/End focus navigation.

媒体端点已实现，`features.media=true` 只表示协议支持，仍需要可信数据运行环境与解码依赖。返回标准版本化 JSON envelope，`data.media={mimeType:'image/png',encoding:'base64',base64,width,height,sourceWidth,sourceHeight,size,orientation:'stored-image'}`。UI 选择样本后请求 `size=detail`，验证 PNG 类型/签名、像素尺寸与原图几何后转为 Blob URL；不把 token 写入图片 URL，不使用图中的 path/url，不回退传原图或外链。加载、错误和样本切换时不保留上一张图像；路由离开时 abort 并 revoke 对象 URL。

The implemented media endpoints return a versioned JSON envelope containing the above media DTO. The UI explicitly requests `detail` for the selected sample, validates MIME/PNG signature/dimensions against the graph's stored image, then renders a Blob URL. Authentication remains one `X-UI-Token` header with credentials omitted. No file-path/URL fallback or token-bearing image URL exists. Loading/failure clears the previous image; switching samples or leaving the route aborts requests and revokes object URLs.

限制：thumb 最长边 384、detail 最长边 1600，不放大、不裁剪、不进行 EXIF 转置，保持存储图像方向和宽高比。输出 PNG ≤4 MiB，base64 ≤5,592,408 字符；输入 ≤32 MiB、≤4,000 万像素。支持 worker 可解码的 JPEG/PNG/WebP/JPEGXL（后者依赖 codec），拒绝动画、非恒等 EXIF、SVG/HTML。worker 超时 30 秒。成功与错误均返回 no-store、nosniff 和 sandbox CSP；依赖缺失、源变化、超时与解码失败显式报错，媒体能力不意味着真实模型推理可用。

Limits: thumbnail/detail longest edges are 384/1600 pixels, with no upscaling, cropping or EXIF transpose. The output is at most 4 MiB PNG (5,592,408 base64 characters); source input is at most 32 MiB and 40 million pixels. JPEG/PNG/WebP/JPEGXL depend on available worker codecs; animation, nonidentity EXIF, SVG and HTML are rejected. Worker deadline is 30 seconds. Success and error responses are no-store, nosniff and sandboxed. Missing dependencies, changed sources, timeout and decoding errors stay explicit. Full-image `displayBbox` overlays must not use caption-preview crop boxes.

Caption controls expose weighted formats, tag source/weight/drop/shuffle, natural-language short probability, graph keep/drop and fit settings. Advanced full JSON remains available and validated. Preview shows seed/variant, optional expected target, actual planner geometry, character budget and provenance. `hardTokenLimit:false` is intentional: character fitting is not a strict token guarantee; preview RNG row=0 is not an unknown training catalog row. Preview errors retain edits. A changed draft requires a new ID.

路由离开遵循 `__studioCleanup`：abort 请求、清 timer、移除监听并释放媒体 URL；晚响应不得覆盖新页面。后台标签页暂停任务轮询。所有动作防重复忙碌，错误展示稳定 code 与可定位的字段诊断。远程 API capability=false，不创建凭据字段，不外传数据，不在日志或 storage 保存模型凭据；未来支持仍需要单独外传确认。

On route cleanup, requests are aborted, timers/listeners removed and object URLs released; late responses cannot replace another view. Polling pauses while the tab is hidden. Busy guards prevent duplicate actions. Remote API capability is false: no remote credentials UI, no model-credential storage and no image egress. Future remote support still needs explicit egress consent.

### 验证范围 / Verification scope

定向测试 `node tests/gbc_ui.mjs` 运行真实 ES modules 与现有轻量 DOM 测试工具，wire 为内存 fixture；另跑词典/主题门禁与修改 JS 语法检查。最终浏览器主题/窄屏/键盘/真实接口回放由主代理验收。没有声称真实模型生成或训练已验证；不安装依赖、不下载模型、不调用付费 API、不运行真实训练。

The targeted Node tests execute real modules and the existing structural DOM harness with memory-only wire fixtures. They do not certify browser layout or real inference. Final browser verification is separate. Real model generation and training have **not** been verified by this frontend implementation.
