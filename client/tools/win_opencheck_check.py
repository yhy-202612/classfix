"""Windows 真机验证 opencheck 的四层证据。用法：

    python win_opencheck_check.py <课件.pptx> [--safe]

输出全部是原始事实（时间、HRESULT、页数、异常模块），供主控判断，不做美化。
"""

import argparse
import hashlib
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from classfix import opencheck as oc


def digest(path):
    handle = hashlib.sha1()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            handle.update(chunk)
    return handle.hexdigest()


def show(title, value):
    print("\n== %s ==" % title)
    print(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("deck")
    parser.add_argument("--safe", action="store_true", help="额外跑安全模式对比（会短暂打开 PowerPoint）")
    args = parser.parse_args()
    deck = Path(args.deck).resolve()
    print("platform:", sys.platform)
    print("deck:", deck, deck.stat().st_size, "bytes")

    before = digest(deck)
    expected = oc.expected_slides(deck)
    show("expected_slides（OOXML 里登记的页数）", expected)

    started = time.time()
    com = oc.com_probe(deck, expected=expected)
    show("P0 com_probe", "state=%s  seconds=%.1f  hresult=0x%08X  slides=%s  vendor=%s\nmessage=%s\nbad_slides=%s\nbad_shapes=%s\ndialog=%s"
         % (com.state, com.seconds, com.hresult, com.slides, com.vendor or "(none)",
            com.message, com.bad_slides[:5], com.bad_shapes[:5], com.dialog))
    show("com_probe 总耗时", "%.1f s" % (time.time() - started))

    environment = oc.env_evidence()
    show("P1 env_evidence", "state=%s disabled=%d startup=%d recovery=%d crashes=%d events=%d\nfirst_crash=%s\nfirst_event=%s\nnote=%s"
         % (environment.state, len(environment.disabled), len(environment.startup),
            len(environment.recovery), len(environment.crashes), len(environment.events),
            environment.crashes[:1], environment.events[:1], environment.note))

    safe = oc.SafeResult(state="skipped")
    if args.safe:
        safe = oc.safe_mode_probe(deck)
        show("P2 safe_mode_probe", "state=%s seconds=%.1f dialog=%s" % (safe.state, safe.seconds, safe.dialog))

    show("P3 最近对话框文本（com_probe 期间抓到的）", com.dialog or "（没有捕获到对话框）")

    after = digest(deck)
    show("不变式：原文件未被改动", "OK（sha1 前后一致）" if before == after else "VIOLATED（sha1 变化）")

    report = oc.inspect_open(deck, safe=args.safe)
    show("组装报告", "%s | %s" % (report.kind, report.summary()))
    for check in report.checks:
        print("  - [%s] %s: %s" % (check.status, check.title, check.detail.replace("\n", " / ")))
    for finding in report.findings:
        print("  * %s（%s, 优先级 %d）\n    evidence: %s\n    action: %s"
              % (finding.title, finding.confidence, finding.priority, finding.evidence, finding.action))
    show("facts", report.facts)

    # 负例：截断文件必须被判为打不开，且不能抛异常
    with tempfile.TemporaryDirectory() as folder:
        broken = Path(folder) / "broken.pptx"
        shutil.copy2(deck, broken)
        size = broken.stat().st_size
        with open(broken, "r+b") as stream:
            stream.truncate(max(1024, size // 3))
        negative = oc.com_probe(broken, expected=expected)
        show("负例（截断到 1/3 的副本）", "state=%s hresult=0x%08X message=%s"
             % (negative.state, negative.hresult, negative.message))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
