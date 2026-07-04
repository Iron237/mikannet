# P0 探针结论(2026-06-12)

## 网络
- **本机所有外部服务直连均不通,必须走代理 `http://127.0.0.1:10808`**(http/https/socks5 均支持,mihomo)。
  Mikan、bgm.tv、TMDB 经代理全部可达。默认配置:全服务走代理,按服务开关保留。
- 访问本机/容器服务(qB)必须绕过代理(NO_PROXY 或 trust_env=False)。

## Mikan(mikanani.me)
- 首页可提取当季番剧 ID(`a[href^="/Home/Bangumi/"]`,89 个)。
- 搜索 `/Home/Search?searchstr=`:`ul.list-inline.an-ul li` 内锚点+title。
- 番剧页 `/Home/Bangumi/{id}`:`p.bangumi-title` 标题;`.subgroup-text`(id=字幕组ID)+ 紧随 table 为该组种子表;
  `.bangumi-info` 含 Bangumi 链接/官网/放送开始/放送星期。
- **番剧页种子表有截断(约 15 条),RSS `/RSS/Bangumi?bangumiId=&subgroupid=` 才是全量(实测 49 条)**
  → 补齐数据源优先 RSS,番剧页种子表做展示/兜底。与原计划假设相反。
- RSS 字段:title、link=Episode 页(作 guid)、enclosure=.torrent URL+length、published。无 torrent 命名空间扩展可依赖。
- bgm.tv subject 链接覆盖率:抽样 8/8。

## bgm.tv v0 API
- `GET /v0/subjects/{id}`:name/name_cn/date(年代)/platform/eps/summary/rating.score/images.large 直接可用。
- 制作公司在 infobox key=`动画制作`(备选 `製作`/`制作`)。
- 无直接"连载中"字段:用 date+eps+(Mikan 首页是否在当季)推断。

## qBittorrent(v5.2.1 / API 2.15.1,linuxserver 镜像)
- **qB 5.x 强制 Host 头端口校验:容器内外端口必须一致(如 18080:18080 + WEBUI_PORT=18080),
  否则一律 401 `Invalid Host header, port mismatch`**。容器间访问(app→qb:8080)天然一致,无此问题。
- 登录成功=204+QBT_SID cookie,失败=401(qbittorrent-api 已封装)。
- 验证通过:分类创建(可带 save_path)、按 torrent 文件字节添加(qB 容器无代理,**.torrent 必须由 app 经代理取回字节再投递**,
  不能让 qB 自己拉 URL)、中文 save_path、progress/dlspeed/size/eta/state 字段、暂停/恢复/删除(含删文件)、全局限速。
- 开发实例:容器 `mikannet-qb-probe`,http://localhost:18080,admin / mikannet-dev,
  挂载 `G:\Works\Bots\RSS video\.probe-qb\{config,downloads}`。

## 标题解析(anitopy 2.1.1,语料 n=358)
- 集数解析 92.2% + 合集识别 2.2% = 94.4%;失败 5.6%(20 条,见 titles_failed.txt)。
- 失败模式:`[整理搬运]` 多部合集(按合集规则排除即可)、`六四位元字幕组★..★04★` 星号分隔、
  哆啦A梦 `[164PART1]`、`S01v2` 整季版本号。
- P1 规则链:前置预处理(★→空格等)+ 合集正则 + PART 处理,即可 ≥95%。

## TMDB
- API 经代理可达(401=待 API key)。探针 probe_tmdb.py 就绪,等 key 后运行验证图片 CDN。
