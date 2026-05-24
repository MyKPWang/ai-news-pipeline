# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a WeChat Official Account (微信公众号) publishing tool. It receives structured news data, generates an HTML article using a Jinja2 template, and uploads it to the WeChat draft box via the WeChat API.

## Commands

```bash
# Install dependencies
pip install requests jinja2

# Run the tool directly (requires configured news-config.json)
python publish_news.py
```

## Architecture

- `publish_news.py` - Main module containing:
  - `publish_news(data, title, sources, author)` - Primary entry point
  - `get_config()` - Reads `news-config.json`
  - `ensure_thumb_media_id()` - Uploads cover image if needed
  - `generate_html()` - Renders HTML using Jinja2
  - `save_html()` - Saves HTML to `output/news_YYYYMMDD.html`
  - `upload_to_wechat()` - Uploads draft via WeChat API

- `news-config.json` - WeChat API credentials (app_id, app_secret, thumb_media_id)
- `templates/news.html` - Jinja2 HTML template
- `output/` - Generated HTML files directory

## Workflow

1. Read config from `news-config.json`
2. Ensure cover image is uploaded and `thumb_media_id` is cached
3. Render HTML from Jinja2 template with news data
4. Save HTML to `output/`
5. Upload to WeChat draft box

## Data Structure

The `data` dict passed to `publish_news()` has:
- `hot_items`: List[str] - Hot topics list (optional)
- `insight`: str - Market insight paragraph (optional)
- `categories`: List[dict] - News categories, each with `name` and `items`

Each item in `categories[].items` has: `title`, `summary`, `rewritten_title`, `source`, `link`, `time_ago`
