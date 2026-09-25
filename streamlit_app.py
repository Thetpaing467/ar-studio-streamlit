import streamlit as st
import os
import re
import math
import time
import hashlib
import ffmpeg
import shutil
import subprocess
from google import genai
from gradio_client import Client, handle_file

# ============================================================
# Config
# ============================================================
LANGUAGE = "Myanmar"
MODEL = "gemini-3.6-flash"
VOXCPM_PRIMARY = "openbmb/VoxCPM-Demo"
VOXCPM_FALLBACK = "hgghfhjfhjguyjf/Voxcpm-Burmese-Tts"
PASSWORD = "voxcpm2026"

# Calibrated Speed
BURMESE_CPS_MIN = 20
BURMESE_CPS_MAX = 24
BLOCK_SECONDS = 12
GAP_MAX = 10.0
BLOCK_CPS_MIN = 15
BLOCK_CPS_MAX = 30

# Hydra Logic
HEALTH_MAX = 100
HEALTH_SUCCESS = 5
HEALTH_FAILURE = -10
HEALTH_DISABLE_THRESHOLD = 5
COOLDOWN_429 = 60
COOLDOWN_DEAD = 86400
# ============================================================
# Password Check
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
# Hydra — Multi-Key Manager
# ============================================================
class HydraKeyManager:
    """Multi-Key Manager — Hydra Logic"""
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys if k.strip()]
        self.health = {k: HEALTH_MAX for k in self.keys}
        self.cooldown = {k: 0.0 for k in self.keys}
        self.errors = {k: 0 for k in self.keys}
        self.current_index = 0

    def _get_available(self):
        now = time.time()
        return [
            i for i, k in enumerate(self.keys)
            if self.cooldown[k] <= now and self.health[k] > 0
        ]

    def get_client(self):
        if not self.keys:
            raise Exception("API Key မထည့်ရသေးပါ။")

        available = self._get_available()

        if not available:
            self.cooldown = {k: 0.0 for k in self.keys}
            available = list(range(len(self.keys)))

        if not available:
            self.health = {k: HEALTH_MAX for k in self.keys}
            self.errors = {k: 0 for k in self.keys}
            available = list(range(len(self.keys)))

        available.sort(key=lambda i: self.health[self.keys[i]], reverse=True)
        self.current_index = available[0]
        return genai.Client(api_key=self.keys[self.current_index])

    def rotate(self, permanent=False):
        key = self.keys[self.current_index]
        self.health[key] = max(0, self.health[key] + HEALTH_FAILURE)
        self.errors[key] += 1

        if permanent:
            self.cooldown[key] = time.time() + COOLDOWN_DEAD
        else:
            self.cooldown[key] = time.time() + COOLDOWN_429

        if self.errors[key] >= HEALTH_DISABLE_THRESHOLD:
            self.cooldown[key] = time.time() + COOLDOWN_DEAD

        for i in range(1, len(self.keys) + 1):
            nxt = (self.current_index + i) % len(self.keys)
            k = self.keys[nxt]
            if self.cooldown[k] <= time.time() and self.health[k] > 0:
                self.current_index = nxt
                return True
        return False

    def mark_success(self):
        key = self.keys[self.current_index]
        self.health[key] = min(HEALTH_MAX, self.health[key] + HEALTH_SUCCESS)
        self.errors[key] = 0

    def status(self):
        now = time.time()
        active = sum(1 for k in self.keys
                     if self.cooldown[k] <= now and self.health[k] > 0)
        return {
            "total": len(self.keys),
            "active": active,
            "cooldown": len(self.keys) - active,
            "avg_health": sum(self.health.values()) // max(1, len(self.keys)),
        }


def call_gemini(contents, km):
    """Hydra Failover"""
    attempts = 0
    max_attempts = len(km.keys) * 3

    while attempts < max_attempts:
        client = km.get_client()
        try:
            resp = client.models.generate_content(model=MODEL, contents=contents)
            km.mark_success()
            return resp

        except Exception as e:
            err = str(e)

            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                st.warning(f"⚠️ Key {km.current_index+1} — 429")
                km.rotate(permanent=False)
                attempts += 1

            elif "503" in err or "UNAVAILABLE" in err:
                st.warning(f"⚠️ 503 — ၅ စက္ကန့်")
                time.sleep(5)
                attempts += 1

            elif "401" in err or "403" in err or "400" in err:
                st.warning(f"⚠️ Key {km.current_index+1} — မှား")
                km.rotate(permanent=True)
                attempts += 1

            else:
                raise e

    raise Exception(f"Key {len(km.keys)} ခုလုံး — ကုန်ပါပြီ။")


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
  # ============================================================
# TTS — Primary + Fallback
# ============================================================
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
# SRT — Calibrated
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


