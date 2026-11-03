"""ORDERTRACE 자극 생성기.

120fps 합성 원본(빨강·파랑 원이 흰 기준선을 통과) → 5/10/15fps로 샘플링 →
두 가지 재구성으로 60fps 출력 프레임을 만든다.
  repeat : 관측 프레임을 그대로 반복(hold)
  interp : 관측 프레임 사이를 Farneback 광학흐름 기반 움직임 보상 보간으로 채움(고정 알고리즘)
정답(어느 원이 먼저 기준선을 통과했는지)은 120fps 원본의 해석적 궤적에서 계산한다.

출력: ordertrace/stimuli/<clip_id>/sheet.jpg (60프레임 스프라이트 시트) + stimuli/manifest.json

사용:  python3 make_stimuli.py            # 기본 설계 (연습 1 + 본시행 24)
       python3 make_stimuli.py --seed 7   # 다른 난수 시드
"""

import argparse
import json
import math
import random
from pathlib import Path

import cv2
import numpy as np

W, H = 320, 240
SRC_FPS, OUT_FPS, DURATION = 120, 60, 1.0
N_SRC = int(SRC_FPS * DURATION)      # 120
N_OUT = int(OUT_FPS * DURATION)      # 60
BASELINE_Y = 150
RADIUS = 14
RED, BLUE = (40, 40, 230), (230, 90, 40)   # BGR
BG, LINE = (28, 28, 28), (245, 245, 245)
X_RED, X_BLUE = 100, 220
GRID_COLS = 10

# 본시행 설계: SOA(ms, 양수면 빨강이 먼저) × 관측 fps × 재구성 방식
SOAS_MS = [-83, -33, 33, 83]
FPS_LEVELS = [5, 10, 15]
METHODS = ["repeat", "interp"]
PROFILES = ["accel", "decel"]   # 등속이 아니어야 보간이 순서를 바꿀 여지가 생긴다


def y_of(t, t_cross, profile, y0=40.0):
    """시각 t(초)에 원 중심의 y. t_cross에 정확히 기준선을 통과하도록 궤적을 맞춘다."""
    if profile == "accel":      # 가속: 정지 출발
        a = 2 * (BASELINE_Y - y0) / t_cross ** 2
        return y0 + 0.5 * a * t * t
    if profile == "decel":      # 감속: 통과 뒤 느려짐
        v0 = 2 * (BASELINE_Y - y0) / t_cross
        a = -v0 / (2 * t_cross)
        return y0 + v0 * t + 0.5 * a * t * t
    v = (BASELINE_Y - y0) / t_cross
    return y0 + v * t


def render(t, t_red, t_blue, profile):
    img = np.full((H, W, 3), BG, np.uint8)
    cv2.line(img, (0, BASELINE_Y), (W, BASELINE_Y), LINE, 2, cv2.LINE_AA)
    cv2.circle(img, (X_RED, int(round(y_of(t, t_red, profile)))), RADIUS, RED, -1, cv2.LINE_AA)
    cv2.circle(img, (X_BLUE, int(round(y_of(t, t_blue, profile)))), RADIUS, BLUE, -1, cv2.LINE_AA)
    return img


def source_frames(t_red, t_blue, profile):
    return [render(i / SRC_FPS, t_red, t_blue, profile) for i in range(N_SRC)]


def warp(img, flow, scale):
    """img의 각 픽셀을 flow*scale 만큼 이동시킨 결과를 역매핑으로 근사."""
    h, w = img.shape[:2]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    mx = gx - flow[..., 0] * scale
    my = gy - flow[..., 1] * scale
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def interpolate(f0, f1, t):
    """f0와 f1 사이 비율 t(0~1)의 중간 프레임. 양방향 Farneback 흐름 + 가중 혼합 (고정 파라미터)."""
    g0, g1 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY), cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
    params = dict(pyr_scale=0.5, levels=4, winsize=21, iterations=3, poly_n=7, poly_sigma=1.5, flags=0)
    fwd = cv2.calcOpticalFlowFarneback(g0, g1, None, **params)
    bwd = cv2.calcOpticalFlowFarneback(g1, g0, None, **params)
    a = warp(f0, fwd, t)            # f0을 t만큼 앞으로
    b = warp(f1, bwd, 1 - t)        # f1을 (1-t)만큼 뒤로
    return cv2.addWeighted(a, 1 - t, b, t, 0)


