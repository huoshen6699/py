# coding=utf-8
"""
暴风影视聚合爬虫（内置去广告清洗）
- 单源（暴风）
- 首页直接平铺分类标签（筛选升级为分类, 无"全部(最新)"入口）
- 播放 M3U8 时自动通过本地代理清洗广告（按 /adjump/ 路径删除）
- 无需外部清洗服务器
- 无弹幕
- 缓存优化：只缓存最近一次播放的清洗结果，换源自动覆盖，无过期时间

=== 修复清单（2026-09-28） ===
1. _segment_has_ad: 不再依赖 .ts 后缀, 检测段内任意 URI 行(带查询参数不漏判)
2. _rewrite_urls_in_segment: EXTINF 后夹 #EXT-X-KEY 标签行不再被当 ts 篡改
3. 重组时补回 #EXT-X-DISCONTINUITY(防独立编码段边界花屏)
4. _normalize_url: 只剥离纯缓存破坏参数(原盲删 v/t 导致缓存串台)
5. homeContent: 重试+兜底分类表+父级过滤+脏分类名清洗
6. _fetch_m3u8: 带 UA + Referer 兜底重试
7. homeVideoContent: 拉第一页填充并缓存(原恒为空)
8. localProxy 未知 action 返回 404; 直链补 header
9. fetch_json: HTTPS 失败自动降级 http/跳过证书验证
10. ★筛选升级为分类: 首页 class 直接平铺全部子分类(无"全部(最新)"),
    categoryContent 的 tid 即分类 ID
"""
import sys
import json
import concurrent.futures
import re
import requests
import time
from urllib.parse import urljoin, urlparse, quote, unquote, parse_qs, urlencode, urlunparse
sys.path.append('..')
from base.spider import Spider