def calc_target_chars(video_duration):
    return {"min": int(video_duration * BURMESE_CPS_MIN),
            "max": int(video_duration * BURMESE_CPS_MAX)}


def check_script_budget(script, video_duration):
    target = calc_target_chars(video_duration)
    actual = len(script)
    if actual < target["min"]:
        return False, f"Script တို — {actual} < {target['min']}"
    if actual > target["max"]:
        return False, f"Script ရှည် — {actual} > {target['max']}"
    return True, f"OK ({actual})"


def script_to_srt(script, video_duration, srt_path):
    sentences = script.replace("။", "။|").split("|")
    sentences = [s.strip() + "။" for s in sentences if s.strip()]
    if not sentences:
        return None

    max_block_chars = int(BLOCK_SECONDS * BURMESE_CPS_MAX)
    blocks = []
    current = ""
    for s in sentences:
        if len(current) + len(s) > max_block_chars and current:
            blocks.append(current.strip())
            current = s + " "
        else:
            current += s + " "
    if current.strip():
        blocks.append(current.strip())

    with open(srt_path, "w", encoding="utf-8") as f:
        current_time = 0.0
        for i, block in enumerate(blocks, 1):
            dur = len(block) / 22.0
            dur = max(len(block) / BURMESE_CPS_MAX,
                      min(dur, len(block) / BURMESE_CPS_MIN))
            start = current_time
            end = min(current_time + dur, video_duration)
            f.write(f"{i}\n{srt_time(start)} --> {srt_time(end)}\n{block}\n\n")
            current_time = end
            if current_time >= video_duration - 0.5:
                break
    return srt_path


def ts_to_sec(s):
    s = s.strip()
    m = re.match(r'^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$', s)
    if m:
        h, mi, se, ms = m.groups()
        return int(h)*3600 + int(mi)*60 + int(se) + int(ms.ljust(3, '0')) / 1000.0
    m = re.match(r'^(\d{1,2}):(\d{2})[,.](\d{1,3})$', s)
    if m:
        mi, se, ms = m.groups()
        return int(mi)*60 + int(se) + int(ms.ljust(3, '0')) / 1000.0
    return None


def validate_srt(srt_path, video_duration):
    with open(srt_path, "r", encoding="utf-8") as f:
        raw = f.read().replace("\r\n", "\n").replace("\r", "\n")
    chunks = re.split(r"\n\s*\n", raw.strip())
    errors, warnings, blocks = [], [], []

    for ch in chunks:
        lines = [ln for ln in ch.split("\n") if ln.strip()]
        if not lines:
            continue
        ts_line = next((ln for ln in lines if "-->" in ln), None)
        if not ts_line:
            continue
        parts = re.split(r"\s*-->\s*", ts_line)
        if len(parts) != 2:
            continue
        start = ts_to_sec(parts[0])
        end = ts_to_sec(parts[1])
        if start is None or end is None or end <= start:
            continue
        ts_idx = lines.index(ts_line)
        text = " ".join(lines[ts_idx + 1:]).strip()
        if text:
            blocks.append({"start": start, "end": end, "text": text})

    blocks.sort(key=lambda b: b["start"])
    for i in range(len(blocks) - 1):
        if blocks[i + 1]["start"] < blocks[i]["start"] + 1e-9:
            warnings.append(f"Block {i+1} overlap")

    for i, b in enumerate(blocks):
        dur = b["end"] - b["start"]
        if dur <= 0:
            continue
        cps = len(b["text"]) / dur
        if cps < BLOCK_CPS_MIN or cps > BLOCK_CPS_MAX:
            errors.append(f"Block {i+1} CPS={cps:.1f}")

    if blocks:
        if blocks[0]["start"] > GAP_MAX:
            errors.append(f"First start {blocks[0]['start']:.2f}s")
        if blocks[-1]["end"] < video_duration - 0.97:
            errors.append(f"Last end {blocks[-1]['end']:.2f}s")

    for i in range(len(blocks) - 1):
        gap = blocks[i + 1]["start"] - blocks[i]["end"]
        if gap > GAP_MAX:
            errors.append(f"Gap {gap:.2f}s")

    total_chars = sum(len(b["text"]) for b in blocks)
    glo_lo = video_duration * BURMESE_CPS_MIN
    glo_hi = video_duration * BURMESE_CPS_MAX
    if not (glo_lo <= total_chars <= glo_hi):
        errors.append(f"Global chars={total_chars} ({glo_lo:.0f}-{glo_hi:.0f})")

    last_end = blocks[-1]["end"] if blocks else 0
    coverage = (last_end / video_duration) if video_duration else 0

    return {
        "pass": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "blocks": len(blocks),
        "coverage": round(coverage * 100, 1),
        "total_chars": total_chars,
        "target_min": int(glo_lo),
        "target_max": int(glo_hi),
                               }
  # ============================================================
