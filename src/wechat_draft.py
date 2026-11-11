"""WeChat draft-box API client for the official `cgi-bin` endpoints.

Why this module exists
----------------------
Publishing is deliberately decoupled from content generation. The pipeline
renders the report HTML first and only then hands it to this module, so a
publish failure never costs a collection run or a MiniMax rewrite.

Two properties matter operationally:

* **Actionable whitelist errors.** The official API refuses calls whose
  caller IP is not in the account whitelist with `errcode 40164`. The
  offending IP is embedded in the error message, so it is parsed out and
  surfaced as :attr:`WeChatIpNotWhitelistedError.ip`. The operator can add
  that IP to the WeChat admin console and re-run the upload.

* **Idempotent uploads.** :func:`upsert_draft` updates an existing draft
  whose title matches instead of creating a second copy, so re-running the
  upload script never leaves duplicates in the draft box.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import requests

API_BASE = "https://api.weixin.qq.com/cgi-bin"

# The WeChat error text looks like:
#   invalid ip 220.196.12.5 ipv6 ::ffff:220.196.12.5, not in whitelist rid: ...
_IPV4_IN_ERRMSG = re.compile(r"invalid ip (\d{1,3}(?:\.\d{1,3}){3})")


class WeChatApiError(RuntimeError):
    """A WeChat official-API call failed."""

    def __init__(
        self,
        message: str,
        *,
        errcode: int | None = None,
        errmsg: str | None = None,
    ) -> None:
        super().__init__(message)
        self.errcode = errcode
        self.errmsg = errmsg


class WeChatIpNotWhitelistedError(WeChatApiError):
    """errcode 40164: the caller's egress IP is not in the account whitelist.

    ``ip`` is the address WeChat observed. It is the exact value that has to
    be added under 设置与开发 -> 基本配置 -> IP白名单.
    """

    def __init__(self, ip: str | None, errmsg: str) -> None:
        super().__init__(
            "WeChat refused the request: the current egress IP "
            f"{ip or 'unknown'} is not in the account IP whitelist "
            f"(errcode 40164: {errmsg}).",
            errcode=40164,
            errmsg=errmsg,
        )
        self.ip = ip


@dataclass(frozen=True)
class DraftResult:
    """Outcome of an :func:`upsert_draft` call."""

    media_id: str
    action: str  # "created" or "updated"
    title: str

    @property
    def created(self) -> bool:
        return self.action == "created"


def _raise_for_error(payload: Mapping[str, Any], what: str) -> None:
    errcode = payload.get("errcode")
    if errcode in (None, 0):
        return
    errmsg = str(payload.get("errmsg", ""))
    if errcode == 40164:
        match = _IPV4_IN_ERRMSG.search(errmsg)
        raise WeChatIpNotWhitelistedError(match.group(1) if match else None, errmsg)
    raise WeChatApiError(
        f"{what} failed: errcode={errcode} errmsg={errmsg}",
        errcode=int(errcode),
        errmsg=errmsg,
    )


def fetch_access_token(
    app_id: str,
    app_secret: str,
    *,
    session: Any | None = None,
    timeout: int = 15,
) -> str:
    """Fetch an ``access_token`` for the official API.

    Raises :class:`WeChatIpNotWhitelistedError` when the account IP whitelist
    rejects the caller, and :class:`WeChatApiError` for any other failure.
    """
    http = session or requests
    resp = http.get(
        f"{API_BASE}/token",
        params={
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": app_secret,
        },
        timeout=timeout,
    )
    payload = resp.json()
    token = payload.get("access_token")
    if not token:
        _raise_for_error(payload, "access_token request")
        raise WeChatApiError(
            f"access_token request returned no token: {payload}",
            errcode=payload.get("errcode"),
            errmsg=str(payload.get("errmsg", "")),
        )
    return str(token)


def list_drafts(
    token: str,
    *,
    session: Any | None = None,
    timeout: int = 30,
    page_size: int = 20,
) -> list[dict]:
    """Return every draft in the box.

    ``no_content`` is 0 so the article titles come back; the title is what
    :func:`find_draft_by_title` matches on.
    """
    http = session or requests
    items: list[dict] = []
    offset = 0
    total: int | None = None
    while True:
        resp = http.post(
            f"{API_BASE}/draft/batchget",
            params={"access_token": token},
            json={"offset": offset, "count": page_size, "no_content": 0},
            timeout=timeout,
        )
        payload = resp.json()
        _raise_for_error(payload, "draft/batchget")
        page = list(payload.get("item") or [])
        if total is None:
            total = int(payload.get("total_count") or 0)
        items.extend(page)
        if not page:
            break
        offset += len(page)
        if total and offset >= total:
            break
    return items


def find_draft_by_title(
    token: str,
    title: str,
    *,
    session: Any | None = None,
    timeout: int = 30,
) -> dict | None:
    """Find the draft carrying ``title``.

    Returns a dict with ``media_id``, ``index`` (position of the matching
    article inside the draft) and the existing ``article`` payload, or ``None``
    when no draft carries that title.
    """
    for item in list_drafts(token, session=session, timeout=timeout):
        articles: Sequence[Mapping[str, Any]] = (
            (item.get("content") or {}).get("news_item") or []
        )
        for index, article in enumerate(articles):
            if article.get("title") == title:
                return {
                    "media_id": item.get("media_id"),
                    "index": index,
                    "article": dict(article),
                    "update_time": item.get("update_time"),
                }
    return None


def _build_article(
    *,
    title: str,
    html: str,
    thumb_media_id: str,
    author: str,
    digest: str,
    cover_crop_percent_list: Sequence[Mapping[str, Any]] | None,
) -> dict:
    article: dict[str, Any] = {
        "article_type": "news",
        "title": title,
        "author": author,
        "digest": digest,
        "content": html,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    if cover_crop_percent_list:
        article["cover_info"] = {"crop_percent_list": list(cover_crop_percent_list)}
    return article


def add_draft(
    token: str,
    article: Mapping[str, Any],
    *,
    session: Any | None = None,
    timeout: int = 30,
) -> str:
    """Create a new draft and return its media_id."""
    http = session or requests
    resp = http.post(
        f"{API_BASE}/draft/add",
        params={"access_token": token},
        json={"articles": [dict(article)]},
        timeout=timeout,
    )
    payload = resp.json()
    _raise_for_error(payload, "draft/add")
    media_id = payload.get("media_id")
    if not media_id:
        raise WeChatApiError(f"draft/add returned no media_id: {payload}")
    return str(media_id)


def update_draft(
    token: str,
    media_id: str,
    article: Mapping[str, Any],
    *,
    index: int = 0,
    session: Any | None = None,
    timeout: int = 30,
) -> str:
    """Replace one article of an existing draft and return its media_id."""
    http = session or requests
    resp = http.post(
        f"{API_BASE}/draft/update",
        params={"access_token": token},
        json={
            "media_id": media_id,
            "index": index,
            "articles": dict(article),
        },
        timeout=timeout,
    )
    payload = resp.json()
    _raise_for_error(payload, "draft/update")
    return str(media_id)


def upsert_draft(
    *,
    title: str,
    html: str,
    thumb_media_id: str,
    app_id: str,
    app_secret: str,
    author: str = "Valkyrie",
    digest: str | None = None,
    cover_crop_percent_list: Sequence[Mapping[str, Any]] | None = None,
    session: Any | None = None,
    timeout: int = 30,
) -> DraftResult:
    """Create the draft, or overwrite the existing one with the same title.

    Re-running the upload therefore never produces a duplicate: the second
    run updates the first run's draft in place.
    """
    token = fetch_access_token(app_id, app_secret, session=session, timeout=timeout)
    article = _build_article(
        title=title,
        html=html,
        thumb_media_id=thumb_media_id,
        author=author,
        digest=digest or f"{title} | 今日移动端AI资讯速览",
        cover_crop_percent_list=cover_crop_percent_list,
    )
    existing = find_draft_by_title(token, title, session=session, timeout=timeout)
    if existing and existing.get("media_id"):
        media_id = update_draft(
            token,
            str(existing["media_id"]),
            article,
            index=int(existing.get("index") or 0),
            session=session,
            timeout=timeout,
        )
        return DraftResult(media_id=media_id, action="updated", title=title)
    media_id = add_draft(token, article, session=session, timeout=timeout)
    return DraftResult(media_id=media_id, action="created", title=title)


def iter_draft_titles(drafts: Iterable[Mapping[str, Any]]) -> list[str]:
    """Helper for diagnostics: every title currently in the draft box."""
    titles: list[str] = []
    for item in drafts:
        for article in ((item.get("content") or {}).get("news_item") or []):
            title = article.get("title")
            if title:
                titles.append(str(title))
    return titles
