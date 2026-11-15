"""GPU 서버에서 작업을 읽고 결과를 덧붙인다."""
import argparse
import sys
import json
from pathlib import Path

import numpy as np
import soundfile as sf


MAX_CHARS_PER_SEC = 9.0  # 한국어 낭독은 초당 4~7글자. 이보다 빠르면 문장이 빠졌다고 본다


def too_fast(text, wav, rate):
    hop, quiet = _quiet(wav, rate)
    spoken = max(1, int((~quiet).sum())) * hop / rate
    return len(text.replace(" ", "")) / spoken > MAX_CHARS_PER_SEC


def chunks(lines, limit):
    """이어지는 문장을 limit 글자까지 한 덩어리로 묶는다. limit이 0이면 문장마다 나눈다."""
    if limit <= 0:
        return list(lines)
    out = []
    for line in lines:
        if out and len(out[-1]) + 1 + len(line) <= limit:
            out[-1] += " " + line
        else:
            out.append(line)
    return out


def _quiet(wav, rate):
    """20ms 단위로 -45dB 아래인 구간 표시."""
    hop = int(0.02 * rate)
    n = len(wav) // hop
    rms = np.sqrt((np.asarray(wav[:n * hop], dtype=np.float64).reshape(n, hop) ** 2).mean(1) + 1e-12)
    return hop, 20 * np.log10(rms) < -45


def trim(wav, rate, pad=0.05):
    """앞뒤 무음을 잘라 낸다. 소리가 없는 덩어리는 그대로 둔다."""
    hop, quiet = _quiet(wav, rate)
    loud = np.flatnonzero(~quiet)
    if not len(loud):
        return wav
    keep = int(pad * rate)
    return wav[max(0, loud[0] * hop - keep):min(len(wav), (loud[-1] + 1) * hop + keep)]


def sentence_pause(pieces, rate, floor=0.7):
    """덩어리 안에서 모델이 문장 끝에 둔 쉼(floor초 이상)의 중앙값. 없으면 None."""
    pauses = []
    for wav in pieces:
        hop, quiet = _quiet(wav, rate)
        if quiet.all():
            continue
        runs = np.diff(np.r_[0, quiet.astype(int), 0])
        for start, end in zip(np.flatnonzero(runs == 1), np.flatnonzero(runs == -1)):
            if (end - start) * hop / rate >= floor:
                pauses.append((end - start) * hop / rate)
    return float(np.median(pauses)) if pauses else None


# 앞 묶음이 끝난 문장부호에 따라 경계 쉼을 조절한다(기준 쉼 대비 배율).
PAUSE_SCALE = {"?": 1.2, "!": 1.1, "…": 1.4, ",": 0.6}


def join(pieces, rate, gap=None, ends=None):
    """덩어리를 다듬어 잇는다. gap이 None이면 덩어리 안 문장 끝 쉼과 같은 길이로 맞춘다(못 재면 0.5초).
    ends가 있으면 각 덩어리 텍스트의 마지막 문장부호에 따라 그 뒤 쉼을 PAUSE_SCALE만큼 조절한다."""
    pieces = [trim(np.asarray(p, dtype=np.float32), rate) for p in pieces]
    if gap is None:
        gap = sentence_pause(pieces, rate) or 0.5
    out = []
    for i, piece in enumerate(pieces):
        out.append(piece)
        if i < len(pieces) - 1:
            mark = (ends[i].rstrip()[-1:] if ends else "") or "."
            out.append(np.zeros(round(gap * PAUSE_SCALE.get(mark, 1.0) * rate), dtype=np.float32))
    return np.concatenate(out)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("jobs")
    parser.add_argument("results")
    parser.add_argument("--model")
    parser.add_argument("--stub", action="store_true")
    parser.add_argument("--gap", default="auto",
                        help="덩어리 사이 쉼(초). auto면 덩어리 안 문장 끝 쉼에 맞춘다")
    parser.add_argument("--retries", type=int, default=2, help="말 속도가 비정상인 조각을 다시 합성할 횟수")
    parser.add_argument("--chunk-chars", type=int, default=0,
                        help="이어지는 문장을 이 글자 수까지 묶어 합성(0이면 문장마다)")
    args = parser.parse_args(argv)
    if not args.stub and not args.model:
        parser.error("GPU 실행에는 --model이 필요합니다")
    model, load_error = None, None
    if not args.stub:
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
            model = Qwen3TTSModel.from_pretrained(args.model, device_map="cuda:0",
                                                 dtype=torch.bfloat16, attn_implementation="sdpa")
        except Exception as exc:
            load_error = str(exc)
    result_path = Path(args.results)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with Path(args.jobs).open(encoding="utf-8") as jobs, result_path.open("a", encoding="utf-8") as results:
        for line in jobs:
            if not line.strip():
                continue
            result = dict(content_id=None, ok=False, wav=None, duration_sec=None, error=None)
            try:
                job = json.loads(line)
                result.update(content_id=job["content_id"], wav=job["out_wav"])
                if load_error is not None:
                    raise RuntimeError(load_error)
                # 전체를 한 번에 넣으면 문장 누락·불규칙한 묵음이 생기고, 문장마다 나누면 어조가 끊긴다.
                # 몇 문장씩 묶어 합성하고, 덩어리 사이 쉼은 모델이 덩어리 안에서 둔 문장 끝 쉼에 맞춘다.
                lines = chunks([l.strip() for l in job["text"].splitlines() if l.strip()], args.chunk_chars)
                if args.stub:
                    rate = 24000
                    pieces = [np.zeros(max(1, round(len(l) / 6.5 * rate)), dtype=np.float32) for l in lines]
                else:
                    wavs, rate = model.generate_custom_voice(text=lines, speaker=[job["speaker"]] * len(lines),
                                                             language=[job["language"]] * len(lines))
                    pieces = [np.asarray(w, dtype=np.float32) for w in wavs]
                    # 문장을 건너뛰고 일찍 끝난 조각은 말 속도가 비정상적으로 빠르다. 그 조각만 다시 합성한다.
                    for i, line in enumerate(lines):
                        for _ in range(args.retries):
                            if not too_fast(line, pieces[i], rate):
                                break
                            again, _ = model.generate_custom_voice(text=line, speaker=job["speaker"], language=job["language"])
                            print(f"retry {job['content_id']} #{i + 1}", file=sys.stderr, flush=True)
                            pieces[i] = np.asarray(again[0], dtype=np.float32)
                        if too_fast(line, pieces[i], rate):
                            raise RuntimeError(f"{i + 1}번째 문장이 재합성 뒤에도 누락 의심(말 속도 비정상)")
                wav = join(pieces, rate, None if args.gap == "auto" else float(args.gap), ends=lines)
                path = Path(job["out_wav"])
                path.parent.mkdir(parents=True, exist_ok=True)
                sf.write(str(path), wav, rate)
                result.update(ok=True, duration_sec=sf.info(str(path)).duration)
            except Exception as exc:
                result["error"] = str(exc)
            results.write(json.dumps(result, ensure_ascii=False) + "\n")
            results.flush()


if __name__ == "__main__":
    main()
