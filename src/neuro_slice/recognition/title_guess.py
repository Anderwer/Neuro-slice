from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Any, Callable

from openai import OpenAI


DEFAULT_SYSTEM_PROMPT = (
    "You are a professional song identification assistant. "
    "Given a lyrics excerpt, identify the most likely song title. "
    "Return exactly one line in the format: Artist - Title. "
    "If you are not confident, return exactly: 未知歌曲"
)


@dataclass(slots=True)
class TitleGuessResult:
    raw: str | None
    normalized: str | None
    title: str | None
    artist: str | None
    confident: bool

    @property
    def is_known(self) -> bool:
        return self.confident and bool(self.normalized)


class TitleGuessClient:
    """
    Optional song title recognition client.

    This client is intentionally lightweight:
    - `mode="none"` disables remote title recognition.
    - `mode="openai"` uses any OpenAI-compatible chat completion endpoint.

    The caller is expected to decide whether the result is good enough to use
    for naming exported files.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        mode: str = "none",
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        request_timeout_seconds: float = 30.0,
        max_lyrics_chars: int = 2500,
        api_sleep_seconds: float = 0.0,
        max_retries: int = 5,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.enabled = enabled
        self.mode = (mode or "none").strip().lower()
        self.api_key = api_key or ""
        self.base_url = base_url or None
        self.model = model
        self.request_timeout_seconds = request_timeout_seconds
        self.max_lyrics_chars = max_lyrics_chars
        self.api_sleep_seconds = api_sleep_seconds
        self.max_retries = max(1, int(max_retries))
        self.system_prompt = system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
        self.log_callback = log_callback

        self._client: OpenAI | None = None

    @classmethod
    def from_config(cls, config: Any) -> "TitleGuessClient":
        """
        Construct from a flexible config object.

        Supported inputs:
        - dict-like object
        - object with attributes:
          enabled, mode, api_key, base_url, model,
          request_timeout_seconds, max_lyrics_chars, api_sleep_seconds
        """
        if isinstance(config, dict):
            getter = config.get
        else:
            getter = lambda key, default=None: getattr(config, key, default)

        return cls(
            enabled=bool(getter("enabled", False)),
            mode=str(getter("mode", "none")),
            api_key=getter("api_key", "") or "",
            base_url=getter("base_url", "") or None,
            model=str(getter("model", "gpt-4o-mini")),
            request_timeout_seconds=float(getter("request_timeout_seconds", 30.0)),
            max_lyrics_chars=int(getter("max_lyrics_chars", 2500)),
            api_sleep_seconds=float(getter("api_sleep_seconds", 0.0)),
            max_retries=int(getter("max_retries", 5)),
            system_prompt=str(getter("system_prompt", DEFAULT_SYSTEM_PROMPT)),
            log_callback=getter("log_callback", None),
        )

    @property
    def available(self) -> bool:
        if not self.enabled:
            return False
        if self.mode == "none":
            return False
        if self.mode == "openai":
            return bool(self.api_key and self.model)
        return False

    def guess(self, lyrics: str) -> TitleGuessResult:
        """
        Guess a song title from lyrics.

        Returns a structured result. When the client is disabled or the input
        is too short, the result is simply marked as not confident.
        """
        prepared = self._prepare_lyrics(lyrics)
        if not self.available or not prepared:
            return TitleGuessResult(
                raw=None,
                normalized=None,
                title=None,
                artist=None,
                confident=False,
            )

        raw = self._guess_openai(prepared)
        normalized = self._normalize_result(raw)
        artist, title = self._split_artist_title(normalized)

        return TitleGuessResult(
            raw=raw,
            normalized=normalized,
            title=title,
            artist=artist,
            confident=bool(normalized),
        )

    def guess_title(self, lyrics: str) -> str | None:
        """
        Convenience wrapper that returns the normalized `Artist - Title` string,
        or `None` if no confident result is available.
        """
        result = self.guess(lyrics)
        return result.normalized if result.is_known else None

    def _prepare_lyrics(self, lyrics: str) -> str | None:
        text = (lyrics or "").strip()
        if len(text) < 30:
            return None
        return text[: self.max_lyrics_chars]

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.request_timeout_seconds,
            )
        return self._client

    def _log(self, message: str) -> None:
        if self.log_callback is None:
            return
        try:
            self.log_callback(message)
        except Exception:
            pass

    def _guess_openai(self, lyrics: str) -> str | None:
        if self.mode != "openai":
            return None

        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._log(
                f"识曲请求第 {attempt}/{self.max_retries} 次尝试 | model={self.model}"
            )
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    temperature=0.0,
                    max_tokens=80,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {
                            "role": "user",
                            "content": (
                                "Please identify the song from the following lyrics excerpt.\n\n"
                                f"{lyrics}"
                            ),
                        },
                    ],
                )

                if self.api_sleep_seconds > 0:
                    sleep(self.api_sleep_seconds)

                content = response.choices[0].message.content
                if not content:
                    self._log(f"识曲第 {attempt}/{self.max_retries} 次尝试返回空内容。")
                    return None

                self._log(f"识曲第 {attempt}/{self.max_retries} 次尝试成功。")
                return content.strip()
            except Exception as exc:
                last_error = exc
                self._log(
                    f"识曲第 {attempt}/{self.max_retries} 次尝试失败：{exc}"
                )
                if attempt >= self.max_retries:
                    break
                sleep(max(self.api_sleep_seconds, 1.0))

        if last_error is not None:
            raise last_error

        return None

    @staticmethod
    def _normalize_result(value: str | None) -> str | None:
        if not value:
            return None

        text = value.strip().splitlines()[0].strip()
        if not text:
            return None

        text = text.replace("：", " - ").replace(":", " - ")
        text = " ".join(text.split())

        lowered = text.lower()
        unknown_markers = {
            "未知歌曲",
            "unknown song",
            "unknown",
            "not sure",
            "cannot determine",
            "can't determine",
        }
        if lowered in unknown_markers:
            return None

        # Remove wrapping quotes/backticks if the model returns them.
        text = text.strip("`\"' ")

        # Normalize common separators to `Artist - Title`.
        separators = [" - ", " — ", " – ", " —", "–", "—"]
        for sep in separators:
            if sep in text:
                left, right = text.split(sep, 1)
                left = left.strip(" -")
                right = right.strip(" -")
                if left and right:
                    return f"{left} - {right}"

        # If we can't confidently split artist/title, keep the raw title only.
        # The caller can still use it as a filename fallback if desired.
        if len(text) >= 2:
            return text

        return None

    @staticmethod
    def _split_artist_title(value: str | None) -> tuple[str | None, str | None]:
        if not value:
            return None, None

        if " - " in value:
            artist, title = value.split(" - ", 1)
            artist = artist.strip() or None
            title = title.strip() or None
            return artist, title

        return None, value.strip() or None


def guess_song_title(lyrics: str, config: Any) -> str | None:
    """
    Functional helper used by the rest of the pipeline.

    Example:
        title = guess_song_title(lyrics, recognition_config)
    """
    client = TitleGuessClient.from_config(config)
    return client.guess_title(lyrics)