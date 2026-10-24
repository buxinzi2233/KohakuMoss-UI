# ArtistSourceGrabber 采集接入

这是 UI 自己的采集工作台与后端 HTTP 适配器，不嵌入或代理 Grabber 的旧网页；训练器、采集器代码均不导入、不修改。适配器位于 `src/kohakumoss_ui/contract/grabber.py`，HTTP 路由位于 `server/http.py`。

## 运行与依赖

照常启动 UI：

```sh
PYTHONPATH=src python3 -m kohakumoss_ui --root ../KohakuMossTrainer --no-open
```

默认采集器根为 `trainer.root.parent / ArtistSourceGrabber`。可用环境变量覆盖：

- `KOHAKUMOSS_GRABBER_ROOT`：可信采集器 checkout 根，须有 `app.py`。
- `KOHAKUMOSS_GRABBER_PYTHON`：明确指定采集器 Python 可执行文件；否则优先采集器 `.venv/bin/python`，不存在时使用当前 UI Python。
- `KOHAKUMOSS_UI_STATE`：UI 状态目录；凭据存入其中的 `grabber-credentials.json`。

本轮安全文件读取与启动日志读取实现面向 POSIX（`openat`/`O_NOFOLLOW`/`pread`）。没有安装依赖、自动加载模型、自动登录或自动采图操作；采集器依赖未安装时启动会明确失败。`available` 仅表示 checkout 的 `app.py` 存在，**不表示依赖就绪**。无需 Node 构建步骤。

点击启动服务或显式调用 `POST /api/grabber/service/start {}` 才会执行：

```text
<selected-python> <grabber-root>/app.py --no-browser
```

运行目录为采集器根，`PYTHONUNBUFFERED=1`，独立进程会话。ASG 自己绑定 `127.0.0.1:8710..8719`；适配器只从其 stdout 的真实地址公告获取端口，再以当前进度 HTTP 响应确认 ready。不扫描/接管已有端口，也不连接手动启动的 ASG。GET status 不隐式启动服务。启动失败/15 秒超时时仅清理此次拥有的子进程，先 terminate，2 秒未退出才 kill；不处理任意记录 PID。

## 生命周期：关闭 UI 不等于停止采集

- 关闭浏览器页面不影响 UI 服务或 ASG。
- 关闭 UI **进程**也明确保留 ASG 子进程和当前任务，既不发送 stop/shutdown，也不发信号，避免上游 daemon 下载线程在服务关闭时被截断。
- stdout/stderr 写入私有、无目录项的临时文件；启动只保留 16 KiB 内存读取窗口，不回显日志（其中可能有第三方错误/凭据）。UI 退出后子进程保有文件描述符，不会因管道关闭产生 BrokenPipe；临时文件占用会持续至子进程退出。此文件未设置总磁盘大小上限。
- `status` 额外返回 `pid`、`port`（无活进程时 null），用于明确当前服务身份。UI 重启**不自动重新接管旧进程**；点击启动会启动新 ASG，使用下一空闲端口，也没有旧任务历史。旧服务仍可独立完成任务；应在关闭 UI 前记录其端口/PID。
- 需彻底退出残留 ASG 时，先通过原服务 `/api/stop` 请求停止并轮询 `/api/progress` 至终态，再向**确认过身份的原端口** `POST /api/shutdown {}`；不要按端口盲杀进程。上游 localhost API 没有 token，本 UI 不扩展 shutdown/force-kill 路由。
- 多页面同一 UI 的 service/start 使用锁，task/start 使用非阻塞锁防止提交竞争；上游已有 preparing/running 任务时仍返回 409。不是持久任务队列、不是自动重试。

## UI API 契约

所有路径均继承 UI 的 token 校验（`X-UI-Token`、会话 cookie 或现有 query token）。缺失/错误为 **403**，不是 401。图片路由也必须认证。UI 与 ASG 的回环 HTTP 不使用系统 proxy，所有重定向都拒绝。来源抓取自己的 `proxy` 配置不受此限制。

