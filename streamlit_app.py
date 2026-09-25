import streamlit as st
import os
import re
import time
import ffmpeg
import shutil
import subprocess
from gradio_client import Client, handle_file

# ============================================================
# Config
# ============================================================
VOXCPM_PRIMARY = "openbmb/VoxCPM-Demo"
VOXCPM_FALLBACK = "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts"
PASSWORD = "voxcpm2026"

# ============================================================
# Password
# ============================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔐 Private App")
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        if pwd == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ Password မှား")
    st.stop()

# ============================================================
# TTS — Primary + Fallback
# ============================================================
def split_script(text, max_chars=400):
    sentences = text.replace("။", "။|").split("|")
    sentences = [s.strip() + "။" for s in sentences if s.strip()]
    chunks = []
    current = ""
    for s in sentences:
        if len(current) + len(s) <= max_chars:
            current += s
        else:
            if current:
                chunks.append(current)
            if len(s) > max_chars:
                for i in range(0, len(s), max_chars):
                    chunks.append(s[i:i + max_chars])
                current = ""
            else:
                current = s
    if current:
        chunks.append(current)
    return chunks


def tts_primary(chunks, ref_audio_path, progress_callback=None):
    client = Client(VOXCPM_PRIMARY)
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
        chunk_path = f"chunk_primary_{i}.wav"
        shutil.copy(audio_path, chunk_path)
        audio_files.append(chunk_path)
    return audio_files


def tts_fallback(chunks, ref_audio_path, progress_callback=None):
    client = Client(VOXCPM_FALLBACK)
    audio_files = []
    if not ref_audio_path:
        raise Exception("Fallback — Reference Audio လိုတယ်")
    ref_file = handle_file(ref_audio_path)
    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback(i, len(chunks), chunk)
        result = client.predict(
            target_text=chunk,
            ref_audio=ref_file,
            ref_text="မြန်မာ အသံနမူနာ",
            cfg_value=2.0,
            inference_timesteps=10,
            api_name="/tts"
        )
        audio_path = result[0] if isinstance(result, (tuple, list)) else result
        chunk_path = f"chunk_fallback_{i}.wav"
        shutil.copy(audio_path, chunk_path)
        audio_files.append(chunk_path)
    return audio_files


def run_tts_chunked(text, output_path, ref_audio_path=None, progress_callback=None):
    chunks = split_script(text, max_chars=400)
    audio_files = None
    try:
        st.info("🎙️ Primary Space...")
        audio_files = tts_primary(chunks, ref_audio_path, progress_callback)
        st.success("✅ Primary — အောင်မြင်")
    except Exception as e:
        st.warning(f"⚠️ Primary fail: {str(e)[:150]}")
        st.info("🔄 Fallback Space...")
        try:
            audio_files = tts_fallback(chunks, ref_audio_path, progress_callback)
            st.success("✅ Fallback — အောင်မြင်")
        except Exception as e2:
            raise Exception(f"Primary + Fallback fail:\n{e}\n---\n{e2}")

    with open("concat_list.txt", "w", encoding="utf-8") as f:
        for audio in audio_files:
            f.write(f"file '{audio}'\n")

    ffmpeg.input("concat_list.txt", format="concat", safe=0).output(
        output_path, acodec="libmp3lame", audio_bitrate="192k", ar=48000
    ).run(overwrite_output=True)
    return output_path


# ============================================================
# UI
# ============================================================
st.set_page_config(page_title="🎬 VoxCPM2 Movie Recap", page_icon="🎬")
st.title("🎬 VoxCPM2 Movie Recap")
st.write("Gemini Web မှ Script ရယူပြီး VoxCPM2 အသံနဲ့ Recap ဖန်တီးပါ")

# ============================================================
# 🆕 Step 1: Gemini Web — Script Manual
# ============================================================
st.header("📝 Step 1: Gemini Web → Script")

st.info(
    "**အဆင့် ၁:** [gemini.google.com](https://gemini.google.com) ဖွင့် → "
    "Video Upload → Prompt ပေး → Script ရ → Copy"
)

with st.expander("📋 Prompt (Copy → Gemini Web)", expanded=True):
    st.code(
        "Watch this video carefully and write a clear, continuous movie recap script "
        "in Myanmar language for audio narration that matches the length of the video. "
        "Return plain speech text only without markdown titles.",
        language="text"
    )

st.markdown("**Gemini Web လင့်:** [gemini.google.com](https://gemini.google.com)")

# ============================================================
# 🆕 Step 2: Script Paste
# ============================================================
st.header("📝 Step 2: Script Paste")

script = st.text_area(
    "Script (Gemini Web မှ Copy → Paste ဒီမှာ)",
    height=250,
    placeholder="မြန်မာ Script ဒီမှာ paste ပါ..."
)

# ============================================================
# 🆕 Step 3: Reference Audio + Video Upload
# ============================================================
st.header("🎙️ Step 3: Reference Audio + Video")

if "ref_audio_path" not in st.session_state:
    st.session_state.ref_audio_path = None

ref_audio = st.file_uploader(
    "Reference Audio (၅-၁၅ စက္ကန့်) — VoxCPM2 Voice Clone",
    type=["wav", "mp3", "m4a"]
)

if ref_audio is not None:
    ref_path = "reference_voice.wav"
    with open(ref_path, "wb") as f:
        f.write(ref_audio.read())
    st.session_state.ref_audio_path = ref_path
    st.success("✅ Reference Audio — သိမ်းပြီး")

video_file = st.file_uploader(
    "📹 Video Upload (Recap Render အတွက်)",
    type=["mp4", "mov", "avi", "mkv"]
)

# ============================================================
# 🆕 Step 4: Generate Recap
# ============================================================
st.header("🚀 Step 4: Generate Recap")

if st.button("✨ Generate Recap Video", type="primary"):
    if not script.strip():
        st.error("❌ Script paste ပါ — Step 2")
        st.stop()
    if video_file is None:
        st.error("❌ Video Upload — Step 3")
        st.stop()

    # Video Save
    with st.spinner("📹 ဗီဒီယို စစ်ဆေးနေသည်..."):
        video_filename = "input_video.mp4"
        with open(video_filename, "wb") as f:
            f.write(video_file.read())
        probe = ffmpeg.probe(video_filename)
        video_duration = float(probe['format']['duration'])
        st.write(f"📹 အရှည်: {video_duration:.2f} စက္ကန့်")

    # TTS
    st.write("🎙️ VoxCPM2 → အသံ...")
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

    # Render
    with st.spinner("🎬 Recap Video Render..."):
        final_path = "final_recap.mp4"
        input_video = ffmpeg.input(video_filename)
        input_audio = ffmpeg.input(audio_path).audio.filter('atempo', tempo)
        stream = ffmpeg.output(
            input_video.video, input_audio, final_path,
            vcodec='libx264', crf=18, preset='medium',
            acodec='aac', audio_bitrate='192k'
        )
        ffmpeg.run(stream, overwrite_output=True)

    st.success("✅ ပြီးပါပြီ!")
    st.video(final_path)

    with open(final_path, "rb") as f:
        st.download_button("📥 Recap Video Download", f, file_name="final_recap.mp4")
