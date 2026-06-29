#!/usr/bin/env python3
"""
微信公众号 Docker RSS API 测试脚本
设计目标：充分验证 API 的功能和边界条件

运行方式：
    python3 scripts/wechat_api_test.py

前置条件：
    - we-mp-rss Docker 运行中
    - secrets.yaml 和 config.yaml 已配置
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import yaml, requests, json
from datetime import datetime, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
with open('secrets.yaml') as f:
    secrets = yaml.safe_load(f)
with open('config.yaml') as f:
    cfg = yaml.safe_load(f)

BASE_URL = cfg.get('docker_api', {}).get('base_url', 'http://localhost:4000')
USERNAME = secrets.get('docker_api', {}).get('username', '')
PASSWORD = secrets.get('docker_api', {}).get('password', '')

# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------
def get_token():
    resp = requests.post(f'{BASE_URL}/api/v1/wx/auth/token',
                         data={'username': USERNAME, 'password': PASSWORD}, timeout=10)
    resp.raise_for_status()
    return resp.json().get('access_token', '')

def api_get(path, params=None, token=None):
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    resp = requests.get(f'{BASE_URL}{path}', params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()

# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------
now = datetime.now()
THRESHOLD_24H = int((now - timedelta(hours=24)).timestamp())
THRESHOLD_48H = int((now - timedelta(hours=48)).timestamp())

def age(pt):
    """返回 publish_time 距离现在多少小时"""
    if not pt:
        return 999.0
    return (now.timestamp() - pt) / 3600

def flag(pt, threshold=THRESHOLD_24H):
    return '✅' if pt >= threshold else '❌'

def extract_rows(data):
    """从 API 响应中提取文章列表"""
    if isinstance(data, dict):
        for key in ['data', 'list']:
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    return val
                if isinstance(val, dict) and 'list' in val:
                    return val['list']
    return []

# ---------------------------------------------------------------------------
# TEST 1: API 认证
# ---------------------------------------------------------------------------
def test_auth():
    print('\n' + '='*60)
    print('TEST 1: API 认证')
    print('='*60)
    token = get_token()
    assert token, '未获取到 token'
    print(f'✅ 认证成功，token: {token[:20]}...')
    return token

# ---------------------------------------------------------------------------
# TEST 2: 全局查询 - offset/limit 组合
# ---------------------------------------------------------------------------
def test_global_pagination(token):
    print('\n' + '='*60)
    print('TEST 2: 全局查询 - offset/limit 组合')
    print('='*60)

    # 测不同 limit
    for limit in [10, 50, 100]:
        data = api_get('/api/v1/wx/articles', {'offset': 0, 'limit': limit}, token)
        rows = extract_rows(data)
        print(f'  limit={limit:3d} → 返回 {len(rows)} 条')

    # 测 offset 翻页
    print('\n  offset 翻页连续性（limit=20）:')
    all_pts = []
    for offset in range(0, 100, 20):
        data = api_get('/api/v1/wx/articles', {'offset': offset, 'limit': 20}, token)
        rows = extract_rows(data)
        pts = [r.get('publish_time', 0) for r in rows]
        all_pts.extend(pts)
        print(f'    offset={offset:3d} → {len(rows)} 条 | 最新={pts[0] if pts else 0} | 最老={pts[-1] if pts else 0}')

    # 验证：全局数据是否严格按 publish_time 降序
    is_sorted = all(all_pts[i] >= all_pts[i+1] for i in range(len(all_pts)-1))
    print(f'\n  ✅ 全局查询 publish_time 严格降序: {is_sorted}')

    # 统计 <24h / 24-48h / >48h 分布
    lt24 = sum(1 for p in all_pts if p >= THRESHOLD_24H)
    lt48 = sum(1 for p in all_pts if THRESHOLD_48H <= p < THRESHOLD_24H)
    gt48 = sum(1 for p in all_pts if p < THRESHOLD_48H)
    print(f'  全局100条时间分布: <24h={lt24} | 24-48h={lt48} | >48h={gt48}')

    return all_pts

# ---------------------------------------------------------------------------
# TEST 3: per-mp_id 查询 - 各账号独立验证
# ---------------------------------------------------------------------------
def test_per_mp_queries(token):
    print('\n' + '='*60)
    print('TEST 3: per-mp_id 查询 - 各账号排序验证')
    print('='*60)

    # 先获取全局查询中出现的所有 mp_id
    data = api_get('/api/v1/wx/articles', {'offset': 0, 'limit': 100}, token)
    rows = extract_rows(data)
    mp_ids = list({r.get('mp_id') for r in rows if r.get('mp_id')})

    print(f'  账号数量: {len(mp_ids)}')
    issues = []

    for mp_id in mp_ids[:5]:  # 重点测前5个账号
        data_mp = api_get('/api/v1/wx/articles',
                          {'offset': 0, 'limit': 50, 'mp_id': mp_id}, token)
        rows_mp = extract_rows(data_mp)
        pts = [r.get('publish_time', 0) for r in rows_mp]

        if not pts:
            print(f'  {mp_id}: 无数据')
            continue

        # 验证是否按 publish_time 降序
        is_sorted = all(pts[i] >= pts[i+1] for i in range(len(pts)-1)) if len(pts) > 1 else True
        latest_age = age(pts[0])
        status = '✅' if is_sorted else '❌'
        print(f'  {mp_id}: {len(rows_mp)}条 | 降序={status} | 最新={latest_age:.1f}h前')
        if not is_sorted:
            issues.append(mp_id)
            # 找出乱序位置
            for i in range(len(pts)-1):
                if pts[i] < pts[i+1]:
                    print(f'    ⚠️ 乱序位置 {i}: {pts[i]} < {pts[i+1]}')

    if issues:
        print(f'\n  ❌ 有 {len(issues)} 个账号 per-mp_id 查询排序异常')
    else:
        print(f'\n  ✅ 所有测试账号 per-mp_id 查询均按 publish_time 降序')

# ---------------------------------------------------------------------------
# TEST 4: 全局查询 vs per-mp_id 查询 数据对比
# ---------------------------------------------------------------------------
def test_global_vs_per_mp(token):
    print('\n' + '='*60)
    print('TEST 4: 全局查询 vs per-mp_id 查询 数据覆盖对比')
    print('='*60)

    # 选 APPSO (2392024520) 作为重点分析对象
    target_mp = 'MP_WXS_2392024520'

    data_global = api_get('/api/v1/wx/articles', {'offset': 0, 'limit': 100}, token)
    rows_global = extract_rows(data_global)

    data_mp = api_get('/api/v1/wx/articles',
                       {'offset': 0, 'limit': 100, 'mp_id': target_mp}, token)
    rows_mp = extract_rows(data_mp)

    global_pts = [r.get('publish_time', 0) for r in rows_global if r.get('mp_id') == target_mp]
    mp_pts = [r.get('publish_time', 0) for r in rows_mp]

    print(f'  APPSO 在全局查询(100条)中有 {len(global_pts)} 条')
    print(f'  APPSO per-mp_id 查询(100条)中有 {len(mp_pts)} 条')
    print(f'  APPSO 全局查询最新: {age(global_pts[0]):.1f}h前' if global_pts else '  无数据')
    print(f'  APPSO per-mp_id最新: {age(mp_pts[0]):.1f}h前' if mp_pts else '  无数据')

    # 验证：per-mp_id 的第1条 是否 >= 全局查询中该账号的第1条
    if global_pts and mp_pts:
        diff = mp_pts[0] - global_pts[0]
        print(f'  per-mp_id 最新 vs 全局最新 差值: {diff}s ({diff/3600:.1f}h)')
        if diff > 0:
            print(f'  ⚠️ per-mp_id 最新的文章比全局更新的更大？检查排序逻辑')

    # 统计 per-mp_id 中各时间段文章数
    lt24 = sum(1 for p in mp_pts if p >= THRESHOLD_24H)
    lt48 = sum(1 for p in mp_pts if THRESHOLD_48H <= p < THRESHOLD_24H)
    gt48 = sum(1 for p in mp_pts if p < THRESHOLD_48H)
    print(f'  APPSO per-mp_id 时间分布: <24h={lt24} | 24-48h={lt48} | >48h={gt48}')

# ---------------------------------------------------------------------------
# TEST 5: 分页边界测试
# ---------------------------------------------------------------------------
def test_pagination_boundary(token):
    print('\n' + '='*60)
    print('TEST 5: 分页边界测试')
    print('='*60)

    # 全局查询最大 limit
    data = api_get('/api/v1/wx/articles', {'offset': 0, 'limit': 100}, token)
    rows = extract_rows(data)
    total = len(rows)
    print(f'  全局查询 limit=100 返回: {total} 条')

    # offset 超过总数据量
    data_over = api_get('/api/v1/wx/articles', {'offset': 9999, 'limit': 100}, token)
    rows_over = extract_rows(data_over)
    print(f'  全局查询 offset=9999 返回: {len(rows_over)} 条（预期空列表）')

    # offset=总数据量附近
    if total > 0:
        data_edge = api_get('/api/v1/wx/articles', {'offset': total-5, 'limit': 20}, token)
        rows_edge = extract_rows(data_edge)
        print(f'  全局查询 offset={total-5} 返回: {len(rows_edge)} 条')

# ---------------------------------------------------------------------------
# TEST 6: time_filter 模拟
# ---------------------------------------------------------------------------
def test_time_filter_simulation(token):
    print('\n' + '='*60)
    print('TEST 6: time_filter 模拟（lookback_hours=24）')
    print('='*60)

    data = api_get('/api/v1/wx/articles', {'offset': 0, 'limit': 100}, token)
    rows = extract_rows(data)

    lt24 = [r for r in rows if r.get('publish_time', 0) >= THRESHOLD_24H]
    gt24 = [r for r in rows if r.get('publish_time', 0) < THRESHOLD_24H]

    print(f'  全局100条: <24h={len(lt24)} 条, >24h={len(gt24)} 条')

    # 各账号 >24h 文章数和最新发布
    by_mp = defaultdict(list)
    for r in rows:
        by_mp[r.get('mp_id', 'unknown')].append(r)

    print('\n  各账号 >24h 文章统计:')
    for mp_id, articles in sorted(by_mp.items(), key=lambda x: -max(r.get('publish_time',0) for r in x[1]) if x[1] else 0):
        pts = [r.get('publish_time', 0) for r in articles]
        latest = max(pts) if pts else 0
        old = [r for r in articles if r.get('publish_time', 0) < THRESHOLD_24H]
        if old:
            print(f'    {mp_id}: >24h={len(old)} 条 | 最新发布={age(latest):.1f}h前')

    print(f'\n  结论: 如果 >24h 数量多，说明账号本身发布频率低，不是 API 排序问题')

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    print('='*60)
    print('微信公众号 Docker RSS API 测试')
    print(f'测试时间: {now.isoformat()}')
    print(f'阈值: <24h cutoff = {THRESHOLD_24H} ({datetime.fromtimestamp(THRESHOLD_24H).strftime("%m-%d %H:%M")})')
    print('='*60)

    token = test_auth()
    test_global_pagination(token)
    test_per_mp_queries(token)
    test_global_vs_per_mp(token)
    test_pagination_boundary(token)
    test_time_filter_simulation(token)

    print('\n' + '='*60)
    print('全部测试完成 ✅')
    print('='*60)
