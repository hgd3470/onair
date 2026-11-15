"""브리핑 낭독용 숫자 읽기. 팀 규칙대로 숫자를 한글 읽기로 바꿔 TTS가 잘못 읽지 않게 한다."""
DIGITS = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
SMALL = ["", "십", "백", "천"]
LARGE = ["", "만", "억", "조"]
NATIVE_HOURS = ["열두", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열", "열한", "열두"]
MONTHS = {6: "유월", 10: "시월"}


def _four(n):
    out = ""
    for i in range(3, -1, -1):
        d = n // 10 ** i % 10
        if d:
            out += ("" if d == 1 and i else DIGITS[d]) + SMALL[i]
    return out


def read_int(n):
    """정수 → 한자어 읽기. 1384 → 천삼백팔십사, 10000 → 만, -3 → 마이너스 삼."""
    n = int(n)
    if n == 0:
        return "영"
    if n < 0:
        return "마이너스 " + read_int(-n)
    parts, i = [], 0
    while n:
        group = n % 10000
        if group:
            word = "" if group == 1 and i == 1 else _four(group)   # 만 원 (일만 원 X)
            parts.append(word + LARGE[i])
        n //= 10000
        i += 1
    return "".join(reversed(parts))


def read_number(x, decimals=0):
    """소수는 '점' 뒤를 한 자리씩 읽는다. 1358.7 → 천삼백오십팔 점 칠."""
    value = round(float(x), decimals)
    sign = "마이너스 " if value < 0 else ""
    value = abs(value)
    whole = int(value)
    text = sign + read_int(whole)
    if decimals:
        frac = f"{value:.{decimals}f}".split(".")[1].rstrip("0")
        if frac:
            text += " 점 " + "".join(DIGITS[int(d)] or "영" for d in frac)
    return text


def read_temperature(celsius):
    t = round(float(celsius))
    return ("영하 " + read_int(-t) if t < 0 else read_int(t)) + " 도"


def read_clock(hour, minute):
    """24시간 → '오전 여덟 시 사십육 분'. 정각이면 분을 뺀다."""
    ampm = "오전" if hour < 12 else "오후"
    text = f"{ampm} {NATIVE_HOURS[hour % 12]} 시"
    return text + (f" {read_int(minute)} 분" if minute else "")


def read_minutes(total):
    total = max(1, round(total))
    hours, minutes = divmod(total, 60)
    if not hours:
        return f"{read_int(minutes)} 분"
    return f"{NATIVE_HOURS[hours] if hours <= 12 else read_int(hours)} 시간" + (f" {read_int(minutes)} 분" if minutes else "")


def read_month_day(month, day):
    return f"{MONTHS.get(month, read_int(month) + '월')} {read_int(day)}일"
