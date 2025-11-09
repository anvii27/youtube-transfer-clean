# youtube_transfer_streamlit.py
import openai
import streamlit as st
import os
import json
import subprocess
import datetime
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from dotenv import load_dotenv

load_dotenv()

# Get API key from Streamlit secrets or environment variables
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    st.warning("OPENAI_API_KEY not found in environment or Streamlit secrets.")
else:
    openai.api_key = OPENAI_API_KEY  # Set the API key consistently here

# ---------------- CONFIG ----------------
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl", "https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_FILE = "client_secret.json"
DOWNLOAD_DIR = "downloads"
LOG_FILE = "transfer_log.json"
TOKEN_OLD = "token_old.json"
TOKEN_NEW = "token_new.json"

# ----------------- UTIL ------------------
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or (st.secrets["OPENAI_API_KEY"] if "OPENAI_API_KEY" in st.secrets else None)
if not OPENAI_API_KEY:
    st.warning("OPENAI_API_KEY not found. Put it in a .env file or Streamlit secrets under OPENAI_API_KEY.")
else:
    openai.api_key = OPENAI_API_KEY

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

@st.cache_data
def load_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return {"processed": {}}

def save_log(log):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


# -------------- AUTH --------------------

def get_authenticated_service_installed(token_filename):
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    # Save a minimal token file for refresh
    with open(token_filename, "w") as f:
        json.dump({
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }, f)
    youtube = build("youtube", "v3", credentials=creds)
    return youtube

# -------------- YT helpers --------------

def get_uploads_playlist_id(youtube):
    resp = youtube.channels().list(part="contentDetails,snippet", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("No channels found for this credential.")
    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    channel_title = items[0]["snippet"].get("title", "")
    return uploads_id, channel_title

def list_videos_in_playlist(youtube, playlist_id, max_results=200):
    videos = []
    nextPageToken = None
    while True:
        resp = youtube.playlistItems().list(part="snippet,contentDetails",
                                            playlistId=playlist_id,
                                            maxResults=50,
                                            pageToken=nextPageToken).execute()
        for it in resp.get("items", []):
            snippet = it["snippet"]
            content = it["contentDetails"]
            video_id = content["videoId"]
            title = snippet.get("title")
            published = snippet.get("publishedAt")
            desc = snippet.get("description", "")
            videos.append({"videoId": video_id, "title": title, "publishedAt": published, "description": desc})
        nextPageToken = resp.get("nextPageToken")
        if not nextPageToken or len(videos) >= max_results:
            break
    # fetch stats
    for i in range(0, len(videos), 50):
        chunk = videos[i:i+50]
        ids = ",".join(v["videoId"] for v in chunk)
        stats = youtube.videos().list(part="statistics,contentDetails", id=ids).execute()
        stats_map = {s["id"]: s for s in stats.get("items", [])}
        for v in chunk:
            s = stats_map.get(v["videoId"], {})
            v["views"] = int(s.get("statistics", {}).get("viewCount", 0)) if s.get("statistics") else 0
            v["duration"] = s.get("contentDetails", {}).get("duration", "")
    return videos

# -------------- AI selection -------------

def ai_suggest_indices(videos, instruction):
    if not OPENAI_API_KEY:
        return []
    short_list = []
    for i, v in enumerate(videos):
        short_list.append({"index": i, "videoId": v["videoId"], "title": v["title"],
                           "publishedAt": v.get("publishedAt"), "views": v.get("views", 0)})
    system = "You are a helpful assistant that returns a JSON array of indices to select."
    user_msg = f"Videos:\n{json.dumps(short_list, indent=2)}\n\nInstruction:\n{instruction}\n\nReturn only a JSON array of integers."
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini", messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg}
            ], temperature=0
        )
        text = resp["choices"][0]["message"]["content"].strip()
        import re
        m = re.search(r"\[.*\]", text, re.DOTALL)
        arr_text = m.group(0) if m else text
        arr = json.loads(arr_text)
        indices = [int(x) for x in arr if isinstance(x, int) and 0 <= x < len(videos)]
        return indices
    except Exception as e:
        st.error(f"AI error: {e}")
        return []

# --------------- download/upload ------------

def download_video(video_id, outdir=DOWNLOAD_DIR):
    outtmpl = os.path.join(outdir, f"{video_id}.%(ext)s")
    cmd = ["yt-dlp", "--no-mtime", "-f", "bestvideo+bestaudio/best", "-o", outtmpl,
           f"https://www.youtube.com/watch?v={video_id}"]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError("yt-dlp failed")
    # Find the output file with correct extension
    for fname in os.listdir(outdir):
        if fname.startswith(video_id + "."):
            return os.path.join(outdir, fname)
    raise FileNotFoundError("Downloaded file not found")

