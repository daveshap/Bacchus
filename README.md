# Bacchus

Bacchus turns a narration recording into a **4K60 MP4** with animated graphics, voiceover, music and sound effects. Each project also produces timed SRT captions and a JSON render receipt. The studio runs on your computer and opens in your browser.

**Windows x64** · [Download the 0.8.3 installer](https://github.com/daveshap/Bacchus/raw/refs/heads/main/releases/0.8.3/Bacchus-0.8.3-windows-x64.msi) · [SHA-256 checksum](releases/0.8.3/Bacchus-0.8.3-windows-x64.sha256)

## Install and open the studio

1. Download the unsigned MSI. To verify it, run `Get-FileHash -Algorithm SHA256 .\Bacchus-0.8.3-windows-x64.msi` in the download folder and compare the result with the checksum above. If upgrading, select **Quit Bacchus** in the running studio first.
2. Run the installer and open **Bacchus** from the Start menu. The MSI includes Python, the studio, the BFG drawing engine and the Canvas fonts. Installation and everyday use require no Git, source checkout, Node.js or separate Python installation.
3. On **Set up Bacchus**, keep the suggested project workspace or browse to a folder of your own. If you choose a different folder, select **Save workspace**, quit and reopen Bacchus before starting a project there.
4. Check **Readiness** and set up the components for your workflow using the table below. Select **Finish setup** to save provider settings, or **Continue offline** to enter the studio without a provider. For a new offline project, clear **Auto mode** when you create it.

| Component | When you need it | How to set it up |
| --- | --- | --- |
| Edge, Chrome or Chromium | Canvas previews, proofs and final renders. The studio interface can open in your usual browser. | Install a supported browser on the rendering computer. For a nonstandard location, set `BACCHUS_CHROME` to the absolute path of its executable before launching Bacchus. |
| FFmpeg and ffprobe | Audio import, mixing, previews, encoding and verification | On Windows, choose **Install media tools** in Settings if Readiness reports them missing. Bacchus verifies the download. A compatible system installation also works. GPU encoding is optional. |
| Word timing evidence | Projects based on a real recording | Choose **Install timing model** to download English Parakeet, or **Use an existing model folder** to verify a supported copy. The download is about 460 MiB and needs about 1.1 GiB free during installation. You can also import a recording-matched timing file in Intake. |
| An AI provider | Auto production or optional AI assistance in Manual | In **Settings → Scene planning**, choose Anthropic or OpenAI, enter its API key and configure a model that supports structured responses and image evidence. The local Manual starter plan works offline. |
| OpenAI image generation | Creating artwork when the project needs new images | Add an OpenAI key and enable **Generate artwork with OpenAI**. Existing library artwork and local image uploads also work. |

**Try it offline.** After setting up the browser and media tools, choose **New project → Create example**. *The City After Dark* is a silent, four-scene Manual project with an approved plan and no artwork requests. In **Composition**, choose **Prepare verified mix**, tick **I checked the silent deliverable**, then select **Approve composition**. In **Preview & QC**, inspect all four scenes and their transitions. The silent-video warning is expected. Accept the candidate once the checks pass and each scene is approved, then render the MP4 in **Export**. The example requires no recording, timing model or provider key.

## Produce one video in Auto

1. Choose **New project**, enter a project name and select a style. Optionally enter a creative brief and choose how to score the video. Keep **Auto mode** selected.
2. Open **Auto token and spending limits** if you want a ceiling for this project. Blank limits are open. Zero blocks paid requests. For a USD limit, supply your maximum input and output rates for each text model used. Image calls use the applicable per-image USD or token ceiling.
3. Attach an MP3, FLAC or WAV and select **Create project**. Adding the recording here starts production. To import your own word timings or adjust settings first, create the project without a recording, add it in **Intake**, import the cues, then select **Run Auto**.
4. Follow progress in **Intake**. The selected provider independently checks intake, plan, artwork uses, soundtrack measurements, scene proofs, QC and the encoded delivery. Reviewers receive the evidence for their gate, including proof images or decoded MP4 frames where relevant. A passed review can be reused for identical evidence.
5. When delivery completes, select **Download MP4**, **Download captions** or **Download receipt**. Bacchus also keeps these files in the project workspace.

**If Auto stops**, read the error in **Intake** and expand **Independent reviews** to see any recorded findings. Hard failures, including API errors, stop the run immediately. For repairable findings, Auto attempts one correction and checks the evidence again. After another failure it stops. Open the relevant stage in the sidebar or adjust Settings, then run Auto again. The usage ledger and retry history survive restarts. An interrupted provider call can retain its budget reservation. Dollar figures use the rates you entered and the provider determines the actual charge.

## Run Bacchus headless with an AI client

The Windows installer includes `Bacchus-MCP.exe`. A local MCP client launches it over standard input and output. On the first tool call it connects to the studio for your saved workspace, or starts one without opening a browser. Multiple clients and the desktop can work with the same projects. An Auto run or queued render continues after an ordinary chat disconnect. You can reconnect and ask for progress by project ID.

**Connect your client.** In PowerShell, set the executable path, then run the registration command for the client you use. These commands register Bacchus for your user account. Open a new client session after registration. [Codex](https://developers.openai.com/codex/mcp/) · [Claude Code](https://code.claude.com/docs/en/mcp) · [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/)

```powershell
$bacchusMcp = Join-Path $env:LOCALAPPDATA 'Programs\Bacchus\Bacchus-MCP.exe'

# Codex
codex mcp add bacchus -- "$bacchusMcp"

# Claude Code
claude mcp add --transport stdio --scope user bacchus -- "$bacchusMcp"

# Gemini CLI
gemini mcp add --scope user --transport stdio bacchus "$bacchusMcp"
```

**First connection check.** Ask the assistant, “Use Bacchus to check render readiness and list my styles.” These tools work without a provider key. Rendering needs a Chromium-based browser and FFmpeg/ffprobe. Auto uses Bacchus’s configured planning and review provider. A chat subscription or MCP connection does not supply those credentials. An existing studio uses the keys already active in it. For unattended startup, supply the provider key through the host environment your MCP client passes to Bacchus. For Codex, you can add `env_vars = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]` under `[mcp_servers.bacchus]` in your user configuration to pass keys already set in the environment. For word timings, provide recording-matched JSON, install the model in Settings, or set `BACCHUS_TIMING_MODEL_DIR` to a verified local model folder.

**Produce a video.** Give the assistant an absolute path to the narration on the machine running Bacchus, a brief, a style, and your token or spend limit. A typical request is “Use Bacchus to animate this recording in Warm Editorial. Use the timing JSON beside the recording, cap Auto at 80,000 tokens, and return the verified MP4, captions and receipt.” Adjust the limit for your project. File imports and project creation are local. `run_auto` starts the independent checks and any paid provider work. The studio enforces the same limits and stop conditions as desktop Auto.

| What the assistant can do | MCP tools |
| --- | --- |
| Check setup, discover styles and find a project | `get_readiness`, `list_styles`, `list_projects`, `get_project` |
| Create a project and prepare its inputs | `create_project`, `attach_narration`, `import_timings`, `set_auto_settings` |
| Run production and inspect a prepared frame by timestamp | `run_auto`, `get_project`, `preview_frame` |
| Create an offline Manual example or render a Manual project after its review | `create_demo`, `render_accepted` |
| Retrieve the verified MP4, SRT and receipt as local file paths and download links | `get_delivery` |

Project updates take the current `expected_revision`. If it changes, read the project again before editing. Manual approvals happen in the studio. The MCP image preview shows the prepared animation, and `get_delivery` verifies the actual delivery files. If the most recent attempt failed, the result can identify an earlier intact delivery.

To use a separate workspace, pass `--data` and its absolute directory as arguments to the MCP executable. Run `& $bacchusMcp --stop` to close the idle studio for the default or saved workspace, or include the same `--data` override. Studio download URLs work while the host is running. Verified local file paths remain usable after shutdown.

For a source installation, point the MCP client at the virtual environment’s Python executable, set its working directory to the source root, and pass `-m bacchus.mcp_main`. For a packaged macOS or Linux build, use the `Bacchus-MCP` binary alongside the desktop executable. To run only the studio HTTP service from source, use `python -m bacchus --no-browser`.

## Walk a project through Manual

Clear **Auto mode** in **New project** to approve each stage yourself. For a first narrated cut, a short recording and a brief describing the audience and the main point are enough to get started.

| Stage | What to do |
| --- | --- |
| **Intake** | Upload the MP3, FLAC or WAV, up to 60 minutes and 384 MiB. Derive word timings locally or import the matching JSON. Check the words and their positions against the audio. Enter your brief, tick the timing acknowledgment, then choose **Lock recording and timings**. |
| **Scene plan** | Choose **Start with a basic plan** or **Generate with AI**. Edit and save the scenes, cue ranges, artwork requests and motion. A chart needs labeled values and units in the narration or brief. Add approved figures to the brief one per line, for example `Bus riders: 40 percent`. Choose **Approve saved plan**. |
| **Artwork** | For each requested asset, pin eligible art from the project's style library, upload a single-frame PNG, JPEG or WebP, or generate an image. Choose **Approve source image** and **Prepare artwork proofs** when offered. Inspect each use alongside its narration, then **Approve this use** or record what needs correction. |
| **Composition** | Choose an original local score, a generic track, uploaded music or silence. Set levels and cue sound effects. Choose **Prepare verified mix**, listen to the mastered WAV, tick the listening acknowledgment and select **Approve composition**. |
| **Preview & QC** | Choose **Prepare animation**, then **Run animation checks**. Inspect the contact sheets and play motion previews around cues and transitions. Resolve failed checks, select **Approve scene and transitions** for each scene, review caption findings, then **Accept candidate**. |
| **Export** | Select **Render video**. Bacchus encodes the accepted cut, decodes and verifies it, and makes the MP4, SRT and receipt available together. Editing the cut can require fresh approval for the affected evidence. |

### Import word timings

In **Intake → Import word timings**, use the SHA-256 of the original uploaded file, the recording duration in seconds, and an ordered array of measured word spans. The example below shows the shape for a two-second recording. Replace the hash, duration, words and times with those for your file.

```json
{
  "audio": { "sha256": "<64 hexadecimal characters from the original recording>" },
  "duration": 2.0,
  "words": [
    { "text": "Hello", "start": 0.15, "end": 0.55 },
    { "text": "there.", "start": 0.65, "end": 1.08 }
  ]
}
```

On Windows, obtain the hash with `Get-FileHash -Algorithm SHA256 .\narration.mp3`. Word starts must increase, every word needs an end time, and the duration must match within 0.25 seconds. Resolve any findings in the cue sheet before import. Intake supports correcting individual words and times. To insert or remove words, import a replacement sheet.

Local recognition uses the sherpa-onnx INT8 conversion of NVIDIA Parakeet TDT 0.6B v2. Bacchus verifies the supported encoder, decoder, joiner and tokens files before using an existing model directory. [Model instructions](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html#sherpa-onnx-nemo-parakeet-tdt-0-6b-v2-int8-english) · [Model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) · [Model license](https://creativecommons.org/licenses/by/4.0/)

## Styles, artwork and storage

Choose a built-in style or use **Styles → New style** to save a guide, palette, music and effects preferences, and up to four PNG or JPEG visual references of up to 8 MiB each. New projects pin that style revision. In a project's **Artwork** panel, choose **Publish to art library** after the planned uses pass, or **Record artwork issue** to block reuse of a defective version. In **Art library**, select an eligible revision and choose **Use in project**. **Import studio folder or catalog** accepts local images or a catalog and reports missing files and checksum mismatches.

**Export style and library** creates a ZIP with the guide revisions, reference images, artwork versions, manifest and issue records. **Styles → Import style and library → Import ZIP** creates a separate editable style. The ZIP and its expanded contents must each fit within 512 MiB, with up to 256 guide revisions. For a legacy Bacchus library, launch with `--library <folder>` to copy its contents into the workspace's style libraries. To move projects and preserve their existing style and library references, quit Bacchus and copy the **whole workspace**. For damaged library metadata, **Move damaged entry aside** preserves the record under the library's quarantine folder for repair.

| Location | Contents |
| --- | --- |
| Active workspace | Projects, input recordings, plans, artwork, style guides, proof images, mix and render staging, final deliveries, studio settings, managed models and tools. |
| Default workspace | Windows uses `%LOCALAPPDATA%\Bacchus`. macOS uses `~/Library/Application Support/Bacchus`. Linux uses `$XDG_DATA_HOME/bacchus`, defaulting to `~/.local/share/bacchus`. |
| Program and temporary files | Windows installs the application under `%LOCALAPPDATA%\Programs\Bacchus`. Audio conversion and Chromium profiles use system temp. Provider request logs default to a per-user location under system temp. |

In **Settings → Project workspace**, browse to a folder or enter its absolute path, select **Save workspace**, then quit and reopen Bacchus. Existing projects stay in the previous folder. The app remembers the new choice for ordinary launches. `--data <folder>` overrides it for one launch. Bacchus blocks project and log writes in Git checkouts, linked worktrees and its installed program directory.

## Providers and logs

Anthropic is the default scene-planning provider. To use OpenAI for planning and Auto reviews, select it in **Settings**. Optional artwork generation needs its own OpenAI setting. **Draft guide with Claude** and **Ask Claude to review proof** use Anthropic. In Manual, the person reviewing the proof records the decision. Keys entered in Settings last for the running session. Alternatively, supply `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` before launch. For unattended configuration, `BACCHUS_PLANNER_PROVIDER=openai` selects OpenAI planning and `BACCHUS_USE_OPENAI_IMAGES=1` enables image generation.

Starting Auto authorizes its provider calls. Depending on the stage, the provider receives transcripts, briefs, timing data, style references, artwork, proof frames or media measurements. **Settings → API log folder** shows where Bacchus writes request and response bodies and status metadata. Logs can contain project content. Bacchus redacts configured credentials. Choose a private absolute folder in Settings or set `BACCHUS_API_LOG_DIR` to retain logs beyond the system temp folder's lifetime.

## Delivery and troubleshooting

- **Output format.** Bacchus renders 3840×2160 at 60 fps in H.264, BT.709 SDR, with 48 kHz stereo AAC on narrated deliveries. The master targets −14 LUFS integrated. The receipt records the encoder, renderer, candidate and file hashes. Captions are a separate UTF-8 SRT. For narrated projects, Bacchus builds them from the approved word timings. **Caption review** flags cues over 20 characters per second, cues shorter than a second, and other exceptions. Review these passages, speaker changes and meaningful sounds before publishing an accessibility track.
- **Missing renderer or media tools.** Check **Settings → Readiness** for the browser, FFmpeg and ffprobe. **Video encoding** can test supported hardware encoders and fall back to libx264. Choose **CPU only** to use the software encoder directly.
- **A delivery changed or a render failed.** Use **Check delivery files** in Export. **Render a fresh copy** repairs a missing or altered delivery. **Retry export** retries a failed render. The most recent intact delivery stays available during a rerender.
- **Disconnected studio.** Reopen Bacchus from the Start menu. Use **Quit Bacchus** in the sidebar before upgrading, moving a workspace or uninstalling through Windows **Settings → Apps**.

## Run from source, test and build

The downloadable installer targets Windows x64. Developers can run from source on Windows, macOS or Linux. The native build workflow defines macOS ARM64/x64 and Linux x64 bundles. [Build workflow](.github/workflows/release.yml)

Use **Python 3.13** and an installed Chromium-based browser. The pinned application dependencies are Pillow, NumPy and sherpa-onnx. Packaging adds PyInstaller. For media tests and rendering, supply **FFmpeg 5.1+** and matching **ffprobe** on `PATH`. Use a full build with libx264, AAC, PCM audio, MP3 encoding for the test fixtures, the H.264 metadata bitstream filter, and the audio mixing, loudness and scaling filters. The runtime checks the capabilities it needs. [Application requirements](requirements.txt) · [Packaging requirements](packaging/requirements.txt) · [Media capability checks](bacchus/media_tools.py)

From the source root, create a virtual environment with `python -m venv .venv`. Activate it with `.\.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` on macOS/Linux. Install the dependencies and launch Bacchus.

```sh
python -m pip install -r requirements.txt
python -m bacchus
```

Use a workspace outside the source checkout. Run the tests and build the desktop bundle on the target OS.

```sh
python -m pip install -r packaging/requirements.txt
python -m unittest discover -s tests -v
python packaging/build.py
```

On Windows, verify the frozen app and build the MSI.

```powershell
python packaging/smoke_bundle.py dist/Bacchus/Bacchus.exe --mcp --render
$version = python -m bacchus --version
python packaging/build_msi.py --bundle dist/Bacchus --version $version --output "release/Bacchus-$version-windows-x64.msi"
```

The packaged smoke test uses a temporary user profile and exercises workspace selection, a captioned export and delivery recovery. The release workflow also defines a Windows install/uninstall check. Versioned installers and checksums are kept in [the download archive](releases/).

Bacchus is [MIT licensed](LICENSE). The bundled font notices and BFG drawing-runtime provenance are in [the engine resources](bacchus/resources/engine/). For the implementation behind the production stages, see the [workflow notes](docs/source-workflows.md).
