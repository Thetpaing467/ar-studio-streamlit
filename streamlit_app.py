import streamlit as st
import os
import re
import json
import time
import ffmpeg
import shutil
import subprocess
import sys
from pathlib import Path
from google import genai
from gradio_client import Client, handle_file

# ============================================================
# PyQt5 Setup — Offscreen (Streamlit Cloud)
# ============================================================
os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import (QImage, QPainter, QFont, QColor, QFontMetrics,
                              QBrush, QPen, QPainterPath, QFontDatabase)
    from PyQt5.QtCore import Qt
    _QT_APP = QApplication.instance() or QApplication(sys.argv)
    PYQT_OK = True
except Exception as e:
    PYQT_OK = False
    PYQT_ERR = str(e)

# ============================================================
# Config
# ============================================================
LANGUAGE = "Myanmar"
MODEL = "gemini-3.6-flash"
VOXCPM_SPACE = "openbmb/VoxCPM-Demo"
PASSWORD = "voxcpm2026"
FONT_FILE = "akkayar.ttf"
FONT_NAME = "Akkhayar"

# ============================================================
# Password
# ============================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔐 Private App")
    st.write("Password ထည့်ပါ။")
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        if pwd == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ Password မှားနေပါတယ်။")
    st.stop()

# ============================================================
# Font Register — PyQt5
# ============================================================
@st.cache_resource
def register_font():
    """akkayar.ttf ကို PyQt5 မှာ Register"""
    if not PYQT_OK:
        return "Arial", "PyQt5 မရ"
    if not os.path.exists(FONT_FILE):
        return "Arial", f"Font မရှိ: {FONT_FILE}"
    font_id = QFontDatabase.addApplicationFont(os.path.abspath(FONT_FILE))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        return families[0], f"✅ {families[0]}"
    return "Arial", "Font register fail"

FONT_FAMILY, FONT_STATUS = register_font()

# ============================================================
# KeyManager
# ============================================================
class KeyManager:
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys if k.strip()]
        self.current_index = 0
        self.exhausted = set()

    def get_client(self):
        if not self.keys:
            raise Exception("API Key မထည့်ရသေးပါ။")
        if len(self.exhausted) >= len(self.keys):
            self.exhausted.clear()
            self.current_index = 0
        return genai.Client(api_key=self.keys[self.current_index])

    def rotate(self):
        self.exhausted.add(self.current_index)
        for i in range(len(self.keys)):
            nxt = (self.current_index + 1 + i) % len(self.keys)
            if nxt not in self.exhausted:
                self.current_index = nxt
                return True
        return False

    def remaining(self):
        return len(self.keys) - len(self.exhausted)


def call_gemini(contents, km):
    attempts = 0
    max_total = len(km.keys) * 3 if km.keys else 3
    while attempts < max_total:
        if km.remaining() == 0:
            km.exhausted.clear()
            km.current_index = 0
        client = km.get_client()
        try:
            return client.models.generate_content(model=MODEL, contents=contents)
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                km.rotate()
                attempts += 1
            elif "503" in err or "UNAVAILABLE" in err:
                time.sleep(3)
                attempts += 1
            elif "401" in err or "403" in err or "400" in err:
                km.rotate()
                attempts += 1
            else:
                raise e
    raise Exception("Retry ကုန်ပါပြီ။ Key စစ်ပါ။")


def split_script(text, max_chars=400):
    sentences = text.replace("။", "။|").split("|")
    sentences = [s.strip() + "။" for s in sentences if s.strip()]
    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            if current:
                chunks.append(current)
            if len(sentence) > max_chars:
                for i in range(0, len(sentence), max_chars):
                    chunks.append(sentence[i:i + max_chars])
                current = ""
            else:
                current = sentence
    if current:
        chunks.append(current)
    return chunks


def run_tts_chunked(text, output_path, ref_audio_path=None, progress_callback=None):
    client = Client(VOXCPM_SPACE)
    chunks = split_script(text, max_chars=400)
    audio_files = []
    ref_file = handle_file(ref_audio_path) if ref_audio_path else None

    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback(i, len(chunks), chunk)
        result = client.predict(
            text_input=chunk,
            control_instruction="A warm young woman, calm and expressive",
            reference_wav_path_input=ref_file,
            use_prompt_text=False,
            prompt_text_input="",
            cfg_value_input=2.0,
            do_normalize=True,
            denoise=False,
            api_name="/generate",
        )
        audio_path = result[0] if isinstance(result, (tuple, list)) else result
        chunk_path = f"chunk_{i}.wav"
        shutil.copy(audio_path, chunk_path)
        audio_files.append(chunk_path)

    with open("concat_list.txt", "w", encoding="utf-8") as f:
        for audio in audio_files:
            f.write(f"file '{audio}'\n")

    ffmpeg.input("concat_list.txt", format="concat", safe=0).output(
        output_path, acodec="libmp3lame", audio_bitrate="192k", ar=48000
    ).run(overwrite_output=True)

    return output_path


