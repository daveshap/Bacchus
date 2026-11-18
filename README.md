# Bacchus

Bacchus turns an MP3, FLAC, or WAV narration and a creative brief into a reviewed MP4 with captions. It derives word cues locally, uses Claude by default for AI scene planning and artwork briefs, and supports optional image generation. The studio checks scene coverage, tracks approvals, mixes sound, and renders the approved cut locally.

## Install Bacchus on Windows x64

Download the [Bacchus 0.6.2 Windows installer](releases/0.6.2/Bacchus-0.6.2-windows-x64.msi) and its [SHA-256 checksum](releases/0.6.2/Bacchus-0.6.2-windows-x64.sha256). This unsigned preview includes Python and the app's runtime dependencies. The `v0.6.2` tag records the source and installer for this release.

1. Compare the MSI's SHA-256 with the checksum file using `Get-FileHash -Algorithm SHA256 .\Bacchus-0.6.2-windows-x64.msi` in PowerShell. Run the MSI, then open **Bacchus** from the Start menu. The app opens **Set up Bacchus** in your default browser on first launch.
2. Under **Readiness**, choose **Install media tools** if the app reports that its media tools are unavailable. Bacchus downloads and verifies FFmpeg and ffprobe in its own data folder.
3. For local word cues, choose **Install timing model**. The English Parakeet download is about 460 MiB and needs about 1.1 GiB of free space during installation. To reuse a supported model, expand **Use an existing model folder**, enter its path, and select **Verify and use folder**. You can also import a recording-matched cue sheet in Intake.
4. Select **Finish setup** to save provider settings, or **Continue offline**. The offline workflow uses the media tools and either a verified local model or a recording-matched cue sheet. In the studio, **Create example** opens a sample with an approved plan so you can try preview, review, and export without a recording or API key.
5. Select **Quit Bacchus** in the sidebar before running a newer installer or uninstalling Bacchus from **Settings → Apps** in Windows.

If the browser shows **Local studio disconnected** or **Studio unavailable**, reopen Bacchus from the Start menu.

