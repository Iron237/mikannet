"""Reviewed routing table. Each tool has exactly one fixed upstream operation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    description: str


def operation(method, path, description):
    return Operation(method, "/api/" + path, description)


TOOLS = {
    "system_version": operation("GET", "system/version", "读取运行版本与依赖版本。"),
    "system_health": operation("GET", "system/health", "检查应用和下载器连接。"),
    "search_anime": operation("GET", "search", "按番名搜索蜜柑，获取 mikan_bangumi_id。"),
    "recommend_subgroups": operation("GET", "search/bangumi/{mikan_bangumi_id}", "比较追更与补番推荐、画质、语言和覆盖集数。可分页读取 subgroups。"),
    "preview_subscription": operation("GET", "search/preview", "订阅前按字幕组和关键词预览 RSS 过滤结果。"),
    "list_subscriptions": operation("GET", "subscriptions", "列出订阅、本地 ID、启停状态和最近检查结果。"),
    "create_subscription": operation("POST", "subscriptions", "创建订阅并触发首次轮询。只追新集请传 backfill=false。"),
    "update_subscription": operation("PATCH", "subscriptions/{sub_id}", "修改订阅规则、路径或 enabled，并触发后台重新检查。"),
    "poll_subscription": operation("POST", "subscriptions/{sub_id}/poll", "立即检查一条订阅；符合规则的新资源可能提交给下载器。"),
    "replace_subscription_source": operation("POST", "subscriptions/{sub_id}/replace-source", "更换字幕组并清空旧源专属的手动选择，保留订阅历史。"),
    "remove_subscription": operation("DELETE", "subscriptions/{sub_id}", "删除单个订阅及相关记录和下载器任务，默认保留物理文件。"),
    "list_downloads": operation("GET", "tasks", "按状态列出下载任务、进度和异常。"),
    "pause_download": operation("POST", "tasks/{task_id}/pause", "暂停一个已提交的下载。"),
    "resume_download": operation("POST", "tasks/{task_id}/resume", "恢复下载或重新提交先前失败的任务。"),
    "retry_postprocess": operation("POST", "tasks/{task_id}/postprocess", "重新入队媒体探测和整理。"),
    "list_library": operation("GET", "bangumi", "列出已入库番剧及本地 bangumi_id。verify_files=false 只读数据库快照。"),
    "get_anime": operation("GET", "bangumi/{bangumi_id}", "查询本地番剧详情。在 episodes 中查 file_id（files[].id）。"),
    "library_calendar": operation("GET", "bangumi/calendar/week", "查看已入库番剧的一周放送情况。"),
    "discover_calendar": operation("GET", "discover/calendar", "查看当季番剧放送日历。"),
    "library_issues": operation("GET", "bangumi/resource-issues", "查找缺集和资源异常。"),
    "resource_strategy": operation("GET", "bangumi/{bangumi_id}/resource-strategy", "查看缺集、现有资源及补全策略。"),
    "scan_anime": operation("POST", "bangumi/{bangumi_id}/auto-scan", "启动单部扫描。review 只生成候选，其余模式可能下载资源。"),
    "scan_status": operation("GET", "bangumi/auto-scan/status", "查看智能扫描进度。"),
    "scan_history": operation("GET", "bangumi/{bangumi_id}/auto-history", "查看扫描记录和候选。"),
    "approve_scan": operation("POST", "bangumi/{bangumi_id}/auto-history/{log_id}/approve", "批准一次扫描的候选并提交下载。"),
    "scan_library": operation("POST", "import/library-scan", "扫描媒体目录并登记文件，返回后查询 library_scan_status。"),
    "library_scan_status": operation("GET", "import/library-scan/status", "查看目录扫描进度与结果。"),
    "list_bd_releases": operation("GET", "bd/releases", "列出已登记的 BD 发行。"),
    "get_bd_release": operation("GET", "bd/releases/{release_id}", "查看发行详情和碟片信息。"),
    "unassigned_files": operation("GET", "files/unassigned", "列出尚未关联到分集的视频。"),
    "reprobe_file": operation("POST", "files/{file_id}/reprobe", "重新探测一个视频。"),
    "match_danmaku": operation("GET", "files/{file_id}/danmaku/matches", "获取单个视频的精确匹配或候选、凭据状态及已有 XML。"),
    "search_danmaku": operation("GET", "files/{file_id}/danmaku/search", "按番名和集数搜索弹幕库。"),
    "save_danmaku": operation("POST", "files/{file_id}/danmaku", "将所选 episode_id、shift 的弹幕 XML 保存到视频旁。"),
    "auto_danmaku": operation("POST", "files/{file_id}/danmaku/auto", "自动保存唯一精确匹配的弹幕，返回 saved/existing/unconfigured 等状态。"),
    "check_update": operation("GET", "system/update/check", "检查可用版本和更新类型。"),
    "update_status": operation("GET", "system/update/status", "查询升级进度。"),
    "search_releases": operation("GET", "search/multi", "跨来源搜索蜜柑、Nyaa 或动漫花园的发布。"),
    "add_library_anime": operation("POST", "bangumi/from-mikan", "按蜜柑 ID 将作品加入番剧库。"),
    "update_anime_policy": operation("PATCH", "bangumi/{bangumi_id}", "调整该番剧的季号、集数起点、自动获取和智能下载模式。"),
    "refresh_metadata": operation("POST", "bangumi/{bangumi_id}/refresh-metadata", "刷新单部番剧元数据。"),
    "anime_series": operation("GET", "bangumi/{bangumi_id}/series", "查询关联季、续集与本地库 ID。"),
    "assign_file": operation("POST", "files/{file_id}/assign", "将视频关联到正确的剧集或特典类型。"),
    "activate_file": operation("POST", "files/{file_id}/activate", "切换当前使用的视频版本。"),
    "scan_bd": operation("POST", "bd/scan", "扫描已配置的 BD 目录并更新发行记录。"),
    "bd_scan_status": operation("GET", "bd/scan/status", "查看 BD 扫描结果。"),
    "remove_download": operation("DELETE", "tasks/{task_id}", "从下载器移除该任务并更新记录。delete_files=true 会同时删除数据。"),
    "get_preferences": operation("GET", "config", "读取轮询、资源推荐及自动弹幕偏好。"),
    "set_preferences": operation("PUT", "config", "更新指定的非凭据偏好，未传字段保持原值。"),
}

TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
BOOL = {"type": "boolean"}
PREFERENCES = {
    "danmaku_auto_download": BOOL, "auto_dl_prefer_bd": BOOL,
    "auto_dl_resolution": {"enum": ["720p", "1080p", "2160p"]},
    "auto_dl_sub_lang": {"enum": ["简中", "繁中", "不限"]},
    "poll_interval_min": {"type": "integer", "minimum": 1},
    "auto_dl_interval_min": {"type": "integer", "minimum": 0},
}


def body(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False, "minProperties": 1}


# REST 中以 dict 接收的业务字段须在这里显式列出。
BODIES = {
    "activate_file": body({"preferred": BOOL}),
    "set_preferences": body(PREFERENCES),
    "add_library_anime": body({"mikan_bangumi_id": {"type": "integer", "minimum": 1}, "title": TEXT}, ["mikan_bangumi_id"]),
    "assign_file": body({"episode_number": {"type": "number", "minimum": 0},
                         "type": {"enum": ["regular", "special", "credits", "trailer", "other"]}}),
    "update_anime_policy": body({"auto_best": BOOL, "auto_download_disabled": BOOL,
                                 "auto_mode": {"enum": ["review", "fill_only", "fill_upgrade"]},
                                 "ep_start": {"type": "integer", "minimum": 1},
                                 "season_number": {"type": "integer", "minimum": 1}}),
    "update_subscription": body({
        "include_keywords": TEXTS, "exclude_keywords": TEXTS, "pinned_guids": TEXTS,
        "blocked_guids": TEXTS, "exclude_batch": BOOL, "backfill": BOOL, "enabled": BOOL,
        "episode_offset": {"type": "integer"}, "save_path": TEXT, "subgroup_name": TEXT}),
    "replace_subscription_source": body({
        "mikan_subgroup_id": {"type": "string", "minLength": 1}, "subgroup_name": TEXT}, ["mikan_subgroup_id"]),
    "scan_anime": body({"mode": {"enum": ["review", "fill_only", "fill_upgrade"]}}, ["mode"]),
}
