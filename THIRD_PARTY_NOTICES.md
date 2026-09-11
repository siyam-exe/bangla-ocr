# Third-party notices

Bangla OCR source code is Apache-2.0. Installed libraries, downloaded binaries,
and model weights are separate works under their own terms. This file is a
release inventory, not legal advice.

## Direct runtime components

| Component | Validated version | License / terms | Project |
|---|---:|---|---|
| CPython embedded runtime | 3.12.10 | Python Software Foundation License | https://www.python.org |
| Flask | 3.1.3 | BSD-3-Clause | https://github.com/pallets/flask |
| NumPy | 2.5.1 | BSD-3-Clause and bundled dependency notices | https://numpy.org |
| OpenCV Python headless | 4.11.0.86 | Apache-2.0 | https://github.com/opencv/opencv-python |
| Pillow | 12.3.0 | MIT-CMU | https://python-pillow.github.io |
| pypdf | 6.15.0 | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| pypdfium2 / PDFium | 5.12.1 | BSD-3-Clause, Apache-2.0, and dependency licenses | https://github.com/pypdfium2-team/pypdfium2 |
| RapidFuzz | 3.14.5 | MIT | https://github.com/rapidfuzz/RapidFuzz |
| Waitress | 3.0.2 | ZPL-2.1 | https://github.com/Pylons/waitress |
| EasyOCR | 1.7.2 | Apache-2.0 | https://github.com/JaidedAI/EasyOCR |
| Surya code | 0.22.1 | Apache-2.0 | https://github.com/datalab-to/surya |
| PyTorch | 2.13.0 | Apache-2.0 and bundled dependency notices | https://pytorch.org |
| TorchVision | 0.28.0 | BSD-3-Clause | https://github.com/pytorch/vision |
| llama.cpp | b10107 (`c0bc8591e`) | MIT | https://github.com/ggml-org/llama.cpp |

Transitive packages are installed from their original distributions and retain
their included metadata and license files. In the Windows application, nested
package license files are stored in `runtime/third-party-licenses.zip` to avoid
Windows path-length failures. Generate a complete environment inventory with:

```powershell
.\.venv\Scripts\python.exe -m pip list --format=json
```

## Surya model weights: important

The Surya code license does **not** replace the model-weight license. Surya
0.22.1 states that its model weights use a modified AI Pubs OpenRAIL-M license,
free for research, personal use, and startups under USD 5 million in
funding/revenue. Broader commercial use requires reviewing Datalab's current
terms or obtaining a commercial license.

The first OCR run downloads weights from the upstream model host. The
repository and Windows package do not redistribute those weights. Check the
current upstream license before commercial deployment because model terms can
change independently of this code.

## Installer build tool

The Windows Setup file is built with Inno Setup 6.7.3. Inno Setup is a build
tool and is not bundled in the source repository or installed on the user's
computer. Its own terms apply to anyone compiling the installer, especially in
a commercial context.

## llama.cpp binaries

`install-runtime.ps1` downloads the official b10107 Windows CPU, CUDA, and
Vulkan archives and checks pinned SHA-256 digests before extraction. Each
runtime receives its source manifest and a copy of the upstream MIT license.
Binaries are installed into ignored `tools/` directories and are not committed
to this repository.

## Benchmark fixture

The real scanned excerpts and their reference transcriptions under
`benchmarks/fixture/` are not covered by the Apache-2.0 code license. They are
included for reproducible OCR evaluation under the scope described in
`benchmarks/fixture/NOTICE.md`; rights in the underlying book remain with their
respective rights holders.