The checked-in installer targets Windows x64. The [native release workflow](.github/workflows/release.yml) defines macOS and Linux bundles as GitHub Actions artifacts. See [Build and test](#build-and-test) to run from source or build locally.

## Storage

Bacchus stores projects, settings, managed models, style guides, reference images, and artwork libraries in `%LOCALAPPDATA%\Bacchus` on Windows, `~/Library/Application Support/Bacchus` on macOS, or `$XDG_DATA_HOME/bacchus` on Linux (default `~/.local/share/bacchus`). **Settings** shows the data and library paths. The Windows MSI installs program files in `%LOCALAPPDATA%\Programs\Bacchus`.

To back up Bacchus, quit the app and copy its data folder. To restore a backup, copy its contents into an empty data folder with Bacchus closed. The application supports `--data <folder>` for a different storage location, such as an external drive.

## Styles and project kickoff

Choose a saved style in **New project**. Bacchus shows the name of its artwork library and how many reusable assets it contains. Professional Corporate and Hand-drawn Explainer come with Markdown guides. Warm Editorial, Studio Ink, and Night Signal are also available.

Open **Styles** to name a style and library, answer the music, effects, and visual questions, set palette colors, and attach up to four PNG or JPEG reference images per revision (8 MiB each). Write or import a Markdown guide, or choose **Draft guide with Claude** to draft one from the answers and references. Review and save the guide. Bacchus keeps the questionnaire answers as entered. Creating and editing styles works offline.

New projects pin the active saved revision of the selected style. Existing projects keep the guide, colors, and reference images they started with. The style guides scene planning and illustration requests. Its music and sound-effects choices provide a starting composition that you can adjust and approve for the cut.

## Configure AI

Bacchus uses Anthropic's Claude Sonnet 5.5 (`claude-sonnet-5-5`) by default to plan scenes, choose motion and write artwork briefs. Enter an Anthropic API key in **Settings** or set `ANTHROPIC_API_KEY` before launch to enable planning. Bacchus renders the approved plan locally. You can select another Claude model with structured outputs in **Settings** or through `BACCHUS_CLAUDE_MODEL`. [Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) · [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

To use an optional OpenAI feature, add an OpenAI API key in **Settings** or set `OPENAI_API_KEY`. Then enable the specific feature you want.

- **Image generation.** Enable **Generate artwork with OpenAI** in **Settings** or set `BACCHUS_USE_OPENAI_IMAGES=1`. When requested, Bacchus sends the selected artwork brief and style to generate an image. [Image generation API](https://developers.openai.com/api/reference/resources/images/methods/generate)
- **Scene planning.** Choose OpenAI as your planning provider in **Settings** or set `BACCHUS_PLANNER_PROVIDER=openai`. [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

Each provider call starts at your request. Planning sends the transcript, brief, cue timings, style guide and its reference images, and relevant approved artwork descriptions to the selected provider. Optional image generation can use the same references. **Draft guide with Claude** sends style answers, guide text, and selected references. In both review stages, **Ask Claude to review proof** sends one proof image with the relevant scene context for a second opinion. Bacchus uses high effort for that review with the default Claude model. A person records the decision for every proof. Keys entered in **Settings** last until Bacchus exits. Nonsecret preferences persist in the data folder.

### API logs

**Settings** shows the active API log folder. Choose an absolute path there or set `BACCHUS_API_LOG_DIR` before launch. Otherwise Bacchus writes to a per-user folder under the system's temporary directory. Each outbound provider call gets its own folder with metadata, a request body, and the response body when one is available. Metadata includes the project, stage, HTTP status, provider request ID when supplied, and whether the response hit the transport size limit. Interrupted calls can leave a pending record.

Logs can contain prompts, transcripts, artwork, and audio. Bacchus omits authentication headers and redacts configured keys from logged payloads. Keep the folder private. The system may clear temporary files, so choose a persistent folder for logs you want to keep.

## Make a cut

1. Choose a style and import an MP3, FLAC, or WAV. Import a recording-matched cue sheet or derive word timings with the local Parakeet model. Check its words against the audio, correct any cues, then set the creative brief and lock intake.
2. Generate a plan or start from the local editable draft. A layered scene can reveal text, artwork, a bar chart, or a flow diagram on independently selected narration words. Review the whole plan and artwork requirements before approving it.
3. Pin library artwork, upload prepared images, or make illustrations with optional OpenAI image generation. Approve the source versions for checking, then pass each planned use. Publish reusable assets after they pass. Record defective or incomplete artwork as an issue and replace the version or revise the planned use.
4. Choose a built-in score, import music and place effects, or select no music. Set music levels or silence by narration cue range. Prepare and measure the soundtrack, listen to the prepared WAV, and approve that exact file. Once the artwork and soundtrack are approved, prepare the animation.
5. Select **Run animation checks** to produce contact sheets from the prepared cut with frame samples around entrances, motion, cues, exits, and joins. Scrub the cut and listen to the mix for sync. Resolve any failed checks, pass each scene, and select **Accept candidate**.
6. Press **Render video** to export an H.264 MP4, SRT captions, and a receipt with both review stages.

The local starter plan, image import, and built-in soundtracks work offline. Narration uploads can be up to 30 minutes and 384 MiB. Bacchus has three visual treatments and title, editorial, illustration, and layered layouts. Export is 720p at 24 fps.

The recording is the timing authority. Under **Import word timings** in Intake, choose a `timing.json` with `audio.sha256` for the original uploaded bytes, the recording `duration` in seconds, and `words` with `text`, `start`, and `end` times in seconds. For audio-only intake, install Parakeet from Setup, Settings, or Intake. Bacchus uses the verified model in its own data folder or a folder you explicitly select. A selected Parakeet TDT v2 INT8 folder must contain `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx`, and `tokens.txt` matching a supported fingerprint set. The recognizer derives word spans from the waveform without a provider key. Review and correct the draft before selecting **Lock recording and timings**. To add or remove words, import a complete replacement cue sheet. [Parakeet TDT model instructions](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html#sherpa-onnx-nemo-parakeet-tdt-0-6b-v2-int8-english)

The ONNX files are the sherpa-onnx INT8 conversion of NVIDIA Parakeet TDT 0.6B v2, licensed under CC BY 4.0. [NVIDIA model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) · [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/)

Bacchus keeps original recordings, normalized media, plans, review proofs, decisions, and exports in its data folder. Proofs bind to the artwork version and the scene inputs they show. Changes to the cut invalidate its acceptance. Checks that still show the current inputs can carry forward.

**Video encoding** in Settings defaults to **Automatic GPU (CPU fallback)**. Bacchus tests candidates at the delivery format. It tries NVIDIA NVENC, Intel Quick Sync, and AMD AMF on Windows, NVENC and VAAPI on Linux, and VideoToolbox on macOS. Windows can also test a compatible system FFmpeg alongside the managed build. Bacchus falls back to libx264 when hardware encoding is unavailable or fails. Choose **CPU only** to skip hardware tests. The delivery and its receipt identify the actual encoder and any fallback. Scene composition uses the local Pillow renderer.

Bacchus decodes the rendered video and any audio before it marks the delivery complete. It fingerprints the MP4, captions, and receipt and checks the files before reuse or download. **Download MP4** names the downloaded video after the project. In Export, **Check delivery files** refreshes the delivery status. Use **Render a fresh copy** for missing or changed files, **Retry export** after a failed render, or **Rerender video** to make a new verified delivery using the current encoder setting. These actions use the accepted candidate. The most recent intact delivery stays available during a rerender. After an interrupted render, Bacchus clears abandoned render staging folders on startup. For older deliveries, Bacchus checks the MP4 against the original receipt and records initial captions and receipt fingerprints on upgrade.

### Review captions

**Caption review** in **Preview & QC** shows the caption count and any passages that need a closer look. Bacchus builds the UTF-8 SRT directly from approved word timings. Caption breaks are independent of scene and planning cues. The English defaults aim for one or two lines of up to 42 characters, one to six seconds on screen with a seven-second ceiling, and about 17 characters per second. The review calls out cues above 20 characters per second, cues under one second, and other timing or layout exceptions.

Caption review is advisory. Check the flagged passages against the recording, and review speaker changes and meaningful sounds before publishing an accessibility caption track. New narrated deliveries record the caption method and review findings in the receipt. Choose **Rerender video** to generate a new delivery with the updated captions from an already accepted cut.

## Reuse artwork

After an uploaded or generated version passes its planned uses, publish it to the project's **Art library**. Each style has a separate library. Publishing a corrected version under the same original project artwork ID creates a new revision when its image changes. Open the library to inspect earlier revisions and pin an eligible version to a project using that style. A project keeps a copy of that exact image and records its library revision in the render receipt. Flagging a published version as defective or incomplete prevents future reuse of that version and preserves its history.

The planner receives IDs, names, and descriptions for up to 20 eligible library versions from the selected style, ranked by relevance to the project. If it selects a match, Bacchus pins that version when it saves the plan. Review those selections along with the scene plan. Requalify versions saved by older releases by passing their current uses and publishing that evidence.

Style guides and reference images live in `styles/` and artwork versions in `style-libraries/` inside the Bacchus data folder. On upgrade, Bacchus copies versions from its old shared library into the original styles. Use `--library <folder>` to copy from a previously configured external Bacchus library. The legacy files stay available at their original locations. Keep each style's `entries/` and `blobs/` together for backup. The library's JSON files store version histories, and the blobs are PNGs named by content hash.

The library shows damaged metadata alongside usable artwork. Use **Move damaged entry aside** to retain the original record under that library’s `quarantine/` folder for repair. If you have an intact backup of the entry, restore it under `entries/` with Bacchus closed. Existing projects can open and use their own copies of previously pinned images. Bacchus checks each image’s fingerprint when you reuse it from the library.

### Back up and share a style

On **Styles**, select a saved style and choose **Export style and library**. The same action is available in **Art library**. The ZIP contains the current `style.md`, older guide revisions, reference images, artwork versions, and a `manifest.json` with style settings, fingerprints, and artwork issue records. Move damaged entries aside before exporting the healthy library. Use the data-folder backup above to keep complete projects and any quarantined records.

On **Styles**, expand **Import style and library**, choose the file, and select **Import ZIP**. Bacchus checks the archive and creates a separate, editable style and library on the receiving installation. The limit is 512 MiB for both the ZIP file and its expanded contents, with up to 256 saved guide revisions. The imported style is available for new projects. Each project reviews and approves its own planned artwork uses.

## Build and test

The app entry point, interface, and planner resources are in `bacchus/`. Installer, bundle, and release verification tools are in `packaging/`. Exact release installers and checksums live in `releases/`.

Use Python 3.13 to match CI. Install FFmpeg 5.1 or newer and its matching `ffprobe` on `PATH` for local media tests and bundle verification. Use a build with `libx264`, `aac`, and `pcm_s16le` encoders and the `sidechaincompress`, `alimiter`, `aloop`, and `ebur128` filters. [FFmpeg downloads](https://ffmpeg.org/download.html)

On Ubuntu 22.04, use a newer FFmpeg build for the render smoke test. Ubuntu supplies FFmpeg 4.4.2 and Bacchus's full-decode check uses `-fps_mode`, available in FFmpeg 5.1. [Ubuntu 22.04 package](https://packages.ubuntu.com/en/jammy-updates/amd64/ffmpeg) · [FFmpeg 5.1 options](https://ffmpeg.org/doxygen/5.1/ffmpeg__opt_8c_source.html)

Create a virtual environment with `python -m venv .venv`. Activate it with `.\.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` on macOS and Linux. Install dependencies and launch from the repository root.

```text
python -m pip install -r requirements.txt
python -m bacchus
```

To test and build the desktop bundle on the target platform, use the same environment.

```text
python -m pip install -r packaging/requirements.txt
python -m unittest discover -s tests -v
python packaging/build.py
```

On Windows, smoke-test the frozen executable and assemble the MSI.

```powershell
python packaging/smoke_bundle.py dist/Bacchus/Bacchus.exe --render
$version = python -m bacchus --version
python packaging/build_msi.py --bundle dist/Bacchus --version $version --output "release/Bacchus-$version-windows-x64.msi"
```

The [native release workflow](.github/workflows/release.yml) also checks installation and removal on Windows. Bacchus ships under the [MIT License](LICENSE). See the [harness research](docs/harness-research.md) and [source workflow comparison](docs/source-workflows.md) for design context.
