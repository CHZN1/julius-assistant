import numpy as np, cv2, json, glob
from pathlib import Path
from PIL import Image
import pillow_heif
from insightface.app import FaceAnalysis

pillow_heif.register_heif_opener()

PROJ  = Path(__file__).resolve().parent
FACES = PROJ/"faces"
DB    = PROJ/"artifacts/face_db.npz"

app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=-1, det_size=(640, 640))

def load(path):
    """HEIC/JPG/PNG -> BGR uint8 for insightface."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((1600, 1600))          # plenty for detection, much faster
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

names, vecs, srcs = [], [], []
for person_dir in sorted(p for p in FACES.iterdir() if p.is_dir()):
    person = person_dir.name
    files = sorted(f for f in person_dir.iterdir()
                   if f.suffix.lower() in {".heic",".jpg",".jpeg",".png"})
    print(f"\n{person}: {len(files)} photos")

    for f in files:
        faces = app.get(load(f))
        if not faces:
            print(f"  SKIP {f.name} — no face detected"); continue
        if len(faces) > 1:
            print(f"  {f.name} — {len(faces)} faces, using largest")
        face = max(faces, key=lambda x: (x.bbox[2]-x.bbox[0])*(x.bbox[3]-x.bbox[1]))
        w = int(face.bbox[2]-face.bbox[0])
        if w < 80:
            print(f"  SKIP {f.name} — face too small ({w}px)"); continue
        names.append(person)
        vecs.append(face.normed_embedding)
        srcs.append(f.name)
        print(f"  ok   {f.name}  face {w}px  det {face.det_score:.2f}")

vecs = np.array(vecs, dtype="float32")
names = np.array(names)
print(f"\nenrolled {len(vecs)} embeddings")

# --- consistency check: flag outliers -------------------------------
for person in np.unique(names):
    m = names == person
    V = vecs[m]
    centroid = V.mean(0); centroid /= np.linalg.norm(centroid)
    sims = V @ centroid
    print(f"\n{person}: mean self-similarity {sims.mean():.3f}")
    for s, src in sorted(zip(sims, np.array(srcs)[m])):
        flag = "  <-- outlier, consider removing" if s < sims.mean()-2*sims.std() else ""
        print(f"   {s:.3f}  {src}{flag}")

np.savez(DB, vecs=vecs, names=names, srcs=np.array(srcs))
print(f"\nsaved {DB}")