def upload_video(youtube, filepath, title, description, tags=None, privacy="public"):
    body = {
        "snippet": {"title": title, "description": description, "tags": tags or []},
        "status": {"privacyStatus": privacy}
    }
    media = MediaFileUpload(filepath, chunksize=-1, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            st.write(f"Upload progress: {int(status.progress() * 100)}%")
    return response.get("id")

def delete_video(youtube, video_id):
    youtube.videos().delete(id=video_id).execute()

# ---------------- UI State Save Helpers ------------------

def reset_state():
    for k in ['youtube_old', 'uploads_playlist_id', 'old_title', 'youtube_new', 'new_title', 'ai_indices', 'manual_indices', 'videos']:
        if k in st.session_state:
            del st.session_state[k]

# ---------------- Streamlit UI App ------------------

st.title("YouTube Transfer — AI-assisted (Streamlit)")
st.markdown("""
A simple interface to move videos from one channel to another.<br>
Authenticate **old channel** first, then **new channel**.
""", unsafe_allow_html=True)

log = load_log()

col1, col2 = st.columns(2)

with col1:
    if st.button("Authenticate OLD Channel"):
        try:
            youtube_old = get_authenticated_service_installed(TOKEN_OLD)
            uploads_playlist_id, old_title = get_uploads_playlist_id(youtube_old)
            st.session_state['youtube_old'] = youtube_old
            st.session_state['uploads_playlist_id'] = uploads_playlist_id
            st.session_state['old_title'] = old_title
            st.success(f"Authenticated old channel: {old_title}")
        except Exception as e:
            st.error(e)

with col2:
    if st.button("Authenticate NEW Channel"):
        try:
            youtube_new = get_authenticated_service_installed(TOKEN_NEW)
            _, new_title = get_uploads_playlist_id(youtube_new)
            st.session_state['youtube_new'] = youtube_new
            st.session_state['new_title'] = new_title
            st.success(f"Authenticated new channel: {new_title}")
        except Exception as e:
            st.error(e)

if 'uploads_playlist_id' in st.session_state and 'youtube_old' in st.session_state:
    youtube_old = st.session_state['youtube_old']
    uploads_id = st.session_state['uploads_playlist_id']
    if 'videos' not in st.session_state:
        videos = list_videos_in_playlist(youtube_old, uploads_id)
        st.session_state['videos'] = videos
    videos = st.session_state['videos']
    st.write(f"Found {len(videos)} videos in old channel: **{st.session_state.get('old_title','')}**")

    for i, v in enumerate(videos):
        processed = v['videoId'] in log['processed']
        st.checkbox(f"[{i}] {v['title']} ({v.get('views',0)} views) {'(processed)' if processed else ''}", key=f"chk_{i}")

    sel_mode = st.radio("Selection mode", ["manual", "AI"], index=1, key="sel_mode")
    if sel_mode == "AI":
        instruction = st.text_input("Tell the AI which videos to pick (e.g. 'top 5 most viewed', 'only vlogs from 2023')")
        if st.button("Ask AI"):
            indices = ai_suggest_indices(videos, instruction)
            st.session_state['ai_indices'] = indices
            st.write("AI suggested indices:", indices)
    else:
        manual_indices = [i for i in range(len(videos)) if st.session_state.get(f"chk_{i}")]
        st.session_state['manual_indices'] = manual_indices

    process_button = st.button("Process selected videos")
    if process_button:
        youtube_new = st.session_state.get('youtube_new')
        if not youtube_new:
            st.warning("Authenticate the NEW channel before uploading.")
        else:
            indices = st.session_state.get('ai_indices') if st.session_state.get("sel_mode") == 'AI' else st.session_state.get('manual_indices')
            if not indices:
                st.warning("No indices selected")
            else:
                for idx in indices:
                    v = videos[idx]
                    vid = v['videoId']
                    if vid in log['processed']:
                        st.info(f"Skipping {v['title']} (already processed)")
                        continue
                    try:
                        st.write(f"Downloading {v['title']}")
                        filepath = download_video(vid)
                        st.write("Uploading...")
                        new_id = upload_video(youtube_new, filepath, v['title'], v['description'])
                        new_url = f"https://youtu.be/{new_id}"
                        st.success(f"Uploaded: {new_url}")
                        log['processed'][vid] = {
                            "old_title": v['title'],
                            "new_video_id": new_id,
                            "new_url": new_url,
                            "timestamp": datetime.datetime.utcnow().isoformat()+"Z",
                            "local_file": filepath
                        }
                        save_log(log)
                        # Ask about deletion right after upload
                        if st.button(f"Delete original: {v['title']}", key=f"del_{vid}"):
                            try:
                                delete_video(youtube_old, vid)
                                st.success("Deleted from old channel")
                                log['processed'][vid]['deleted_old'] = True
                                save_log(log)
                            except Exception as e:
                                st.error(f"Failed to delete: {e}")
                                log['processed'][vid]['deleted_old'] = False
                                save_log(log)
                    except Exception as e:
                        st.error(f"Error with {v['title']}: {e}")
                        log['processed'][vid] = {
                            "old_title": v['title'],
                            "error": str(e),
                            "timestamp": datetime.datetime.utcnow().isoformat()+"Z"
                        }
                        save_log(log)

st.markdown("---")
st.write("Transfer log (last 20):")
proc_dict = load_log()['processed']
log_preview = dict(list(proc_dict.items())[-20:]) if len(proc_dict) > 20 else proc_dict
st.json(log_preview)
