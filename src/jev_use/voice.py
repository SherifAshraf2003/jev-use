"""Speech to text, on device, with Apple's Speech framework.

Audio never leaves the machine: recognition is forced on-device whenever the
locale supports it. Speech recognition and the microphone are privacy
permissions macOS attributes to the terminal that launched this process, so
the prompts name the terminal, not jev-use.

Callbacks are routed to a background queue and the main run loop is pumped
while waiting, because a command-line process has no run loop of its own and
the framework's default is to deliver results on the main queue.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import AVFoundation
import Speech
from Foundation import NSDate, NSLocale, NSOperationQueue, NSRunLoop

logger = logging.getLogger(__name__)

# Stop once nothing new has been heard for this long after speech began.
SILENCE_SECONDS = 1.5
# Give up if no speech starts at all within this long.
NO_SPEECH_SECONDS = 6.0
MAX_SECONDS = 15.0
# After the microphone stops, how long to wait for the final transcription.
FINALIZE_SECONDS = 1.5

SETTINGS_PATH = "System Settings > Privacy & Security"


class SegmentStitcher:
    """Rebuild one utterance from partial transcriptions that restart after pauses.

    On-device recognition restarts its transcription after a pause: measured,
    "Search for Claude code skills", a pause, then the next partial is just "In"
    and it continues from there, with the earlier words gone. A trailing silence
    can do the same and leave an empty transcription. Keeping only the latest
    partial therefore lost the command even though every word had been heard.

    Word timestamps cannot tell a restart from an ordinary revision, since both
    report the first word at 0. So a partial counts as a restart when it is
    empty, or when it both changes the first word and gets shorter; revisions
    such as "Sofa for" becoming "Search for" keep their length and are not.
    """

    def __init__(self) -> None:
        self._committed: list[str] = []
        self._current = ""

    def update(self, text: str) -> bool:
        """Feed one partial. Returns True if it carried new speech."""
        text = text.strip()
        if text == self._current:
            return False
        if not text:
            self._commit()
            return False
        if self._current and self._is_restart(text):
            self._commit()
        self._current = text
        return True

    def _is_restart(self, text: str) -> bool:
        new, old = text.split(), self._current.split()
        return new[0].lower() != old[0].lower() and len(new) < len(old)

    def _commit(self) -> None:
        if self._current:
            self._committed.append(self._current)
        self._current = ""

    @property
    def text(self) -> str:
        return " ".join([*self._committed, self._current]).strip()


def _pump(seconds: float) -> None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def authorize(timeout: float = 60.0) -> None:
    """Ask for speech-recognition permission if it has not been decided yet."""
    status = Speech.SFSpeechRecognizer.authorizationStatus()
    if status == Speech.SFSpeechRecognizerAuthorizationStatusNotDetermined:
        answer: dict[str, int] = {}

        # pyobjc aborts the whole process if a callback for a void block returns
        # anything but None, so these are functions rather than lambdas.
        def on_answer(status: int) -> None:
            answer["s"] = status

        Speech.SFSpeechRecognizer.requestAuthorization_(on_answer)
        deadline = time.monotonic() + timeout
        while "s" not in answer and time.monotonic() < deadline:
            _pump(0.1)
        status = answer.get("s", status)
    if status != Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
        raise PermissionError(
            "speech recognition is not allowed for this terminal. Turn it on in "
            f"{SETTINGS_PATH} > Speech Recognition, then restart the terminal."
        )


def listen_once(
    locale: str = "en-US",
    *,
    on_partial: Callable[[str], None] | None = None,
    max_seconds: float = MAX_SECONDS,
    silence_seconds: float = SILENCE_SECONDS,
) -> str:
    """Record one utterance and return its transcription, or "" if nothing was heard.

    Stops on its own after `silence_seconds` of quiet once speech has started.
    """
    recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(
        NSLocale.localeWithLocaleIdentifier_(locale)
    )
    if recognizer is None or not recognizer.isAvailable():
        raise RuntimeError(f"speech recognition is not available for locale {locale!r}")
    recognizer.setQueue_(NSOperationQueue.alloc().init())

    request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
    request.setShouldReportPartialResults_(True)
    if recognizer.supportsOnDeviceRecognition():
        request.setRequiresOnDeviceRecognition_(True)
    else:
        logger.warning(
            "on-device recognition unavailable for %s; audio may be sent to Apple", locale
        )

    engine = AVFoundation.AVAudioEngine.alloc().init()
    node = engine.inputNode()

    def on_audio(buffer: Any, when: Any) -> None:
        request.appendAudioPCMBuffer_(buffer)

    node.installTapOnBus_bufferSize_format_block_(0, 1024, node.outputFormatForBus_(0), on_audio)
    engine.prepare()
    ok, error = engine.startAndReturnError_(None)
    if not ok:
        node.removeTapOnBus_(0)
        raise PermissionError(
            f"could not start the microphone ({error}). Allow it for this terminal in "
            f"{SETTINGS_PATH} > Microphone, then restart the terminal."
        )

    stitcher = SegmentStitcher()
    state: dict[str, Any] = {"last": None, "final": False}
    lock = threading.Lock()

    def handle(result: Any, error: Any) -> None:
        with lock:
            if result is not None:
                partial = str(result.bestTranscription().formattedString())
                logger.debug("partial %r final=%s", partial, bool(result.isFinal()))
                # Only real speech resets the silence timer; a restart to empty is not speech.
                if stitcher.update(partial):
                    state["last"] = time.monotonic()
                    if on_partial is not None:
                        on_partial(stitcher.text)
                if result.isFinal():
                    state["final"] = True
            if error is not None:
                logger.debug("recognition ended: %s", error)
                state["final"] = True

    task = recognizer.recognitionTaskWithRequest_resultHandler_(request, handle)
    started = time.monotonic()
    try:
        while True:
            _pump(0.05)
            now = time.monotonic()
            with lock:
                last, final = state["last"], state["final"]
            if final:
                break
            if last is not None and now - last >= silence_seconds:
                break
            if last is None and now - started >= NO_SPEECH_SECONDS:
                break
            if now - started >= max_seconds:
                break
    finally:
        engine.stop()
        node.removeTapOnBus_(0)
        request.endAudio()

    deadline = time.monotonic() + FINALIZE_SECONDS
    while time.monotonic() < deadline:
        with lock:
            if state["final"]:
                break
        _pump(0.05)
    # Read before cancelling: cancelling delivers one more callback on another thread.
    with lock:
        heard = stitcher.text
    task.cancel()
    return heard