def reconstruct(src, obs_fps, method, phase):
    """src(120fps)에서 obs_fps로 관측한 뒤 method로 60fps N_OUT 프레임을 만든다."""
    step = SRC_FPS // obs_fps                       # 24, 12, 8
    obs_idx = list(range(phase, N_SRC, step))
    obs = [src[i] for i in obs_idx]
    out = []
    for k in range(N_OUT):
        t_src = k * SRC_FPS / OUT_FPS               # 출력 프레임 k의 원본 시각(프레임 단위)
        j = int((t_src - phase) // step) if t_src >= phase else -1
        if j < 0:
            out.append(obs[0]); continue
        if j >= len(obs) - 1:
            out.append(obs[-1]); continue
        if method == "repeat":
            out.append(obs[j])
        else:
            frac = (t_src - obs_idx[j]) / step
            out.append(obs[j] if frac < 1e-6 else interpolate(obs[j], obs[j + 1], frac))
    return out


def sheet(frames):
    rows = math.ceil(len(frames) / GRID_COLS)
    canvas = np.full((rows * H, GRID_COLS * W, 3), 0, np.uint8)
    for i, f in enumerate(frames):
        r, c = divmod(i, GRID_COLS)
        canvas[r * H:(r + 1) * H, c * W:(c + 1) * W] = f
    return canvas


def build(out_dir, seed):
    rng = random.Random(seed)
    trials = []
    profiles = PROFILES * 100
    i = 0
    for soa in SOAS_MS:
        for fps in FPS_LEVELS:
            for method in METHODS:
                trials.append(dict(soa_ms=soa, obs_fps=fps, method=method, profile=profiles[i % 2 if fps != 10 else (i + 1) % 2],
                                   kind="main"))
                i += 1
    trials.append(dict(soa_ms=167, obs_fps=15, method="repeat", profile="accel", kind="practice"))

    manifest = []
    for n, tr in enumerate(trials):
        base = 0.50 + rng.uniform(-0.05, 0.05)
        d = tr["soa_ms"] / 1000 / 2
        t_red, t_blue = base - d, base + d      # soa>0 → 빨강이 먼저 통과
        phase = rng.randrange(SRC_FPS // tr["obs_fps"])
        src = source_frames(t_red, t_blue, tr["profile"])
        frames = reconstruct(src, tr["obs_fps"], tr["method"], phase)
        cid = f"{tr['kind'][0]}{n:02d}_{tr['method']}_{tr['obs_fps']}fps_{'r' if tr['soa_ms'] > 0 else 'b'}{abs(tr['soa_ms'])}"
        d_out = out_dir / cid
        d_out.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d_out / "sheet.jpg"), sheet(frames), [cv2.IMWRITE_JPEG_QUALITY, 92])
        truth = "red" if tr["soa_ms"] > 0 else "blue" if tr["soa_ms"] < 0 else "same"
        manifest.append(dict(id=cid, kind=tr["kind"], method=tr["method"], obs_fps=tr["obs_fps"], soa_ms=tr["soa_ms"],
                             profile=tr["profile"], phase=phase, truth=truth, t_red=round(t_red, 4), t_blue=round(t_blue, 4),
                             sheet=f"stimuli/{cid}/sheet.jpg", frames=len(frames), fps=OUT_FPS, cols=GRID_COLS, w=W, h=H))
        print(cid, flush=True)
    (out_dir / "manifest.json").write_text(json.dumps(dict(seed=seed, src_fps=SRC_FPS, out_fps=OUT_FPS, duration=DURATION,
                                                           baseline_y=BASELINE_Y, clips=manifest), ensure_ascii=False, indent=1))
    print(f"{len(manifest)} clips → {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--out", default=str(Path(__file__).parent / "stimuli"))
    a = ap.parse_args()
    build(Path(a.out), a.seed)
