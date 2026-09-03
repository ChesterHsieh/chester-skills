#!/usr/bin/env python3
"""逐帧侦测「主机位」镜头，输出要保留的时间段。

原理：固定机位的画面高度一致，把整片降采样成小图后取中位数，就是主机位的
「平均长相」。每一帧对它算 L1 距离——主机位的帧距离小，特写／观众／转场卡
距离大，两群通常分得很开，用 Otsu 自动找谷底当阈值。

用法:
    python3 detect.py VIDEO [--json out.json] [--fps 30] [--threshold 12]
    python3 detect.py VIDEO --contact-sheet sheets/   # 先用缩图确认镜头结构

输出 JSON: {"fps":30, "nframes":9600, "threshold":11.8,
            "keep":[[start_frame,end_frame],...],   # end 不含
            "cut":[[start_frame,end_frame],...]}
"""
import argparse, json, os, subprocess, sys
import numpy as np

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
W, H = 64, 36          # 降采样尺寸；够分辨镜头，又快


def probe_fps(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip().rstrip(",")
    num, _, den = out.partition("/")
    return float(num) / float(den or 1)


def load_frames(path, fps):
    """整片降采样成 (n, H, W, 3) float32。"""
    cmd = [FFMPEG, "-nostdin", "-v", "error", "-i", path,
           "-vf", f"fps={fps},scale={W}:{H}",
           "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    stride = W * H * 3
    n = len(raw) // stride
    if n == 0:
        sys.exit(f"读不到画面: {path}")
    return np.frombuffer(raw, np.uint8)[:n * stride].reshape(n, H, W, 3).astype(np.float32)


def distances(frames):
    """每帧对「主机位参考帧」的距离。两段式：先粗估参考，再用近半数的帧重算。"""
    ref = np.median(frames, axis=0)
    d = np.abs(frames - ref).mean(axis=(1, 2, 3))
    ref = np.median(frames[d < np.percentile(d, 50)], axis=0)
    return np.abs(frames - ref).mean(axis=(1, 2, 3))


def auto_threshold(d):
    """自动阈值。

    Otsu 在这里单独用会太宽松：主机位帧数量压倒性地多，直方图严重偏斜，
    谷底会被推到很高，结果把「整片纯色的转场卡」之类距离中等的镜头也算进
    主机位。所以再算一个以主机位那一群自身离散度为准的上界（MAD），取两者
    较小的——宁可多剪，不要漏。
    """
    med = float(np.median(d))
    mad = float(np.median(np.abs(d - med))) or 1e-6
    robust = med + 8 * 1.4826 * mad
    return max(min(otsu(d), robust), med + 1e-6)


def otsu(d, bins=256):
    """在距离直方图上找双峰的谷底。"""
    hist, edges = np.histogram(d, bins=bins)
    p = hist / hist.sum()
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(p)
    w1 = 1 - w0
    m0 = np.cumsum(p * centers) / np.maximum(w0, 1e-12)
    m1 = (np.sum(p * centers) - np.cumsum(p * centers)) / np.maximum(w1, 1e-12)
    var = w0 * w1 * (m0 - m1) ** 2
    return float(centers[int(np.argmax(var))])


def segment(d, threshold, fps, min_seg, smooth, erode):
    keep = d < threshold
    win = max(1, int(round(smooth * fps)) | 1)          # 奇数窗，去单帧抖动
    sm = np.convolve(keep.astype(float), np.ones(win) / win, mode="same") > 0.5

    segs, start = [], None
    for i, v in enumerate(sm):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segs.append([start, i]); start = None
    if start is not None:
        segs.append([start, len(sm)])

    out = []
    for a, b in segs:
        while a < b and d[a] >= threshold:               # 边界往内收到真的主机位帧
            a += 1
        while b > a and d[b - 1] >= threshold:
            b -= 1
        a, b = a + erode, b - erode                      # 再各削 erode 帧当保险
        if a <= erode:                                   # 贴到片头／片尾就吸附回去
            a = 0
        if b >= len(d) - erode:
            b = len(d)
        if (b - a) / fps >= min_seg:
            out.append([a, b])
    return out


def contact_sheets(path, outdir, fps, every=2.0, cols=8, rows=5):
    """输出缩图墙，人眼先确认镜头结构。每格间隔 every 秒。"""
    import os
    os.makedirs(f"{outdir}/frames", exist_ok=True)
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-i", path,
                    "-vf", f"fps=1/{every},scale=240:-1",
                    f"{outdir}/frames/f_%04d.jpg", "-y"], check=True)
    n = len(os.listdir(f"{outdir}/frames"))
    per = cols * rows
    for i in range((n + per - 1) // per):
        subprocess.run([FFMPEG, "-nostdin", "-v", "error",
                        "-start_number", str(i * per + 1),
                        "-i", f"{outdir}/frames/f_%04d.jpg", "-frames:v", "1",
                        "-vf", f"scale=240:-1,tile={cols}x{rows}:padding=4:color=white",
                        f"{outdir}/sheet_{i}.jpg", "-y"], check=True)
        base = i * per * every
        print(f"{outdir}/sheet_{i}.jpg  涵盖 {base:.0f}s – {base + per * every:.0f}s，"
              f"每格 {every:.0f}s，{cols} 格一列")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--json", help="把结果写到这个档案")
    ap.add_argument("--fps", type=float, help="取样率，预设=影片原生 fps（务必逐帧）")
    ap.add_argument("--threshold", type=float, help="距离阈值，预设用 Otsu 自动找")
    ap.add_argument("--min-seg", type=float, default=1.0, help="短于这个秒数的保留段直接丢掉")
    ap.add_argument("--smooth", type=float, default=0.3, help="平滑窗口秒数")
    ap.add_argument("--erode", type=int, default=1, help="每段头尾各再削掉几帧")
    ap.add_argument("--contact-sheet", metavar="DIR", help="只出缩图墙，不做侦测")
    args = ap.parse_args()

    fps = args.fps or probe_fps(args.video)

    if args.contact_sheet:
        contact_sheets(args.video, args.contact_sheet, fps)
        return

    frames = load_frames(args.video, fps)
    d = distances(frames)
    th = args.threshold if args.threshold is not None else auto_threshold(d)
    keep = segment(d, th, fps, args.min_seg, args.smooth, args.erode)

    cut, prev = [], 0
    for a, b in keep:
        if a > prev:
            cut.append([prev, a])
        prev = b
    if prev < len(d):
        cut.append([prev, len(d)])

    res = {"fps": fps, "nframes": len(d), "threshold": round(th, 2),
           "keep": keep, "cut": cut}

    print(f"阈值 {th:.2f}（距离分布 p10={np.percentile(d,10):.1f} "
          f"p50={np.percentile(d,50):.1f} p90={np.percentile(d,90):.1f}）")
    kept = sum(b - a for a, b in keep) / fps
    print(f"保留 {kept:.1f}s / {len(d)/fps:.1f}s，共 {len(keep)} 段\n")
    print("保留段:")
    for a, b in keep:
        print(f"  {a/fps:8.3f} – {b/fps:8.3f}  ({(b-a)/fps:6.2f}s)")
    print("剪掉段:")
    for a, b in cut:
        print(f"  {a/fps:8.3f} – {b/fps:8.3f}  ({(b-a)/fps:6.2f}s)")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=2)
        print(f"\n已写入 {args.json}")


if __name__ == "__main__":
    main()
