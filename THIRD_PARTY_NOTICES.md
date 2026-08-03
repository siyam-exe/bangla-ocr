# Third-party notices

Bangla OCR source code is Apache-2.0. Installed libraries, downloaded binaries,
and model weights are separate works under their own terms. This file is a
release inventory, not legal advice.

## Direct runtime components

| Component | Validated version | License / terms | Project |
|---|---:|---|---|
| Flask | 3.1.3 | BSD-3-Clause | https://github.com/pallets/flask |
| NumPy | 2.5.1 | BSD-3-Clause and bundled dependency notices | https://numpy.org |
| OpenCV Python headless | 4.11.0.86 | Apache-2.0 | https://github.com/opencv/opencv-python |
| Pillow | 12.3.0 | MIT-CMU | https://python-pillow.github.io |
| pypdf | 6.14.2 | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| pypdfium2 / PDFium | 5.12.1 | BSD-3-Clause, Apache-2.0, and dependency licenses | https://github.com/pypdfium2-team/pypdfium2 |
| RapidFuzz | 3.14.5 | MIT | https://github.com/rapidfuzz/RapidFuzz |
| Waitress | 3.0.2 | ZPL-2.1 | https://github.com/Pylons/waitress |
| EasyOCR | 1.7.2 | Apache-2.0 | https://github.com/JaidedAI/EasyOCR |
| Surya code | 0.22.1 | Apache-2.0 | https://github.com/datalab-to/surya |
| PyTorch | 2.13.0 | Apache-2.0 and bundled dependency notices | https://pytorch.org |
| TorchVision | 0.28.0 | BSD-3-Clause | https://github.com/pytorch/vision |
| llama.cpp | b10107 (`c0bc8591e`) | MIT | https://github.com/ggml-org/llama.cpp |

Transitive packages are installed from their original distributions and retain
their included metadata and license files. Generate a complete environment
inventory with:

```powershell
.\.venv\Scripts\python.exe -m pip list --format=json
```

## Surya model weights: important

The Surya code license does **not** replace the model-weight license. Surya
0.22.1 states that its model weights use a modified AI Pubs OpenRAIL-M license,
free for research, personal use, and startups under USD 5 million in
funding/revenue. Broader commercial use requires reviewing Datalab's current
terms or obtaining a commercial license.

The installer downloads weights from the upstream model host. The repository
does not redistribute those weights. Check the current upstream license before
commercial deployment because model terms can change independently of this
code.

## llama.cpp binaries

`install-runtime.ps1` downloads official b10107 Windows archives and checks the
release SHA-256 digests before extraction. Binaries are installed into ignored
`tools/` directories and are not committed to this repository.

## Benchmark fixture

The Bengali text and generated page images under `benchmarks/fixture/` are
original project material dedicated to the public domain under CC0-1.0. See
`benchmarks/fixture/LICENSE.txt`.
