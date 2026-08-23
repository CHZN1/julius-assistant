import cv2, numpy as np, torch, torch.nn as nn
from collections import deque
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from torchvision import transforms
from facenet_pytorch import InceptionResnetV1
from insightface.app import FaceAnalysis

PROJ = Path.home()/"Documents/fer2013-emotion-cnn"
ART  = PROJ/"artifacts"
DEV  = "mps" if torch.backends.mps.is_available() else "cpu"

CLASSES = ["surprise","fear","disgust","happiness","sadness","anger","neutral"]
COLORS = {"surprise":(40,230,230), "fear":(40,150,255), "disgust":(190,60,190),
          "happiness":(60,220,60), "sadness":(230,140,40), "anger":(50,50,235),
          "neutral":(180,180,180)}
EMOJI = {"surprise":"😲","fear":"😨","disgust":"🤢","happiness":"😀",
         "sadness":"😢","anger":"😠","neutral":"😐"}

SMOOTH, DETECT_EVERY, ID_THRESH = 12, 4, 0.40

# ---------- emotion models ----------
class FERNet(nn.Module):
    def __init__(self, n=7):
        super().__init__()
        self.backbone = InceptionResnetV1(pretrained=None)
        self.head = nn.Sequential(
            nn.BatchNorm1d(512), nn.Dropout(0.4),
            nn.Linear(512,256), nn.BatchNorm1d(256), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(256,n))
    def forward(self, x): return self.head(self.backbone(x))

def load_fer(name):
    m = FERNet().to(DEV)
    m.load_state_dict(torch.load(ART/f"models/{name}.pt", map_location=DEV),
                      strict=False)
    return m.eval()

vgg  = load_fer("VGGFace2_ft")
affn = load_fer("AffNet_RAFDB")
print(f"device {DEV} | VGGFace2_ft + AffNet_RAFDB (RAF-DB test: 0.8889 acc)")

tf_eval = transforms.Compose([
    transforms.Resize((160,160)), transforms.ToTensor(),
    transforms.Normalize([0.5]*3, [0.5]*3)])

# ---------- identity ----------
db = np.load(ART/"face_db.npz", allow_pickle=True)
DB_VECS, DB_NAMES = db["vecs"], db["names"]

app = FaceAnalysis(name="buffalo_l",
                   providers=["CoreMLExecutionProvider","CPUExecutionProvider"])
app.prepare(ctx_id=-1, det_size=(320,320))

# ---------- emoji ----------
try:    FONT = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 56)
except Exception: FONT = None

def draw_emoji(frame, ch, xy):
    if FONT is None: return frame
    pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA))
    try: ImageDraw.Draw(pil).text(xy, ch, font=FONT, embedded_color=True)
    except Exception: return frame
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGBA2BGR)

# ---------- inference ----------
@torch.no_grad()
def emo_pair(bgr_crop):
    """Both models on one preprocessing pass, fused by geometric mean."""
    rgb = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2RGB)
    x = tf_eval(Image.fromarray(rgb)).unsqueeze(0).to(DEV)
    p1 = torch.softmax(vgg(x),  1).cpu().numpy()[0]
    p2 = torch.softmax(affn(x), 1).cpu().numpy()[0]
    g = np.exp((np.log(np.clip(p1,1e-9,1)) + np.log(np.clip(p2,1e-9,1)))/2)
    return g/g.sum()

def identify(vec):
    sims = DB_VECS @ vec
    i = int(sims.argmax())
    return (str(DB_NAMES[i]) if sims[i] >= ID_THRESH else "unknown", float(sims[i]))

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
                x1,y1,x2,y2 = [max(int(v),0) for v in f.bbox]
                bbox  = (x1,y1,x2,y2)
                ident = identify(f.normed_embedding)
            else:
                bbox = None; hist.clear()

        if bbox:
            x1,y1,x2,y2 = bbox
            crop = frame[y1:y2, x1:x2]
            if crop.size:
                hist.append(emo_pair(crop))
                avg = np.mean(hist, axis=0)
                top = CLASSES[int(avg.argmax())]; col = COLORS[top]

                cv2.rectangle(frame, (x1,y1), (x2,y2), col, 3)
                name, sim = ident
                cv2.putText(frame, f"{name} {sim:.2f}", (x1, max(y1-42,22)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)
                cv2.putText(frame, f"{top} {avg.max():.2f}", (x1, max(y1-14,46)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)

                for r, j in enumerate(avg.argsort()[::-1]):
                    c = CLASSES[j]; yy = 34 + r*30
                    cv2.rectangle(frame, (150, yy-15), (150+int(avg[j]*220), yy+8),
                                  COLORS[c], -1)
                    cv2.putText(frame, f"{c:<10}{avg[j]:.2f}", (154, yy+4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1)
                    frame = draw_emoji(frame, EMOJI[c], (96, yy-22))

                frame = draw_emoji(frame, EMOJI[top], (x2+10, y1))
        else:
            cv2.putText(frame, "no face", (14,34),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)

        cv2.imshow("julius — q quits", frame)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"): break
        if k == ord("r"): hist.clear()
finally:
    cap.release(); cv2.destroyAllWindows()