# ============================================================
# SRT — Script + Audio Duration
# ============================================================
def srt_time(sec):
    ms = int(round((sec - int(sec)) * 1000))
    total = int(sec)
    if ms >= 1000:
        total += 1
        ms = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def script_to_srt(script, audio_path, srt_path):
    audio_dur = float(ffmpeg.probe(audio_path)['format']['duration'])
    sentences = script.replace("။", "။|").split("|")
    sentences = [s.strip() + "။" for s in sentences if s.strip()]
    if not sentences:
        return None

    total_chars = sum(len(s) for s in sentences)
    durations = [(len(s) / total_chars) * audio_dur for s in sentences]

    with open(srt_path, "w", encoding="utf-8") as f:
        current = 0.0
        for i, (sent, dur) in enumerate(zip(sentences, durations), 1):
            f.write(f"{i}\n{srt_time(current)} --> {srt_time(current + dur)}\n{sent}\n\n")
            current += dur
    return srt_path


# ============================================================
# 🆕 PyQt5 PNG Subtitle Module
# ============================================================
def wrap_burmese_text(text, fm, max_w):
    """မြန်မာစာ Line Break"""
    lines = []
    for para in text.split('\n'):
        current_line = ""
        for char in para:
            if fm.horizontalAdvance(current_line + char) <= max_w:
                current_line += char
            else:
                last_space = current_line.rfind(' ')
                if last_space != -1 and last_space > len(current_line) * 0.6:
                    lines.append(current_line[:last_space].strip())
                    current_line = current_line[last_space + 1:] + char
                else:
                    lines.append(current_line)
                    current_line = char
        if current_line:
            lines.append(current_line)
    return '\n'.join(lines)


