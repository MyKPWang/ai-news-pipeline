#!/usr/bin/env python3
"""
生成 GitHub AI 趋势榜图片（HTML 渲染 + Playwright 截图方案）
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# LLM 调用（生成中文项目介绍）
# ---------------------------------------------------------------------------

MINI_MAX_API_KEY = ""
MINI_MAX_BASE_URL = "https://api.minimaxi.chat/v1"


def _call_llm(prompt: str, max_tokens: int = 800, timeout: int = 60) -> str:
    """调用 MiniMax LLM"""
    global MINI_MAX_API_KEY, MINI_MAX_BASE_URL
    if not MINI_MAX_API_KEY:
        try:
            secret_path = Path.home() / ".openclaw" / "workspace" / "ai-news-pipeline" / "secrets.yaml"
            import yaml
            with open(secret_path) as f:
                secrets = yaml.safe_load(f)
            MINI_MAX_API_KEY = secrets.get("minimax_api_key", "")
            MINI_MAX_BASE_URL = secrets.get("minimax_base_url", MINI_MAX_BASE_URL)
        except Exception:
            return ""

    if not MINI_MAX_API_KEY:
        return ""

    import urllib.request
    payload = {
        "model": "MiniMax-M2.5",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            MINI_MAX_BASE_URL + "/text/chatcompletion_v2",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {MINI_MAX_API_KEY}"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("choices"):
                return result["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return ""


def fetch_github_readme(owner: str, repo: str) -> str:
    """抓取 GitHub 项目 README"""
    import urllib.request
    for branch in ["main", "master"]:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        try:
            req = urllib.request.Request(raw_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
                if content and len(content) > 50:
                    return content[:5000]
        except Exception:
            pass
    return ""


# 5 天缓存
_CACHE_PATH = Path.home() / ".openclaw" / "workspace" / "ai-news" / "github_desc_cache.json"
_CACHE_TTL = 5 * 24 * 3600


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def generate_chinese_desc(owner: str, repo: str, fallback_desc: str) -> str:
    """为项目生成中文介绍（80-150字），带5天缓存"""
    cache_key = f"{owner}/{repo}"
    now = time.time()
    cache = _load_cache()
    entry = cache.get(cache_key)

    if entry and (now - entry.get("ts", 0)) < _CACHE_TTL:
        return entry["desc"]

    readme = fetch_github_readme(owner, repo)
    if not readme:
        cache[cache_key] = {"desc": fallback_desc, "ts": now}
        _save_cache(cache)
        return fallback_desc

    readme_clean = re.sub(r"<[^>]+>", " ", readme)
    readme_clean = re.sub(r"\s+", " ", readme_clean).strip()[:2500]
    prompt = (
        "你是一个专业的中文技术写作人员。请仔细阅读以下项目的 README 内容，"
        "用 80-150 字的中文（纯中文，禁止任何英文单词，禁止中英混杂）介绍这个项目。"
        "必须以句号（。）结尾，禁止在句子中间截断。如果 README 内容不足以生成介绍，请明确说明[暂无足够信息]。\n\n"
        f"README 内容：\n{readme_clean}"
    )
    result = _call_llm(prompt, max_tokens=800, timeout=60)
    if not result or len(result.strip()) < 10:
        result = fallback_desc
    elif "暂无足够信息" in result:
        result = fallback_desc
    else:
        result = re.sub(r"\*+\\*?", "", result)
        result = re.sub(r"#{1,6}\s*", "", result)
        result = re.sub(r"`[^`]*`", "", result)
        result = re.sub(r">\s*", "", result)
        result = re.sub(r"\n+", " ", result).strip()
        if not result[-1] if result else False in "。！？":
            for punct in ("。", "！", "？"):
                idx = result.rfind(punct)
                if idx >= 0:
                    result = result[:idx + 1]
                    break

    cache[cache_key] = {"desc": result, "ts": now}
    _save_cache(cache)
    return result


# ---------------------------------------------------------------------------
# HTML 模板
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background: #0d1117; color: #e6edf3; padding: 24px; }}
  .header {{ text-align: center; margin-bottom: 20px; }}
  .header .date {{ font-size: 13px; color: #7d8590; margin-top: 4px; }}
  .header .subtitle {{ font-size: 13px; color: #7d8590; margin-top: 4px; }}
  .table {{ width: 100%; border-collapse: collapse; }}
  .table th {{ background: #161b22; color: #7d8590; font-size: 12px; font-weight: 600; text-align: left; padding: 10px 12px; border-bottom: 1px solid #30363d; }}
  .table td {{ padding: 12px; border-bottom: 1px solid #21262d; vertical-align: top; font-size: 13px; line-height: 1.5; }}
  .table tr:last-child td {{ border-bottom: none; }}
  .rank {{ color: #7d8590; font-size: 12px; width: 40px; text-align: center; }}
  .rank.top3 {{ color: #f0883e; font-weight: bold; }}
  .repo-name {{ color: #58a6ff; font-weight: 600; font-size: 14px; }}
  .repo-name a {{ color: inherit; text-decoration: none; }}
  .desc {{ color: #8b949e; margin-top: 3px; font-size: 12px; }}
  .stars {{ color: #f0883e; font-size: 12px; white-space: nowrap; }}
  .lang {{ background: #1f6feb; color: #fff; font-size: 10px; padding: 1px 6px; border-radius: 10px; margin-right: 6px; }}
  .rank-cell {{ text-align: center; }}
  .top-badge {{ background: #f0883e; color: #fff; font-size: 10px; padding: 2px 5px; border-radius: 3px; margin-left: 4px; }}
</style>
</head>
<body>
<div class="header">
  <div style="font-size:20px;font-weight:bold;color:#e6edf3;">🔥 GitHub AI 项目趋势榜</div>
  <div class="date">{{ date }}</div>
  <div class="subtitle">数据来源：GitHub Trending | 整理：Valkyrie</div>
</div>
<table class="table">
<thead>
<tr>
  <th class="rank">#</th>
  <th>项目</th>
  <th style="width:80px;text-align:center;">⭐ Stars</th>
</tr>
</thead>
<tbody>
{% for repo in repos %}
<tr>
  <td class="rank-cell{% if repo.rank <= 3 %} top3{% endif %}">{% if repo.rank <= 3 %}<span class="top-badge">TOP{{ repo.rank }}</span>{% else %}{{ repo.rank }}{% endif %}</td>
  <td>
    <div class="repo-name"><a href="{{ repo.link }}" target="_blank">{{ repo.name }}</a></div>
    <div class="desc">{{ repo.description }}</div>
  </td>
  <td style="text-align:center;">
    <span class="stars">{{ repo.stars }}</span>
  </td>
</tr>
{% endfor %}
</tbody>
</table>
</body>
</html>"""


