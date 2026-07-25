# 采用 AniDB 作剧集级元数据源,并据此重整 kind / 剧集类型模型

## 背景与决策

系统已用 bgm.tv 取番剧级元数据(中文译名/封面/简介/制作公司/评分)。但本地番剧库(尤其 BD rip)需要**剧集级**的权威结构:把 NCOP/NCED(无字幕 OP/ED)、特别篇、PV、映像特典等"特典"准确归类,而不是靠文件名瞎猜或一股脑丢进"其他文件"。bgm.tv 的剧集类型偏粗(本篇/SP/OP/ED/CM/其他),AniDB 是动漫库里粒度最细的——剧集类型分 `regular / special / credits(专收 NCOP·NCED)/ trailer / parody / other`,且支持 ed2k 哈希精确配集。

**决定**:引入 **AniDB 作为剧集级元数据源**(bgm.tv 仍负责番剧级元数据)。

- **番剧 → aid**:用第三方托管 API `anidb-search`(https://anidb.rotcool.me/api/s,MIT,支持中文/拼音搜)拿候选 `{标题: aid}`——AniDB 官方 HTTP **没有搜索接口**,官方机制是下 `anime-titles.dat.gz` 全量转储本地模糊匹配,而 anidb-search 正好封装了它并加了中文/拼音。唯一/精确命中自动绑,多候选详情页手动选,可手动改 aid。
- **aid → 剧集**:AniDB 官方 HTTP API(`request=anime&aid=`)拉剧集+类型,**按需触发**(BD 库扫描 / 详情页「同步 AniDB 剧集」按钮),结果连 aid+各 eid 缓存进 DB(≥24h 不重拉,满足 AniDB 强制缓存要求),串行 ≥2s/次,需注册 client 名。未配置/失败 → 退化到文件名启发式,不阻塞。
- **文件 → 剧集**:默认按「集号 + 类型」配 AniDB 剧集表;**仅当集号+类型仍歧义(0 或 >1 候选)且配置了 UDP 账号**时,才对该文件算 ed2k 哈希走 UDP `FILE` 精配(避免对每个数 GB 的 BD 文件都过一遍 SMB)。

**连带模型重整**(AniDB 把"作品类型"与"剧集类型"分得很干净,纠正了原 `EpisodeType` 混层级的问题):

- 番剧加 `kind`(`tv`/`movie`/`ova`)——作品级形态。来源优先级:AniDB anime type > bgm.tv platform > 手动覆盖。`movie`/`ova` 详情页走「影片本体 + 版本列表」布局。
- `Episode.type` 由原 `EP/MOVIE/OVA/SP` 改为对齐 AniDB 的 `REGULAR / SPECIAL / CREDITS / TRAILER / OTHER`。`MOVIE`/`OVA` 移除——它们是作品级,归 `kind`(一部剧场版 = 它自己的番剧)。

## 考虑过的替代

- **只用 bgm.tv 剧集 API**(`/v0/episodes`):已集成、中文、走代理、零新依赖,类型(本篇/SP/OP/ED/CM/其他)也够用。否决原因:粒度不及 AniDB——AniDB 的 `credits` 类型专收 NCOP/NCED,且能 ed2k 哈希精配歧义特典,这是 BD 场景的核心诉求。bgm.tv 仍保留作番剧级元数据。
- **爬 VCB-Studio wiki**:中文 BD rip 社区标准,但无 API、页面格式不统一、和 Mikan HTML 一样是脆弱面。否决。
- **AniDB 官方 titles 转储自行模糊匹配**(不用 anidb-search):可行且无第三方依赖,但缺中文/拼音匹配。作为 anidb-search 挂掉时的退路保留(它开源、本质只是包了同一份转储)。

## 后果

- **第三方依赖**:`anidb-search` 是个人 Vercel 部署(周更)。挂了会断"番剧→aid"自动匹配;缓解:base URL 可在设置里改(自托管),或退回手动绑 aid / 本地 titles 转储。
- **AniDB 易封 IP**:必须串行 ≥2s、强制本地缓存 ≥24h、按需而非创建即拉(全量导入几十部时尤其要克制)。
- **配置负担**:设置页需加 AniDB 启用开关、HTTP client 名+版本、anidb-search base URL、可选 UDP 账号(供 ed2k)。未配置时整条链路优雅退化到文件名启发式。
- **数据迁移**:`Episode.type` 枚举值变更(`EP`→`REGULAR`,去 `MOVIE`/`OVA`,加 `CREDITS`/`TRAILER`/`OTHER`),需迁移既有 Episode 行 + 新增 `Bangumi.kind/anidb_aid`、`Episode.anidb_eid` 列(走 `database._migrate_columns`)。
