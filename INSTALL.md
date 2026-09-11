# Install and run Bangla OCR

## Windows installer

The normal installation does not need Python, PowerShell, Git, or a terminal.

1. Open the [GitHub Releases page](https://github.com/siyam-exe/bangla-ocr/releases).
2. Download the universal installer:

   | Installer | Requirements | Behavior | Limitations |
   |---|---|---|---|
   | `Bangla-OCR-1.1.0-windows-x64-universal-setup.exe` | Windows 10 or 11, x64 | Recommends CPU, NVIDIA CUDA, or experimental Vulkan after detecting the computer | Larger download because CPU is always included |

3. Run the selected Setup file.
4. Choose an installation folder or keep the default.
5. Open Bangla OCR from the Start Menu.

The default location is:

```text
%LOCALAPPDATA%\Programs\Bangla OCR
```

The installer is per-user and does not request administrator access. It ships
a private Python 3.12 runtime and does not add Python to PATH.

The beta installer is not code signed, so Windows may show a SmartScreen
warning. Compare its SHA-256 value with the value published on the release page
before running it.

The installer contains CPU, CUDA, and Vulkan runtimes. During Setup it lists
the detected display adapters and preselects:

| Detected hardware | Default components |
|---|---|
| NVIDIA graphics | CPU + CUDA |
| AMD or Intel graphics | CPU + Vulkan |
| No supported GPU, or detection failed | CPU |

CPU is always installed as the recovery path. The acceleration page shows the
runtime sizes and lets an experienced user add or remove the optional GPU
runtimes. Vulkan remains experimental. Detection is a recommendation, not
proof that the GPU runtime can complete OCR; the application performs that
check later.

Unattended installs use the same automatic choice. Administrators can override
it with `/RUNTIME=cpu`, `/RUNTIME=cuda`, `/RUNTIME=vulkan`, or `/RUNTIME=all`.

## Portable version

Download the portable ZIP, extract the entire `Bangla OCR` folder to a writable
location, and run `Bangla OCR.exe`. Do not move only the EXE and do not run it
inside the ZIP. Application files and document data stay under that extracted
folder.

## What opens when you start it

`Bangla OCR.exe` opens a small native launcher. It checks the local address,
starts the OCR service without a terminal window, and waits for a real health
response. When the service is ready, select **Open Bangla OCR**. The dashboard
opens in your default browser at <http://127.0.0.1:8765>.

Closing the browser does not stop an OCR job. Closing the launcher hides it in
the notification area. Use the tray menu to reopen the dashboard or stop the
application. If OCR is active, the launcher warns before stopping it.

## System and storage requirements

- Windows 10 or 11, x64
- About 2.1 GiB for the universal application before model downloads
- Additional space for Surya models, imported PDFs, rendered pages, and exports
- Internet access during the first Surya model download

CUDA and Vulkan can accelerate Surya inference. PDF rendering, preprocessing,
storage, and parts of page analysis still use the CPU. Reinstalling or updating
replaces bundled inference runtimes while preserving documents and downloaded
models.

## Application folders

The installed or portable root contains:

```text
Bangla OCR\
|-- Bangla OCR.exe
|-- config\
|-- documents\
|   `-- imports\
|-- models\
|-- runtime\
|   `-- python\
|-- workspace\
|   |-- logs\
|   |-- surya\
|   `-- temp\
`-- tools\
    |-- llama.cpp-cpu\
    |-- llama.cpp-cuda\
    `-- llama.cpp-vulkan\
```

Document workspaces, review decisions, and exports are stored under
`documents`. Imported PDFs are copied to `documents\imports`. Downloaded model
files stay under `models`. Startup details are written to
`workspace\logs\application.log`. The private Python installation remains
under `runtime\python` and is not scanned as temporary OCR data.

## Update and uninstall

Install a newer Setup file over the same location to update the application.
Stop Bangla OCR first. The installer replaces application files but does not
delete `documents` or `models`.

Use **Apps > Installed apps > Bangla OCR > Uninstall** to remove the program.
Documents and downloaded models are intentionally left in the installation
folder. Delete those folders yourself only after backing up anything you want
to keep.

## Source installation

Source installation is for development and troubleshooting. It
requires Windows, Python 3.12 with the `py` launcher, and PowerShell 5.1 or
newer.

```powershell
.\setup.ps1 -WithSurya -Runtime Auto
.\bangla-ocr.ps1 doctor
.\bangla-ocr.ps1 launcher
```

`Auto` installs the pinned CPU, CUDA, and Vulkan llama.cpp runtimes. The
application then probes them and selects a usable runtime. You can install only
one runtime when download or disk size matters:

```powershell
.\setup.ps1 -WithSurya -Runtime Cuda
.\setup.ps1 -WithSurya -Runtime Vulkan
.\setup.ps1 -WithSurya -Runtime Cpu
```

Python 3.14 is not supported by the complete OCR stack. The source package
requires Python 3.12. The Windows installer includes the correct version, so
the user's own Python installation is irrelevant.

## Troubleshooting

If startup fails:

1. Select **Open log** in the launcher.
2. Check whether another program is using port 8765.
3. Make sure antivirus software has not removed files from the private runtime.
4. Reinstall over the same folder to repair application files.

For a source checkout, run:

```powershell
.\bangla-ocr.ps1 doctor
.\.venv\Scripts\python.exe scripts\check_dependencies.py --allow-surya-pillow-override
```

The doctor report separates Windows display-adapter inventory from actual
runtime support. Its `surya_hardware.runtimes` section records whether each CPU,
CUDA, or Vulkan runtime is installed, whether its device probe succeeded, the
reported devices, the pinned runtime version, and a bounded failure detail.
A GPU listed by Windows is not considered usable unless the corresponding
llama.cpp runtime also reports it.

The Settings screen shows the same evidence in a readable form. You can save
Automatic, CUDA, Vulkan, or CPU for new jobs, refresh hardware detection after
a driver change, and run an explicit model startup check. The model startup
check can take several minutes the first time and may download model files.

Bangla OCR binds only to the local computer by default. Do not expose port 8765
directly to the public internet.
