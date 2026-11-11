# -*- coding: utf-8 -*-
# 移动4K (Yidong4K) TVBox/Drpy 源 (type 3) —— v1.1
# 站点: https://vidsite.cloudseekr.com/vid/app/
# 修改: 支持多画质线路(4K/1080P/720P/原画)，默认优先4K；按文件名自动识别画质分组
import base64, hashlib, json, os, re, time
import urllib.parse
import urllib.request

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider:
        pass

try:
    from Crypto.Cipher import AES as _AES
except Exception:
    _AES = None

BASE = "https://vidsite.cloudseekr.com"
HDR = {
    "Origin": BASE,
    "Accept": "application/json, text/plain, */*",
    "Cache-Control": "no-cache",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
    "Referer": BASE + "/vid/app/",
    "Pragma": "no-cache",
    "Content-Type": "application/json; charset=utf-8",
}
YN_KEY = b"PVGDwmcvfs1uV3d1"
YN_HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Content-Type": "application/json;charset=UTF-8",
    "Referer": "https://yun.139.com/",
    "x-deviceinfo": "||3|12.27.0|chrome|131.0.0.0|1||windows 10|546X902|zh-CN|||",
    "hcy-cool-flag": "1",
    "authorization": "",
}
YN_INFO = ("https://share-kd-njs.yun.139.com/yun-share/richlifeApp/devapp/"
           "IOutLink/getOutLinkInfoV6")
YN_CONTENT = ("https://share-kd-njs.yun.139.com/yun-share/richlifeApp/devapp/"
              "IOutLink/getContentInfoFromOutLink")

COLS = [("1", "电影"), ("2", "电视剧"), ("3", "动漫")]

_YNCACHE = {}


def _yn_enc(text):
    if not _AES:
        return None
    iv = os.urandom(16)
    b = text.encode()
    b += bytes([16 - len(b) % 16]) * (16 - len(b) % 16)
    return base64.b64encode(
        iv + _AES.new(YN_KEY, _AES.MODE_CBC, iv).encrypt(b)).decode()


def _yn_dec(text):
    if not _AES:
        return ""
    try:
        b = base64.b64decode(text)
        c = _AES.new(YN_KEY, _AES.MODE_CBC, b[:16]).decrypt(b[16:])
        return c[:-c[-1]].decode("utf-8", "ignore")
    except Exception:
        return ""


def _ep_longname(name):
    base = re.sub(r"\.\w+$", "", name)
    m = re.search(r"[Ee](\d{1,3})(?!\d)", name)
    if not m:
        m = re.search(r"(?<!\d)(\d{1,3})(?!\d)\s*(?:集)?", base)
    return ("第%d集" % int(m.group(1))) if m else base


def _walk(link, caID, dirName, out, depth=0):
    d = {}
    try:
        req = urllib.request.Request(
            YN_INFO, data=_yn_enc(json.dumps({"getOutLinkInfoReq": {
                "account": "", "linkID": link, "passwd": "", "caSrt": 1,
                "coSrt": 1, "srtDr": 0, "bNum": 1, "pCaID": caID, "eNum": 200}})).encode(),
            headers=YN_HDR, method="POST")
        d = (json.loads(_yn_dec(urllib.request.urlopen(req, timeout=60).read().decode()))
             .get("data") or {})
    except Exception:
        pass
    for co in (d.get("coLst") or []):
        nm = co.get("coName") or ""
        if not re.search(r"\.(mkv|mp4|ts|avi|rmvb|wmv|mov|webm|m3u8)$", nm, re.I):
            continue
        out.append({"name": nm, "coID": co.get("coID") or "",
                    "url": co.get("presentURL") or "", "parent": dirName})
    if depth >= 5:
        return
    for ca in (d.get("caLst") or []):
        _walk(link, ca.get("caID") or "", ca.get("caName") or "", out, depth + 1)


