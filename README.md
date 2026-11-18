# Bacchus

Bacchus turns recorded narration and a creative brief into a reviewed MP4. AI can transcribe the recording, plan the whole cut, and design or generate artwork. The local studio validates scene coverage, tracks approvals, mixes sound, and renders the approved candidate.

## Install Bacchus

The [Bacchus 0.3.0 Windows preview](releases/0.3.0/) contains the installer and its SHA-256 checksum. The `v0.3.0` tag records the corresponding source and installer.

**Windows x64.** Run the Bacchus MSI and open **Bacchus** from the Start menu. Bacchus opens its local workspace in your browser. In **Setup**, choose **Install media tools** if the readiness checks are incomplete. This downloads a verified FFmpeg build into Bacchus's user data. Select **Quit Bacchus** in the sidebar to stop the local app.

The installer includes Python and the app's dependencies. You can use Bacchus offline once its media tools are available. Provider keys are optional for manual workflows.

## Storage

Bacchus keeps its runtime data separate from the installed app and from folders you import media from. **Settings** shows both the active storage folder and the art library folder.

| Platform | Default data folder |
| --- | --- |
| Windows | `%LOCALAPPDATA%\Bacchus` |
| macOS | `~/Library/Application Support/Bacchus` |
| Linux | `$XDG_DATA_HOME/bacchus`, or `~/.local/share/bacchus` |

Use the launcher's `--data <folder>` option to keep Bacchus data in another location, such as an external drive. Early development builds stored state in a checkout's `projects/` folder. To bring that state forward, close Bacchus and copy the contents of that folder into an empty Bacchus data folder, then launch against that location.

## Configure AI

Enter an OpenAI API key in **Settings**, or set `OPENAI_API_KEY` before launch, to enable transcription, planning, and artwork generation. Bacchus defaults to `gpt-transcribe` for transcription, `gpt-6-luna` for planning, and `gpt-image-2.5-flare-2026-09-08` for illustrations. Change the model fields in Settings or provide `BACCHUS_TRANSCRIBE_MODEL`, `BACCHUS_OPENAI_MODEL`, and `BACCHUS_IMAGE_MODEL` before the first setup. [File transcription](https://developers.openai.com/api/docs/guides/speech-to-text) · [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs) · [Image generation API](https://developers.openai.com/api/reference/resources/images/methods/generate)

To select Claude for planning, choose Anthropic and enter its key and a Claude model ID that supports structured outputs. The equivalent environment variables are `BACCHUS_PLANNER_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, and `BACCHUS_CLAUDE_MODEL`. The OpenAI key controls transcription and artwork independently. [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

Keys entered in the GUI last until the Bacchus process exits. Nonsecret settings persist in the data folder, and keys supplied through the environment remain available on launch. Each provider call starts from a button. Transcription sends a compact copy of the recording to OpenAI. Planning sends the transcript, brief, cue timings, and a compact set of relevant approved artwork names and descriptions. Image generation sends the selected art brief and style to OpenAI. Bacchus records attempts and usage where available, limits requests per project, and leaves retries to you.

## Make a cut

1. Import an MP3, FLAC, or WAV. Transcribe it in Bacchus, paste an existing transcript, or import recording-matched word timings. Pick a visual style, set the creative brief, and lock intake.
2. Generate a plan or start from the local editable draft. Review the scenes, motion, and artwork requirements, then approve the saved plan.
3. Generate the requested illustrations in Bacchus or use the combined request with your image tool. Upload any prepared artwork and approve images for this cut.
4. Choose a built-in score with transition accents, import music and place effects, or select no music. Listen to the same mix used in the final video, then approve it.
5. Review the cut, run checks, and accept the candidate. Press **Render video** to download the H.264 MP4, SRT captions, and a render receipt.

The offline example starts with an approved visual plan. For your own recording, manual transcript, the local starter plan, image import, and built-in soundtracks also work offline. The app supports recordings up to 30 minutes and three visual treatments over the same title, editorial, and illustration layouts. Export is 720p at 24 fps. OpenAI file transcription and manually entered transcript text use estimated timing. Import a legacy `timing.json` with matching `audio.sha256`, `duration`, and `words` to use source-linked cues. Editing the imported transcript text switches it to estimated timing.

Bacchus keeps the original uploaded recording, normalized media, plans, approvals, request metadata, and exports in its data folder. Editing a plan, referenced asset, or sound mix requires fresh approval of the resulting candidate. Earlier completed MP4s and receipts remain on disk.

## Reuse artwork

Approving an uploaded or generated image saves it to the shared **Art library**. Existing approved images have a **Save to library** action. Approving a replacement under the same original project artwork ID creates a new revision when its image changes. Open the library to inspect earlier revisions and pin any one of them to a project under its own scene ID. A project keeps a copy of that exact image and records its library revision in the render receipt.

The planner receives a shortlist of up to 20 library images matched to the brief and narration by title, filename, and art description. If it selects a match, Bacchus pins that version when it saves the plan. Review those selections along with the scene plan.

By default the art library lives in `library/` inside the Bacchus data folder. The launcher's `--library <folder>` option can point to a separate storage or backup location. Keep `entries/` and `blobs/` together. The library's JSON files store version histories, and the blobs are PNGs named by their content hash. A separate Git checkout can track those two directories and ignore `.library.lock` and temporary `.tmp` files.

## Development

Use Python 3.11 or newer. Create and activate a virtual environment, install `requirements.txt`, and start the source checkout with `python -m bacchus`. The source server opens [Bacchus](http://127.0.0.1:8755/) and runs until you close it from the terminal.

Use a full FFmpeg build with H.264 (`libx264`), AAC and PCM encoding, and the `sidechaincompress` and `alimiter` filters. Run `python -m unittest discover -s tests -v`. A separate native release workflow builds installers and bundles. The app ships under the [MIT License](LICENSE). [FFmpeg download and license information](https://ffmpeg.org/download.html)

See the [public harness research](docs/harness-research.md) and the [source workflow audit](docs/source-workflows.md) for the design evidence and production gaps.
