from dataclasses import dataclass

from .model import Check, Finding, Report, SYMPTOMS, sort_findings
from .pptx import find_ffprobe, probe_asset, probe_file, read_deck


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    options: tuple[tuple[str, str], ...]


QUESTIONS = {
    "original": Question("original", "原始视频用本地播放器能单独播放吗？",
                         (("yes", "能正常播放"), ("no", "也无法正常播放"), ("unknown", "没试过或找不到原视频"))),
    "source": Question("source", "你知道 PPT 里的视频是怎么插入的吗？",
                       (("embedded", "嵌入在 PPT 里"), ("linked", "链接到单独的文件"),
                        ("online", "从网站插入"), ("unknown", "不确定"))),
    "error": Question("error", "PowerPoint 显示了哪类提示？",
                      (("format", "格式不支持或无法播放"), ("missing", "找不到文件或链接"),
                       ("none", "没有明确提示"), ("other", "其他提示"))),
    "trigger": Question("trigger", "故障在哪种情况下出现？",
                        (("normal", "普通编辑视图"), ("slide", "放映时点击视频"), ("auto", "应该自动播放"),
                         ("unknown", "不确定"))),
    "scope": Question("scope", "异常画面出现在哪里？",
                      (("external", "只有教学屏或投影异常"), ("all", "所有屏幕都异常"),
                       ("unknown", "尚未对比"))),
    "sound": Question("sound", "电脑播放其他内容时有声音吗？",
                      (("yes", "其他内容有声音"), ("no", "其他内容也没有声音"),
                       ("unknown", "还没试过"))),
    "original_sound": Question("original_sound", "原视频单独播放时有声音吗？",
                               (("yes", "原视频有声音"), ("no", "原视频也没声音"),
                                ("unknown", "没试过或找不到原视频"))),
}


def questions(case, answers, has_media):
    if case == "open":
        return []
    keys = ["original"]
    if not has_media:
        keys.append("source")
    keys += {"click": ["trigger", "error"], "format": ["error"], "picture": ["scope"],
             "silent": ["sound"]}[case]
    if case == "silent" and answers.get("original") != "no":
        keys.append("original_sound")
    return [QUESTIONS[key] for key in keys]


def summarize_probe(probe, source):
    prefix = "original_" if source == "原视频" else ""
    label = {"failed": "warning", "missing": "problem", "unreadable": "problem"}
    if probe.state not in ("ok", "partial"):
        return [Check(prefix + "codec", f"{source} · 媒体编码", label.get(probe.state, "unknown"), probe.detail)]
    resolution = f" · {probe.width} × {probe.height}" if probe.width and probe.height else ""
    return [
        Check(prefix + "container", f"{source} · 容器", "ok" if probe.state == "ok" else "warning",
              f"{probe.container or '未知'}{f'（标识 {probe.brand}）' if probe.brand else ''}。{probe.detail}"),
        Check(prefix + "video_codec", f"{source} · 视频编码", "ok" if probe.video else "problem",
              ("、".join(probe.video) + resolution + (f" · {'、'.join(probe.pixels)}" if probe.pixels else ""))
              if probe.video else "未检测到视频轨。"),
        Check(prefix + "audio_codec", f"{source} · 音频编码", "ok" if probe.audio else "warning",
              "、".join(probe.audio) if probe.audio else "未检测到音轨。"),
        Check(prefix + "compat", f"{source} · PowerPoint 兼容性", "ok" if standard_video(probe) else "warning",
              "符合推荐的 MP4 / H.264 / AAC 组合。仍需在目标电脑放映验证。"
              if standard_video(probe) else "检测值与常见推荐组合有差异，实际播放取决于目标电脑。"),
    ]


def standard_video(probe):
    return (probe.mp4 and bool(probe.video) and all(x == "h264" for x in probe.video)
            and all(x == "aac" for x in probe.audio) and all(x in ("yuv420p", "yuvj420p") for x in probe.pixels))


