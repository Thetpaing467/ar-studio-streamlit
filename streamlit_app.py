import streamlit as st
import os
import re
import json
import time
import shutil
import subprocess
from pathlib import Path
from google import genai
from google.genai import types

try:
    from gradio_client import Client as GradioClient
except ImportError:
    GradioClient = None

# ============================================================
# Config
# ============================================================
APP_DIR = Path(__file__).parent
WORK = APP_DIR / "work"
WORK.mkdir(exist_ok=True)

MAX_SECONDS = 600
DEFAULT_MODEL = "gemini-2.0-flash-exp"
DEFAULT_CHOU_SPACE = "Steve2412/Chou-Pro-TTS-SRT"
PASSWORD = "voxcpm2026"

# ============================================================
# Password
# ============================================================
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("🔐 AR Studio")
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        if pwd == PASSWORD:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("❌ Password မှား")
    st.stop()

# ============================================================
# Utilities
# ============================================================
def cmd(args):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-3000:] or "Command failed")
    return p.stdout


def duration(path):
    return float(cmd([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ]).strip())


def clean_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Gemini JSON မပြန်ပါ။")
    return json.loads(m.group(0))


def srt_time(sec):
    ms = int(round((sec - int(sec)) * 1000))
    total = int(sec)
    if ms >= 1000:
        total += 1
        ms = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ============================================================
# Key Manager
# ============================================================
class KeyManager:
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys if k.strip()]
        self.idx = 0
        self.dead = set()

    def get(self):
        if not self.keys:
            raise Exception("API Key မထည့်ရသေးပါ။")
        if len(self.dead) >= len(self.keys):
            self.dead.clear()
            self.idx = 0
        return genai.Client(api_key=self.keys[self.idx])

    def rotate(self):
        self.dead.add(self.idx)
        for i in range(len(self.keys)):
            nxt = (self.idx + 1 + i) % len(self.keys)
            if nxt not in self.dead:
                self.idx = nxt
                return
        self.dead.clear()
        self.idx = 0


def call_gemini(contents, km, config=None):
    for _ in range(len(km.keys) * 3 if km.keys else 3):
        client = km.get()
        try:
            return client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=contents,
                config=config
            )
        except Exception as e:
            err = str(e)
            if any(x in err for x in ["429", "RESOURCE_EXHAUSTED",
                                       "401", "403", "400", "503"]):
                km.rotate()
                time.sleep(2)
            else:
                raise e
    raise Exception("Retry ကုန်ပါပြီ။")


# ============================================================
# Step 1: Gemini — Script + Timestamp
# ============================================================
def gemini_recap(video_path, target_seconds, extra, km):
    client = km.get()
    src_duration = duration(video_path)

    # Upload to Gemini
    f = client.files.upload(file=str(video_path))
    while f.state.name == "PROCESSING":
        time.sleep(2)
        f = client.files.get(name=f.name)

    target = min(int(target_seconds), MAX_SECONDS)

    prompt = f"""မင်းက AR Studio အတွက် Professional Burmese Movie Recap Writer ဖြစ်တယ်။
ဒီ video ကို အစ၊ အလယ်၊ အဆုံး၊ ဇာတ်သိမ်းအထိ သေချာလေ့လာပြီး
မြန်မာ Unicode နဲ့ original recap narration ရေးပါ။

SOURCE VIDEO DURATION: {src_duration:.2f} seconds
TARGET FINAL NARRATION: {target} seconds

မဖြစ်မနေ:
- ဇာတ်လမ်းကို ending အထိ ပြီးပြည့်စုံစွာ ရှင်းပြ။
- Video ထဲမရှိတဲ့ character/event/twist ကို မတီထွင်။
- စကားပြောသလို သဘာဝကျပြီး cinematic, engaging Burmese ဖြစ်ရမည်။
- Hook → setup → conflict → escalation → climax → ending အစဉ်လိုက်။
- Segment တစ်ခုချင်းစီအတွက် source_start/source_end timestamp ထည့်။
- Timestamp များသည် 0 မှ {src_duration:.2f} အတွင်းသာ။
- Output JSON တစ်ခုတည်းသာ။

Additional context:
{extra or "မရှိပါ။"}

JSON:
{{
 "title": "string",
 "hook": "string",
 "segments": [
   {{
     "id": "seg_001",
     "text": "မြန်မာ narration",
     "source_start": 0.0,
     "source_end": 8.0
   }}
 ]
}}
"""

    r = call_gemini(
        [f, prompt], km,
        config=types.GenerateContentConfig(
            temperature=0.25,
            response_mime_type="application/json"
        )
    )

    data = clean_json(r.text)

    # Validate segments
    segs = []
    for i, s in enumerate(data.get("segments", []), 1):
        t = str(s.get("text", "")).strip()
        if not t:
            continue
        a = max(0, min(float(s.get("source_start", 0)), src_duration))
        b = max(a + 0.5, min(float(s.get("source_end", a + 1)), src_duration))
        segs.append({
            "id": s.get("id", f"seg_{i:03d}"),
            "text": t,
            "source_start": a,
            "source_end": b
        })

    if not segs:
        raise RuntimeError("Gemini script segments မရှိပါ။")

    data["segments"] = segs
    return data


