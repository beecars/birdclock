#!/usr/bin/env python3
"""
Bird Clock - Web Server
Serves a simple web page showing the current hour's bird.
Run alongside birdclock.py.
Access at http://birdclock.local:5000 from any device on your home network.
Install Flask with: pip3 install flask
"""

from flask import Flask, render_template_string, request, jsonify
import json
import os
import datetime

from bird_names import bird_name_from_filename

app = Flask(__name__)

SCHEDULE_FILE = "/home/birdclock/birdclock_schedule.json"
START_HOUR = 7
END_HOUR = 21

# Shared with birdclock.py, which reads this before each clip
VOLUME_FILE = "/home/birdclock/birdclock_volume.json"
DEFAULT_VOLUME = 80
MAX_VOLUME = 150

# ── HTML template ──────────────────────────────────────────────────────────────

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bird Clock</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: Georgia, serif;
            background-color: #1a1a2e;
            color: #e8e0cc;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }

        .clock-header {
            font-size: 14px;
            letter-spacing: 0.3em;
            text-transform: uppercase;
            opacity: 0.5;
            margin-bottom: 10px;
            font-family: monospace;
        }

        .time {
            font-size: 48px;
            font-weight: bold;
            letter-spacing: 0.05em;
            color: #f0e0a0;
            margin-bottom: 40px;
            font-family: 'Courier New', monospace;
        }

        .card {
            background: rgba(255,255,255,0.07);
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 20px;
            padding: 40px;
            max-width: 500px;
            width: 100%;
            text-align: center;
        }

        .label {
            font-size: 11px;
            letter-spacing: 0.25em;
            text-transform: uppercase;
            opacity: 0.45;
            margin-bottom: 16px;
            font-family: monospace;
        }

        .bird-name {
            font-size: 36px;
            font-weight: bold;
            margin-bottom: 8px;
            color: #f5f0e0;
            line-height: 1.2;
        }

        .bird-scientific {
            font-size: 16px;
            font-style: italic;
            opacity: 0.55;
            margin-bottom: 30px;
        }

        .photos {
            display: flex;
            gap: 12px;
            justify-content: center;
            flex-wrap: wrap;
            margin-top: 20px;
        }

        .photos img {
            width: 140px;
            height: 140px;
            object-fit: cover;
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.15);
        }

        .inactive {
            opacity: 0.6;
            font-size: 18px;
            text-align: center;
            line-height: 1.8;
        }

        .footer {
            margin-top: 30px;
            font-size: 11px;
            opacity: 0.3;
            font-family: monospace;
            letter-spacing: 0.1em;
        }

        .volume-control {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-top: 24px;
            width: 100%;
            max-width: 500px;
        }

        .volume-control button {
            background: none;
            border: none;
            font-size: 22px;
            cursor: pointer;
            padding: 4px;
            line-height: 1;
        }

        .volume-control input[type="range"] {
            flex: 1;
            accent-color: #f0e0a0;
        }

        .volume-control input[type="range"]:disabled {
            opacity: 0.4;
        }

        .volume-label {
            font-family: monospace;
            font-size: 12px;
            opacity: 0.6;
            min-width: 2.5em;
            text-align: right;
        }

        /* Auto-refresh every 60 seconds */
    </style>
    <meta http-equiv="refresh" content="60">
</head>
<body>
    <div class="clock-header">🐦 Bird Clock</div>
    <div class="time">{{ current_time }}</div>

    <div class="card">
        {% if bird_name %}
            <div class="label">Bird of the Hour</div>
            <div class="bird-name">{{ bird_name }}</div>

            <div class="photos">
                {% for photo_url in photo_urls %}
                    <img src="{{ photo_url }}" alt="{{ bird_name }}"
                         onerror="this.style.display='none'">
                {% endfor %}
            </div>
        {% else %}
            <div class="inactive">
                🌙 The birds are resting.<br>
                Songs play between 7am and 9pm.
            </div>
        {% endif %}
    </div>

    <div class="volume-control">
        <button id="muteBtn" onclick="toggleMute()">{{ '🔇' if muted else '🔊' }}</button>
        <input type="range" id="volumeSlider" min="0" max="{{ max_volume }}" value="{{ volume }}"
               {% if muted %}disabled{% endif %} oninput="setVolume(this.value)">
        <span id="volumeLabel" class="volume-label">{{ volume }}</span>
    </div>

    <div class="footer">{{ today }} &nbsp;·&nbsp; refreshes every minute</div>

    <script>
        async function setVolume(v) {
            document.getElementById('volumeLabel').textContent = v;
            await fetch('/api/volume', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({volume: parseInt(v, 10)})
            });
        }

        async function toggleMute() {
            const btn = document.getElementById('muteBtn');
            const slider = document.getElementById('volumeSlider');
            const newMuted = btn.textContent.trim() !== '🔇';
            btn.textContent = newMuted ? '🔇' : '🔊';
            slider.disabled = newMuted;
            await fetch('/api/volume', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({muted: newMuted})
            });
        }
    </script>
