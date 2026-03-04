from sentence_transformers import SentenceTransformer
import torch

device = "cuda" if torch.cuda.is_available() else "cpu"
print( f"The device var got set to .... {device}")
model = SentenceTransformer("all-MiniLM-L6-v2", device=device)

sentences = ["this is a test", "another one"]
embeddings = model.encode(
    sentences,
    batch_size=32,              # tune based on VRAM
    convert_to_tensor=True      # stays on GPU
)
