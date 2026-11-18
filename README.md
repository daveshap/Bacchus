# Bacchus

Bacchus turns recorded narration and a creative brief into a reviewed MP4. AI can transcribe the recording, plan the whole cut, and design or generate artwork. The local studio validates scene coverage, tracks approvals, mixes sound, and renders the approved candidate.

## Install Bacchus on Windows x64

Download the [Bacchus 0.5.1 Windows installer](releases/0.5.1/Bacchus-0.5.1-windows-x64.msi) and its [SHA-256 checksum](releases/0.5.1/Bacchus-0.5.1-windows-x64.sha256). This unsigned preview includes Python and the app's runtime dependencies. The `v0.5.1` tag records the source and installer for this release.

1. Run the MSI, then open **Bacchus** from the Start menu. Bacchus opens its local workspace in your default browser.
2. In **Setup**, choose **Install media tools** if the readiness checks are incomplete. This downloads a verified FFmpeg build into Bacchus's user data.
3. For local word cues, choose **Install timing model** in Setup. Bacchus downloads and verifies the 460 MiB English Parakeet model archive in its own data folder. If you already have a compatible model, choose **Verify and use folder** to save its location. You can also import measured cues for a recording.
4. To close the app, select **Quit Bacchus** in the sidebar. Quit Bacchus before running a newer installer or uninstalling it from **Settings → Apps** in Windows.

Manual workflows work offline once the media tools are available. Add provider keys in Bacchus **Settings** to use AI features.

## Storage

Bacchus stores projects, settings, style guides, reference images and separate artwork libraries in `%LOCALAPPDATA%\Bacchus` on Windows. **Settings** shows the data and library locations. The Windows installer manages the application files separately.

To back up Bacchus, quit the app and copy its data folder. To restore a backup, copy its contents into an empty data folder with Bacchus closed. The application supports `--data <folder>` for a different storage location, such as an external drive.

## Styles and project kickoff

Choose a saved style in **New project**. Bacchus shows the name of its artwork library and how many reusable assets it contains. Professional Corporate and Hand-drawn Explainer come with Markdown guides. Warm Editorial, Studio Ink, and Night Signal are also available.

Open **Styles** to name a style and library, answer the music, effects, and visual questions, set palette colors, and attach example images. Write or import a Markdown guide, or ask Claude to draft one from the answers and reference images you choose to supply. Review and save the guide. Bacchus keeps the questionnaire answers as entered. Creating and editing styles works offline.

Each project records its style revision. New projects can choose a later revision while existing projects keep the guide, colors, and reference images they started with. The style guides scene planning and illustration requests. Its music and sound-effects choices provide a starting composition that you can adjust and approve for the cut.

## Configure AI

Bacchus uses Anthropic's Claude Sonnet 5.5 (`claude-sonnet-5-5`) by default to plan scenes, choose motion and write artwork briefs. Enter an Anthropic API key in **Settings** or set `ANTHROPIC_API_KEY` before launch to enable planning. Bacchus renders the approved plan locally. You can select another Claude model with structured outputs in **Settings** or through `BACCHUS_CLAUDE_MODEL`. [Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) · [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

To use an optional OpenAI feature, add an OpenAI API key in **Settings** or set `OPENAI_API_KEY`. Then enable the specific feature you want.

