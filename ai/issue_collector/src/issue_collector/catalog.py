"""Public discovery endpoints. Availability != permission to republish articles."""
# Independent origin is further collapsed when a report credits a wire service.
PUBLISHERS = {
 'yna.co.kr': ('연합뉴스', 'yonhap', False),
 'hani.co.kr': ('한겨레', 'hani', False),
 'khan.co.kr': ('경향신문', 'khan', False),
 'hankyung.com': ('한국경제', 'hankyung', False),
 'mk.co.kr': ('매일경제', 'mk', False),
 'fnnews.com': ('파이낸셜뉴스', 'fnnews', False),
 'newsis.com': ('뉴시스', 'newsis', False),
 'news1.kr': ('뉴스1', 'news1', False),
 'ytn.co.kr': ('YTN', 'ytn', False),
 'imbc.com': ('MBC', 'mbc', False),
 'kbs.co.kr': ('KBS', 'kbs', False),
 'jtbc.co.kr': ('JTBC', 'jtbc', False),
 'edaily.co.kr': ('이데일리', 'edaily', False),
 'sedaily.com': ('서울경제', 'sedaily', False),
 'asiae.co.kr': ('아시아경제', 'asiae', False),
 'korea.kr': ('대한민국 정책브리핑', 'government', True),
 'bok.or.kr': ('한국은행', 'bok', True),
 'fsc.go.kr': ('금융위원회', 'fsc', True),
 'mois.go.kr': ('행정안전부', 'mois', True),
 'mofa.go.kr': ('외교부', 'mofa', True),
 'kostat.go.kr': ('국가데이터처', 'statistics', True),
 'moef.go.kr': ('기획재정부', 'finance_ministry', True),
 'mofe.go.kr': ('재정경제부', 'finance_ministry', True),
 'president.go.kr': ('대통령실', 'president', True),
 'assembly.go.kr': ('국회', 'assembly', True),
 'krx.co.kr': ('한국거래소', 'krx', True),
}
# SBS RSS explicitly restricts commercial use; disabled, never bypassed by search.
DISABLED = [{'publisher': 'SBS', 'url': 'https://news.sbs.co.kr/news/rss.do', 'reason': 'personal_noncommercial_only'},
            {'publisher': '정책브리핑 RSS', 'url': 'https://www.korea.kr/etc/noticeView.do?newsId=132038885', 'reason': 'rss_discontinued_2026'}]
FEEDS = [
 ('issue', 'https://www.yna.co.kr/rss/politics.xml'),
 ('issue', 'https://www.yna.co.kr/rss/society.xml'),
 ('issue', 'https://www.hani.co.kr/rss/politics/'),
 ('issue', 'https://www.hani.co.kr/rss/society/'),
 ('issue', 'https://www.khan.co.kr/rss/rssdata/politic_news.xml'),
 ('issue', 'https://www.khan.co.kr/rss/rssdata/society_news.xml'),
 ('issue', 'https://www.hankyung.com/feed/politics'),
 ('issue', 'https://www.mk.co.kr/rss/30200030/'),
 ('both', 'https://imnews.imbc.com/rss/news/news_00.xml'),
 ('both', 'https://www.fnnews.com/rss/r20/fn_realnews_all.xml'),
 ('market', 'https://www.yna.co.kr/rss/economy.xml'),
 ('market', 'https://www.yna.co.kr/rss/market.xml'),
 ('market', 'https://www.hani.co.kr/rss/economy/'),
 ('market', 'https://www.khan.co.kr/rss/rssdata/economy_news.xml'),
 ('market', 'https://www.hankyung.com/feed/finance'),
 ('market', 'https://www.hankyung.com/feed/economy'),
 ('market', 'https://www.mk.co.kr/rss/50200011/'),
 ('market', 'https://www.mk.co.kr/rss/30100041/'),
 ('market', 'https://www.bok.or.kr/portal/bbs/P0000559/news.rss?menuNo=200690'),
]
LISTINGS = [
 ('issue', 'https://imnews.imbc.com/news/2026/politics/'),
 ('issue', 'https://www.korea.kr/briefing/pressReleaseList.do'),
 ('market', 'https://www.edaily.co.kr/news/economy'),
 ('market', 'https://www.fsc.go.kr/no010101'),
]

def publisher(url):
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or '').lower()
    return next((v for k, v in PUBLISHERS.items() if host == k or host.endswith('.' + k)), None)
