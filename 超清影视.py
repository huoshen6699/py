# -*- coding: utf-8 -*-
# 小鸡看看 (gugugu.tv 官方 App) TVBox 爬虫
# 协议要点 (2026-10-28 逆向其安卓 App 的 libsecurity-bridge.so 并实测通过):
# - 接口主机: https://xjggg.top (App 专用接口; 网页版要账号登录, App 版靠签名放行)
#   备用域名 xjgugugu.top 不提供 App 接口 (实测 404), 故主机只有一个, 属协议事实
# - 每请求带签名头 x-aid/x-ave/x-time(毫秒)/x-nonc/x-sign:
#   x-sign = SHA256大写hex("finger=SF-" + FINGER + "&id=com.xmoebr.bdx&nonce=" + nonce
#                          + "&sk=" + SK + "&time=" + 毫秒时间戳 + "&v=105")
#   FINGER = 官方安装包签名证书的 SHA256 指纹 (大写hex, 固定值)
#   SK = App 原生层密钥 (固定值; 由 GG_NATIVE_MATERIAL_V1 与证书指纹/库校验值经
#        SHA256 流异或派生, 代码里按原算法现算, 与 App 本机算出的完全一致)
#   nonce = SHA256(设备UUID) 前8位大写 + 8位数字 (格式与官方 App 一致)
# - 播放: detail_v2 / playback_v2 给每条线路每集一个 qqqq- 加密串,
#   点播时 GET /api.php/app/decode/url/?url=串&vodFrom=线路代码 解出直链 (该接口 code=1 为成功)
# - 本壳 base.fetch 默认读超时仅 5 秒, 所有请求显式 timeout=15 并带重试
import json
import time
import uuid
import hashlib
from urllib.parse import urlencode

from base.spider import Spider as BaseSpider

FINGER = '20325CC2D4A95E4088BB202A4F66B4E792060ED84F5E3F0E8B41D2DE0AE3A279'
SK = bytes(hashlib.sha256(b'GG_NATIVE_MATERIAL_V1' + bytes.fromhex('20325cc2d4a95e4088bb202a4f66b4e792060ed84f5e3f0e8b41d2de0ae3a279') + bytes.fromhex('43ee12ffa18b7c0889999fd80ccf64804d390b079073f3d80d9b547548a2f40b') + b'\x00\x00\x00\x00').digest()[i] ^ bytes.fromhex('e40b53b233d3c80d2721cc1efba48e55ac6939c208285d6c06a092fe4020')[i] for i in range(30))
APP_ID = 'com.xmoebr.bdx'
VERSION = '105'

YEARS = ['2026', '2025', '2024', '2023', '2022', '2021', '2020', '2019', '2018', '2017', '2016', '2015']
AREAS = ['中国大陆', '中国香港', '中国台湾', '美国', '日本', '韩国', '英国', '法国', '德国', '泰国', '印度']
CLASSES = {
    '1': ['剧情', '动作', '喜剧', '爱情', '科幻', '悬疑', '恐怖', '犯罪', '战争', '奇幻', '冒险', '动画', '纪录片', '武侠', '家庭'],
    '2': ['剧情', '爱情', '喜剧', '悬疑', '犯罪', '古装', '都市', '奇幻', '武侠', '战争', '谍战', '家庭', '青春'],
    '3': ['热血', '冒险', '奇幻', '动作', '搞笑', '恋爱', '科幻', '治愈', '日常', '战斗', '校园'],
    '4': ['真人秀', '脱口秀', '音乐', '美食', '旅游', '纪实', '搞笑', '文化', '选秀'],
}