</body>
</html>
"""

# ── Helper functions ───────────────────────────────────────────────────────────

def load_current_bird():
    """Read the schedule file and return the bird name for the current hour."""
    if not os.path.exists(SCHEDULE_FILE):
        return None
    try:
        with open(SCHEDULE_FILE, "r") as f:
            data = json.load(f)
        today = datetime.date.today().isoformat()
        if data.get("date") != today:
            return None
        hour = datetime.datetime.now().hour
        if hour < START_HOUR or hour > END_HOUR:
            return None
        filenames = data["schedule"].get(str(hour))
        if not filenames:
            return None
        return bird_name_from_filename(filenames[0])

    except Exception as e:
        print(f"Error reading schedule: {e}")
        return None


def get_wikipedia_photos(bird_name):
    """
    Fetch bird photos from Wikipedia's API.
    Returns a list of up to 3 image URLs.
    """
    import urllib.request
    import urllib.parse

    try:
        # Search Wikipedia for the bird
        search_query = urllib.parse.quote(bird_name)
        search_url = (
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{search_query}"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "BirdClock/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            import json as json_module
            page_data = json_module.loads(response.read())

        photos = []

        # Get the main thumbnail if available
        if "thumbnail" in page_data:
            photos.append(page_data["thumbnail"]["source"])

        # Also fetch from the images API for more photos
        title = page_data.get("title", bird_name).replace(" ", "_")
        images_url = (
            f"https://en.wikipedia.org/w/api.php?action=query&titles={title}"
            f"&prop=images&format=json&imlimit=10"
        )
        req2 = urllib.request.Request(images_url, headers={"User-Agent": "BirdClock/1.0"})
        with urllib.request.urlopen(req2, timeout=5) as response2:
            images_data = json_module.loads(response2.read())

        pages = images_data.get("query", {}).get("pages", {})
        for page in pages.values():
            for img in page.get("images", []):
                name = img.get("title", "")
                # Only include jpg/png images, skip icons and flags
                if any(name.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
                    if not any(skip in name.lower() for skip in ["icon", "flag", "map", "logo", "stub"]):
                        # Build Wikimedia Commons URL
                        filename = name.replace("File:", "").replace(" ", "_")
                        import hashlib
                        md5 = hashlib.md5(filename.encode()).hexdigest()
                        url = f"https://upload.wikimedia.org/wikipedia/commons/{md5[0]}/{md5[:2]}/{filename}"
                        if url not in photos:
                            photos.append(url)
                if len(photos) >= 3:
                    break

        return photos[:3]

    except Exception as e:
        print(f"Could not fetch photos for {bird_name}: {e}")
        return []


def load_volume_state():
    """Read the current volume/mute state, falling back to defaults if unset or corrupt."""
    if not os.path.exists(VOLUME_FILE):
        return {"volume": DEFAULT_VOLUME, "muted": False}
    try:
        with open(VOLUME_FILE, "r") as f:
            data = json.load(f)
        return {
            "volume": max(0, min(MAX_VOLUME, int(data.get("volume", DEFAULT_VOLUME)))),
            "muted": bool(data.get("muted", False)),
        }
    except Exception:
        return {"volume": DEFAULT_VOLUME, "muted": False}


def save_volume_state(state):
    """Persist volume/mute state so birdclock.py can pick it up before the next clip."""
    with open(VOLUME_FILE, "w") as f:
        json.dump(state, f)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    bird_name = load_current_bird()
    photo_urls = get_wikipedia_photos(bird_name) if bird_name else []
    current_time = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
    today = datetime.date.today().strftime("%B %d, %Y")
    volume_state = load_volume_state()

    return render_template_string(
        HTML_TEMPLATE,
        bird_name=bird_name,
        photo_urls=photo_urls,
        current_time=current_time,
        today=today,
        volume=volume_state["volume"],
        muted=volume_state["muted"],
        max_volume=MAX_VOLUME,
    )


@app.route("/api/volume", methods=["GET"])
def get_volume():
    return jsonify(load_volume_state())


@app.route("/api/volume", methods=["POST"])
def set_volume():
    payload = request.get_json(silent=True) or {}
    state = load_volume_state()

    if "volume" in payload:
        try:
            state["volume"] = max(0, min(MAX_VOLUME, int(payload["volume"])))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid volume"}), 400

    if "muted" in payload:
        state["muted"] = bool(payload["muted"])

    save_volume_state(state)
    return jsonify(state)


if __name__ == "__main__":
    # host="0.0.0.0" makes it accessible from other devices on your network
    app.run(host="0.0.0.0", port=5000, debug=False)
