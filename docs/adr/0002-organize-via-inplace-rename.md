# 用 qB 原地重命名整理成 Jellyfin 结构(修订 ADR-0001)

下载完成后,系统可(默认开,设置页可关)用 qBittorrent 的 `renameFile`/`renameFolder` 接口,把种子内的视频文件**原地重命名**成 Jellyfin 标准结构,并在剧集文件夹写入 `tvshow.nfo` + `poster.jpg` + `fanart.jpg`。

布局(订阅 save_path 文件夹 = `download_root/番剧名` 直接作为 Jellyfin 剧集文件夹):
```
番剧名/                       ← = 订阅保存目录
  tvshow.nfo  poster.jpg  fanart.jpg
  Season 01/
    番剧名 S01E08.mkv
  Season 00/                 ← SP/OVA/剧场版
    番剧名 S00E01.mkv
```

季号 `Season NN` 来自 `Bangumi.season_number`(默认 1、由标题自动猜、详情页可手改);NFO 带 `tmdbid`/`bangumi` uniqueid,Jellyfin 按 id 精准匹配,与中文文件夹名无关。

## 为什么改 ADR-0001

- ADR-0001 拒绝整理的两大理由是"移动/重命名断做种"与"硬链接在 SMB 上不可行"。`renameFile` **原地改名**两者都不触发:qB 改名后仍按新路径做种,SMB 上只是一次 rename(秒级、不复制、无需硬链接)。
- 用户需求变化:要接 Jellyfin,需要磁盘上有规整、可刮削的结构,而不只是 WebUI 虚拟库。

## Consequences

- 标题解析 + 文件↔剧集映射仍是核心(整理依赖映射结果定出 `SxxExx`)。
- 整理后 `VideoFile.relative_path` 跟随更新;整理幂等(已是目标名则跳过)。
- 仅对 **qB 后端**保证(BitComet WebUI 改名能力未知,organize 按下载器名跳过)。
- 元数据复用已缓存的 bgm.tv/TMDB 数据(标题/年份/制作/简介/封面),离线写盘,无额外抓取。
- 开关:`organize_enabled` / `nfo_enabled`(设置页,DB 覆盖 env)。