class Spider(BaseSpider):
    def init(self, extend=''):
        self.host = 'https://xjggg.top'
        self.device = str(uuid.uuid4())
        self.headers_base = {
            'x-device-id': self.device,
            'x-device-brand': 'Xiaomi',
            'x-device-model': 'Xiaomi 15 Pro',
            'x-update-id': 'embedded',
            'User-Agent': 'okhttp/4.9.0',
        }

    def getName(self):
        return '小鸡看看'

    def _nonce(self):
        head = hashlib.sha256(self.device.encode()).hexdigest()[:8].upper()
        seed = hashlib.sha256((self.device + str(time.time())).encode()).hexdigest()
        tail = ''.join(str(int(seed[i * 2:i * 2 + 2], 16) % 10) for i in range(8))
        return head + tail

    def _sign(self, ts, nonce):
        payload = ('finger=SF-' + FINGER + '&id=' + APP_ID + '&nonce=' + nonce + '&sk=').encode() + SK + ('&time=' + ts + '&v=' + VERSION).encode()
        return hashlib.sha256(payload).hexdigest().upper()

    def _api(self, path, params=None, attempts=2):
        url = self.host + path
        if params:
            url += '?' + urlencode(params)
        last = None
        for _ in range(attempts):
            ts = str(int(time.time() * 1000))
            nonce = self._nonce()
            headers = dict(self.headers_base)
            headers.update({
                'x-aid': APP_ID, 'x-ave': VERSION, 'x-time': ts,
                'x-nonc': nonce, 'x-sign': self._sign(ts, nonce),
            })
            try:
                resp = self.fetch(url, headers=headers, timeout=15)
                text = resp.text if hasattr(resp, 'text') else str(resp)
                last = json.loads(text)
                if isinstance(last, dict) and last.get('code') in (200, 1):
                    return last
            except Exception:
                last = None
        return last if isinstance(last, dict) else {}

    @staticmethod
    def _vod(item):
        return {
            'vod_id': str(item.get('vod_id', '')),
            'vod_name': item.get('vod_name', ''),
            'vod_pic': item.get('vod_pic', ''),
            'vod_remarks': item.get('vod_remarks', ''),
            'vod_year': str(item.get('vod_year', '')),
        }

    @staticmethod
    def _filters():
        out = {}
        for tid in ('1', '2', '3', '4'):
            out[tid] = [
                {'key': 'class', 'name': '类型', 'value': [{'n': '全部', 'v': ''}] +
                 [{'n': c, 'v': c} for c in CLASSES.get(tid, [])]},
                {'key': 'area', 'name': '地区', 'value': [{'n': '全部', 'v': ''}] +
                 [{'n': a, 'v': a} for a in AREAS]},
                {'key': 'year', 'name': '年份', 'value': [{'n': '全部', 'v': ''}] +
                 [{'n': y, 'v': y} for y in YEARS]},
            ]
        return out

    def homeContent(self, filter):
        result = {'class': [], 'filters': self._filters(), 'list': []}
        try:
            data = self._api('/api.php/app/index/home').get('data') or {}
            for c in data.get('categories') or []:
                result['class'].append({'type_id': str(c.get('type_id')), 'type_name': c.get('type_name', '')})
            result['list'] = [self._vod(v) for v in (data.get('recommend') or [])]
        except Exception:
            pass
        return result

    def homeVideoContent(self):
        try:
            data = self._api('/api.php/app/index/home').get('data') or {}
            return {'list': [self._vod(v) for v in (data.get('recommend') or [])]}
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        result = {'list': [], 'page': int(pg), 'pagecount': 1, 'limit': 24, 'total': 0}
        try:
            params = {'type_id': tid, 'page': pg}
            for k in ('class', 'area', 'year'):
                v = (extend or {}).get(k)
                if v:
                    params[k] = v
            resp = self._api('/api.php/app/filter/vod', params)
            result['list'] = [self._vod(v) for v in (resp.get('data') or [])]
            result['pagecount'] = int(resp.get('pageCount') or 1)
            result['total'] = int(resp.get('total') or len(result['list']))
            result['limit'] = int(resp.get('limit') or 24)
        except Exception:
            pass
        return result

    def detailContent(self, ids):
        result = {'list': []}
        try:
            vod_id = ids[0]
            detail = (self._api('/api.php/app/vod/detail_v2', {'vod_id': vod_id}).get('data') or {})
            meta = detail.get('detail') or {}
            pb = (self._api('/api.php/app/vod/playback_v2', {'vod_id': vod_id}).get('data') or {})
            sources = pb.get('sources') or []
            if not sources and detail.get('playback'):
                sel = detail['playback'].get('selected_source')
                if sel:
                    sources = [{'from': sel.get('from'), 'display_name': sel.get('from'), 'episodes': sel.get('episodes') or []}]
            froms, urls = [], []
            for s in sources:
                eps = s.get('episodes') or []
                if not eps:
                    continue
                froms.append(s.get('display_name') or s.get('from') or '')
                line = []
                for ep in eps:
                    title = ep.get('title') or ('第%s集' % ep.get('position', ''))
                    line.append('%s$%s|%s' % (title, s.get('from', ''), ep.get('url', '')))
                urls.append('#'.join(line))
            vod = {
                'vod_id': str(meta.get('vod_id', vod_id)),
                'vod_name': meta.get('vod_name', ''),
                'vod_pic': meta.get('vod_pic', ''),
                'vod_year': str(meta.get('vod_year', '')),
                'vod_area': meta.get('vod_area', ''),
                'vod_remarks': meta.get('vod_remarks', ''),
                'vod_actor': meta.get('vod_actor', ''),
                'vod_director': meta.get('vod_director', ''),
                'vod_content': meta.get('vod_content', ''),
                'type_name': meta.get('type_name', ''),
                'vod_play_from': '$$$'.join(froms),
                'vod_play_url': '$$$'.join(urls),
            }
            result['list'] = [vod]
        except Exception:
            pass
        return result

    def playerContent(self, flag, id, vipFlags):
        result = {'parse': 0, 'url': '', 'header': {'User-Agent': 'okhttp/4.9.0'}}
        try:
            from_code, blob = (id.split('|', 1) + [''])[:2] if '|' in id else ('', id)
            for _ in range(3):
                resp = self._api('/api.php/app/decode/url/', {'url': blob, 'vodFrom': from_code}, attempts=1)
                url = resp.get('data')
                if resp.get('code') == 1 and isinstance(url, str) and url.startswith('http'):
                    result['url'] = url
                    break
        except Exception:
            pass
        return result

    def searchContent(self, key, quick, pg='1'):
        result = {'list': [], 'page': int(pg), 'pagecount': 1, 'limit': 15, 'total': 0}
        try:
            resp = self._api('/api.php/app/search/index', {'wd': key, 'page': pg})
            result['list'] = [self._vod(v) for v in (resp.get('data') or [])]
            result['pagecount'] = int(resp.get('pageCount') or 1)
            result['total'] = int(resp.get('total') or len(result['list']))
            result['limit'] = int(resp.get('limit') or 15)
        except Exception:
            pass
        return result

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass
