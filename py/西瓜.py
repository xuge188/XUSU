# coding=utf-8
#!/usr/bin/python
"""
西瓜视频（影视分类）
- 分类/筛选：POST /api/cinema/filterv2/albums
- 推荐：多分类首页合并
- 搜索：优先 searchv2，失败则多页片库标题匹配
- 详情分集：ib.snssdk.com/vapp/lvideo/api/info/
- 播放直链：vas.snssdk.com/video/openapi/v1/ （Authorization=auth_token + ptoken=business_token）
"""
import sys
sys.path.append('..')
from base.spider import Spider
import base64
import json
import re
import time
from urllib import request, parse


class Spider(Spider):
    SITE = 'https://www.ixigua.com'
    FILTER_API = SITE + '/api/cinema/filterv2/albums'
    SEARCH_API = SITE + '/api/searchv2/lvideo/{0}/{1}'
    LVIDEO_API = 'https://ib.snssdk.com/vapp/lvideo/api/info/'
    PLAY_API = 'https://vas.snssdk.com/video/openapi/v1/'
    ARTICLE_API = 'https://ib.snssdk.com/video/app/article/full/0/0/{0}/0/0/0/'
    PAGE_SIZE = 18

    CATE = {
        '电视剧': 'dianshiju',
        '电影': 'dianying',
        '动漫': 'dongman',
        '纪录片': 'jilupian',
        '少儿': 'shaoer',
        '综艺': 'zongyi',
    }
    CATE_NAME = {v: k for k, v in CATE.items()}

    # 清晰度选择：筛选器可选值（v 为内部标记，与 QUALITY_MAP 对应）
    CLARITY_FILTER = [
        {'n': '自动 (默认最高)', 'v': 'auto'},
        {'n': '4K 超清', 'v': '4k'},
        {'n': '1080P 高清', 'v': '1080p'},
        {'n': '720P', 'v': '720p'},
        {'n': '480P', 'v': '480p'},
        {'n': '360P', 'v': '360p'},
    ]
    # 标记 -> definition 关键字（与接口返回的 definition 字段匹配）
    QUALITY_MAP = {
        'auto': '',
        '4k': '4k',
        '1080p': '1080p',
        '720p': '720p',
        '480p': '480p',
        '360p': '360p',
    }
    # 标记 -> 目标分辨率高度，definition 匹配不到时按高度就近取
    PREFER_HEIGHT = {'4k': 2160, '1080p': 1080, '720p': 720, '480p': 480, '360p': 360}
    # 多播放源（逐集选画质）的标签顺序：标记 + 显示名
    CLARITY_ORDER = [
        ('4k', '4K超清'),
        ('1080p', '1080P高清'),
        ('720p', '720P'),
        ('480p', '480P'),
        ('360p', '360P'),
    ]
    # 兜底源名（探测不到可用清晰度时使用）
    CLARITY_DEFAULT_TAGS = ['4k', '1080p', '720p', '480p', '360p']
    # 接口 filters.type：少儿必须传「儿童」，否则会串成电视剧
    TYPE_FILTER = {
        'dianshiju': '电视剧',
        'dianying': '电影',
        'dongman': '动漫',
        'jilupian': '纪录片',
        'shaoer': '儿童',
        'zongyi': '综艺',
    }
    # albumTypeList 期望值，滤掉串台
    ALBUM_TYPE = {
        'dianshiju': 2,
        'dianying': 1,
        'dongman': 3,
        'jilupian': 5,
        'shaoer': 13,
        'zongyi': 4,
    }

    def getName(self):
        return '西瓜视频'

    def init(self, extend=''):
        self.userid = ''
        self.quality = 'auto'  # 清晰度：auto/4k/1080p/720p/480p/360p
        # 筛选表写入 config，兼容壳直接读 self.config['filter']
        self.config['filter'] = self.build_filters()

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def homeContent(self, filter):
        if not self.userid:
            try:
                self.userid = self.get_userid() or ''
            except Exception:
                self.userid = ''
        classes = [{'type_name': k, 'type_id': v} for k, v in self.CATE.items()]
        if self.userid:
            classes.append({'type_name': '关注', 'type_id': 'follow'})
        result = {'class': classes}
        if filter:
            result['filters'] = self.config.get('filter') or self.build_filters()
        # 部分壳只读 homeContent 的 list 当推荐
        try:
            result['list'] = self.build_home_videos(36)
        except Exception:
            result['list'] = []
        return result

    def homeVideoContent(self):
        try:
            return {'list': self.build_home_videos(48)}
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        result = {'list': [], 'page': pg, 'pagecount': 1, 'limit': 0, 'total': 0}
        page = self.to_int(pg, 1)
        extend = extend or {}
        # 记录用户在筛选器里选的清晰度，供 playerContent 取对应直链
        if extend.get('clarity'):
            self.quality = extend.get('clarity')
        videos = []
        try:
            if tid == 'follow':
                if not self.userid:
                    return result
                offset = 0 if page < 2 else 20 * page
                url = (
                    self.SITE
                    + '/api/userv2/follow/list?authorId={0}&sortType=desc&cursor={1}'.format(
                        self.userid, offset
                    )
                )
                rsp = self.fetch(url, headers=self.header)
                videos = self.parse_follow(rsp.text if rsp else '')
            else:
                videos = self.fetch_albums(tid, page, extend)
        except Exception:
            videos = []
        result['list'] = videos
        result['limit'] = self.PAGE_SIZE
        result['total'] = len(videos)
        if len(videos) >= self.PAGE_SIZE:
            result['pagecount'] = page + 1
        else:
            result['pagecount'] = page
        return result

    def detailContent(self, array):
        result = {}
        if not array:
            return result
        meta = self.unpack_id(array[0])
        if not meta.get('albumId'):
            return result

        album_id = meta['albumId']
        title = meta.get('title') or album_id
        pic = meta.get('pic') or ''
        actor = meta.get('actor') or ''
        area = meta.get('area') or ''
        year = meta.get('year') or ''
        tags = meta.get('tags') or ''
        intro = meta.get('intro') or ''
        eps = self.to_int(meta.get('eps'), 1)
        aweme = meta.get('aweme') or ''
        is_user = meta.get('user') == '1'

        play_items = []
        if is_user:
            play_items = self.fetch_user_videos(album_id, title)
        else:
            play_items = self.fetch_album_playlist(album_id)
            if not play_items:
                play_items = self.build_fallback_playlist(album_id, aweme, eps)

        if not play_items:
            return result

        info = self.fetch_album_info(album_id)
        if info:
            tags = tags or info.get('tags') or ''
            area = area or info.get('area') or ''
            intro = intro or info.get('intro') or ''
            actor = actor or info.get('actor') or ''
            year = year or info.get('year') or ''
            if info.get('director'):
                meta['director'] = info.get('director')
            if info.get('eps'):
                eps = info.get('eps') or eps
            if info.get('pic') and not pic:
                pic = info.get('pic')

        play_from, play_url = self.build_clarity_sources(play_items)
        vod = {
            'vod_id': array[0],
            'vod_name': title,
            'vod_pic': pic,
            'type_name': tags,
            'vod_year': year,
            'vod_area': area,
            'vod_remarks': (str(eps) + '集') if eps > 1 else '',
            'vod_actor': actor,
            'vod_director': meta.get('director') or '',
            'vod_content': intro,
            'vod_play_from': play_from,
            'vod_play_url': play_url,
        }
        return {'list': [vod]}

    def searchContent(self, key, quick, pg='1'):
        return self.searchContentPage(key, quick, pg)

    def searchContentPage(self, key, quick, pg='1'):
        videos = []
        key = (key or '').strip()
        if not key:
            return {'list': videos}
        page = self.to_int(pg, 1)

        # 1) 官方搜索（壳有 cookie / 反爬放行时可用）
        try:
            offset = 0 if page < 2 else (page - 1) * 10
            url = self.SEARCH_API.format(parse.quote(key), offset)
            rsp = self.fetch(url, headers=self.header)
            text = rsp.text if rsp else ''
            if text and text.lstrip()[:1] == '{':
                videos = self.parse_search(text)
                if videos:
                    return {'list': videos}
        except Exception:
            pass

        # 2) 片库多页标题匹配（官方搜索被拦时的可用兜底）
        if quick and page > 1:
            return {'list': videos}
        try:
            videos = self.search_by_catalog(key, page=page, quick=quick)
        except Exception:
            videos = []
        return {'list': videos}

    def playerContent(self, flag, id, vipFlags):
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36'
            ),
            'Referer': self.SITE + '/',
            'Origin': self.SITE,
        }
        raw = (id or '').strip()
        if raw.rsplit('_', 1)[-1] in ('true', 'false'):
            raw = raw.rsplit('_', 1)[0]

        play_url = self.resolve_direct_play(raw)
        if play_url:
            return {
                'parse': 0,
                'jx': 0,
                'playUrl': '',
                'url': play_url,
                'header': headers,
            }

        # 直链失败再交给壳解析
        if raw.startswith('http://') or raw.startswith('https://'):
            url = raw
        elif '?id=' in raw:
            album_id, ep_id = raw.split('?id=', 1)
            target = (ep_id or album_id).strip()
            url = self.SITE + '/' + target
        else:
            url = self.SITE + '/' + raw
        return {
            'parse': 1,
            'jx': 1,
            'playUrl': '',
            'url': url,
            'header': headers,
        }

    # -------------------- fetch helpers --------------------

    def build_home_videos(self, limit=48):
        out = []
        seen = set()
        # 推荐优先热门分类
        for tid in ('dianshiju', 'dianying', 'zongyi', 'dongman'):
            try:
                rows = self.fetch_albums(tid, 1, {'sort': '热度最高'})
            except Exception:
                rows = []
            if not rows:
                try:
                    rows = self.fetch_albums(tid, 1, {})
                except Exception:
                    rows = []
            for vod in rows:
                vid = vod.get('vod_id') or ''
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                out.append(vod)
                if len(out) >= limit:
                    return out
        return out

    def ft_values(self, names, keep_all_label=True):
        """TVBox 筛选项：n 展示，v 传给接口的中文值。"""
        arr = []
        for name in names:
            arr.append({'n': name, 'v': name})
        return arr

    def build_filters(self):
        sort_vals = self.ft_values(['综合排序', '最新上线', '热度最高', '评分最高', '最多播放'])
        paid_vals = self.ft_values(['全部资费', '免费', '付费'])
        area_tv = self.ft_values(
            ['全部地区', '内地', '韩国', '中国香港', '中国台湾', '美国', '日本', '泰国', '英国', '新加坡', '其他']
        )
        area_movie = self.ft_values(
            ['全部地区', '内地', '韩国', '中国香港', '美国', '日本', '英国', '法国', '其他']
        )
        area_anime = self.ft_values(['全部地区', '内地', '日本', '美国', '其他'])
        tag_tv = self.ft_values(
            ['全部类型', '爱情', '古装', '悬疑', '喜剧', '剧情', '奇幻', '动作', '犯罪', '都市', '家庭', '历史', '军旅', '武侠', '战争', '其他']
        )
        tag_movie = self.ft_values(
            ['全部类型', '喜剧', '动作', '爱情', '科幻', '恐怖', '战争', '动画', '犯罪', '奇幻', '惊悚', '历史', '古装']
        )
        tag_anime = self.ft_values(['全部类型', '热血', '恋爱', '奇幻', '搞笑', '冒险', '科幻', '治愈', '其他'])
        tag_doc = self.ft_values(['全部类型', '自然', '历史', '人物', '社会', '科技', '其他'])
        tag_kids = self.ft_values(['全部类型', '动画', '益智', '儿歌', '其他'])
        tag_zy = self.ft_values(['全部类型', '真人秀', '访谈', '音乐', '搞笑', '选秀', '其他'])

        def pack(area, tag):
            return [
                {'key': 'area', 'name': '地区', 'value': area},
                {'key': 'tag', 'name': '类型', 'value': tag},
                {'key': 'sort', 'name': '排序', 'value': sort_vals},
                {'key': 'paid', 'name': '资费', 'value': paid_vals},
                {'key': 'clarity', 'name': '清晰度', 'value': self.CLARITY_FILTER},
            ]

        return {
            'dianshiju': pack(area_tv, tag_tv),
            'dianying': pack(area_movie, tag_movie),
            'dongman': pack(area_anime, tag_anime),
            'jilupian': pack(area_movie, tag_doc),
            'shaoer': pack(area_anime, tag_kids),
            'zongyi': pack(area_tv, tag_zy),
        }

    def fetch_albums(self, tid, page, extend=None):
        extend = dict(extend or {})
        # 综艺「综合排序」会整页串成电视剧，改走最新上线
        if tid == 'zongyi':
            sort = (extend.get('sort') or '综合排序').strip() or '综合排序'
            if sort == '综合排序':
                extend['sort'] = '最新上线'
        expect = self.ALBUM_TYPE.get(tid) or 0
        if expect <= 0:
            return self.fetch_albums_page(tid, page, extend, 0)
        need_skip = max(page - 1, 0) * self.PAGE_SIZE
        matched = []
        for api_page in range(1, 31):
            if len(matched) >= need_skip + self.PAGE_SIZE:
                break
            body = self.request_albums_body(tid, api_page, extend)
            raw = self.parse_album_list(body, 0)
            if not raw:
                break
            matched.extend(self.parse_album_list(body, expect))
        return matched[need_skip:need_skip + self.PAGE_SIZE]

    def fetch_albums_page(self, tid, page, extend=None, expect_album_type=0):
        body = self.request_albums_body(tid, page, extend or {})
        return self.parse_album_list(body, expect_album_type)

    def request_albums_body(self, tid, page, extend=None):
        extend = extend or {}
        id_txt = self.TYPE_FILTER.get(tid) or self.CATE_NAME.get(tid, '电视剧')
        offset = 0 if page < 2 else self.PAGE_SIZE * (page - 1)
        area = (extend.get('area') or '全部地区').strip() or '全部地区'
        tag = (extend.get('tag') or '全部类型').strip() or '全部类型'
        sort = (extend.get('sort') or '综合排序').strip() or '综合排序'
        paid = (extend.get('paid') or '全部资费').strip() or '全部资费'
        filters = {
            'type': id_txt,
            'area': area,
            'tag': tag,
            'sort': sort,
            'paid': paid,
        }
        payload = {
            'pinyin': tid,
            'filters': filters,
            'offset': offset,
            'limit': self.PAGE_SIZE,
        }
        headers = dict(self.header)
        headers['Referer'] = self.SITE + '/cinema/filter/{0}/'.format(tid)
        headers['Origin'] = self.SITE
        headers['content-type'] = 'application/json'
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = request.Request(url=self.FILTER_API, data=data, headers=headers, method='POST')
        response = request.urlopen(req, timeout=20)
        return response.read().decode('utf-8', 'ignore')

    def search_by_catalog(self, key, page=1, quick=False):
        """片库扫描兜底：按标题/演员包含匹配。

        背景：官方搜索接口 /api/searchv2/lvideo 已被 Argus 签名保护，
        纯 Python 无法调用（带 ixigua 域名 Referer 一律返回 SPA 网页壳），
        因此只能用筛选接口 /api/cinema/filterv2/albums 做片库扫描兜底。

        三个关键优化：
        1) 用 fetch_albums_page 而不是 fetch_albums——后者内部带「专辑类型过滤循环」，
           遇到串台分类（综艺返回电视剧）会为了凑够 18 部同类型专辑连翻几十页，
           把预算吃光导致后面的分类扫不到。
        2) 带超时预算，超了就先返回已找到的，避免 TVBox 判搜索失败。
        3) 片库索引缓存：扫过的专辑留在内存，下次搜索秒出，并逐次向深处补扫，越用越全。
        """
        key_l = (key or '').strip().lower()
        if not key_l:
            return []
        page = max(self.to_int(page, 1), 1)
        if not getattr(self, '_cat_index', None):
            self._cat_index = {}   # albumId -> vod，已扫描的片库
            self._cat_scanned = {} # tid -> 已扫到的页码
        if getattr(self, '_cat_done', None) is None:
            self._cat_done = set() # 已翻到空页（扫到尽头）的分类
        budget = 5.0 if quick else 12.0  # 秒
        deadline = time.time() + budget
        order = ['dianying', 'dianshiju', 'zongyi', 'dongman', 'jilupian', 'shaoer']
        index_cap = 6000  # 内存上限

        def hit(vod):
            name = (vod.get('vod_name') or '').lower()
            actor = (self.unpack_id(vod.get('vod_id') or '').get('actor') or '').lower()
            return key_l in name or key_l in actor

        # 1) 先查本地索引（不联网，秒回）；命中够多就不必再联网
        hits = [v for v in self._cat_index.values() if hit(v)]
        if len(hits) >= max(page * 20, 10):
            return self._slice_dedup(hits, page)

        # 2) 预算内继续扫描，扩充索引（从上次扫到的页继续翻）
        for tid in order:
            if time.time() > deadline:
                break
            if tid in self._cat_done:  # 该分类已扫到尽头，跳过
                continue
            start = self._cat_scanned.get(tid, 0) + 1
            # 综艺「综合排序」必然串台，改走最新上线
            sort = '最新上线' if tid == 'zongyi' else '综合排序'
            for pg in range(start, start + (1 if quick else 2)):
                if time.time() > deadline:
                    break
                try:
                    rows = self.fetch_albums_page(tid, pg, {'sort': sort}, 0)
                except Exception:
                    rows = []
                if not rows:
                    self._cat_done.add(tid)
                    break
                for vod in rows:
                    aid = self.unpack_id(vod.get('vod_id') or '').get('albumId') or ''
                    if aid and len(self._cat_index) < index_cap:
                        self._cat_index[aid] = vod
                self._cat_scanned[tid] = pg

        # 3) 用扩充后的索引再匹配一次
        hits = [v for v in self._cat_index.values() if hit(v)]
        return self._slice_dedup(hits, page)

    def _slice_dedup(self, hits, page, size=20):
        out = []
        seen = set()
        for vod in hits:
            aid = self.unpack_id(vod.get('vod_id') or '').get('albumId') or ''
            if not aid or aid in seen:
                continue
            seen.add(aid)
            out.append(vod)
        start = (page - 1) * size
        return out[start:start + size]

    def fetch_lvideo(self, album_id, episode_id=''):
        qs = {
            'album_id': str(album_id),
            'aid': '1768',
            'format': 'json',
            'query_type': '0',
        }
        if episode_id:
            qs['episode_id'] = str(episode_id)
        url = self.LVIDEO_API + '?' + parse.urlencode(qs)
        headers = {
            'User-Agent': 'okhttp/3.12.1',
            'Accept': 'application/json',
            'Referer': 'https://m.ixigua.com/',
        }
        try:
            req = request.Request(url, headers=headers)
            text = request.urlopen(req, timeout=25).read().decode('utf-8', 'ignore')
            return json.loads(text)
        except Exception:
            return {}

    def fetch_album_playlist(self, album_id):
        # 优先移动端长视频接口（不受 PC 反爬影响）
        info = self.fetch_lvideo(album_id)
        items = self.playlist_from_lvideo(info, album_id)
        if items:
            return items

        # 旧 PC 详情接口兜底
        url = self.SITE + '/api/albumv2/details?albumId={0}'.format(album_id)
        try:
            rsp = self.fetch(url, headers=self.header)
            text = rsp.text if rsp else ''
            if not text or text.lstrip()[:1] != '{':
                return []
            root = json.loads(text)
            if root.get('code') != 200:
                return []
            data = root.get('data') or {}
            playlist = data.get('playlist') or []
            items = []
            for value in playlist:
                title = value.get('title') or '正片'
                ep = value.get('episodeId') or ''
                aid = value.get('albumId') or album_id
                if ep:
                    play = '{0}?id={1}'.format(aid, ep)
                else:
                    play = str(aid)
                items.append('{0}${1}'.format(title, play))
            return items
        except Exception:
            return []

    def playlist_from_lvideo(self, info, album_id):
        items = []
        cells = []
        for block in info.get('block_list') or []:
            if block.get('type') == 1001:
                cells = block.get('cells') or []
                break
        if not cells and info.get('episode'):
            cells = [{'episode': info.get('episode')}]
        for cell in cells:
            ep = (cell or {}).get('episode') or {}
            ep_id = str(ep.get('episode_id') or '')
            seq = ep.get('seq')
            title = (ep.get('title') or ep.get('name') or '').strip()
            if not title:
                title = '第{0}集'.format(seq) if seq else '正片'
            if not ep_id:
                vi = ep.get('video_info') or {}
                vid = vi.get('vid') or ''
                if not vid:
                    continue
                play = '{0}?vid={1}'.format(album_id, vid)
            else:
                play = '{0}?id={1}'.format(album_id, ep_id)
            items.append('{0}${1}'.format(title, play))
        return items

    def fetch_album_info(self, album_id):
        info = self.fetch_lvideo(album_id)
        album = info.get('album') or {}
        if album:
            tags = '/'.join(album.get('tag_list') or [])
            area = '/'.join(album.get('area_list') or [])
            directors = album.get('director_list') or []
            director = '/'.join([d.get('name') for d in directors if d.get('name')])
            actors = album.get('actor_list') or []
            actor = '/'.join([a.get('name') for a in actors[:6] if a.get('name')])
            covers = album.get('cover_list') or []
            pic = ''
            if covers:
                pic = covers[0].get('url') or ''
                urls = covers[0].get('url_list') or []
                if not pic and urls:
                    pic = urls[0]
            return {
                'tags': tags,
                'area': area,
                'intro': album.get('intro') or '',
                'director': director,
                'actor': actor,
                'year': str(album.get('year') or ''),
                'eps': self.to_int(album.get('total_episodes') or album.get('latest_seq'), 0),
                'pic': pic,
            }

        url = self.SITE + '/api/albumv2/details?albumId={0}'.format(album_id)
        try:
            rsp = self.fetch(url, headers=self.header)
            text = rsp.text if rsp else ''
            if not text or text.lstrip()[:1] != '{':
                return {}
            root = json.loads(text)
            if root.get('code') != 200:
                return {}
            ainfo = (root.get('data') or {}).get('albumInfo') or {}
            tags = '/'.join(ainfo.get('tagList') or [])
            area = '/'.join(ainfo.get('areaList') or [])
            directors = ainfo.get('directorList') or []
            director = '/'.join([d.get('name') for d in directors if d.get('name')])
            return {
                'tags': tags,
                'area': area,
                'intro': ainfo.get('intro') or '',
                'director': director,
                'actor': '',
                'year': str(ainfo.get('year') or ''),
            }
        except Exception:
            return {}

    def fetch_user_videos(self, user_id, title):
        url = self.SITE + '/api/videov2/author/new_video_list?to_user_id={0}'.format(user_id)
        try:
            rsp = self.fetch(url, headers=self.header)
            text = rsp.text if rsp else ''
            if not text or text.lstrip()[:1] != '{':
                return []
            root = json.loads(text)
            if root.get('code') != 200:
                return []
            video_list = (root.get('data') or {}).get('videoList') or []
            items = []
            for value in video_list:
                name = value.get('title') or title
                gid = value.get('group_id')
                if not gid:
                    continue
                items.append('{0}${1}'.format(name, gid))
            return items
        except Exception:
            return []

    def build_fallback_playlist(self, album_id, aweme, eps):
        target = aweme or album_id
        if eps <= 1:
            return ['正片$' + str(target)]
        items = []
        n = min(max(eps, 1), 120)
        for i in range(1, n + 1):
            items.append('第{0}集${1}'.format(i, target))
        if eps > 120:
            items.append('全集$' + str(target))
        return items

    def resolve_direct_play(self, raw):
        raw = (raw or '').strip()
        if not raw:
            return ''
        # 多清晰度：play 串末尾可能带 @清晰度标记（如 albumId?ep=1@1080p）
        if '@' in raw:
            body, q = raw.rsplit('@', 1)
            if q in self.QUALITY_MAP:
                self.quality = q
                raw = body.strip()

        # 已是媒体地址
        if raw.startswith('http://') or raw.startswith('https://'):
            if any(x in raw for x in ('.mp4', '.m3u8', 'video/tos', '365yg.com', 'ixigua.com/video')):
                if 'ixigua.com/' in raw and '/video/' not in raw and '365yg' not in raw:
                    pass
                else:
                    return raw
            # 页面 URL：尝试抽数字 id
            m = re.search(r'ixigua\.com/(?:i)?(\d+)', raw)
            if m:
                return self.resolve_by_group_or_album(m.group(1))
            return ''

        album_id = ''
        episode_id = ''
        vid = ''
        if '?id=' in raw:
            album_id, episode_id = raw.split('?id=', 1)
            album_id = album_id.strip()
            episode_id = episode_id.strip()
        elif '?vid=' in raw:
            album_id, vid = raw.split('?vid=', 1)
            album_id = album_id.strip()
            vid = vid.strip()
        elif raw.isdigit():
            return self.resolve_by_group_or_album(raw)
        else:
            album_id = raw

        if album_id:
            url = self.resolve_lvideo_play(album_id, episode_id=episode_id, vid=vid)
            if url:
                return url
        if episode_id and episode_id.isdigit():
            return self.resolve_by_group_or_album(episode_id)
        return ''

    def resolve_by_group_or_album(self, oid):
        # 先当专辑
        url = self.resolve_lvideo_play(oid)
        if url:
            return url
        # 再当短视频/图文 group_id
        return self.resolve_article_play(oid)

    def resolve_lvideo_play(self, album_id, episode_id='', vid=''):
        info = self.fetch_lvideo(album_id, episode_id=episode_id)
        vi = self.pick_video_info(info, episode_id=episode_id, vid=vid)
        if not vi:
            return ''
        return self.openapi_play_url(vi)

    def pick_video_info(self, info, episode_id='', vid=''):
        candidates = []
        if info.get('episode'):
            candidates.append(info.get('episode') or {})
        for block in info.get('block_list') or []:
            if block.get('type') != 1001:
                continue
            for cell in block.get('cells') or []:
                candidates.append((cell or {}).get('episode') or {})

        if episode_id:
            for ep in candidates:
                if str(ep.get('episode_id') or '') == str(episode_id):
                    return ep.get('video_info') or {}
        if vid:
            for ep in candidates:
                vi = ep.get('video_info') or {}
                if str(vi.get('vid') or '') == str(vid):
                    return vi
        # 默认第一集 / 当前集
        for ep in candidates:
            vi = ep.get('video_info') or {}
            if vi.get('vid') and vi.get('auth_token') and vi.get('business_token'):
                return vi
        return {}

    def openapi_play_url(self, vi):
        vl = self.openapi_video_list(vi)
        if not vl:
            return ''
        return self.pick_best_main_url(vl)

    def openapi_video_list(self, vi):
        vid = vi.get('vid') or ''
        auth = vi.get('auth_token') or ''
        biz = vi.get('business_token') or ''
        if not vid or not auth or not biz:
            return {}
        qs = parse.urlencode(
            {
                'action': 'GetPlayInfo',
                'video_id': vid,
                'nobase64': '1',
                'ptoken': biz,
                'vfrom': 'xgplayer',
            }
        )
        url = self.PLAY_API + '?' + qs
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Authorization': auth,
            'Origin': self.SITE,
            'Referer': self.SITE + '/',
            'Accept': 'application/json',
        }
        try:
            req = request.Request(url, headers=headers)
            text = request.urlopen(req, timeout=25).read().decode('utf-8', 'ignore')
            root = json.loads(text)
        except Exception:
            return {}
        data = root.get('data') or {}
        # 该接口成功 status=10
        if data.get('message') != 'success' and data.get('status') not in (10, '10', 0, '0'):
            return {}
        return data.get('video_list') or {}

    def pick_best_main_url(self, video_list):
        if not isinstance(video_list, dict):
            return ''
        items = [it for it in video_list.values() if isinstance(it, dict)]
        if not items:
            return ''

        prefer = (getattr(self, 'quality', '') or 'auto')
        target_def = self.QUALITY_MAP.get(prefer)
        if target_def:
            # 1) 按 definition 精确匹配
            for it in items:
                if str(it.get('definition') or '').lower() == target_def:
                    return self._url_of(it)
            # 2) 按目标高度就近匹配（如 4k->2160、1080p->1080）
            th = self.PREFER_HEIGHT.get(target_def)
            if th:
                best = None
                best_diff = 1 << 30
                for it in items:
                    h = self.to_int(it.get('vheight'), 0)
                    if not h:
                        continue
                    d = abs(h - th)
                    if d < best_diff or (
                        d == best_diff and best and h > self.to_int(best.get('vheight'), 0)
                    ):
                        best_diff = d
                        best = it
                if best:
                    return self._url_of(best)
            # 指定的清晰度取不到，回退到下方自动选最高

        # 自动：选最高可用清晰度（4k/HDR 易卡，降权）
        rank = {
            '4k': 4000,
            '1080p': 1080,
            '720p': 720,
            '540p': 540,
            '480p': 480,
            '360p': 360,
            'normal': 400,
        }
        best = None
        best_score = -1
        for item in items:
            defn = str(item.get('definition') or '').lower()
            # 电视优先 1080；过高码率 4k/qihao 易卡
            if defn in ('4k', 'qihao', 'hdr', 'dolby'):
                score = 900
            elif defn in rank:
                score = rank[defn]
            else:
                score = min(self.to_int(item.get('vheight'), 0), 1080)
            if score > best_score:
                best_score = score
                best = item
        if not best:
            return ''
        main = best.get('main_url') or best.get('backup_url_1') or ''
        return self.maybe_b64_url(main)

    def _url_of(self, item):
        main = (item or {}).get('main_url') or (item or {}).get('backup_url_1') or ''
        return self.maybe_b64_url(main)

    def resolve_video_list(self, raw):
        """按 play 串解析出该分集的「全清晰度 video_list」，供清晰度探测与多源构建。"""
        raw = (raw or '').strip()
        if not raw:
            return {}
        album_id = ''
        episode_id = ''
        vid = ''
        if '?id=' in raw:
            album_id, episode_id = raw.split('?id=', 1)
        elif '?vid=' in raw:
            album_id, vid = raw.split('?vid=', 1)
        elif raw.isdigit():
            album_id = raw
        else:
            album_id = raw
        album_id = album_id.strip()
        episode_id = episode_id.strip()
        vid = vid.strip()
        if album_id:
            info = self.fetch_lvideo(album_id, episode_id=episode_id)
            vi = self.pick_video_info(info, episode_id=episode_id, vid=vid)
            if vi:
                return self.openapi_video_list(vi)
        if episode_id and episode_id.isdigit():
            info = self.fetch_lvideo(episode_id)
            vi = self.pick_video_info(info)
            if vi:
                return self.openapi_video_list(vi)
        return {}

    def detect_clarities(self, play_items):
        """探测该剧集实际可用的清晰度标记集合（取首个能解析的分集）。"""
        tags = set()
        for it in play_items:
            play = it.split('$', 1)[1] if '$' in it else ''
            if not play:
                continue
            vl = self.resolve_video_list(play)
            if not vl:
                continue
            for item in vl.values():
                if not isinstance(item, dict):
                    continue
                d = str(item.get('definition') or '').lower()
                if d in self.QUALITY_MAP and d:
                    tags.add(d)
                else:
                    h = self.to_int(item.get('vheight'), 0)
                    if h:
                        for t, hgt in self.PREFER_HEIGHT.items():
                            if abs(h - hgt) <= 80:
                                tags.add(t)
            if tags:
                return tags
        return tags

    def build_clarity_sources(self, play_items):
        """把分集列表拼成多清晰度播放源。

        返回 (vod_play_from, vod_play_url)：
        - vod_play_from: 清晰度显示名，用 $$$ 分隔（即 TVBox 里的“播放器/线路”名）
        - vod_play_url: 每组对应一个清晰度，组间 $$$，组内分集用 #，
          每个分集的 play 串末尾带 @清晰度标记，播放时据此选画质。
        """
        if not play_items:
            return '西瓜视频', ''
        avail = self.detect_clarities(play_items)
        tags = [t for t, _ in self.CLARITY_ORDER if (not avail or t in avail)]
        if not tags:
            tags = list(self.CLARITY_DEFAULT_TAGS)
        names = [disp for t, disp in self.CLARITY_ORDER if t in tags]
        groups = []
        for t in tags:
            parts = []
            for it in play_items:
                if '$' in it:
                    title, play = it.split('$', 1)
                else:
                    title, play = it, ''
                parts.append('{0}${1}@{2}'.format(title, play, t))
            groups.append('#'.join(parts))
        return '$$$'.join(names), '$$$'.join(groups)

    def maybe_b64_url(self, s):
        s = (s or '').strip()
        if not s:
            return ''
        if s.startswith('http://') or s.startswith('https://'):
            return s
        try:
            pad = '=' * ((4 - len(s) % 4) % 4)
            out = base64.b64decode(s + pad).decode('utf-8', 'ignore').strip()
            if out.startswith('http://') or out.startswith('https://'):
                return out
        except Exception:
            pass
        return ''

    def resolve_article_play(self, group_id):
        url = self.ARTICLE_API.format(group_id)
        headers = {
            'User-Agent': 'okhttp/3.12.1',
            'Accept': 'application/json',
        }
        try:
            req = request.Request(url, headers=headers)
            text = request.urlopen(req, timeout=20).read().decode('utf-8', 'ignore')
            root = json.loads(text)
        except Exception:
            return ''
        data = root.get('data') or {}
        vpi = data.get('video_play_info') or ''
        if not vpi:
            return ''
        try:
            if isinstance(vpi, str):
                vpi = json.loads(vpi)
        except Exception:
            return ''
        # video_list 或 dynamic_video
        video_list = vpi.get('video_list') or {}
        if isinstance(video_list, dict) and video_list:
            return self.pick_best_main_url(video_list)
        dyn = ((vpi.get('dynamic_video') or {}).get('dynamic_video_list')) or []
        best = None
        best_h = -1
        for item in dyn:
            h = self.to_int(item.get('vheight') or item.get('height'), 0)
            if h >= best_h:
                best_h = h
                best = item
        if best:
            return self.maybe_b64_url(best.get('main_url') or best.get('backup_url_1') or '')
        return ''

    def get_userid(self):
        try:
            rsp = self.fetch(self.SITE + '/', headers=self.header)
            html = rsp.text if rsp else ''
            return self.get_RegexGetText(html, r'"identity":\{"id":"(\d+?)"', 1)
        except Exception:
            return ''

    # -------------------- parsers --------------------

    def parse_album_list(self, json_txt, expect_album_type=0):
        videos = []
        try:
            root = json.loads(json_txt)
        except Exception:
            return videos
        if root.get('code') != 200:
            return videos
        album_list = (root.get('data') or {}).get('albumList') or []
        for vod in album_list:
            if expect_album_type > 0:
                types = vod.get('albumTypeList') or []
                try:
                    types = [int(x) for x in types]
                except Exception:
                    types = []
                if expect_album_type not in types:
                    continue
            title = (vod.get('title') or '').strip()
            album_id = vod.get('albumId')
            if not title or not album_id:
                continue
            covers = vod.get('coverList') or []
            pic = ''
            if covers:
                pic = covers[0].get('url') or ''
            actors = vod.get('actorList') or []
            if actors and len(actors) > 4:
                actors = actors[:4]
            actor = '/'.join([str(a) for a in actors]) if actors else ''
            areas = vod.get('areaList') or []
            area = '/'.join([str(a) for a in areas]) if areas else ''
            tags = vod.get('tagList') or []
            tag = '/'.join([str(t) for t in tags]) if tags else ''
            intro = vod.get('intro') or ''
            year = str(vod.get('year') or '')
            eps = self.to_int(vod.get('totalEpisodes') or vod.get('latestSeq'), 1)
            aweme = str(vod.get('AwemeItemId') or '')
            remarks = vod.get('subTitle') or vod.get('bottomLabel') or ''
            vod_id = self.pack_id(
                title=title,
                album_id=album_id,
                actor=actor,
                pic=pic,
                year=year,
                area=area,
                intro=intro,
                tags=tag,
                eps=eps,
                aweme=aweme,
            )
            videos.append(
                {
                    'vod_id': vod_id,
                    'vod_name': title,
                    'vod_pic': pic,
                    'vod_remarks': remarks,
                }
            )
        return videos

    def parse_follow(self, json_txt):
        videos = []
        try:
            root = json.loads(json_txt)
        except Exception:
            return videos
        if root.get('code') != 0:
            return videos
        rows = (root.get('data') or {}).get('data') or []
        for vod in rows:
            title = (vod.get('name') or '').strip()
            user_id = vod.get('user_id')
            if not title or not user_id:
                continue
            pic = vod.get('avatar_url') or ''
            remarks = vod.get('description') or ''
            vod_id = self.pack_id(
                title=title,
                album_id=user_id,
                actor=title,
                pic=pic,
                year='',
                area='',
                intro=remarks,
                tags='',
                eps=1,
                aweme='',
                user=True,
            )
            videos.append(
                {
                    'vod_id': vod_id,
                    'vod_name': title,
                    'vod_pic': pic,
                    'vod_remarks': remarks,
                }
            )
        return videos

    def parse_search(self, html):
        videos = []
        try:
            root = json.loads(html)
        except Exception:
            return videos
        if root.get('code') != 0:
            return videos
        data_list = (root.get('data') or {}).get('data') or []
        for row in data_list:
            display = row.get('display') or {}
            title = (display.get('name') or '').strip()
            album_id = ''
            episode_link = display.get('episode_link') or {}
            # 兼容多种字段
            album_id = str(
                row.get('album_id')
                or display.get('album_id')
                or episode_link.get('album_id')
                or ''
            )
            if not title or not album_id:
                continue
            cover = display.get('video_cover_info') or {}
            pic = cover.get('url') or ''
            actor = display.get('actor') or ''
            remarks = display.get('rating') or ''
            vod_id = self.pack_id(
                title=title,
                album_id=album_id,
                actor=actor,
                pic=pic,
                year='',
                area='',
                intro='',
                tags='',
                eps=1,
                aweme='',
            )
            videos.append(
                {
                    'vod_id': vod_id,
                    'vod_name': title,
                    'vod_pic': pic,
                    'vod_remarks': remarks,
                }
            )
        return videos

    # -------------------- id pack/unpack --------------------

    def pack_id(self, title, album_id, actor, pic, year, area, intro, tags, eps, aweme, user=False):
        def clean(s):
            return (s or '').replace('###', ' ').replace('\n', ' ').strip()

        parts = [
            clean(title),
            clean(str(album_id)),
            clean(actor),
            clean(pic),
            clean(year),
            clean(area),
            clean(intro),
            clean(tags),
            str(eps or 1),
            clean(str(aweme)),
            '1' if user else '0',
        ]
        return '###'.join(parts)

    def unpack_id(self, raw):
        parts = (raw or '').split('###')
        while len(parts) < 11:
            parts.append('')
        return {
            'title': parts[0],
            'albumId': parts[1],
            'actor': parts[2],
            'pic': parts[3],
            'year': parts[4],
            'area': parts[5],
            'intro': parts[6],
            'tags': parts[7],
            'eps': parts[8] or '1',
            'aweme': parts[9],
            'user': parts[10] or '0',
        }

    # -------------------- utils --------------------

    def get_RegexGetText(self, Text, RegexText, Index):
        m = re.search(RegexText, Text or '', re.M | re.I)
        return m.group(Index) if m else ''

    def to_int(self, v, default=0):
        try:
            return int(v)
        except Exception:
            return default

    def localProxy(self, param):
        return [200, 'text/plain', b'', '']

    config = {
        'player': {},
        'filter': {},  # init() 里用 build_filters() 填充
    }

    header = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://www.ixigua.com/cinema/filter/dianshiju/',
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://www.ixigua.com',
    }
