"""관심사 코드 정본과 기존 카탈로그 분류 이관."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

CODES = {
    "C01": "일·직장", "C02": "연애·결혼", "C03": "가족·육아", "C04": "친구·인간관계",
    "C05": "마음·습관·성장", "C06": "자취·살림·생활팁", "C07": "음식·맛집·식문화",
    "C08": "돈·소비·생활경제", "C09": "건강·운동·생활리듬", "C10": "반려동물·자연",
    "C11": "여행·지역·문화", "C12": "패션·뷰티·자기표현", "C13": "과학·기술·AI",
    "C14": "역사·인물", "C15": "언어·독서·인문·가치관", "C16": "뉴스·사회·시사",
    "C17": "사건·미스터리·추리", "C18": "음악·영화·방송·예술", "C19": "스포츠·게임·취미",
    "C20": "자동차·교통·이동", "C21": "성경·말씀·신앙",
}
GROUPS = {
    "A": ["C01", "C02", "C03", "C04", "C05"],
    "B": ["C06", "C07", "C08", "C09", "C10", "C11", "C12"],
    "C": ["C13", "C14", "C15", "C16", "C17"],
    "D": ["C18", "C19", "C20"],
    "E": ["C21"],
}
GROUP_NAMES = {"A": "사람과 일상", "B": "생활과 취향", "C": "지식과 세상", "D": "문화·취미·이동", "E": "성경과 신앙"}


def expand(selection):
    """그룹·코드 목록을 입력 순서대로 펼친다. 알 수 없는 값은 거부한다."""
    result = []
    for value in selection:
        if value in GROUPS:
            codes = GROUPS[value]
        elif value in CODES:
            codes = [value]
        else:
            raise ValueError(f"Unknown interest selection: {value!r}")
        for code in codes:
            if code not in result:
                result.append(code)
    return result


LEGACY_BY_ID = {
    "story-s01": "C02",
    "story-h01": "C09",
    "story-i01": "C02",
    "story-e01": "C03",
    "story-m05": "C05",
    "story-e02": "C04",
    "story-e03": "C03",
    "story-i02": "C13",
    "story-i03": "C08",
    "story-s02": "C02",
    "story-m01": "C15",
    "story-m03": "C03",
    "story-i04": "C08",
    "story-m04": "C19",
    "story-i05": "C04",
    "story-s03": "C02",
    "story-s04": "C02",
    "story-i06": "C18",
    "story-s05": "C01",
    "story-i07": "C05",
    "story-e04": "C03",
    "story-s06": "C01",
    "story-s07": "C02",
    "story-s08": "C03",
    "story-s09": "C02",
    "story-s10": "C02",
    "story-s11": "C02",
    "story-s12": "C02",
    "story-s13": "C02",
    "story-s14": "C03",
    "gen-wiki-blackhole": "C13",
    "gen-ency-jagyeongnu": "C14",
    "gen-korea-traffic-148973209": "C20",
    "gen-korea-economy-148973686": "C16",
    "gen-seed-blinddate-reversal": "C02",
    "gen-wiki-commons-tragedy": "C01",
    "gen-wikiquote-franklin": "C05",
    "gen-wikisource-nakyeop": "C15",
}
LEGACY_DEFAULT = {"science": "C13", "history": "C14", "life": "C06", "insight": "C15", "motivation": "C05"}


def migrate(db_path, dry_run=False):
    """Return (changes, unmapped): triples (id, old, new), pairs (id, old).

    Ads and existing interest codes are preserved. Dry runs open read-only;
    real changes are committed together. A missing database is never created.
    """
    uri = Path(db_path).resolve().as_uri() + ("?mode=ro" if dry_run else "?mode=rw")
    changes, unmapped = [], []
    with closing(sqlite3.connect(uri, uri=True)) as db:
        with db:
            db.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
            for content_id, kind, old in db.execute(
                "SELECT content_id, kind, category FROM content ORDER BY content_id"
            ).fetchall():
                if kind == "ad" or old == "ad" or old in CODES:
                    continue
                new = LEGACY_BY_ID.get(content_id, LEGACY_DEFAULT.get(old))
                if new is None:
                    unmapped.append((content_id, old))
                    continue
                changes.append((content_id, old, new))
            if not dry_run:
                db.executemany("UPDATE content SET category=? WHERE content_id=?",
                               [(new, cid) for cid, old, new in changes])
    return changes, unmapped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("migrate")
    command.add_argument("db_path")
    command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    changes, unmapped = migrate(args.db_path, dry_run=args.dry_run)
    for cid, old, new in changes:
        print(f"CHANGE {cid}: {old} -> {new}")
    for cid, old in unmapped:
        print(f"UNMAPPED {cid}: {old}")
    print(f"{'DRY RUN' if args.dry_run else 'MIGRATED'}: {len(changes)} changes, {len(unmapped)} unmapped")


if __name__ == "__main__":
    main()