- **Image generation.** Enable OpenAI image generation in **Settings** or set `BACCHUS_USE_OPENAI_IMAGES=1`. When requested, Bacchus sends the selected artwork brief and style to generate an image. [Image generation API](https://developers.openai.com/api/reference/resources/images/methods/generate)
- **Scene planning.** Choose OpenAI as your planning provider in **Settings** or set `BACCHUS_PLANNER_PROVIDER=openai`. [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

Each provider call starts at your request. Planning sends the transcript, brief, cue timings, style guide and its example images, and relevant approved artwork descriptions to the selected provider. Optional image generation can use the same examples. **Draft with Claude** sends style answers, guide text and selected examples. In both review stages, **Ask Claude** sends one proof image with the relevant approved scene context for a second opinion. Bacchus uses high effort for that review with the default Claude model. A person records the decision for every proof. Keys entered in **Settings** last until Bacchus exits. Nonsecret preferences persist in the data folder.

### API logs

**Settings** shows the active API log folder. Choose an absolute path there or set `BACCHUS_API_LOG_DIR` before launch. Otherwise Bacchus writes to a per-user folder under the system's temporary directory. Each outbound provider call gets its own folder with metadata, a request body, and the response body when one is available. Metadata includes the project, stage, HTTP status, provider request ID when supplied, and whether the response hit the transport size limit. Interrupted calls can leave a pending record.

Logs can contain prompts, transcripts, artwork, and audio. Bacchus omits authentication headers and redacts configured keys from logged payloads. Keep the folder private. The system may clear temporary files, so choose a persistent folder for logs you want to keep.

## Make a cut

1. Choose a style and import an MP3, FLAC, or WAV. Import a recording-matched cue sheet or derive word timings with the local Parakeet model. Check its words against the audio, correct any cues, then set the creative brief and lock intake.
2. Generate a plan or start from the local editable draft. A layered scene can reveal text, artwork, a bar chart, or a flow diagram on independently selected narration words. Review the whole plan and artwork requirements before approving it.
3. Pin library artwork, upload prepared images, or make illustrations with optional OpenAI image generation. Approve the source versions for checking, then pass each planned use. Publish reusable assets after they pass. Record defective or incomplete artwork as an issue and replace the version or revise the planned use.
4. Choose a built-in score, import music and place effects, or select no music. Set music levels or silence by narration cue range. Prepare and measure the soundtrack, listen to the prepared WAV, and approve that exact file. Once the artwork and soundtrack are approved, prepare the animation.
5. Run **Animation checks** to produce contact sheets from the prepared cut with frame samples around entrances, motion, cues, exits, and joins. Scrub the cut and listen to the mix for sync. Resolve any failed checks, pass each scene, and accept the candidate.
6. Press **Render video** to export an H.264 MP4, SRT captions, and a receipt with both review stages.

The offline example starts with an approved visual plan. The local starter plan, image import and built-in soundtracks work offline. The app supports recordings up to 30 minutes, with three visual treatments and title, editorial, illustration, and layered layouts. Export is 720p at 24 fps.

The recording is the timing authority. Import a `timing.json` with its `audio.sha256`, `duration`, and `words` with `text`, `start`, and `end`. For audio-only intake, install the model from Setup, Settings, or Intake. An existing Parakeet TDT v2 INT8 folder must contain `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx` and `tokens.txt`. Bacchus verifies those files and remembers the folder. The local recognizer derives individual timestamps from the waveform without a provider key. Review its draft against the audio before locking it. Resume a project with recording-bound cues by reopening its intake. [Parakeet TDT model instructions](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html#sherpa-onnx-nemo-parakeet-tdt-0-6b-v2-int8-english)

The ONNX files are the sherpa-onnx INT8 conversion of NVIDIA Parakeet TDT 0.6B v2, licensed under CC BY 4.0. [NVIDIA model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) · [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/)

Bacchus keeps original recordings, normalized media, plans, review proofs, decisions, and exports in its data folder. Proofs bind to the artwork version and the scene inputs they show. Changes to the cut invalidate its acceptance. Checks that still show the current inputs can carry forward.

**Video encoding** in Settings defaults to **Automatic GPU**. Bacchus tries a real encode with the delivery format. It prefers NVIDIA NVENC where available, can use Intel Quick Sync or AMD AMF on Windows, VAAPI on Linux, and VideoToolbox on macOS. On Windows it can use a compatible FFmpeg on PATH if the app-managed FFmpeg cannot initialize the GPU. It falls back to libx264 if a hardware encoder is unavailable or fails the export checks. Choose **CPU only** to skip hardware tests. Export and its receipt identify the actual encoder and any fallback. Scene composition uses the local Pillow renderer.

Bacchus decodes the rendered video and audio before it marks the delivery complete. It fingerprints the MP4, captions, and receipt and checks the files before reuse or download. If any file is missing or changed, use **Rerender video** to write a new delivery from the accepted candidate using the current encoder setting. The most recent intact delivery stays available during a rerender. For older deliveries, Bacchus checks the MP4 against the original receipt and fingerprints the captions and receipt on upgrade.

## Reuse artwork

After an uploaded or generated version passes its planned uses, publish it to the project's **Art library**. Each style has a separate library. Publishing a corrected version under the same original project artwork ID creates a new revision when its image changes. Open the library to inspect earlier revisions and pin an eligible version to a project using that style. A project keeps a copy of that exact image and records its library revision in the render receipt. Flagging a published version as defective or incomplete prevents future reuse of that version and preserves its history.

The planner receives a shortlist of up to 20 checked library images from the selected style, ranked by title, filename, and art description. If it selects a match, Bacchus pins that version when it saves the plan. Review those selections along with the scene plan. Requalify versions saved by older releases by passing their current uses and publishing that evidence.

Style guides and reference images live in `styles/` and artwork versions in `style-libraries/` inside the Bacchus data folder. On upgrade, Bacchus copies versions from its old shared library into the original styles. Use `--library <folder>` to copy from a previously configured external Bacchus library. The legacy files stay available at their original locations. Keep each style's `entries/` and `blobs/` together for backup. The library's JSON files store version histories, and the blobs are PNGs named by content hash.

The library shows damaged metadata alongside usable artwork. Use **Move damaged entry aside** to retain the original record under that library’s `quarantine/` folder for repair. If you have an intact backup of the entry, restore it under `entries/` with Bacchus closed. Existing projects can open and use their own copies of previously pinned images. Bacchus checks each image’s fingerprint when you reuse it from the library.

## Build and test

The app entry point, interface, and planner resources are in `bacchus/`. Installer, bundle, and release verification tools are in `packaging/`. Exact release installers and checksums live in `releases/`.

Build on the target platform with Python 3.11 or newer and a virtual environment. Media tests use a full FFmpeg build with H.264 (`libx264`), AAC and PCM encoding, and the `sidechaincompress` and `alimiter` filters. [FFmpeg download and license information](https://ffmpeg.org/download.html)

```text
python -m pip install -r requirements.txt -r packaging/requirements.txt
python -m unittest discover -s tests -v
python packaging/build.py
```

On Windows, test the built application and its video export with `python packaging/smoke_bundle.py dist/Bacchus/Bacchus.exe --render`.

The [native release workflow](.github/workflows/release.yml) defines Windows installer and macOS and Linux bundle builds, including packaged application smoke tests. The app ships under the [MIT License](LICENSE). See the [harness research](docs/harness-research.md) and [source workflow audit](docs/source-workflows.md) for design context.