# ============================================================
# Step 2: Chou Pro TTS/SRT
# ============================================================
def chou_tts(text, workdir, space=DEFAULT_CHOU_SPACE, hf_token=None,
             language="my", voice=""):
    if GradioClient is None:
        raise RuntimeError("gradio_client install မဖြစ်ပါ။")

    client = GradioClient(space, hf_token=hf_token or None, verbose=False)

    api = client.view_api(return_format="dict")
    named = api.get("named_endpoints", {}) if isinstance(api, dict) else {}

    if not named:
        raise RuntimeError("Chou endpoint မတွေ့ပါ။")

    # Endpoint ရွေး (TTS/SRT ဖြစ်နိုင်တာ)
    endpoint = None
    for name in named:
        low = name.lower()
        if any(k in low for k in ("tts", "srt", "speech", "voice", "synthesize", "generate")):
            endpoint = name
            break
    if endpoint is None:
        endpoint = list(named.keys())[0]

    spec = named.get(endpoint, {})
    params = spec.get("parameters", []) if isinstance(spec, dict) else []

    # Args တည်ဆောက်
    args = []
    for p in params:
        name = str(p.get("name", "")).lower()
        typ = str(p.get("type", "")).lower()
        required = bool(p.get("required", False))

        if "file" in typ:
            raise RuntimeError("Chou endpoint က file input တောင်းနေပါတယ်။")

        if any(k in name for k in ("text", "script", "input", "prompt")):
            args.append(text)
        elif any(k in name for k in ("language", "lang")):
            args.append(language)
        elif any(k in name for k in ("voice", "speaker")):
            args.append(voice)
        elif required:
            args.append(p.get("default", ""))

    # Predict
    result = client.predict(*args, api_name=endpoint)

    # Result ထဲက audio + srt ရှာ
    found = []
    def walk(x):
        if isinstance(x, str):
            found.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
    walk(result)

    audio = next((x for x in found
                  if x.lower().endswith((".wav", ".mp3", ".m4a", ".flac", ".ogg"))
                  and Path(x).exists()), None)
    srt = next((x for x in found
                if x.lower().endswith(".srt") and Path(x).exists()), None)

    if not audio:
        raise RuntimeError(f"Chou TTS audio မရပါ။ result={result}")

    aout = workdir / "voice.wav"
    shutil.copy2(audio, aout)

    sout = None
    if srt:
        sout = workdir / "voice.srt"
        shutil.copy2(srt, sout)

    return aout, sout


# ============================================================
# Fallback SRT — Script + Audio Duration
# ============================================================
def script_to_srt(script, audio_path, srt_path):
    audio_dur = duration(audio_path)

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
# Step 3: Video ဖြတ်
# ============================================================
def extract_clips(src, segments, folder):
    folder.mkdir(exist_ok=True, parents=True)
    clips = []
    for i, s in enumerate(segments, 1):
        out = folder / f"clip_{i:04d}.mp4"
        cmd([
            "ffmpeg", "-y",
            "-ss", str(s["source_start"]),
            "-to", str(s["source_end"]),
            "-i", str(src), "-an",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-r", "30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(out)
        ])
        clips.append(out)
    return clips


def concat_clips(clips, out):
    list_file = out.parent / "concat.txt"
    list_file.write_text(
        "".join(f"file '{c.as_posix()}'\n" for c in clips),
        encoding="utf-8"
    )
    cmd([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(out)
    ])
    return out


# ============================================================
# Step 4: Final Render
# ============================================================
def render(silent, voice, srt, out):
    vd = duration(voice)
    if vd > MAX_SECONDS + 0.1:
        raise RuntimeError(f"Voice duration {vd:.1f}s > {MAX_SECONDS}s")

    args = ["ffmpeg", "-y", "-i", str(silent), "-i", str(voice)]

    if srt and Path(srt).exists():
        sp = str(srt).replace("\\", "/").replace(":", "\\:")
        args += ["-vf", f"subtitles={sp}"]

    args += [
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(min(vd, MAX_SECONDS)),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out)
    ]
    cmd(args)
    return out, duration(out)


# ============================================================
# UI
# ============================================================
st.set_page_config(page_title="🎬 AR Studio — Recap", page_icon="🎬", layout="wide")
st.title("🎬 AR Studio — Movie Recap")
st.write("Movie → Burmese Recap → Voice → SRT → Cinematic 9:16 MP4")

