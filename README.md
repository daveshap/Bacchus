# Bacchus

Bacchus turns recorded narration and a creative brief into a reviewed MP4. AI can transcribe the recording, plan the whole cut, and design or generate artwork. The local studio validates scene coverage, tracks approvals, mixes sound, and renders the approved candidate.

## Install Bacchus on Windows x64

Download the [Bacchus 0.4.0 Windows installer](releases/0.4.0/Bacchus-0.4.0-windows-x64.msi) and its [SHA-256 checksum](releases/0.4.0/Bacchus-0.4.0-windows-x64.sha256). This unsigned preview includes Python and the app's dependencies. The `v0.4.0` tag records the source and installer for this release.

1. Run the MSI, then open **Bacchus** from the Start menu. Bacchus opens its local workspace in your default browser.
2. In **Setup**, choose **Install media tools** if the readiness checks are incomplete. This downloads a verified FFmpeg build into Bacchus's user data.
3. To close the app, select **Quit Bacchus** in the sidebar. Quit Bacchus before running a newer installer or uninstalling it from **Settings → Apps** in Windows.

Manual workflows work offline once the media tools are available. Add provider keys in Bacchus **Settings** to use AI features.

## Storage

Bacchus stores projects, settings and the default art library in `%LOCALAPPDATA%\Bacchus` on Windows. **Settings** shows the active data and art library locations. The Windows installer manages the application files separately.

To back up Bacchus, quit the app, then copy its data folder and any separately configured art library. To restore a Bacchus 0.3.0 or 0.3.1 backup, copy its contents into an empty data folder with Bacchus closed. The application supports `--data <folder>` for a different storage location, such as an external drive.

## Configure AI

Bacchus uses Anthropic's Claude Sonnet 5.5 (`claude-sonnet-5-5`) by default to plan scenes, choose motion and write artwork briefs. Enter an Anthropic API key in **Settings** or set `ANTHROPIC_API_KEY` before launch to enable planning. Bacchus renders the approved plan locally. You can select another Claude model with structured outputs in **Settings** or through `BACCHUS_CLAUDE_MODEL`. [Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) · [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

To use an optional OpenAI feature, add an OpenAI API key in **Settings** or set `OPENAI_API_KEY`. Then enable the specific feature you want.

- **Image generation.** Enable OpenAI image generation in **Settings** or set `BACCHUS_USE_OPENAI_IMAGES=1`. When requested, Bacchus sends the selected artwork brief and style to generate an image. [Image generation API](https://developers.openai.com/api/reference/resources/images/methods/generate)
- **Scene planning.** Choose OpenAI as your planning provider in **Settings** or set `BACCHUS_PLANNER_PROVIDER=openai`. [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

Each provider call starts at your request. Planning sends the transcript, brief, cue timings, and relevant approved artwork names and descriptions to the selected provider. In both review stages, **Ask Claude** sends one proof image with the relevant approved scene context for a second opinion. Bacchus uses high effort for that review with the default Claude model. A person records the decision for every proof. Keys entered in **Settings** last until Bacchus exits. Nonsecret preferences persist in the data folder.

### API logs

**Settings** shows the active API log folder. Choose an absolute path there or set `BACCHUS_API_LOG_DIR` before launch. Otherwise Bacchus writes to a per-user folder under the system's temporary directory. Each outbound provider call gets its own folder with metadata, a request body, and the response body when one is available. Metadata includes the project, stage, HTTP status, provider request ID when supplied, and whether the response hit the transport size limit. Interrupted calls can leave a pending record.

Logs can contain prompts, transcripts, artwork, and audio. Bacchus omits authentication headers and redacts configured keys from logged payloads. Keep the folder private. The system may clear temporary files, so choose a persistent folder for logs you want to keep.

## Make a cut

1. Import an MP3, FLAC, or WAV. Import a recording-matched cue sheet or derive word timings with the local Parakeet model. Check its words against the audio, correct any cues, then set the style and creative brief and lock intake.
2. Generate a plan or start from the local editable draft. A layered scene can reveal text, artwork, a bar chart, or a flow diagram on independently selected narration words. Review the whole plan and artwork requirements before approving it.
3. Pin library artwork, upload prepared images, or make illustrations with optional OpenAI image generation. Approve the source versions for checking, then pass each planned use. Publish reusable assets after they pass. Record defective or incomplete artwork as an issue and replace the version or revise the planned use.
4. Choose a built-in score, import music and place effects, or select no music. Set music levels or silence by narration cue range. Prepare and measure the soundtrack, listen to the prepared WAV, and approve that exact file. Once the artwork and soundtrack are approved, prepare the animation.
5. Run **Animation checks** to produce contact sheets from the prepared cut with frame samples around entrances, motion, cues, exits, and joins. Scrub the cut and listen to the mix for sync. Resolve any failed checks, pass each scene, and accept the candidate.
6. Press **Render video** to export an H.264 MP4, SRT captions, and a receipt with both review stages.

The offline example starts with an approved visual plan. The local starter plan, image import and built-in soundtracks work offline. The app supports recordings up to 30 minutes, with three visual treatments and title, editorial, illustration, and layered layouts. Export is 720p at 24 fps.

The recording is the timing authority. Import a `timing.json` with its `audio.sha256`, `duration`, and `words` with `text`, `start`, and `end`. For audio-only intake, extract the Parakeet TDT model separately and select the folder containing `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx` and `tokens.txt`. The local recognizer derives individual timestamps from the waveform. Review its draft against the audio before locking it. Older project exports remain downloadable. Resume a project with recording-bound cues by reopening its intake. [Parakeet TDT model instructions](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html#sherpa-onnx-nemo-parakeet-tdt-0-6b-v2-int8-english)

Bacchus keeps original recordings, normalized media, plans, review proofs, decisions, and exports in its data folder. Proofs bind to the artwork version and the scene inputs they show. Changes to the cut invalidate its acceptance. Checks that still show the current inputs can carry forward. The built-in motions apply to the whole scene. The artwork checks use still images.

## Reuse artwork

After an uploaded or generated version passes its planned uses, publish it to the shared **Art library**. Publishing a corrected version under the same original project artwork ID creates a new revision when its image changes. Open the library to inspect earlier revisions and pin an eligible version to a project under its own scene ID. A project keeps a copy of that exact image and records its library revision in the render receipt. Flagging a published version as defective or incomplete prevents future reuse of that version and preserves its history.

The planner receives a shortlist of up to 20 checked library images from the selected style, ranked by title, filename, and art description. If it selects a match, Bacchus pins that version when it saves the plan. Review those selections along with the scene plan. Requalify versions saved by older releases by passing their current uses and publishing that evidence.

By default the art library lives in `library/` inside the Bacchus data folder. The launcher's `--library <folder>` option can point to a separate storage or backup location. Keep `entries/` and `blobs/` together. The library's JSON files store version histories, and the blobs are PNGs named by their content hash. A separate Git checkout can track those two directories and ignore `.library.lock` and temporary `.tmp` files.

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