def create_text_image_full(text, font_size=40, text_color="white",
                            outline_color="black", outline_width=3,
                            width=1080, height=1920, align="bottom",
                            margin_v=280, font_family="Arial",
                            use_box=False, box_color="black", box_alpha=0.5):
    """မြန်မာစာ → PNG Image (Transparent)"""
    if not PYQT_OK:
        raise RuntimeError(f"PyQt5 မရ: {PYQT_ERR}")

    width, height = int(width), int(height)
    img = QImage(width, height, QImage.Format_ARGB32)
    img.fill(Qt.transparent)

    if not text.strip():
        return img

    painter = QPainter(img)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        font = QFont(font_family, int(font_size))
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)

        # Wrap
        text = wrap_burmese_text(text, fm, width - 60)
        lines = text.split('\n')

        max_line_w = int(max(fm.horizontalAdvance(line) for line in lines))
        line_h = int(fm.height() * 1.0)
        total_h = int(len(lines) * line_h)
        x = int((width - max_line_w) / 2)

        # Position
        if align == "bottom":
            y = int(height - margin_v - total_h)
        elif align == "top":
            y = int(margin_v)
        else:
            y = int((height - total_h) / 2)

        # Box
        if use_box:
            box_bg = QColor(box_color)
            box_bg.setAlpha(int(box_alpha * 255))
            painter.setBrush(QBrush(box_bg))
            painter.setPen(Qt.NoPen)
            pad_x = 22
            painter.drawRoundedRect(
                x - pad_x, y - 10,
                max_line_w + pad_x * 2, total_h + 20,
                12, 12
            )

        # Text — Outline + Fill
        t_color = QColor(text_color)
        o_color = QColor(outline_color)
        o_width = int(outline_width)

        current_y = y + fm.ascent()
        for line in lines:
            line_w = int(fm.horizontalAdvance(line))
            line_x = x + int((max_line_w - line_w) / 2)

            path = QPainterPath()
            path.addText(float(line_x), float(current_y), font, line)
            path.setFillRule(Qt.WindingFill)

            if o_width > 0:
                painter.setPen(QPen(o_color, o_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(path)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(t_color))
            painter.drawPath(path)

            current_y += line_h

    finally:
        painter.end()

    return img


def generate_subtitle_pngs(srt_path, png_dir, font_family="Arial",
                            font_size=40, width=1080, height=1920,
                            align="bottom", margin_v=280,
                            text_color="white", outline_color="black",
                            outline_width=3, use_box=True,
                            box_color="black", box_alpha=0.5):
    """SRT → PNG Images + Concat File"""
    os.makedirs(png_dir, exist_ok=True)

    # Blank PNG (background)
    blank_path = os.path.join(png_dir, "blank.png")
    blank_img = QImage(width, height, QImage.Format_ARGB32)
    blank_img.fill(Qt.transparent)
    blank_img.save(blank_path, "PNG")

    # SRT Parse
    with open(srt_path, "r", encoding="utf-8") as f:
        srt = f.read()

    blocks = srt.strip().split("\n\n")
    segments = []

    def ts_to_sec(ts):
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        time_line = lines[1]
        text = " ".join(lines[2:])
        try:
            start_str, end_str = time_line.split(" --> ")
            start = ts_to_sec(start_str)
            end = ts_to_sec(end_str)
            segments.append({"start": start, "end": end, "text": text})
        except Exception:
            continue

    # PNG + Concat
    concat_path = os.path.join(png_dir, "subs_concat.txt")
    with open(concat_path, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        current_time = 0.0
        abs_blank = os.path.abspath(blank_path).replace("\\", "/")

        for i, seg in enumerate(segments):
            txt = seg["text"].strip()
            if not txt:
                continue

            start_t = seg["start"]
            end_t = seg["end"]

            if start_t > current_time:
                f.write(f"file '{abs_blank}'\n")
                f.write(f"duration {start_t - current_time:.3f}\n")

            sub_png = os.path.join(png_dir, f"sub_{i:04d}.png")
            sub_img = create_text_image_full(
                text=txt, font_size=font_size,
                text_color=text_color, outline_color=outline_color,
                outline_width=outline_width,
                use_box=use_box, box_color=box_color, box_alpha=box_alpha,
                width=width, height=height,
                align=align, margin_v=margin_v,
                font_family=font_family
            )
            sub_img.save(sub_png, "PNG")

            abs_sub = os.path.abspath(sub_png).replace("\\", "/")
            f.write(f"file '{abs_sub}'\n")
            f.write(f"duration {end_t - start_t:.3f}\n")
            current_time = end_t

        # Final blank
        f.write(f"file '{abs_blank}'\n")

    return concat_path


def burn_png_subtitle(video_path, png_concat_path, output_path):
    """PNG Concat → Video Overlay"""
    # Video Duration
    probe = ffmpeg.probe(video_path)
    video_dur = float(probe['format']['duration'])

    # FFmpeg Command
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-f", "concat", "-safe", "0", "-i", png_concat_path,
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[vout]",
        "-map", "[vout]",
        "-map", "0:a?",
        "-t", str(video_dur),
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg error:\n{result.stderr[-1500:]}")
    return output_path


# ============================================================
# UI
# ============================================================
st.set_page_config(page_title="🎬 VoxCPM2 Movie Recap", page_icon="🎬")
st.title("🎬 VoxCPM2 Movie Recap")
st.write("ဗီဒီယို upload တင်ပြီး မြန်မာ Subtitle ပါဝင်တဲ့ Recap ဖန်တီးပါ")

# PyQt5 Status
if not PYQT_OK:
    st.error(f"❌ PyQt5 error: {PYQT_ERR}")

# ===== Sidebar — Gemini Key =====
st.sidebar.header("🔑 Gemini API Key")
key_input = st.sidebar.text_area(
    "Gemini API Key(s) — comma နဲ့ ခြား",
    placeholder="AQ.Ab8RN6... , AQ.Ab8RN6...",
    height=100
)

if key_input.strip():
    API_KEYS = [k.strip() for k in key_input.split(",") if k.strip()]
else:
    API_KEYS = [k.strip() for k in st.secrets.get("GEMINI_API_KEYS", "").split(",") if k.strip()]

st.sidebar.write(f"🔑 Key: {len(API_KEYS)} ခု")

# ===== Sidebar — Voice Sample =====
st.sidebar.header("🎙️ Voice Sample")
if "ref_audio_path" not in st.session_state:
    st.session_state.ref_audio_path = None

ref_audio = st.sidebar.file_uploader(
    "Reference Audio (၁၀-၁၅ စက္ကန့်) — တစ်ခါပဲ တင်ပါ",
    type=["wav", "mp3", "m4a"]
)

if ref_audio is not None:
    ref_path = "reference_voice.wav"
    with open(ref_path, "wb") as f:
        f.write(ref_audio.read())
    st.session_state.ref_audio_path = ref_path
    st.sidebar.success("✅ အသံ သိမ်းပြီး")

if st.session_state.ref_audio_path:
    st.sidebar.info("🎙️ Clone အသံ ရှိပြီး ✅")
    if st.sidebar.button("🗑️ အသံ ဖျောက်"):
        st.session_state.ref_audio_path = None
        st.rerun()
else:
    st.sidebar.warning("⚠️ Clone လုပ်ချင်ရင် အသံ တင်ပါ")

# ===== Sidebar — Subtitle =====
st.sidebar.header("📝 Subtitle")
use_subtitle = st.sidebar.toggle("SRT ထည့်", value=True)

if use_subtitle:
    sub_font_size = st.sidebar.slider("Font Size", 16, 72, 40)
    sub_position = st.sidebar.selectbox(
        "နေရာ",
        ["bottom", "center", "top"],
        format_func=lambda x: {"bottom": "အောက်ခြေ", "center": "အလယ်", "top": "အပေါ်"}[x]
    )
    sub_color = st.sidebar.selectbox(
        "အရောင်",
        ["white", "yellow", "#00E5FF", "#39FF14", "#FF6EC7"]
    )
    use_box = st.sidebar.toggle("Box ထည့်", value=True)
else:
    sub_font_size = 40
    sub_position = "bottom"
    sub_color = "white"
    use_box = True

# ===== Font Status =====
st.sidebar.write(f"🔤 Font: {FONT_STATUS}")

# ===== Video Upload =====
video_file = st.file_uploader("📹 ဗီဒီယို Upload", type=["mp4", "mov", "avi", "mkv"])

if video_file is not None:
    if st.button("🚀 Generate Recap", type="primary"):
        if len(API_KEYS) == 0:
            st.error("❌ Gemini API Key မထည့်ရသေးပါ။")
            st.stop()

        if use_subtitle and not PYQT_OK:
            st.error("❌ PyQt5 မရ — Subtitle မလုပ်နိုင်")
            st.stop()

        km = KeyManager(API_KEYS)

        with st.spinner("📹 ဗီဒီယို စစ်ဆေးနေသည်..."):
            video_filename = "input_video.mp4"
            with open(video_filename, "wb") as f:
                f.write(video_file.read())
            probe = ffmpeg.probe(video_filename)
            video_duration = float(probe['format']['duration'])
            st.write(f"📹 အရည်: {video_duration:.2f} စက္ကန့်")

        with st.spinner("✍️ Gemini → Script ရေးနေသည်..."):
            client = km.get_client()
            gfile = client.files.upload(file=video_filename)
            while gfile.state.name == "PROCESSING":
                time.sleep(3)
                gfile = client.files.get(name=gfile.name)
            prompt = (
                f"Watch this video carefully and write a clear, continuous movie recap script "
                f"in {LANGUAGE} language for audio narration that matches the length of the video. "
                f"Return plain speech text only without markdown titles."
            )
            response = call_gemini([gfile, prompt], km)
            script = response.text.strip()
            st.write(f"✅ Script ({len(script)} စာလုံး)")

        st.write("🎙️ VoxCPM2 → အသံ ထုတ်နေသည်...")
        progress_bar = st.progress(0)
        status_text = st.empty()

        def update_progress(i, total, chunk):
            progress_bar.progress((i + 1) / total)
            status_text.write(f"🎙️ [{i+1}/{total}] ({len(chunk)} စာလုံး)")

        audio_path = "recap_voice.mp3"

        try:
            run_tts_chunked(
                script, audio_path,
                ref_audio_path=st.session_state.ref_audio_path,
                progress_callback=update_progress
            )
            st.write("✅ အသံ ထုတ်ပြီး")

            audio_dur = float(ffmpeg.probe(audio_path)['format']['duration'])
            tempo = max(0.8, min(1.2, audio_dur / video_duration))
            st.write(f"🎙️ အသံ ({audio_dur:.1f}s) | Tempo: {tempo:.2f}x")
        except Exception as e:
            st.error(f"❌ VoxCPM2 error: {e}")
            st.stop()

        # ===== SRT ဖန်တီး =====
        srt_path = None
        if use_subtitle:
            with st.spinner("📝 Script → SRT..."):
                srt_path = script_to_srt(script, audio_path, "recap.srt")
                with open(srt_path, "r", encoding="utf-8") as f:
                    st.text_area("📝 SRT", f.read(), height=150)

        # ===== Render =====
with st.spinner("🎬 Recap Video Render..."):
    final_path = "final_recap.mp4"
    temp_path = "temp_recap.mp4"
