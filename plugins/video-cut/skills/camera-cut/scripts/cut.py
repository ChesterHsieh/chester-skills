#!/usr/bin/env python3
"""把 detect.py 找出的保留段接成成片，三种模式。

    reencode  全片重编码。切点最精准、时间戳最干净，但整片重压。
    lossless  纯 -c copy。零画质损失，但每段起点只能落在关键帧上，
              对不齐时会牺牲掉段首的一小截好画面。
    hybrid    预设。对得齐关键帧的段照抄，对不齐的只把「段首到下一个
              关键帧」这一小截重编码，其余照抄。切点干净且几乎无损。

用法:
    python3 cut.py VIDEO --plan plan.json -o out.mp4 [--mode hybrid] [--verify]

关键帧与 `-to` 的坑见 references/gotchas.md，改这支档案前先读。
"""
import argparse, json, os, subprocess, sys, tempfile

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg 失败:\n{' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


def keyframes(path):
    out = run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
               "-show_entries", "frame=pts_time", "-of", "csv=p=0", path]).stdout
    ks = sorted(float(x) for x in out.replace(",", " ").split() if x)
    return ks


def source_params(path):
    out = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=profile,level,pix_fmt,time_base",
               "-of", "default=noprint_wrappers=1", path]).stdout
    p = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
    tb = p.get("time_base", "1/15360")
    return {"profile": (p.get("profile") or "main").lower(),
            "level": p.get("level", "31"),
            "pix_fmt": p.get("pix_fmt", "yuv420p"),
            "timescale": tb.split("/")[-1]}


def build_plan(keep, fps, kf, mode, tol_frames=1):
    """产出 [(mode, start_sec, end_sec)]，mode 为 'C'(照抄) 或 'R'(重压)。

    每一段的**起点**只能落在关键帧上（-c copy 无法从 P 帧开始解），所以起点
    是唯一需要斡旋的地方。tol_frames 允许起点往前吃回几帧去迁就关键帧——
    那几帧是 detect.py 的 --erode 保险削掉的，通常还是主机位，吃回来没差；
    但万一那个关键帧本身就是切镜的第一帧，就会漏镜头，此时设 --tol-frames 0
    强制改用桥接重压。
    """
    plan = []
    for a, b in keep:
        a_t, b_t = a / fps, b / fps
        if mode == "reencode":
            plan.append(("R", a_t, b_t))
            continue

        # 结尾要退 2 帧：-c copy 的 `-to` 是封包层级的，会多含进下一个关键帧
        end_copy = (b - 2) / fps
        back = tol_frames / fps
        aligned = [k for k in kf if a_t - back - 1e-6 <= k <= a_t + back + 1e-6]
        later = [k for k in kf if k > a_t + back and k < b_t]

        if aligned:                                     # 起点本来就在关键帧上
            plan.append(("C", aligned[0], end_copy))
        elif mode == "hybrid" and later:
            plan.append(("R", a_t, later[0]))           # 只重压段首这一小截桥接
            plan.append(("C", later[0], end_copy))
        elif later:                                     # lossless：只能牺牲段首
            plan.append(("C", later[0], end_copy))
        else:                                           # 段内没有可用关键帧
            plan.append(("R", a_t, b_t))
    return plan


def encode_args(sp, crf):
    return ["-c:v", "libx264", "-profile:v", sp["profile"], "-level", sp["level"],
            "-pix_fmt", sp["pix_fmt"], "-preset", "slow", "-crf", str(crf),
            "-x264-params", "keyint=60:scenecut=0",
            "-video_track_timescale", sp["timescale"],
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]


def execute(src, plan, out, crf):
    sp = source_params(src)
    tmp = tempfile.mkdtemp(prefix="camera-cut-")
    parts, listing = [], []
    for i, (m, a, b) in enumerate(plan):
        p = os.path.join(tmp, f"p{i:03d}.mp4")
        base = [FFMPEG, "-nostdin", "-v", "error", "-ss", f"{a:.6f}", "-to", f"{b:.6f}", "-i", src]
        tail = ["-avoid_negative_ts", "make_zero", p, "-y"]
        run(base + (["-c", "copy"] if m == "C" else encode_args(sp, crf)) + tail)
        parts.append((m, a, b, p))
        listing.append(f"file '{p}'")

    lst = os.path.join(tmp, "list.txt")
    open(lst, "w").write("\n".join(listing) + "\n")
    run([FFMPEG, "-nostdin", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", "-movflags", "+faststart", out, "-y"])
    return parts


def verify(out, fps, threshold):
    """在成片上重跑侦测，回报还有几帧不属于主机位。理想是 0。

    验证门槛刻意比侦测时严（×0.75）：接点上漏进来的通常只有一两帧，用原本
    的门槛容易滑过去。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import numpy as np
    from detect import load_frames, distances
    d = distances(load_frames(out, fps))
    bad = np.where(d > threshold * 0.75)[0]
    return len(d), [int(x) for x in bad]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--plan", required=True, help="detect.py --json 产出的档案")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--mode", choices=["hybrid", "lossless", "reencode"], default="hybrid")
    ap.add_argument("--crf", type=int, default=16, help="重编码段的品质，越小越好")
    ap.add_argument("--tol-frames", type=int, default=1,
                    help="段首容许往前吃几帧去对齐关键帧。--verify 报出漏镜头就设 0")
    ap.add_argument("--verify", action="store_true", help="成片重跑侦测确认没漏镜头")
    args = ap.parse_args()

    d = json.load(open(args.plan))
    fps, keep = d["fps"], d["keep"]
    kf = keyframes(args.video)
    plan = build_plan(keep, fps, kf, args.mode, args.tol_frames)

    reenc = sum(b - a for m, a, b in plan if m == "R")
    total = sum(b - a for _, a, b in plan)
    print(f"模式 {args.mode}：{len(plan)} 段，成片约 {total:.1f}s，"
          f"其中重编码 {reenc:.1f}s（{reenc/max(total,1e-9)*100:.1f}%）")
    for m, a, b in plan:
        print(f"  {'照抄' if m == 'C' else '重压'}  {a:8.3f} – {b:8.3f}  ({b-a:6.2f}s)")

    execute(args.video, plan, args.out, args.crf)
    print(f"\n已输出 {args.out}")

    if args.verify:
        n, bad = verify(args.out, fps, d["threshold"])
        if bad:
            print(f"⚠️  成片 {n} 帧中有 {len(bad)} 帧疑似非主机位，帧号 {bad[:20]}")
            print("   先用 --tol-frames 0 重跑；还有的话把这些帧抽出来目视确认，")
            print("   再调大 detect.py 的 --erode。")
        else:
            print(f"✅ 成片 {n} 帧全数属于主机位，没有漏掉的镜头。")


if __name__ == "__main__":
    main()