class Spider(BaseSpider):
    def init(self, extend=""):
        self.fp = hashlib.md5(
            ("AiNewYiDong4K_%d_%d" % (int(time.time() * 1000), time.time_ns()))
            .encode()).hexdigest()
        # 本次详情实际生成的线路名列表（顺序与 vod_play_from 一致），
        # 用于把播放器传来的"线路索引"映射回线路名
        self._yn_lines = []

    def getName(self):
        return "移动4K"

    def _post(self, path, data):
        try:
            req = urllib.request.Request(BASE + path,
                                         data=json.dumps(data).encode(),
                                         headers=HDR, method="POST")
            return json.load(urllib.request.urlopen(req, timeout=20))
        except Exception:
            return {}

    @staticmethod
    def _card(x):
        return {"vod_id": str(x.get("id", "")),
                "vod_name": x.get("title", ""),
                "vod_pic": x.get("appCoverUrl", ""),
                "vod_remarks": ""}

    @staticmethod
    def _yn_enc(text):
        if not _AES:
            return None
        iv = os.urandom(16)
        b = text.encode()
        b += bytes([16 - len(b) % 16]) * (16 - len(b) % 16)
        return base64.b64encode(
            iv + _AES.new(YN_KEY, _AES.MODE_CBC, iv).encrypt(b)).decode()

    @staticmethod
    def _yn_dec(text):
        if not _AES:
            return ""
        try:
            b = base64.b64decode(text)
            c = _AES.new(YN_KEY, _AES.MODE_CBC, b[:16]).decrypt(b[16:])
            return c[:-c[-1]].decode("utf-8", "ignore")
        except Exception:
            return ""

    def _yn_call(self, url, payload):
        enc = self._yn_enc(payload)
        if not enc:
            return {}
        try:
            req = urllib.request.Request(url, data=enc.encode(),
                                         headers=YN_HDR, method="POST")
            return json.loads(self._yn_dec(
                urllib.request.urlopen(req, timeout=60).read().decode()))
        except Exception:
            return {}

    def _yn_play(self, share_url, depth=0):
        m = re.search(r"/i/([A-Za-z0-9]+)", share_url or "")
        if not m or depth > 3:
            return ""
        link = m.group(1)
        r = self._yn_call(YN_INFO, json.dumps({"getOutLinkInfoReq": {
            "account": "", "linkID": link, "passwd": "", "caSrt": 0,
            "coSrt": 0, "srtDr": 1, "bNum": 1, "pCaID": "root", "eNum": 200}}))
        d = r.get("data") or {}
        for co in (d.get("coLst") or []):
            u = co.get("presentURL") or ""
            if u.startswith("http"):
                return u
        for ca in (d.get("caLst") or []):
            caid = ca.get("caID")
            if not caid:
                continue
            r2 = self._yn_call(YN_INFO, json.dumps({"getOutLinkInfoReq": {
                "account": "", "linkID": link, "passwd": "", "caSrt": 1,
                "coSrt": 1, "srtDr": 0, "bNum": 1, "pCaID": caid, "eNum": 200}}))
            d2 = r2.get("data") or {}
            for co in (d2.get("coLst") or []):
                u = co.get("presentURL") or ""
                if u.startswith("http"):
                    return u
            for co in (d2.get("coLst") or []):
                cid = co.get("coID")
                if not cid:
                    continue
                r3 = self._yn_call(YN_CONTENT, json.dumps({
                    "getContentInfoFromOutLinkReq": {
                        "contentId": cid, "linkID": link, "account": ""},
                    "commonAccountInfo": {"account": "", "accountType": 1}}))
                u = (((r3.get("data") or {}).get("contentInfo") or {})
                     .get("presentURL")) or ""
                if u.startswith("http"):
                    return u
        return ""

    # ── 新增: 画质识别与集数分组 ─────────────────────────
    @staticmethod
    def _extract_quality(name):
        n = name.lower()
        if any(k in n for k in ["4k", "2160p", "uhd", "2160"]):
            return "4K", 40
        if any(k in n for k in ["1080p", "fhd", "1920", "1080"]):
            return "1080P", 30
        if any(k in n for k in ["720p", "hd", "720"]):
            return "720P", 20
        return "原画", 10

    @staticmethod
    def _extract_episode(name):
        m = re.search(r"[Ee][Pp]?(\d{1,3})(?!\d)", name)
        if m:
            return "第%d集" % int(m.group(1))
        m = re.search(r"第\s*(\d{1,3})\s*集", name)
        if m:
            return "第%d集" % int(m.group(1))
        base = re.sub(r"\.\w+$", "", name)
        clean = re.sub(r"(?i)[\s._-]?(4k|2160p|uhd|1080p|fhd|720p|hd|sd)[\s._-]?", "", base)
        m = re.search(r"(?<!\d)(\d{1,3})(?!\d)", clean)
        if m:
            return "第%d集" % int(m.group(1))
        return clean.strip() or "正片"

    @staticmethod
    def _ep_sort_key(name):
        m = re.search(r"第(\d+)集", name)
        return int(m.group(1)) if m else 0

    def _group_by_episode(self, files):
        groups = {}
        for f in files:
            name = f.get("name", "")
            url = f.get("url", "")
            if not url:
                continue
            ep = self._extract_episode(name)
            q_name, _ = self._extract_quality(name)
            parent = f.get("parent", "")
            if ep not in groups:
                groups[ep] = {"urls": {}, "parent": parent, "name": name}
            groups[ep]["urls"][q_name] = url
        return groups

    # ── 接口 ──────────────────────────────────
    def homeContent(self, filter):
        return {"class": [{"type_id": a, "type_name": b} for a, b in COLS],
                "list": []}

    def homeVideoContent(self):
        return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg or 1)
        d = self._post("/vidsite/vs/qryPageColumnVideo", {
            "columnId": int(tid), "cateIdList": [0, 0, 0],
            "pageIndex": page, "pageSize": 9}).get("data") or {}
        out = [self._card(x) for x in (d.get("list") or [])]
        return {"list": out, "page": page,
                "pagecount": max(1, int(d.get("pages") or 1)),
                "limit": 9, "total": int(d.get("total") or len(out))}

    def detailContent(self, ids):
        vid = str(ids[0] if isinstance(ids, list) else ids).split(",")[0]
        self._post("/vidsite/vs/addAccessRecord",
                   {"fprint": self.fp, "deviceType": 1})
        d = self._post("/vidsite/vs/qryVideoDetail",
                       {"videoId": int(vid), "fprint": self.fp}).get("data") or {}
        if not d:
            return {"list": []}
        res_url = d.get("resUrl") or ""
        m = re.search(r"/i/([A-Za-z0-9]+)", res_url)
        episodes = ""
        froms = "移动云盘"
        cache_key = res_url
        if m:
            now = time.time()
            cached = _YNCACHE.get(cache_key)
            if cached and now - cached[0] < 600:
                episodes = cached[1]
                froms = cached[2]
                self._yn_lines = list(cached[3]) if len(cached) > 3 else []
            else:
                fl = []
                try:
                    _walk(m.group(1), "root", "", fl)
                except Exception:
                    fl = []

                # 按集数分组，识别画质
                groups = self._group_by_episode(fl)
                sorted_eps = sorted(groups.keys(), key=self._ep_sort_key)

                # 判断是否需要父目录前缀（多季/多目录）
                parents = set(groups[ep]["parent"] for ep in groups if groups[ep]["parent"])
                need_pre = len(parents) > 1

                # 确定可用画质线路（4K优先）
                all_q = set()
                for g in groups.values():
                    all_q.update(g["urls"].keys())
                quality_order = ["4K", "1080P", "720P", "原画"]
                available_q = [q for q in quality_order if q in all_q]

                # 构建多线路播放地址
                sources = []
                from_list = []
                for q in available_q:
                    lines = []
                    for ep in sorted_eps:
                        info = groups[ep]
                        url = info["urls"].get(q)
                        if not url:
                            # fallback到该集最高可用画质
                            for fq in quality_order:
                                if fq in info["urls"]:
                                    url = info["urls"][fq]
                                    break
                        if not url:
                            continue

                        # 显示名称
                        nm = _ep_longname(info["name"])
                        if need_pre and info["parent"]:
                            pm = re.search(r"^.*?([\u4e00-\u9fa5\w·\s]{2,20})$", info["parent"])
                            pre = pm.group(1).strip() if pm else info["parent"]
                            nm = "%s·%s" % (pre, nm)

                        lines.append("%s$%s" % (nm, url))

                    if lines:
                        sources.append("#".join(lines))
                        from_list.append(q)

                if sources:
                    episodes = "$$$".join(sources)
                    froms = "$$$".join(from_list)
                else:
                    episodes = "正片$url:" + res_url
                    froms = "移动云盘"
                    from_list = []

                # 记录线路顺序，供 playerContent 做"索引 -> 线路名"兜底映射
                self._yn_lines = list(from_list)
                _YNCACHE[cache_key] = (now, episodes, froms, list(from_list))
        else:
            episodes = "正片$url:" + res_url
            self._yn_lines = ["移动云盘"]

        return {"list": [{
            "vod_id": vid,
            "vod_name": d.get("title", vid),
            "vod_pic": d.get("appCoverUrl", ""),
            "vod_year": "",
            "vod_actor": d.get("actor", "") or "",
            "vod_director": d.get("director", "") or "",
            "vod_content": d.get("remark", ""),
            "vod_play_from": froms,
            "vod_play_url": episodes,
        }]}

    # ── 兜底: flag -> 线路名 ─────────────────────────
    def _resolve_flag(self, flag):
        """播放器传给 playerContent 的第一个参数（线路标识）有两种可能：
           1) 线路名: "4K" / "1080P" / "720P" / "原画"      —— 直接用
           2) 线路索引: "0" / "1" / "2" ...              —— 按实际线路顺序换算
        索引基准是 detailContent 里 vod_play_from 的真实顺序（可用线路的子集），
        不是固定的 ["4K","1080P","720P","原画"]，所以要用 self._yn_lines。
        """
        if flag is None:
            return ""
        s = str(flag).strip()
        if not s:
            return ""
        if s.isdigit():
            idx = int(s)
            lines = getattr(self, "_yn_lines", None) or ["4K", "1080P", "720P", "原画"]
            if 0 <= idx < len(lines):
                return str(lines[idx]).upper()
            return ""
        return s.upper()

    # ── 修复: 139 云 HLS 主清单选路,避免播放器默认低画质 ──
    def _pick_rendition(self, u, flag):
        """mcloud.139.com 的 playlist.m3u8 是多码率主清单,
        播放器默认会选到低码率(854x360/480)。这里抓取主清单,
        按线路名(4K/1080P/720P/原画)锁定对应清晰度的子清单 URL。"""
        u = str(u or "")
        if "mcloud.139.com" not in u or ".m3u8" not in u:
            return u
        hdr = {"User-Agent": YN_HDR["User-Agent"], "Referer": "https://yun.139.com/"}
        try:
            req = urllib.request.Request(u, headers=hdr)
            with urllib.request.urlopen(req, timeout=15) as r:
                txt = r.read(65536).decode("utf-8", "ignore")
        except Exception:
            return u
        if "#EXT-X-STREAM-INF" not in txt:
            return u  # 不是主清单,直接播
        rends = []
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        for i, l in enumerate(lines):
            if l.startswith("#EXT-X-STREAM-INF"):
                m = re.search(r"RESOLUTION=(\d+)x(\d+)", l)
                if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                    w = int(m.group(1)) if m else 0
                    h = int(m.group(2)) if m else 0
                    rends.append((w * h, w, h, lines[i + 1]))
        if not rends:
            return u
        want = self._resolve_flag(flag)
        asc = sorted(rends)               # 低 -> 高
        desc = sorted(rends, reverse=True)
        if "480" in want:
            pick = next((r for r in asc if r[2] >= 480), asc[-1])
        elif "720" in want:
            pick = next((r for r in asc if r[2] >= 720), asc[-1])
        elif "1080" in want:
            pick = next((r for r in asc if r[2] >= 1080), desc[0])
        else:                             # 4K/2160/原画/默认 -> 最高档
            pick = desc[0]
        return urllib.parse.urljoin(u, pick[3])

    def playerContent(self, flag, id, vipFlags):
        raw = str(id)
        if raw.startswith("url:"):
            real = self._yn_play(raw[4:]) or raw[4:]
            u = real
        else:
            u = raw
        # [修复] 主清单锁定清晰度,否则播放器默认最低码率(854x360/480)
        u = self._pick_rendition(u, flag)
        return {"parse": 0, "playUrl": "", "url": u, "jx": 0,
                "header": {"User-Agent": YN_HDR["User-Agent"],
                           "Referer": "https://yun.139.com/"}}

    def searchContent(self, key, quick, pg):
        d = self._post("/vidsite/vs/qryPageColumnVideo",
                       {"columnId": 0, "cateIdList": [0, 0, 0], "pageIndex": 1,
                        "pageSize": 9, "searchKey": key}).get("data") or {}
        out = [self._card(x) for x in (d.get("list") or [])]
        return {"list": out, "page": 1, "pagecount": 1, "limit": 9,
                "total": int(d.get("total") or len(out))}

    def isVideoFormat(self, url):
        return any(s in str(url).lower() for s in (".m3u8", ".mp4"))

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        return ""