| 方法 / UI 路由 | 请求与响应 |
| --- | --- |
| GET `/api/grabber/status` | `{available,running,managed,root,download_dir,error,pid,port}`；缺失/离线为 200，running 必须实际探测，managed 表示此 UI 启动的子进程仍活着 |
| POST `/api/grabber/service/start` | `{}` → 同上；启动失败仍返回 200 状态，不假称 running |
| GET `/api/grabber/sources` | ASG `/api/sources` 的 `{sources:[元数据],capabilities:{images_only:true}}`；透明保留能力字段，旧服务可能缺失 |
| POST `/api/grabber/source/test` | 单来源原配置 → ASG 同名路径；可能 HTTP 200 `{ok:false,error}` |
| POST `/api/grabber/artists/search` | `{source,query,query_type,...来源配置}` → ASG 同名路径 |
| POST `/api/grabber/tagger/test` | `{tagger_id,tagger_config}` → ASG 同名路径；仅显式请求时才可能加载模型 |
| POST `/api/grabber/task/start` | 单来源原配置或 `{sources,source_configs,...公共任务配置}` → ASG `/api/start`；`{ok:true}` 只是接受，非完成 |
| POST `/api/grabber/task/stop` | `{}` → ASG `/api/stop`，只请求协作停止；忽略 force 等额外参数，继续轮询终态 |
| GET `/api/grabber/progress?logs=0&items=0` | 原 snapshot，包含原始完整计数；偏移必须非负整数；新任务重置偏移，任务完成应 `items=0` 拉取最终标签 |
| POST `/api/grabber/session/open` 或 `/session/check` | `{source:'twitter'|'pixiv',artist?:string}` → ASG `/api/x/session/...` 或 `/api/pixiv/session/...`；不会回传 Cookie |
| GET `/api/grabber/file?path=...` | 仅 ASG `/files/...` 路径；返回同源安全图片/文本，见下文 |
| GET `/api/grabber/credentials?source=...` | `{source,values:{非敏感配置},configured:[已保存秘密字段名]}` |
| POST `/api/grabber/credentials` | `{source,values:{...},clear:[字段名]}` → 同一安全响应 |

普通响应保持 ASG 结构，但**敏感字段/配置对象不回传，已知秘密值和常见日志凭据标记会脱敏**。不得依赖 HTTP 200 代表业务成功。上游 400/401/403/404/409/422/429 保留状态，其他 HTTP 失败（包括重定向）转 502，离线/超时转 503，非法 JSON/超过 16 MiB 响应转 502。UI 校验失败 400，未知路由 404。没有泛用 URL 代理，也没有上游旧页面路由。

## 凭据保存和合并

注册来源严格限于 `danbooru/twitter/openverse/pixiv/gelbooru/safebooru/konachan/yandere`；另接受 `tagger:openai_compatible` 和 `tagger:local_onnx`。后两者的 `values` 是扁平 **tagger_config**，而不是外层 `{tagger_config:...}`。

保存字段白名单：

- Danbooru：login/api_key；Gelbooru、Safebooru：user_id/api_key；Konachan、Yandere：login/password_hash。
- X：auth_token/ct0/x_cookies_file/x_cookies_from_browser/x_browser_profile/x_cookie_mode/x_mode。
- Pixiv：access_token/phpsessid/user_id/pixiv_cookie_mode。
- Openverse：openverse_license。所有来源还可保存 proxy。
- OpenAI-compatible：endpoint/model/api_key/prompt/timeout/max_tokens/temperature。
- Local ONNX：model_path/tags_path/selected_tags_path/general_threshold/threshold/character_threshold/input_size/providers/categories。

秘密为 api_key/auth_token/ct0/access_token/phpsessid/password_hash（别名 auth_key/llm_api_key 也纳入脱敏，但不作为保存字段）。空秘密不覆盖已有值；`clear` 显式删除指定合法字段并优先于 values。未知字段/来源拒绝，非法字段不原样放进错误。文件按现有原子写入协议创建为 0600，损坏 JSON 备份也为 0600。凭据文件是权限受限明文，不是加密密码库；仅给可信本机用户使用。