class Spider(Spider):
    SOURCES = {
        '暴风': {'name': '暴风', 'api': 'https://bfzyapi.com/api.php/provide/vod/'}
    }

    # 内置兜底分类表(实测暴风接口数据, 已排除父级, ac=list 失败时用)
    FALLBACK_CLASSES = [
        {"type_id": "21", "type_name": "动作片"},
        {"type_id": "22", "type_name": "喜剧片"},
        {"type_id": "23", "type_name": "恐怖片"},
        {"type_id": "24", "type_name": "科幻片"},
        {"type_id": "25", "type_name": "爱情片"},
        {"type_id": "26", "type_name": "剧情片"},
        {"type_id": "27", "type_name": "战争片"},
        {"type_id": "28", "type_name": "纪录片"},
        {"type_id": "29", "type_name": "理论片"},
        {"type_id": "50", "type_name": "动画片"},
        {"type_id": "31", "type_name": "国产剧"},
        {"type_id": "32", "type_name": "欧美剧"},
        {"type_id": "33", "type_name": "香港剧"},
        {"type_id": "34", "type_name": "韩国剧"},
        {"type_id": "35", "type_name": "台湾剧"},
        {"type_id": "36", "type_name": "日本剧"},
        {"type_id": "37", "type_name": "海外剧"},
        {"type_id": "38", "type_name": "泰国剧"},
        {"type_id": "40", "type_name": "国产动漫"},
        {"type_id": "41", "type_name": "日韩动漫"},
        {"type_id": "42", "type_name": "欧美动漫"},
        {"type_id": "43", "type_name": "港台动漫"},
        {"type_id": "44", "type_name": "海外动漫"},
        {"type_id": "46", "type_name": "大陆综艺"},
        {"type_id": "47", "type_name": "港台综艺"},
        {"type_id": "48", "type_name": "日韩综艺"},
        {"type_id": "49", "type_name": "欧美综艺"},
        {"type_id": "51", "type_name": "电影解说"},
        {"type_id": "52", "type_name": "预告片"},
        {"type_id": "54", "type_name": "足球"},
        {"type_id": "55", "type_name": "篮球"},
        {"type_id": "56", "type_name": "网球"},
        {"type_id": "57", "type_name": "斯诺克"},
        {"type_id": "65", "type_name": "重生民国"},
        {"type_id": "66", "type_name": "穿越年代"},
        {"type_id": "67", "type_name": "现代言情"},
        {"type_id": "68", "type_name": "反转爽文"},
        {"type_id": "69", "type_name": "女恋总裁"},
        {"type_id": "70", "type_name": "闪婚离婚"},
        {"type_id": "71", "type_name": "都市脑洞"},
        {"type_id": "72", "type_name": "古装仙侠"},
        {"type_id": "74", "type_name": "AI漫剧"},
        
    ]

    def init(self, extend=""):
        self.session = requests.Session()
        try:
            from requests.adapters import HTTPAdapter
            _ad = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
            self.session.mount('http://', _ad)
            self.session.mount('https://', _ad)
        except Exception:
            pass
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        }
        try:
            import urllib3
            urllib3.disable_warnings()
        except Exception:
            pass
        # ---------- 缓存（单条目，覆盖式） ----------
        self._home_cache = None        # 首页分类缓存
        self._home_video_cache = None  # 首页推荐缓存
        self._clean_store = {}         # norm_url -> 清洗后内容(LRU)
        self._clean_order = []

    # ==================== 清洗引擎（按 /adjump/ 路径删除） ====================
    AD_KEYWORD = "/adjump/"
    TIMEOUT = 8
    MAX_RETRIES = 2

    def _fetch_m3u8(self, url):
        """抓 m3u8 带 UA, 失败时用播放域名自身 Referer 兜底重试。"""
        p = urlparse(url)
        alt_headers = dict(self.headers)
        alt_headers['Referer'] = '%s://%s/' % (p.scheme, p.netloc)
        for hdrs in (self.headers, alt_headers):
            for attempt in range(self.MAX_RETRIES):
                try:
                    resp = self.fetch(url, headers=hdrs, timeout=self.TIMEOUT)
                    if resp and getattr(resp, 'text', None) and \
                            resp.text.strip().startswith('#EXTM3U'):
                        return resp.text
                except Exception:
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(0.25)
        return None

    def _parse_segments(self, lines):
        """按 #EXT-X-DISCONTINUITY 划分 segment, 记录 disc 标记。"""
        segs = []
        cur = []
        disc_pending = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line == "#EXT-X-DISCONTINUITY":
                if cur:
                    segs.append({'lines': cur, 'disc': disc_pending})
                    cur = []
                disc_pending = True
            else:
                cur.append(line)
        if cur:
            segs.append({'lines': cur, 'disc': disc_pending})
        return segs

    def _segment_uris(self, segment):
        """提取 segment 中所有 URI 行(ts 地址 + KEY 的 URI)。"""
        uris = []
        for line in segment:
            if line.startswith("#EXT-X-KEY:") and "URI=" in line:
                m = re.search(r'URI="([^"]+)"', line)
                if m:
                    uris.append(m.group(1))
            elif not line.startswith("#"):
                uris.append(line)
        return uris

    def _segment_has_ad(self, segment):
        """判断 segment 是否含广告(路径含 /adjump/)。
        检测段内任意 URI 行, 不依赖 .ts 后缀(带查询参数不漏判)。"""
        for uri in self._segment_uris(segment):
            if self.AD_KEYWORD in uri.lower():
                return True
        return False

    def _rewrite_urls_in_segment(self, segment, base_url):
        """将 segment 中所有相对路径转为绝对 URL。
        EXTINF 后向后扫描到真正 URI, 中间 #EXT-X-KEY 标签行保留并修正。"""
        new_seg = []
        i = 0
        n = len(segment)
        while i < n:
            line = segment[i]
            if line.startswith("#EXT-X-KEY:") and "URI=" in line:
                m = re.search(r'URI="([^"]+)"', line)
                if m and not m.group(1).startswith(("http://", "https://")):
                    new_uri = urljoin(base_url, m.group(1))
                    line = line.replace('URI="%s"' % m.group(1), 'URI="%s"' % new_uri)
                new_seg.append(line)
                i += 1
                continue
            if line.startswith("#EXTINF:"):
                new_seg.append(line)
                j = i + 1
                uri_found = False
                while j < n:
                    l2 = segment[j].strip()
                    if l2.startswith("#"):
                        if l2.startswith("#EXT-X-KEY:") and "URI=" in l2:
                            m = re.search(r'URI="([^"]+)"', l2)
                            if m and not m.group(1).startswith(("http://", "https://")):
                                l2 = l2.replace('URI="%s"' % m.group(1),
                                                'URI="%s"' % urljoin(base_url, m.group(1)))
                        new_seg.append(l2)
                        j += 1
                        continue
                    new_seg.append(l2 if l2.startswith(("http://", "https://"))
                                   else urljoin(base_url, l2))
                    uri_found = True
                    j += 1
                    break
                i = j if uri_found else i + 1
                continue
            new_seg.append(line)
            i += 1
        return new_seg

    def _calc_duration(self, seg_lines):
        dur = 0.0
        for line in seg_lines:
            if line.startswith("#EXTINF:"):
                try:
                    dur += float(line.split(':', 1)[1].split(',')[0])
                except Exception:
                    pass
        return dur, 0

    def _clean_m3u8_content(self, content, base_url, debug=False):
        """主清洗函数：去掉含 /adjump/ 的片段。"""
        lines = content.splitlines()
        header = []
        footer = []
        body_start = 0
        body_end = len(lines)

        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("#EXTM3U") or line.startswith("#EXT-X-"):
                header.append(line)
                body_start = i + 1
            else:
                break

        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "#EXT-X-ENDLIST":
                footer.append(lines[i].strip())
                body_end = i
                break

        # 修复 header 中的 KEY URI
        for i, line in enumerate(header):
            if line.startswith("#EXT-X-KEY:") and "URI=" in line:
                m = re.search(r'URI="([^"]+)"', line)
                if m and not m.group(1).startswith(("http://", "https://")):
                    new_uri = urljoin(base_url, m.group(1))
                    header[i] = line.replace('URI="%s"' % m.group(1), 'URI="%s"' % new_uri)

        body_lines = [l.strip() for l in lines[body_start:body_end] if l.strip()]
        segments = self._parse_segments(body_lines)

        total_dur = 0.0
        removed_dur = 0.0
        cleaned = []
        ad_count = 0
        prev_removed = False
        for seg in segments:
            dur, _ = self._calc_duration(seg['lines'])
            total_dur += dur
            if self._segment_has_ad(seg['lines']):
                ad_count += 1
                removed_dur += dur
                prev_removed = True
                continue
            entry = self._rewrite_urls_in_segment(seg['lines'], base_url)
            # 段间补回 #EXT-X-DISCONTINUITY
            if cleaned and (seg['disc'] or prev_removed):
                cleaned.append(["#EXT-X-DISCONTINUITY"])
            prev_removed = False
            cleaned.append(entry)

        # 删除比例保护线(超线整体放弃, 避免误删)
        if total_dur > 0 and removed_dur / total_dur > 0.25:
            segments = self._parse_segments(body_lines)
            cleaned = []
            prev_removed = False
            for seg in segments:
                if self._segment_has_ad(seg['lines']):
                    prev_removed = True
                    continue
                if cleaned and (seg['disc'] or prev_removed):
                    cleaned.append(["#EXT-X-DISCONTINUITY"])
                prev_removed = False
                cleaned.append(self._rewrite_urls_in_segment(seg['lines'], base_url))
            ad_count = 0

        result = []
        result.extend(header)
        for seg in cleaned:
            result.extend(seg)
        result.extend(footer)

        if debug:
            dbg = [
                "# 总段落数: %d, 广告段: %d, 保留段: %d" % (len(segments), ad_count, len(cleaned)),
                "# 广告关键词: %s" % self.AD_KEYWORD,
                "# ========================",
            ]
            result = dbg + result

        return "\n".join(result)

    def _pick_variant(self, content, base_url):
        """从多码率 playlist 中选最高带宽的 variant。"""
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        best, best_bw = None, -1
        for i, l in enumerate(lines):
            if not l.startswith('#EXT-X-STREAM-INF'):
                continue
            m = re.search(r'BANDWIDTH=(\d+)', l)
            bw = int(m.group(1)) if m else 0
            for j in range(i + 1, len(lines)):
                if not lines[j].startswith('#'):
                    if bw >= best_bw:
                        best, best_bw = urljoin(base_url, lines[j]), bw
                    break
        return best
    # ==================== 清洗引擎结束 ====================

    # ==================== URL 规范化（提高缓存命中） ====================

    CLEAN_CACHE_MAX = 8

    def _clean_get(self, key):
        try:
            v = self._clean_store.get(key)
            if v is None:
                return None
            if key in self._clean_order:
                self._clean_order.remove(key)
            self._clean_order.append(key)
            return v
        except Exception:
            return None

    def _clean_put(self, key, val):
        try:
            if key in self._clean_store and key in self._clean_order:
                self._clean_order.remove(key)
            self._clean_store[key] = val
            self._clean_order.append(key)
            while len(self._clean_order) > self.CLEAN_CACHE_MAX:
                old = self._clean_order.pop(0)
                self._clean_store.pop(old, None)
        except Exception:
            pass

    def _normalize_url(self, url):
        """只剥离纯缓存破坏参数(原盲删 v/t 导致缓存串台)。"""
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        for param in ['_', 'rand', 'cb', 'callback', 'timestamp']:
            query_params.pop(param, None)
        new_query = urlencode(query_params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    # ==================== 爬虫方法 ====================
    def build_url(self, base, query):
        if '?' in base:
            if base.endswith('?') or base.endswith('&'):
                return base + query
            else:
                return base + '&' + query
        else:
            return base + '?' + query

    def fetch_json(self, url, timeout=10, retries=2):
        """HTTPS 失败自动降级 http/跳过证书验证(老旧设备兼容)。"""
        candidates = [url]
        if url.startswith('https://'):
            candidates.append('http://' + url[8:])
        for attempt in range(retries + 1):
            for u in candidates:
                for verify in ((True, False) if u.startswith('https://') else (True,)):
                    try:
                        resp = self.session.get(u, headers=self.headers,
                                                timeout=timeout, verify=verify)
                        if resp.status_code == 200:
                            return resp.json()
                    except Exception:
                        continue
            if attempt < retries:
                time.sleep(0.3 * (attempt + 1))
        return None

    def clean_item(self, item, source_key, source_name, is_detail=False):
        if not is_detail:
            item['vod_id'] = f"{source_key}@@{item['vod_id']}"
        old_remark = item.get('vod_remarks', '')
        item['vod_remarks'] = f"{source_name} | {old_remark}" if old_remark else source_name
        item.pop('vod_down_from', None)
        item.pop('vod_down_url', None)
        return item

    def _fetch_categories(self):
        """获取分类列表(实时 + 失败兜底), 返回 [{type_id, type_name}]。
        供 homeContent 平铺为首页分类标签使用。"""
        url = self.build_url(self.SOURCES['暴风']['api'], "ac=list")
        data = self.fetch_json(url, timeout=8)
        cate = []
        if data and data.get('class'):
            cl = data['class']
            parent_ids = {c.get('type_pid') for c in cl if c.get('type_pid')}
            for c in cl:
                if c['type_id'] in parent_ids:
                    continue
                name = re.sub(r'（中国[^）]*）', '', c.get('type_name', ''))
                name = name.strip()
                if name:
                    cate.append({'type_id': str(c['type_id']), 'type_name': name})
        if not cate:
            cate = [{'type_id': c['type_id'], 'type_name': c['type_name']}
                    for c in self.FALLBACK_CLASSES]
        return cate

    def homeContent(self, filter=False):
        """★改动10: 首页直接平铺全部子分类(无"全部(最新)"入口),
        tid 即分类 ID。"""
        if getattr(self, '_home_cache', None):
            return self._home_cache
        cate = self._fetch_categories()
        result = {'class': cate, 'filters': {}, 'list': []}
        self._home_cache = result
        return result

    def homeVideoContent(self):
        """拉取第一页填充首页信息流并缓存(10分钟)。"""
        if getattr(self, '_home_video_cache', None):
            cached, ts = self._home_video_cache
            if time.time() - ts < 600:
                return cached
        try:
            source_key = next(iter(self.SOURCES))
            source = self.SOURCES[source_key]
            url = self.build_url(source['api'], "ac=detail&pg=1")
            data = self.fetch_json(url, timeout=6)
            result = {'list': []}
            if data and data.get('list'):
                result = {'list': [self.clean_item(v, source_key, source['name'], is_detail=False)
                                   for v in data['list']]}
            self._home_video_cache = (result, time.time())
            return result
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg=1, filter=False, extend=None):
        """★改动10: tid 直接是数字分类 ID。"""
        if not str(tid).isdigit():
            tid = self.FALLBACK_CLASSES[0]['type_id']  # 兜底: 非法 tid 用第一个分类
        source = self.SOURCES['暴风']
        query = f"ac=detail&pg={pg}&t={tid}"
        url = self.build_url(source['api'], query)
        data = self.fetch_json(url, timeout=6)
        if not data:
            return {'list': [], 'page': pg, 'pagecount': 0, 'limit': 20, 'total': 0}
        video_list = []
        for item in data.get('list', []):
            video_list.append(self.clean_item(item, '暴风', source['name'], is_detail=False))
        return {
            'list': video_list,
            'page': data.get('page', pg),
            'pagecount': data.get('pagecount', 0),
            'limit': data.get('limit', 20),
            'total': data.get('total', 0)
        }

    def detailContent(self, ids):
        if not ids:
            return {'list': []}
        full_id = ids[0]
        if '@@' not in full_id:
            return {'list': []}
        source_key, real_id = full_id.split('@@', 1)
        if source_key not in self.SOURCES:
            return {'list': []}
        source = self.SOURCES[source_key]
        url = self.build_url(source['api'], f"ac=detail&ids={real_id}")
        data = self.fetch_json(url, timeout=6)
        if not data or 'list' not in data:
            return {'list': []}
        result_list = []
        for item in data['list']:
            cleaned = self.clean_item(item, source_key, source['name'], is_detail=True)
            cleaned['vod_id'] = full_id
            result_list.append(cleaned)
        return {'list': result_list}

    def searchContent(self, key, quick=False, pg=1):
        keyword = key.strip()
        if not keyword:
            return {'list': [], 'page': pg, 'pagecount': 0, 'limit': 40, 'total': 0}
        encoded_keyword = quote(keyword)
        def search_source(source_key, source_info):
            test_urls = [
                self.build_url(source_info['api'], f"ac=detail&wd={encoded_keyword}&pg={pg}"),
                self.build_url(source_info['api'], f"ac=list&wd={encoded_keyword}&pg={pg}")
            ]
            for url in test_urls:
                data = self.fetch_json(url, timeout=5)
                if not data:
                    continue
                items = None
                if isinstance(data, dict):
                    if 'list' in data:
                        items = data['list']
                    elif 'data' in data and isinstance(data['data'], dict) and 'list' in data['data']:
                        items = data['data']['list']
                    elif 'data' in data and isinstance(data['data'], list):
                        items = data['data']
                if items:
                    return source_key, source_info, data, items
            return source_key, source_info, None, []
        all_items = []
        max_pagecount = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(search_source, k, v): k for k, v in self.SOURCES.items()}
            for future in concurrent.futures.as_completed(futures):
                source_key, source_info, data, items = future.result()
                if items:
                    for item in items:
                        cleaned = self.clean_item(item, source_key, source_info['name'], is_detail=False)
                        all_items.append(cleaned)
                    if data and data.get('pagecount', 0) > max_pagecount:
                        max_pagecount = data.get('pagecount', 0)
        seen = set()
        unique = []
        for v in all_items:
            if v['vod_id'] not in seen:
                seen.add(v['vod_id'])
                unique.append(v)
        return {
            'list': unique,
            'page': pg,
            'pagecount': max_pagecount if max_pagecount else 1,
            'limit': 40,
            'total': 9999
        }

    # ==================== 播放与代理（内置清洗 + 单条目缓存） ====================
    def playerContent(self, flag, id, vipFlags=None):
        raw_url = id
        if '.m3u8' in raw_url:
            proxy_url = self.getProxyUrl(local=True) + f'&action=clean&src={quote(raw_url)}'
            return {
                'parse': 0,
                'url': proxy_url,
            }
        else:
            return {
                'parse': 0,
                'url': raw_url,
                'header': self.headers,
            }

    def localProxy(self, param):
        if param.get('action') != 'clean':
            return [404, 'text/plain', 'unknown action: %s' % param.get('action')]

        src_url = param.get('src')
        if not src_url:
            return [400, 'text/plain', '缺少 src 参数']

        norm_url = self._normalize_url(src_url)
        _hit = self._clean_get(norm_url)
        if _hit is not None:
            return [
                200,
                'application/vnd.apple.mpegurl',
                _hit,
                {
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache, no-store'
                }
            ]

        try:
            content = self._fetch_m3u8(src_url)
            if not content:
                return [500, 'text/plain', '获取 m3u8 失败']
            base_url = src_url.rsplit('/', 1)[0] + '/'
            hops = 0
            while '#EXT-X-STREAM-INF' in content and hops < 3:
                variant = self._pick_variant(content, base_url)
                if not variant:
                    break
                sub_content = self._fetch_m3u8(variant)
                if not sub_content:
                    break
                content = sub_content
                base_url = variant.rsplit('/', 1)[0] + '/'
                hops += 1
            cleaned = self._clean_m3u8_content(content, base_url, debug=False)
            self._clean_put(norm_url, cleaned)
            return [
                200,
                'application/vnd.apple.mpegurl',
                cleaned,
                {
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-cache, no-store'
                }
            ]
        except Exception as e:
            return [500, 'text/plain', '清洗失败: %s' % e]

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass
