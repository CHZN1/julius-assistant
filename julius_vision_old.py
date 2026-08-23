import cv2, numpy as np, json
from collections import deque
from pathlib import Path
from tensorflow import keras
from insightface.app import FaceAnalysis
from PIL import Image, ImageDraw, ImageFont

PROJ = Path.home()/"Documents/fer2013-emotion-cnn"
EMO_MODEL = next(PROJ.rglob("FERPlus_soft_96.keras"))
FACE_DB   = PROJ/"artifacts/face_db.npz"

CLASSES = ["neutral","happiness","surprise","sadness","anger","disgust","fear"]
COLORS = {                                  # BGR
    "neutral":  (180,180,180), "happiness": (60,220,60),
    "surprise": (40,230,230),  "sadness":   (230,140,40),
    "anger":    (50,50,235),   "disgust":   (190,60,190),
    "fear":     (40,150,255),
}
EMOJI = {"neutral":"😐","happiness":"😀","surprise":"😲",
         "sadness":"😢","anger":"😠","disgust":"🤢","fear":"😨"}

IMG_SIZE   = 96
SMOOTH     = 12       # frames averaged for emotion
DETECT_EVERY = 5      # run face detect/ID every N frames; emotion runs every frame
ID_THRESH  = 0.40     # cosine similarity for a match

# ---------- load ----------
emo = keras.models.load_model(EMO_MODEL)
prep = keras.applications.efficientnet.preprocess_input

db = np.load(FACE_DB, allow_pickle=True)
DB_VECS, DB_NAMES = db["vecs"], db["names"]

app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=-1, det_size=(320, 320))

# ---------- emoji via PIL (OpenCV putText can't render them) ----------
EMOJI_FONT = None
for p, sz in [("/System/Library/Fonts/Apple Color Emoji.ttc", 64)]:
    try:
        EMOJI_FONT = ImageFont.truetype(p, sz)
        break
    except Exception:
        pass

def draw_emoji(frame, ch, xy):
    if EMOJI_FONT is None:
        return frame
    pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA))
    try:
        ImageDraw.Draw(pil).text(xy, ch, font=EMOJI_FONT, embedded_color=True)
    except Exception:
        return frame
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGBA2BGR)

def emotion_of(gray_crop):
    img = cv2.resize(gray_crop, (IMG_SIZE, IMG_SIZE)).astype("float32")
    return emo.predict(prep(np.stack([img]*3, -1))[None, ...], verbose=0)[0]

def identify(vec):
    sims = DB_VECS @ vec
    i = int(sims.argmax())
    return (str(DB_NAMES[i]), float(sims[i])) if sims[i] >= ID_THRESH \
           else ("unknown", float(sims[i]))

# ---------- loop ----------
hist = deque(maxlen=SMOOTH)
bbox, ident, n = None, ("—", 0.0), 0

cap = cv2.VideoCapture(0)
print("q quits · r resets smoothing")
try:
    while True:
        ok, frame = cap.read()
        if not ok: break
        n += 1

        if n % DETECT_EVERY == 1:
            faces = app.get(frame)
            if faces:
                f = max(faces, key=lambda a:(a.bbox[2]-a.bbox[0])*(a.bbox[3]-a.bbox[1]))
                x1,y1,x2,y2 = [int(v) for v in f.bbox]
                bbox = (max(x1,0), max(y1,0), max(x2,0), max(y2,0))
                ident = identify(f.normed_embedding)
            else:
                bbox = None; hist.clear()

        if bbox:
            x1,y1,x2,y2 = bbox
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[y1:y2, x1:x2]
            if gray.size:
                hist.append(emotion_of(gray))
                avg = np.mean(hist, axis=0)
                top = CLASSES[int(avg.argmax())]
                col = COLORS[top]

                cv2.rectangle(frame, (x1,y1), (x2,y2), col, 3)
                name, sim = ident
                cv2.putText(frame, f"{name}  {sim:.2f}", (x1, max(y1-42,22)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)
                cv2.putText(frame, f"{top} {avg.max():.2f}", (x1, max(y1-14,46)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)

                for r, j in enumerate(avg.argsort()[::-1]):
                    c = CLASSES[j]; yy = 34 + r*30
                    cv2.rectangle(frame, (150, yy-15), (150+int(avg[j]*220), yy+8),
                                  COLORS[c], -1)
                    cv2.putText(frame, f"{c:<10}{avg[j]:.2f}", (154, yy+4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1)
                    frame = draw_emoji(frame, EMOJI[c], (100, yy-24))

                frame = draw_emoji(frame, EMOJI[top], (x2+10, y1))
        else:
            cv2.putText(frame, "no face", (14, 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)

        cv2.imshow("julius vision — q quits", frame)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"): break
        if k == ord("r"): hist.clear()
finally:
    cap.release(); cv2.destroyAllWindows()