test/search/start/tagger-test 将已保存配置与显式**非空**请求字段合并，显式值优先；0/false 仍有效。来源凭据不会注入另一来源，tagger 凭据只进入 tagger_config。不得把来源 api_key 当成 LLM api_key。为防止非秘密字段回显凭据，endpoint/proxy 禁止 URL 内嵌用户名密码、query、fragment；有认证的网络代理不在本轮支持范围。

本 UI 使用规范 `tagger_id`（也接受字符串 tagger_type/tagger）与嵌套 tagger_config；不支持 ASG 旧的对象选择器或 openai/onnx 别名。`source/test`、`artists/search` 保持单来源，针对当前编辑来源；`tagger/test` 也不接受 sources/source_configs。

### 多来源任务

`task/start` 保留 `{source,...}` 单源契约，另外接受：

```json
{
  "sources": ["danbooru", "yandere"],
  "source_configs": {
    "danbooru": {"artist": "artist_tag", "query_type": "artist", "rating": "g"},
    "yandere": {"artist": "another_tag", "query_type": "artist", "rating": "s"}
  },
  "count": 20,
  "canonical_artist": "my_folder",
  "images_only": false,
  "tagger_id": "none"
}
```

- `sources` 必须是 1–8 项的白名单字符串数组，按首次出现顺序去重。空数组、对象项、未知 ID 拒绝；`source_configs` 必须为对象，其 key 必须已选中。
- 每个来源只合并自己已保存的认证及非秘密选项；显式非空字段优先。来源字段仅允许该来源凭据白名单，加 `artist/query_type/rating`（Twitter 另允许 `x_user_id/x_handle`）。来源的 `proxy/login/cookie mode` 也不能泄漏到别源。
- 多源顶层不接受 `source/artist/rating/login/api_key/proxy` 等来源专属字段；须嵌套。公共字段只有 count、start_page/end_page（及 range_start/range_end）、canonical_artist/canonical_artist_id、tag_format/tag_merge_mode、include_artist/include_meta/skip_video、background_enabled/background_color、images_only 和全局 tagger 选择器/配置。
- 嵌套来源内禁止 tagger、images_only、count 等全局覆盖，也不允许 skip/enabled 隐式跳过。每个来源必须提供非空 `artist` 映射；不会按同名猜 X/Pixiv 身份。
- 提交前核对运行中 ASG 的来源清单、query_types 和 needs_auth 必需 auth_fields；托管浏览器会话仍由 ASG 实际预检，失败明确返回。`multi_source` 缺失时兼容已有多源能力的旧 ASG；若显式声明却不为 true 则 409。新 ASG 声明 `capabilities.multi_source:true`。
- tagger 只在全局合并一次；仅图片模式不读取任何保存的 AI 配置。响应递归剔除 source_configs/tagger_config/config/request，已知秘密在日志中也脱敏。

## 仅下载图片与来源能力

`POST /api/grabber/task/start` 接受 `images_only:boolean`，缺省 false，不改变原标签模式。也接受忽略大小写、去空白后的 `true/1/yes/on` 与 `false/0/no/off` 字符串；null、数字、数组、对象、其他字符串返回 400。开启后适配器强制 `tagger_id:none`，移除其他打标选择器、tagger_config 与旧 llm_/onnx_ 字段，不合并或发送已保存的 AI 配置/秘密；来源认证仍正常合并。

提交前适配器仅通过受管 ASG 的回环 `/api/sources` 检查 `capabilities.images_only` 严格为 JSON true。缺失、false 或类型错误均 **409 拒绝**，提示更新并重启 ArtistSourceGrabber，不向旧服务发送 start，不静默降级成写标签模式。不会为检查能力访问源站。更新 Python 后需要用户自行重启 UI 后端和 ASG；本功能不自动重启/关闭服务，重启前先等待正在运行的任务结束并确认进程身份。