# UI
# ============================================================
st.set_page_config(page_title="🎬 VoxCPM2 Movie Recap", page_icon="🎬")
st.title("🎬 VoxCPM2 Movie Recap")
st.write("ဗီဒီယို upload တင်ပြီး VoxCPM2 အသံနဲ့ Recap ဖန်တီးပါ")

# Sidebar — Gemini Key
st.sidebar.header("🔑 Gemini API Key")
key_input = st.sidebar.text_area(
    "Key(s) — comma နဲ့ ခြား (၁၀+ ခု)",
    placeholder="key1,key2,key3,key4,key5,key6,key7,key8,key9,key10",
    height=150
)

if key_input.strip():
    API_KEYS = [k.strip() for k in key_input.split(",") if k.strip()]
else:
    API_KEYS = [k.strip() for k in st.secrets.get("GEMINI_API_KEYS", "").split(",") if k.strip()]

st.sidebar.write(f"🔑 Keys: {len(API_KEYS)} ခု")

if len(API_KEYS) > 0:
    _km = HydraKeyManager(API_KEYS)
    st_ = _km.status()
    st.sidebar.write(f"✅ Active: {st_['active']}")
    st.sidebar.write(f"⏳ Cooldown: {st_['cooldown']}")
    st.sidebar.write(f"💚 Health: {st_['avg_health']}")

# Sidebar — Reference Audio
st.sidebar.header("🎙️ Reference Audio")
if "ref_audio_path" not in st.session_state:
    st.session_state.ref_audio_path = None

ref_audio = st.sidebar.file_uploader(
    "Reference Audio (၅-၁၅ စက္ကန့်)",
    type=["wav", "mp3", "m4a"]
)

if ref_audio is not None:
    ref_path = "reference_voice.wav"
    with open(ref_path, "wb") as f:
        f.write(ref_audio.read())
    st.session_state.ref_audio_path = ref_path
    st.sidebar.success("✅ အသံ သိမ်းပြီး")

if st.session_state.ref_audio_path:
    st.sidebar.info("🎙️ Reference Audio ✅")
    if st.sidebar.button("🗑️ အသံ ဖျောက်"):
        st.session_state.ref_audio_path = None
        st.rerun()
else:
    st.sidebar.warning("⚠️ Reference Audio တင်ပါ")

# Main
video_file = st.file_uploader("📹 ဗီဒီယို Upload", type=["mp4", "mov", "avi", "mkv"])

if video_file is not None:
    if st.button("🚀 Generate Recap", type="primary"):
        if len(API_KEYS) == 0:
            st.error("❌ Gemini API Key မထည့်ရသေးပါ။")
            st.stop()

        km = HydraKeyManager(API_KEYS)

        with st.spinner("📹 ဗီဒီယို စစ်ဆေးနေသည်..."):
            video_filename = "input_video.mp4"
            with open(video_filename, "wb") as f:
                f.write(video_file.read())
            probe = ffmpeg.probe(video_filename)
            video_duration = float(probe['format']['duration'])
            st.write(f"📹 အရှည်: {video_duration:.2f} စက္ကန့်")

        with st.spinner("✍️ Gemini → Script..."):
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

        budget_ok, budget_msg = check_script_budget(script, video_duration)
        target = calc_target_chars(video_duration)
        st.info(f"📊 Target: {target['min']}–{target['max']} | Actual: {len(script)} | {budget_msg}")

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

        srt_path = None
        with st.spinner("📝 Script → SRT..."):
            srt_path = script_to_srt(script, video_duration, "recap.srt")
            if srt_path:
                with open(srt_path, "r", encoding="utf-8") as f:
                    st.text_area("📝 SRT", f.read(), height=150)

        if srt_path:
            val = validate_srt(srt_path, video_duration)
            if val["pass"]:
                st.success("✅ SRT Validate — PASS")
            else:
                st.error(f"❌ SRT Validate — FAIL ({len(val['errors'])} errors)")

            with st.expander("📊 SRT Validation Report"):
                st.write(f"**Blocks:** {val['blocks']}")
                st.write(f"**Coverage:** {val['coverage']}%")
                st.write(f"**Chars:** {val['total_chars']} (Target: {val['target_min']}–{val['target_max']})")
                if val["errors"]:
                    for e in val["errors"]:
                        st.write(f"- ❌ {e}")
                if val["warnings"]:
                    for w in val["warnings"]:
                        st.write(f"- ⚠️ {w}")

        with st.spinner("🎬 Render..."):
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
            st.download_button("📥 Recap Video", f, file_name="final_recap.mp4")

        if srt_path and os.path.exists(srt_path):
            with open(srt_path, "rb") as f:
                st.download_button("📥 SRT", f, file_name="recap.srt")

        with st.expander("📝 Script"):
            st.text(script)
          
