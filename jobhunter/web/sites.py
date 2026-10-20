"""Chrome 插件支持的招聘网站。插件端的页面逻辑见 extension/content.js 里的同名 adapter。"""
from ..platforms.boss import CITY_CODES as BOSS_CITY_CODES

# 猎聘搜索页 dq 参数（取自 liepin.com/zhaopin 城市筛选项）
LIEPIN_CITY_CODES = {
    "全国": "410", "北京": "010", "上海": "020", "天津": "030", "重庆": "040", "广州": "050020",
    "深圳": "050090", "苏州": "060080", "南京": "060020", "杭州": "070020", "大连": "210040",
    "成都": "280020", "武汉": "170020", "西安": "270020",
}

EXT_SITES = {
    "boss": {"label": "BOSS直聘", "url": "https://www.zhipin.com/web/geek/jobs", "cities": BOSS_CITY_CODES},
    "liepin": {"label": "猎聘", "url": "https://www.liepin.com/zhaopin/", "cities": LIEPIN_CITY_CODES},
    # 脉脉搜索结果不能按城市筛选，城市交给 AI 打分时判断
    "maimai": {"label": "脉脉", "url": "https://maimai.cn/web/search_center?type=job", "cities": None, "beta": True},
}


def build_tasks(site: str, keywords: list[str], cities: list[str]) -> tuple[list[dict], list[str]]:
    """关键词 × 城市 → 插件要执行的搜索列表；返回 (tasks, 不支持的城市)。"""
    codes = EXT_SITES[site]["cities"]
    if codes is None:
        return [{"keyword": kw, "city": "不限", "city_code": ""} for kw in keywords], []
    unknown = [c for c in cities if c not in codes]
    tasks = [{"keyword": kw, "city": c, "city_code": codes.get(c, codes["全国"])}
             for kw in keywords for c in (cities or ["全国"])]
    return tasks, unknown
