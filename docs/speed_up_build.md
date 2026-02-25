The two flags in `pip install --no-cache-dir`:

**`--no-cache-dir`** tells pip not to save downloaded packages to its local cache. This is standard Docker practice because caching inside the image layer just bloats the image size without benefit (Docker has its own layer caching). It actually makes the install *slightly slower* since pip can't reuse anything, but saves image size.

**Why it takes so long (535 seconds!)** is almost certainly not the flags — it's the packages themselves. ML/NLP dependencies are notoriously heavy:

- `torch` alone is often 2-3 GB
- `sentence-transformers` pulls in transformers, tokenizers, huggingface-hub, etc.
- `spacy` has its own compiled extensions
- If any packages need to **compile from source** (no pre-built wheel for your platform), that can add many minutes

**To speed it up**, the most impactful things are:

- Make sure you're on a platform that has pre-built wheels (e.g. `linux/amd64` with a standard Python version like 3.10/3.11) so nothing needs compiling
- Pin your package versions in `requirements.txt` — this lets Docker's layer cache kick in so unchanged steps are skipped entirely on rebuilds
- Split the `pip install` into two separate `RUN` commands if you have stable deps vs. frequently changing ones, so Docker can cache the stable layer
- Consider using a base image that already has PyTorch pre-installed (like `pytorch/pytorch`) to skip that massive download

The flags themselves aren't your bottleneck — it's the sheer size of the ML stack. Is this happening on every build, or just the first time?