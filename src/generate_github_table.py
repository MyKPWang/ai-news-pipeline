#!/usr/bin/env python3
"""
生成 GitHub AI 趋势榜图片（复刻 v11 样式）
"""
import json
import re
import time
from pathlib import Path
from typing import Optional

MINI_MAX_API_KEY = ""
MINI_MAX_BASE_URL = "https://api.minimaxi.chat/v1"


def _call_llm(prompt: str, max_tokens: int = 800, timeout: int = 60) -> str:
    global MINI_MAX_API_KEY, MINI_MAX_BASE_URL
    if not MINI_MAX_API_KEY:
        try:
            import yaml
            with open(Path.home() / ".openclaw" / "workspace" / "ai-news-pipeline" / "secrets.yaml") as f:
                secrets = yaml.safe_load(f)
            MINI_MAX_API_KEY = secrets.get("minimax_api", {}).get("api_key", "")
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
        if result and result[-1] not in "。！？":
            for punct in ("。", "！", "？"):
                idx = result.rfind(punct)
                if idx >= 0:
                    result = result[:idx + 1]
                    break

    cache[cache_key] = {"desc": result, "ts": now}
    _save_cache(cache)
    return result


# ---------------------------------------------------------------------------
# v11 样式模板（复刻）
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap" rel="stylesheet">
    <title>GitHub Trending Weekly Digest</title>
    <style>
        :root {
            --primary-color: #2563eb;
            --bg-page: #f3f4f6;
            --bg-card: #ffffff;
            --text-main: #1f2937;
            --text-sub: #4b5563;
            --border-radius: 12px;
            --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: var(--bg-page);
            color: var(--text-main);
            width: 800px;
            padding: 32px 40px;
            line-height: 1.6;
        }

        .card {
            background: var(--bg-card);
            border-radius: var(--border-radius);
            box-shadow: var(--shadow);
            padding: 24px 28px;
            margin-bottom: 16px;
            position: relative;
            transition: transform 0.2s ease, border-left-color 0.2s ease;
            border-left: 5px solid transparent;
        }

        .card:hover {
            transform: translateY(-3px);
            border-left-color: var(--primary-color);
        }

        .card-top {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            margin-bottom: 10px;
        }

        .rank-badge {
            background: linear-gradient(135deg, #3b82f6, #6366f1);
            color: white;
            font-size: 12px;
            font-weight: 700;
            padding: 4px 12px;
            border-radius: 20px;
            min-width: 46px;
            text-align: center;
        }

        .rank-badge.gold { background: linear-gradient(135deg, #f59e0b, #d97706); }
        .rank-badge.silver { background: linear-gradient(135deg, #94a3b8, #64748b); }
        .rank-badge.bronze { background: linear-gradient(135deg, #cd7f32, #a0522d); }

        .meta { display: flex; align-items: center; gap: 8px; }

        .lang-tag {
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            padding: 4px 8px;
            border-radius: 6px;
            color: #fff;
        }

        .lang-python { background-color: #3776ab; }
        .lang-javascript { background-color: #f7df1e; color: #000; }
        .lang-typescript { background-color: #3178c6; }
        .lang-go { background-color: #00ADD8; }
        .lang-rust { background-color: #dea584; color: #000; }
        .lang-java { background-color: #b07219; }
        .lang-cpp { background-color: #f34b7d; }
        .lang-c { background-color: #555555; }
        .lang-html { background-color: #6e7681; }
        .lang-shell { background-color: #6e7681; }
        .lang-default { background-color: #6e7681; }

        .stars {
            color: #fbbf24;
            font-weight: bold;
            font-size: 0.9rem;
            display: flex;
            align-items: center;
        }

        .stars span { color: var(--text-sub); margin-left: 4px; font-weight: normal; }

        .name { font-size: 17px; font-weight: 700; color: var(--text-main); margin-bottom: 4px; }
        .description { color: var(--text-sub); font-size: 0.95rem; line-height: 1.7; margin: 0; }
    </style>
</head>
<body>

    {% for repo in repos %}
    <div class="card">
        <div class="card-top">
            <div class="rank-badge {% if repo.rank == 1 %}gold{% elif repo.rank == 2 %}silver{% elif repo.rank == 3 %}bronze{% endif %}">#{{ repo.rank }}</div>
            <div class="meta">
                {% if repo.language and repo.language != '-' %}
                <span class="lang-tag lang-{{ repo.language|lower }}">{{ repo.language }}</span>
                {% endif %}
                <div class="stars">新增 ★ <span>{{ repo.stars }}</span></div>
            </div>
        </div>
        <div class="name">{{ repo.name }}</div>
        <p class="description">{{ repo.description }}</p>
    </div>
    {% endfor %}

</body>
</html>"""


def generate_github_trending_table(github_items: list, output_dir: Path) -> Optional[str]:
    """生成 GitHub 趋势榜图片，返回本地路径或 None"""
    if not github_items:
        return None

    try:
        from jinja2 import Template
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(f"   ⚠️ 依赖缺失: {e}，跳过 GitHub 榜单图片生成")
        return None

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
        language = item.extra.get("language") or "-"
        rank = i + 1
        badge = "gold" if rank == 1 else "silver" if rank == 2 else "bronze" if rank == 3 else "default"
        repos.append({
            "rank": rank,
            "name": item.title,
            "link": item.url,
            "description": desc,
            "stars": stars_str,
            "language": language,
            "badge": badge,
        })

    html_content = Template(HTML_TEMPLATE).render(repos=repos)

    html_debug_path = output_dir / "github_trending_debug.html"
    with open(html_debug_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    img_path = str(output_dir / "github_trending.png")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(timeout=30000)  # 30s 超时，防止卡住
            page = browser.new_page()
            page.set_viewport_size({"width": 880, "height": 1600})
            page.goto(f"file://{html_debug_path.absolute()}", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
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
            filename = img_path.split("/")[-1]
            files = {"media": (filename, f, "image/png")}
            r = requests.post(
                f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}",
                files=files, timeout=30
            ).json()
        return r.get("url")
    except Exception as e:
        print(f"   ⚠️ 微信图片上传失败: {e}")
        return None