ASG 仍下载、进行启用的图片铺底和哈希去重；不调用来源构造标签或 AI 预检/打标，不新建、覆盖、读取、合并或删除 `.txt`。已有 sidecar 字节保持原样；重复路径只删本次新下载的冗余图，保留已有图和所有 sidecar（包括孤立标签）。新图/已有/重复/跳过/失败结果均 `caption_url:""`、三个标签数组为空、`tag_status:"skipped"`。关闭该模式后恢复原默认行为。

sources 元数据区分采集查询 `query_types` 和候选搜索 `search_query_types`：Danbooru 均支持 artist/character/tag；Gelbooru、Safebooru、Konachan、Yandere 查询支持三种但候选仅 artist；Openverse 查询支持 artist/tag、候选仅 artist；Twitter/Pixiv 均仅 artist。`supports_artist_search=false` 时候选能力为空。Openverse tag 是 q 关键词，不是严格 AND；Danbooru character 候选未按 category 强制过滤。适配器透明保留这些字段，并按来源分别验证多源任务的查询类型。

## 本地 ONNX 目录选择

`POST /api/grabber/models/scan {directory?:string}` 返回 `{directory,models:[{name,model_path,tags_path}],truncated}`。它是 UI 后端的本地目录索引，**不是浏览器文件上传、原生文件选择对话框或模型下载器**，ASG 不运行也可使用。

默认目录为已保存 local_onnx.model_path 的父目录（父目录存在时），否则 `<ASG>/models`。用户可以显式输入目录，经过 expanduser/resolve 与真实目录检查；不自动扫描 home/整盘、不创建目录。不存在/非目录为 404，非法路径/根符号链接为 400，根目录不可读为 403。扫描最多根目录及两层子目录、4096 条目录项、256 个 `.onnx`、2 秒遍历预算；排除隐藏项与符号链接，进入子目录使用 no-follow 文件描述符，遇到限制或无法读取的子项返回 `truncated:true`。

每个模型只配对同目录的 `selected_tags.csv`（优先），否则同 stem 的 `.csv`；没有时 `tags_path:null`，不能悄悄选择其他 CSV。只读取目录/文件元信息，不读完整模型、不 load ONNX 推理、不安装任何依赖。路径作为 JSON 文本返回，客户端应作为文本/输入值显示，绝不能当 HTML。手输 model_path/tags_path 并通过 credentials 保存的流程不变。

## 下载分组图库与重新打标

**当前任务结果不是图库**：`progress` 是最近一次采集任务的内存快照，不是历史列表。下面的 library 接口列出 downloads 下已存在的下载分组，可在新的 UI 会话中重新浏览磁盘内容；它也不是任务历史或持久队列。

| 方法 / UI 路由 | 请求与响应 |
| --- | --- |
| GET `/api/grabber/library/groups` | ASG 同名路由 → `{groups:[{name,image_count,tagged_count,bytes,mtime}],truncated}` |
| GET `/api/grabber/library/items?group=...&offset=0&limit=60` | → `{group,items:[{filename,path,image_url,caption_url,tags,has_tags,size,mtime}],total,offset,limit,truncated}` |
| POST `/api/grabber/library/retag/start` | `{group,mode,tag_format,tagger_id,tagger_config,confirm_replace,confirm_remote}` → `{ok:true,job}` |
| GET `/api/grabber/library/retag/progress` | 当前 job 对象；没有任务为 `{status:'idle'}` |
| POST `/api/grabber/library/retag/stop` | `{id?:string}` → `{ok:true}`；协作停止，需继续轮询 |

所有 library 操作先检查 ASG `capabilities.library===true`；retag 操作同时要求 `capabilities.retag===true`。缺失/false/错误类型均 409，提示更新并重启 ASG，不假装支持、不自动重启。group 仅允许单级非隐藏目录名（不允许斜线、反斜线、百分号、控制字符），offset 非负整数，limit 1–100。query 使用 urlencode，不能注入其他路径/参数。图片/标签仍通过 UI 已有 `/api/grabber/file?path=...` 同源受保护路由，而不是直连上游 URL。