# Sidebar
st.sidebar.header("🔑 Gemini API Key")
key_input = st.sidebar.text_area(
    "API Key(s) — comma နဲ့ ခြား",
    height=100,
    placeholder="AIzaSy... , AIzaSy..."
)
if key_input.strip():
    API_KEYS = [k.strip() for k in key_input.split(",") if k.strip()]
else:
    API_KEYS = [k.strip() for k in st.secrets.get("GEMINI_API_KEYS", "").split(",") if k.strip()]

st.sidebar.write(f"🔑 Key: {len(API_KEYS)} ခု")

st.sidebar.header("🔊 TTS Settings")
chou_space = st.sidebar.text_input("Chou TTS Space", value=DEFAULT_CHOU_SPACE)
hf_token = st.sidebar.text_input("HF Token (optional)", type="password")
chou_lang = st.sidebar.text_input("Language", value="my")
chou_voice = st.sidebar.text_input("Voice (optional)", value="")

# Main
video_file = st.file_uploader("📹 Movie Upload", type=["mp4", "mov", "mkv", "webm"])

col1, col2 = st.columns([2, 1])
with col1:
    target_min = st.selectbox("🎯 Target Duration (minutes)",
                              [1, 1.5, 2, 3, 5, 7, 10], index=6)
with col2:
    st.write("")
    st.write("")
    st.caption(f"Max: {MAX_SECONDS}s")

extra = st.text_area("📝 Optional Context", height=80,
                     placeholder="Character names, pronunciation...")

if video_file is not None:
    st.video(video_file)

    if st.button("✨ Generate Recap", type="primary"):
        if len(API_KEYS) == 0:
            st.error("❌ Gemini API Key မထည့်ရသေးပါ။")
            st.stop()

        km = KeyManager(API_KEYS)

        # Workdir
        job_id = str(int(time.time() * 1000))
        workdir = WORK / job_id
        workdir.mkdir(parents=True, exist_ok=True)

        video_path = workdir / video_file.name
        with open(video_path, "wb") as f:
            f.write(video_file.read())

        target_sec = int(target_min * 60)

        # Step 1: Gemini Script
        with st.spinner("⚡ Gemini က movie ကိုလေ့လာပြီး script ရေးနေသည်..."):
            try:
                data = gemini_recap(video_path, target_sec, extra, km)
                script = "\n".join(s["text"] for s in data["segments"])
                st.success(f"✅ Script ရပြီ ({len(script)} စာလုံး)")
                st.text_area("📝 Script", script, height=150)
            except Exception as e:
                st.error(f"❌ Gemini error: {e}")
                st.stop()

        # Step 2: Chou TTS
        with st.spinner("🔊 Chou Pro TTS → Voice + SRT..."):
            try:
                voice_path, srt_path = chou_tts(
                    script, workdir,
                    space=chou_space,
                    hf_token=hf_token,
                    language=chou_lang,
                    voice=chou_voice
                )
                st.success("✅ Voice ရပြီ")
                st.audio(str(voice_path))
            except Exception as e:
                st.error(f"❌ Chou TTS error: {e}")
                st.stop()

        # Step 3: SRT fallback
        if not srt_path:
            st.warning("⚠️ Chou SRT မရ — Script ကနေ SRT ဖန်တီးမယ်")
            srt_path = workdir / "voice.srt"
            script_to_srt(script, voice_path, srt_path)

        with open(srt_path, "r", encoding="utf-8") as f:
            st.text_area("📝 SRT", f.read(), height=150)

        # Step 4: Video ဖြတ်
        with st.spinner("✂️ Video ဖြတ်နေသည်..."):
            try:
                clips = extract_clips(video_path, data["segments"], workdir / "clips")
                silent = concat_clips(clips, workdir / "silent.mp4")
                st.success(f"✅ Clips {len(clips)} ခု ဖြတ်ပြီး")
            except Exception as e:
                st.error(f"❌ Video error: {e}")
                st.stop()

        # Step 5: Final Render
        with st.spinner("🎬 Final MP4 render..."):
            try:
                final_path = workdir / "AR_Studio_Recap.mp4"
                final, fd = render(silent, voice_path, srt_path, final_path)
                st.success(f"🎉 ပြီးပါပြီ! ({fd:.1f}s)")
            except Exception as e:
                st.error(f"❌ Render error: {e}")
                st.stop()

        # Output
        st.video(str(final_path))

        with open(final_path, "rb") as f:
            st.download_button("📥 Recap Video Download", f,
                               file_name="AR_Studio_Recap.mp4")

        with open(srt_path, "rb") as f:
            st.download_button("📥 SRT Download", f, file_name="recap.srt")

        with st.expander("📋 Project JSON"):
            st.json(data)
