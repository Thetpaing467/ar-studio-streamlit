import streamlit as st
import os
import re
import json
import time
import ffmpeg
import shutil
import subprocess
from google import genai
from gradio_client import Client, handle_file

LANGUAGE = "Myanmar"
MODEL = "gemini-3.6-flash"
VOXCPM_SPACE = "openbmb/VoxCPM-Demo"

# ===== Password Check =====
PASSWORD = "voxcpm2026"

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

# ===== KeyManager =====
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


def call_gemini(contents, km, config=None):
    attempts = 0
    max_total = len(km.keys) * 3 if km.keys else 3
    last_err = None
    while attempts < max_total:
        if km.remaining() == 0:
            km.exhausted.clear()
            km.current_index = 0
        client = km.get_client()
        try:
            if config:
                return client.models.generate_content(
                    model=MODEL, contents=contents, config=config
                )
            return client.models.generate_content(model=MODEL, contents=contents)
        except Exception as e:
            last_err = e
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
    raise Exception(f"Retry ကုန်ပါပြီ။ Last error: {last_err}")


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

        if isinstance(result, (tuple, list)):
            audio_path = result[0]
        else:
            audio_path = result

        chunk_path = f"chunk_{i}.wav"
        shutil.copy(audio_path, chunk_path)
        audio_files.append(chunk_path)

    with open("concat_list.txt", "w", encoding="utf-8") as f:
        for audio in audio_files:
            f.write(f"file '{audio}'\n")

    ffmpeg.input("concat_list.txt", format="concat", safe=0).output(
        output_path,
        acodec="libmp3lame",
        audio_bitrate="192k",
        ar=48000
    ).run(overwrite_output=True)

    return output_path


# ============================================================
# 🆕 SRT — Script + Audio Duration
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
    """Script + Audio duration → SRT (millisecond)"""
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
# UI
# ============================================================
st.set_page_config(page_title="🎬 VoxCPM2 Movie Recap", page_icon="🎬")
st.title("🎬 VoxCPM2 Movie Recap")
st.write("ဗီဒီယို upload တင်ပြီး VoxCPM2 အသံနဲ့ Recap ဖန်တီးပါ")

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

# ===== 🆕 Sidebar — Subtitle Settings =====
st.sidebar.header("📝 Subtitle")
use_subtitle = st.sidebar.toggle("SRT ထည့်", value=True)

if use_subtitle:
    sub_position = st.sidebar.selectbox(
        "နေရာ",
        ["bottom", "center", "top"],
        format_func=lambda x: {"bottom": "အောက်ခြေ", "center": "အလယ်", "top": "အပေါ်"}[x]
    )
    sub_font_size = st.sidebar.slider("Font Size", 16, 48, 26)
else:
    sub_position = "bottom"
    sub_font_size = 26

# ===== Video Upload =====
video_file = st.file_uploader("📹 ဗီဒီယို Upload", type=["mp4", "mov", "avi", "mkv"])

if video_file is not None:
    if st.button("🚀 Generate Recap", type="primary"):
        if len(API_KEYS) == 0:
            st.error("❌ Gemini API Key မထည့်ရသေးပါ။")
            st.stop()

        km = KeyManager(API_KEYS)

        with st.spinner("📹 ဗီဒီယို စစ်ဆေးနေသည်..."):
            video_filename = "input_video.mp4"
            with open(video_filename, "wb") as f:
                f.write(video_file.read())
            probe = ffmpeg.probe(video_filename)
            video_duration = float(probe['format']['duration'])
            st.write(f"📹 အရှည်: {video_duration:.2f} စက္ကန့်")

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
                script,
                audio_path,
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

        # ===== 🆕 SRT ဖန်တီး =====
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

            # 1. Video + Audio
            input_video = ffmpeg.input(video_filename)
            input_audio = ffmpeg.input(audio_path).audio.filter('atempo', tempo)
            stream = ffmpeg.output(
                input_video.video, input_audio, temp_path,
                vcodec='libx264',
                crf=18,
                preset='medium',
                acodec='aac',
                audio_bitrate='192k'
            )
            ffmpeg.run(stream, overwrite_output=True)

            # 2. 🆕 SRT မြှုပ် (ရွေးထားရင်)
            if use_subtitle and srt_path:
                with st.spinner("📝 SRT မြှုပ်ထည့်နေသည်..."):
                    subprocess.run([
                        "ffmpeg", "-y",
                        "-i", temp_path,
                        "-vf", f"subtitles={srt_path}:force_style='FontSize={sub_font_size}'",
                        "-c:v", "libx264",
                        "-crf", "18",
                        "-preset", "medium",
                        "-c:a", "copy",
                        final_path
                    ], check=True, capture_output=True)
            else:
                shutil.copy(temp_path, final_path)

        st.success("✅ ပြီးပါပြီ!")
        st.video(final_path)

        with open(final_path, "rb") as f:
            st.download_button("📥 Recap Video Download", f, file_name="final_recap.mp4")

        if use_subtitle and srt_path and os.path.exists(srt_path):
            with open(srt_path, "rb") as f:
                st.download_button("📥 SRT Download", f, file_name="recap.srt")

        with st.expander("📝 Script"):
            st.text(script)