retag 的 mode 为 `merge`（与既有标签合并）、`replace`（替换）、`missing`（仅缺标签项），tag_format 为 comma/space，tagger_id 只能 local_onnx/openai_compatible，拒绝 none。replace 必须明确 `confirm_replace:true`；OpenAI-compatible 必须 `confirm_remote:true`（即便用户配置的是本地兼容服务），两确认字段仅接受 JSON boolean，字符串或数字不算确认。只合并选中 tagger 的保存配置，不带来源凭据，也不继承采集 images_only。UI 不写任何标签文件，ASG 负责与采集互斥、备份旧 `.txt` 以及实际写入。

job 包含 `id,status,group,mode,total,processed,written,skipped,failed,current,error,backup_dir,logs`；start 的 ok 只说明任务被接受。stop 的 id 若提供，须为 1–128 位字母数字/下划线/连字符；上游校验任务身份和冲突。无 task 队列或自动重试。

## 标注工作台与翻译（annotations_v1）

标注工作台与翻译是同一采集工作区里的新能力，跨层契约与验收记录见 [annotation-workbench.md](annotation-workbench.md)，接口清单见 [interfaces.md](interfaces.md#标注工作台与翻译接口新增)。

- 路由前缀 `/api/grabber/annotations/`（14 组 method/后缀）与 `/api/translation/`（16 组）。它们不经过 `contract/grabber.py` 旧 `_request` 的脱敏与重试路径，而分别由 `Grabber.annotation_request` 与 `AnnotationContract`/`TranslationService` 处理；ASG 端对应 `/api/annotations/`，翻译路由只在 UI 存在。
- 能力协商：UI 在每次标注转发前读取受管 ASG 的 `/api/sources`，要求 `capabilities.annotations_v1 === true`；旧 ASG 返回 409 `UNSUPPORTED_CAPABILITY`，提示更新并重启受管服务。不自动重启、不降级到旧 `library` 语义，也不把 `library:true` 当作已支持标注。
- 传输：严格 UTF-8 JSON、POST 除认证 token 外不接受 query 字段、body ≤1,000,000 字节、响应 ≤16 MiB、桥接超时 30 秒；成功 `{ok:true,data}`，失败 `{ok:false,error:{code,message,details,retryable}}`，认证失败为 403 `AUTH_REQUIRED`。超时的 POST 结果未知，必须用同一 `requestId` 对账，不自动重发。
- 翻译是 UI-only：译文、计划、任务、请求日志、缓存、词典、设置与凭据都在 UI state 的 `annotations/` 下（目录 0700、文件 0600），不写图旁 sidecar，也不写 ASG 源文件；API key 只存 `translation-credentials.json`，响应只回 `hasKey/keyHint`。
- 当前状态为“已实现（v1）”，验收仅覆盖离线合成 fixture 与临时目录；真实 provider、Windows/macOS、真实 downloads 批量写入等限制见契约 §15.5。

## 缩略图与详情预览

`GET /api/grabber/preview?path=<原 /files/... 路径>&size=thumb|detail` 在 UI 本地生成静态 raster，不依赖 ASG 在线。默认 thumb，最长边分别为 384 / 1600 像素，不放大；修正 EXIF 方向，动画只取首帧，透明图保留透明（WebP 可用时优先，否则 PNG），不透明图为 JPEG。派生图不带原 EXIF 等元数据，不改写原图或标签。原图与文本仍用 `/api/grabber/file` 按需打开，不自动回退下载大原图。

Pillow 在首次生成时惰性导入，是可选依赖；缺少为 503，解码或格式不匹配为 422，源文件超过 32 MiB 或 6400 万像素为 413，生成中源变化为 409（可重试）。size 非法、为空或 path/size 多值为 400。所有失败保持 no-store，客户端应显示错误和独立原图入口。

每次请求先认证，再逐级 no-follow 打开并 fstat，之后才查询内存 LRU。键包括 dev/ino、mtime_ns、ctime_ns、文件大小、源 MIME 与 variant；最多 64 项 / 32 MiB，旧文件变更/删除/链接替换不能复用旧条目。最多同时 2 个解码任务；固定 16 个条带锁合并相同图片的并发工作，缓存锁只保护短暂字典操作。等待线程尚不读取源字节，完成后关闭文件与图像对象；不建立磁盘缓存。

只有成功 preview 响应带 `Cache-Control: private, no-cache`、内容 ETag、`Vary: Cookie, X-UI-Token`，重开需鉴权并检查真实源文件后才允许 `If-None-Match` 返回无 body 的 304；含 `nosniff` / CSP sandbox 及 `X-Preview-Width/Height`。其他路由（包括原图）仍 no-store。Python 修改需用户自行重启 UI 后端生效，不要求重启 ASG，不自动操作正在运行的服务。

## 文件保护与已知限制

图片直接从 `<grabber-root>/downloads.resolve()` 读取，不转发上游文件 URL。拒绝绝对 URL、`..`/`.`、空路径段、反斜线、NUL、多重编码、任何符号链接；按目录描述符逐级 no-follow 打开，防止链接替换穿越。只接受普通文件，最多 64 MiB，扩展名与 magic 检查 PNG/JPEG/WebP/GIF/BMP；`.txt` 按纯文本显示。SVG/HTML、视频、模型和任意文件均拒绝。响应附 `nosniff` 与限制性 CSP sandbox，HTML 风格标签文本也不能执行。

上游没有 task_id、历史查询、可靠事件游标、完整队列或自动重试。offset 是数组切片位置；旧 item 可能原地合并标签，结束应全量刷新；多页面仍可能见到另一个页面启动的新当前任务。源站认证/许可、托管浏览器 Cookie、真实下载、GPU/ONNX/LLM 能力取决于 ASG 自身环境。本机其他进程仍能直连无认证 ASG，这是上游限制，不应把整个工作台暴露公网。

## 定向验证

```sh
PYTHONPATH=src PYTHONPYCACHEPREFIX=/tmp/kohakumoss-grabber-pycache python3 -m unittest discover -s tests -p 'test_grabber*.py' -v
PYTHONPATH=src PYTHONPYCACHEPREFIX=/tmp/kohakumoss-grabber-pycache python3 -m unittest discover -s tests -p 'test_store.py'
PYTHONPATH=src:tests PYTHONPYCACHEPREFIX=/tmp/kohakumoss-grabber-pycache python3 -m unittest test_grabber test_grabber_images_only test_grabber_library test_grabber_spawn -v
PYTHONPYCACHEPREFIX=/tmp/kohakumoss-grabber-pycache python3 -m py_compile src/kohakumoss_ui/contract/grabber.py src/kohakumoss_ui/contract/grabber_models.py src/kohakumoss_ui/server/http.py tests/test_grabber.py tests/test_grabber_library.py
```

`HttpSmokeTests` 会实际启动临时 UI HTTP server 与 fake ASG，并进行回环 HTTP 请求（包括 token、文件 headers、offset、冲突/离线）。`test_grabber_spawn.py` 另外创建临时 fake ASG app.py，通过真实 Popen 启动并从 stdout 获取实际绑定端口，验证 readiness、重复启动、409、协作停止、UI 关闭保活，以及保存秘密/显式秘密和其他选项的合并隔离；测试结束关闭自己的 fake 子进程。其余启动失败路径使用 mock Popen。全部不调用真实来源、不下载模型、不运行训练。

标注/翻译工作台（2026-10-24 实测通过，通过数量与逐项结果见 [annotation-workbench.md](annotation-workbench.md#152-实际运行的测试与通过数量)）：

```sh
env PYTHONPATH=src:tests PYTHONPYCACHEPREFIX=/tmp/km-annotation-cache ../KohakuMossTrainer/.venv/bin/python -m unittest test_annotations test_annotations_store test_translation test_translation_lexicon test_translation_regressions test_annotation_bridge
node tests/annotation_dom.mjs
node tests/annotation_transport.mjs
node tests/translation_lifecycle.mjs
```