def generate_github_trending_table(github_items: list, output_dir: Path) -> Optional[str]:
    """
    为 GitHub 趋势项目生成图片。

    Args:
        github_items: NewsItem 列表，每个 item.extra 包含 stars/forks/language
        output_dir: 图片输出目录

    Returns:
        图片本地路径，或 None（失败时）
    """
    if not github_items:
        return None

    try:
        from jinja2 import Template
        from playwright.sync_api import sync_playwright
        import concurrent.futures
    except ImportError as e:
        print(f"   ⚠️ 依赖缺失: {e}，跳过 GitHub 榜单图片生成")
        return None

    # 并行生成中文介绍
    top10 = github_items[:10]
    repos = []
    for i, item in enumerate(top10):
        parts = item.url.strip("/").split("/")
        owner, repo = parts[-2], parts[-1]
        desc = generate_chinese_desc(owner, repo, item.desc or "暂无描述")
        stars_str = "–"
        if item.extra.get("stars"):
            try:
                stars_k = round(int(item.extra["stars"]) / 1000, 1)
                stars_str = f"{stars_k}K"
            except Exception:
                stars_str = str(item.extra["stars"])
        repos.append({
            "rank": i + 1,
            "name": item.title,
            "link": item.url,
            "description": desc,
            "stars": stars_str,
        })

    date_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    from jinja2 import Template
    html_content = Template(HTML_TEMPLATE).render(date=date_str, repos=repos)

    html_debug_path = output_dir / "github_trending_debug.html"
    with open(html_debug_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    img_path = str(output_dir / "github_trending.png")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_viewport_size({"width": 780, "height": 900})
            page.goto(f"file://{html_debug_path.absolute()}", wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            page.screenshot(path=img_path, full_page=True)
            browser.close()
        print(f"   ✅ GitHub 趋势榜图片已生成: {img_path}")
        return img_path
    except Exception as e:
        print(f"   ⚠️ Playwright 截图失败: {e}")
        return None


def upload_image_to_wechat(img_path: str, config: dict) -> Optional[str]:
    """上传图片到微信素材库，返回永久 URL"""
    import requests

    app_id = config.get("app_id", "")
    app_secret = config.get("app_secret", "")
    if not app_id or not app_secret:
        return None

    try:
        resp = requests.get(
            f"https://api.weixin.qq.com/cgi-bin/token"
            f"?grant_type=client_credential&appid={app_id}&secret={app_secret}",
            timeout=10
        )
        token = resp.json().get("access_token")
        if not token:
            return None

        with open(img_path, "rb") as f:
            files = {"media": (img_path.split("/")[-1], f, "image/png")}
            r = requests.post(
                f"https://api.weixin.qq.com/cgi-bin/media/upload?access_token={token}&type=image",
                files=files, timeout=30
            ).json()
        return r.get("url")
    except Exception as e:
        print(f"   ⚠️ 微信图片上传失败: {e}")
        return None


if __name__ == "__main__":
    # 测试用
    from src.models import NewsItem
    test_items = [
        NewsItem(title="test/repo", url="https://github.com/test/repo", desc="test desc",
                 source="github", extra={"stars": 1234})
    ]
    output = Path("wechat-publish-tool/output")
    output.mkdir(parents=True, exist_ok=True)
    path = generate_github_trending_table(test_items, output)
    print(f"Result: {path}")