def diagnose(case, deck_path, media, original, ffprobe_setting, answers, sound_report=None, previous_feedback="pending"):
    if case == "open":
        from .opencheck import inspect_open
        return inspect_open(deck_path, previous_feedback)
    deck = read_deck(deck_path)
    checks = [deck.check]
    findings = []
    original_probe = None
    ffprobe = find_ffprobe(ffprobe_setting)
    media_status = "unselected"
    if media is not None:
        # Rebind to the current deck when verifying after a teacher replaces a link or reinserts a video.
        fresh = next((m for m in deck.media if (m.slide, m.location, m.target) ==
                      (media.slide, media.location, media.target)), None)
        if fresh is None:
            same_slide = [m for m in deck.media if m.slide == media.slide]
            fresh = same_slide[0] if len(same_slide) == 1 else None
        media = fresh
    if media is not None:
        media_status = media.availability
        status = {"present": "ok", "missing": "problem", "remote": "unknown", "unreadable": "unknown"}[media_status]
        detail = {"present": "目标存在。", "missing": "目标文件或引用缺失。",
                  "remote": "尚未检测 · 网络或共享链接，未尝试连接。",
                  "unreadable": "尚未检测 · 本机无法读取此路径。"}[media_status]
        checks.append(Check("reference", "所选视频引用", status, f"{media.description}。{detail}"))
        if media_status == "missing":
            action = ("请找回原视频，或在 PowerPoint 中删除损坏的链接后重新插入本地副本。"
                      if media.location == "linked" else "用原始视频替换这条媒体引用，或恢复课件备份。")
            findings.append(Finding("视频引用失效", detail, action, "已检测", 1))
        target_probe = probe_asset(deck_path, media, ffprobe)
    else:
        checks.append(Check("reference", "所选视频引用", "unknown", "尚未检测 · 当前未定位到可选的幻灯片视频。"))
        target_probe = None
    lost = deck.missing
    if lost and (media is None or media.availability != "missing"):
        # Picking one healthy clip must not hide the fact that another reference is gone.
        checks.append(Check("reference", "课件其余视频引用", "problem", deck.missing_summary()))
        findings.append(Finding("课件内有视频找不到文件",
                                f"{len(deck.media)} 条视频引用中有 {len(lost)} 条目标不存在。",
                                "选中上面列出的每一条缺失引用逐条确认；链接型视频把源文件放回原路径，"
                                "或重新插入一份嵌入副本。", "已检测", 1))
    if target_probe:
        checks.extend(summarize_probe(target_probe, "PPT 视频"))
    if original:
        original_probe = probe_file(original, ffprobe)
        checks.extend(summarize_probe(original_probe, "原视频"))
        if original_probe.state in ("failed", "unreadable", "partial") and not target_probe:
            findings.append(Finding("原视频可能不完整或采用特殊格式", original_probe.detail,
                                    "用本地播放器试播，或检查同一视频的已知可用备份。", "可能", 5))
    selected = target_probe if target_probe and target_probe.state in ("ok", "partial") else original_probe or target_probe

    if deck.check.status == "problem":
        findings.append(Finding("课件无法读取", deck.check.detail, "用 PowerPoint 试开副本，必要时改用最近可用备份。", "已检测", 2))
    if target_probe and target_probe.state in ("failed", "unreadable", "partial"):
        findings.append(Finding("PPT 视频可能不完整或采用特殊格式", target_probe.detail,
                                "单独测试原视频。若原视频可播，重新插入课件副本；否则优先找原视频备份。", "可能", 5))
    if selected and selected.state in ("ok", "partial"):
        source = "PPT 视频" if selected is target_probe else "原视频"
        if not selected.video:
            findings.append(Finding("媒体里缺少视频轨", f"FFprobe 在{source}里未发现可播放的视频流。",
                                    "确认选择了正确文件，或换用完整的视频副本。", "有检测依据", 4))
        elif case == "silent" and not selected.audio and selected.state == "ok":
            findings.append(Finding("媒体里缺少音频轨", f"FFprobe 在{source}里未发现音轨。",
                                    "改用带声音的源文件，先独立播放确认再插入 PPT。", "有检测依据", 3))
        if selected.video and not standard_video(selected):
            findings.append(Finding("视频编码或容器可能影响兼容性", f"{source}的探测值与推荐组合有差异。",
                                    "用原视频另存一份 MP4（H.264、8 位 yuv420p、AAC），重新插入后试播。", "可能", 20))
        if case == "picture" and (selected.width > 1920 or selected.height > 1080):
            findings.append(Finding("高分辨率可能增加播放负担",
                                    f"主视频轨分辨率为 {selected.width} × {selected.height}。",
                                    "先用 1080p 兼容副本试播，课堂上可改用本地播放器。", "可能", 12))
    if answers.get("original") == "no":
        findings.append(Finding("原视频本身也无法正常播放", "根据你的本地播放反馈。",
                                "先试已知可用的备份文件。若多个正常视频也播不了，再检查播放器。", "用户反馈", 6))
    if answers.get("source") in ("linked", "online") and media is None:
        findings.append(Finding("先确认链接目标", "根据你提供的插入方式，程序尚未定位到这条引用。",
                                "在 PowerPoint 中确认链接来源。课堂紧急时可使用已确认可播的本地副本。", "用户反馈", 9))
    if answers.get("error") == "missing" and media_status != "missing":
        findings.append(Finding("PowerPoint 报告找不到文件", "根据你选择的错误类别。",
                                "确认出问题的幻灯片和视频，然后重新选择该视频引用进行检查。", "用户反馈", 9))
    if case == "click":
        trigger = answers.get("trigger")
        action = {"normal": "在普通编辑视图用视频控制条播放，再按 Shift+F5 测试当前页放映。",
                  "auto": "在「播放」选项卡核对「开始」是否为自动，并查看动画窗格里的触发设置。",
                  "slide": "按 Shift+F5 放映当前页，确认点击视频本身。检查该对象是否被透明形状覆盖。"}
        findings.append(Finding("核对播放触发方式", "PowerPoint 的编辑预览和放映触发方式不同。",
                                action.get(trigger, "先按 Shift+F5 放映当前页，再使用视频控制条测试。"), "待验证", 38))
    elif case == "format":
        findings.append(Finding("确认目标机播放能力", "PPTX 和 FFprobe 读取仅能确认结构与码流信息。",
                                "用本地播放器测试同一视频，再尝试 PowerPoint「文件 → 信息」中的媒体兼容性功能。", "待验证", 42))
    elif case == "picture":
        external = answers.get("scope") == "external"
        findings.append(Finding("检查投影路径" if external else "对比独立播放和放映画面",
                                "问题只出现在教学屏。" if external else "尚需对比渲染路径。",
                                "核对 HDMI 连接、信号源和 Win+P 投影模式，再试镜像显示。" if external
                                else "用本地播放器播放同一视频，比较本机与教学屏的画面。",
                                "用户反馈" if external else "待验证", 10 if external else 40))
    else:
        if sound_report:
            checks.extend(sound_report.checks)
            findings.extend(sound_report.findings)
        if answers.get("sound") == "no":
            findings.append(Finding("其他应用也没有声音", "根据你的播放反馈。",
                                    "先核对 Windows 声音输出与教室音响。选对设备后播放已知有声音的文件。", "用户反馈", 14))
        if answers.get("original_sound") == "no":
            findings.append(Finding("单独播放原视频也没声音", "根据你的原视频播放反馈。",
                                    "先用另一段确认有声的视频测试同一输出，再决定是否替换源文件。", "用户反馈", 13))
        findings.append(Finding("核对 PowerPoint 和视频自身音量", "系统总音量正常时，应用或视频仍可能单独静音。",
                                "放映出问题的视频时打开 Windows 音量混合器，并核对 PowerPoint「播放 → 音量」。",
                                "待验证", 35))
    if previous_feedback == "failed":
        findings.append(Finding("先恢复课堂播放", "上一次放映反馈仍有故障。",
                                "若源文件可单独播放，先用本地播放器播放。课后在新建的空白 PPT 中插入同一视频，判断问题是否局限于原课件。",
                                "用户反馈", 17))
    checks.append(Check("playback", "实际放映结果", "unknown", "尚未检测 · 请在目标电脑放映后反馈，探测无法替代实际播放。"))
    facts = {"case": case, "answers": answers.copy(), "deck_status": deck.check.status,
             "media_status": media_status, "probe_state": selected.state if selected else "unavailable"}
    if deck.media:
        facts.update(video_references=len(deck.media), missing_references=len(deck.missing))
    if selected and selected.state in ("ok", "partial"):
        facts.update(video=list(selected.video), audio=list(selected.audio))
    if sound_report:
        facts.update(sound_report.facts)
    return Report("video", SYMPTOMS[case], checks, sort_findings(findings), facts)
