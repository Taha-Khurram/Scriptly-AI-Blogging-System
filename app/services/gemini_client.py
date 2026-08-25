"""One Gemini client for the whole application.

Before this, nine modules each called ``genai.configure(api_key=...)`` in their
``__init__`` and each hardcoded the model name. ``genai.configure`` mutates
process-global SDK state, so every agent construction re-applied it -- and
because agents are constructed per request, that was a global write on a shared
object from many threads at once. The model name being duplicated nine times
also made the two forced model migrations this project has already been through
a nine-file edit each.

What this module adds beyond deduplication:

* **Timeouts.** A call with no deadline can hold a worker thread indefinitely
  when the upstream stalls. Every request here carries one.
* **Retries with backoff and jitter.** 429 and 503 from Gemini are transient
  and expected. Retrying without jitter makes it worse: several workers that
  hit a rate limit together retry in lockstep and collide again.
* **A distinction the callers need.** A quota exhaustion, a safety block, and a
  malformed response are three different problems with three different correct
  responses, and ``except Exception`` cannot tell them apart.
* **Model reuse.** ``GenerativeModel`` objects are cached per (model, config)
  so a request does not pay to rebuild one.
"""
from __future__ import annotations

import inspect
import json
import logging
import random
import re
import threading
import time

import google.generativeai as genai

from app.core.errors import ConfigurationError, ExternalServiceError

logger = logging.getLogger(__name__)

# ``request_options`` only exists in google-generativeai >= 0.4. Older builds
# forward unknown kwargs into the request proto and raise "Unknown field for
# GenerateContentRequest", so feature-detect once at import rather than
# guessing from a version string.
_SUPPORTS_REQUEST_OPTIONS = (
    'request_options'
    in inspect.signature(genai.GenerativeModel.generate_content).parameters
)

# Substrings that mark an upstream failure as worth retrying. Matched against
# the exception text because the SDK raises a mix of google-api-core errors and
# its own types, and the set is not stable across versions.
_RETRYABLE_MARKERS = (
    '429', 'rate limit', 'resource exhausted', 'resource_exhausted',
    'quota', '500', '502', '503', '504', 'internal error', 'unavailable',
    'deadline exceeded', 'timeout', 'connection reset', 'try again',
)

_NON_RETRYABLE_MARKERS = (
    '400', '401', '403', 'api key not valid', 'permission denied',
    'invalid argument', 'not found', 'unsupported',
)


class GeminiError(ExternalServiceError):
    """Base for every Gemini failure, so routes can catch one class."""

    code = 'ai_unavailable'
    message = 'The AI service is temporarily unavailable. Please try again.'

    def __init__(self, message=None, **kwargs):
        kwargs.setdefault('service', 'gemini')
        super().__init__(message, **kwargs)


class GeminiQuotaError(GeminiError):
    """Rate limit or quota exhausted after every retry."""

    status_code = 429
    code = 'ai_quota_exceeded'
    message = ('The AI service is at its request limit right now. '
               'Please try again in a minute.')


class GeminiSafetyError(GeminiError):
    """The prompt or the response was blocked by a safety filter.

    A 400-class problem, not a 502: retrying the same prompt will be blocked
    again, so the caller must change the input.
    """

    status_code = 400
    code = 'ai_content_blocked'
    message = ('The AI declined to produce content for this topic. '
               'Try rephrasing your prompt.')


class GeminiResponseError(GeminiError):
    """The call succeeded but the response could not be used."""

    code = 'ai_bad_response'
    message = 'The AI returned an unusable response. Please try again.'


def _classify(exc):
    """Map an SDK exception to (retryable, exception class)."""
    text = str(exc).lower()

    if 'safety' in text or 'blocked' in text or 'recitation' in text:
        return False, GeminiSafetyError
    if any(marker in text for marker in _NON_RETRYABLE_MARKERS):
        return False, GeminiError
    if 'quota' in text or '429' in text or 'resource' in text and 'exhaust' in text:
        return True, GeminiQuotaError
    if any(marker in text for marker in _RETRYABLE_MARKERS):
        return True, GeminiError
    # Unknown failures are retried once by the caller's budget: a transient
    # network fault is far more likely here than a permanent logic error, and
    # the retry ceiling bounds the cost of being wrong.
    return True, GeminiError


class GeminiClient:
    """Thread-safe façade over ``google.generativeai``.

    Configured once from the app factory. Agents call
    :meth:`generate_text` or :meth:`generate_json` and never touch the SDK.
    """

    def __init__(self):
        self._api_key = None
        self._default_model = 'gemini-flash-lite-latest'
        self._embedding_model = 'models/gemini-embedding-001'
        self._timeout = 180
        self._max_retries = 2
        self._models = {}
        self._lock = threading.RLock()
        self._configured = False

    # --- Lifecycle --------------------------------------------------------

    def configure(self, *, api_key, model=None, embedding_model=None,
                  timeout=180, max_retries=2):
        """Apply credentials and defaults. Idempotent; call once at startup."""
        with self._lock:
            if not api_key:
                logger.error(
                    'GEMINI_API_KEY is not set; every AI feature will fail '
                    'with a configuration error until it is provided.'
                )
            self._api_key = api_key
            self._default_model = model or self._default_model
            self._embedding_model = embedding_model or self._embedding_model
            self._timeout = timeout
            self._max_retries = max_retries
            self._models.clear()

            if api_key:
                genai.configure(api_key=api_key)
                self._configured = True
                logger.info(
                    'Gemini client configured',
                    extra={'model': self._default_model,
                           'timeout_s': timeout, 'max_retries': max_retries},
                )

    @property
    def is_configured(self):
        return self._configured

    @property
    def default_model(self):
        return self._default_model

    def _require_configured(self):
        if not self._configured:
            raise ConfigurationError(
                'AI features are unavailable because GEMINI_API_KEY is not set.'
            )

    # --- Model handles ----------------------------------------------------

    def get_model(self, model_name=None, *, generation_config=None,
                  system_instruction=None):
        """A cached ``GenerativeModel``.

        Keyed by every argument that affects behaviour, so two agents wanting
        different temperatures get different handles rather than one silently
        winning.
        """
        self._require_configured()
        name = model_name or self._default_model
        key = (
            name,
            json.dumps(generation_config, sort_keys=True) if generation_config else None,
            system_instruction,
        )
        with self._lock:
            model = self._models.get(key)
            if model is None:
                kwargs = {}
                if generation_config:
                    kwargs['generation_config'] = generation_config
                if system_instruction:
                    kwargs['system_instruction'] = system_instruction
                model = genai.GenerativeModel(name, **kwargs)
                self._models[key] = model
            return model

    # --- Generation -------------------------------------------------------

    def generate_text(self, prompt, *, model=None, generation_config=None,
                      system_instruction=None, timeout=None, max_retries=None,
                      label='generate'):
        """Run a prompt and return the response text.

        Raises a :class:`GeminiError` subclass rather than returning an error
        string, so a caller cannot accidentally treat a failure message as
        generated content -- which is how "The AI service is unavailable" ends
        up published as a blog post.
        """
        self._require_configured()
        handle = self.get_model(
            model, generation_config=generation_config,
            system_instruction=system_instruction,
        )
        deadline = timeout or self._timeout
        attempts = (max_retries if max_retries is not None else self._max_retries) + 1

        last_error = None
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                kwargs = {}
                if _SUPPORTS_REQUEST_OPTIONS:
                    kwargs['request_options'] = {'timeout': deadline}
                response = handle.generate_content(prompt, **kwargs)
                text = self._extract_text(response)
                logger.info(
                    'Gemini %s ok', label,
                    extra={'label': label, 'model': model or self._default_model,
                           'attempt': attempt + 1,
                           'duration_ms': round((time.perf_counter() - started) * 1000, 1),
                           'chars': len(text)},
                )
                return text

            except (GeminiSafetyError, GeminiResponseError):
                # Already classified by _extract_text; retrying will not help.
                raise

            except Exception as exc:
                retryable, error_class = _classify(exc)
                last_error = error_class(str(exc)[:300])
                logger.warning(
                    'Gemini %s failed (attempt %s/%s): %s',
                    label, attempt + 1, attempts, exc,
                    extra={'label': label, 'retryable': retryable,
                           'duration_ms': round((time.perf_counter() - started) * 1000, 1)},
                )
                if not retryable or attempt == attempts - 1:
                    break
                # Exponential backoff with full jitter. Without the jitter,
                # workers that hit the same rate limit retry in lockstep and
                # collide on every subsequent attempt.
                backoff = min(2 ** attempt, 8) * (0.5 + random.random())
                time.sleep(backoff)

        raise last_error or GeminiError()

    def stream_text(self, prompt, *, model=None, generation_config=None,
                    system_instruction=None, timeout=None, max_retries=None,
                    label='stream'):
        """Yield the response text in chunks as the model produces it.

        The non-streaming :meth:`generate_text` is still the right call for
        anything whose result is only used once it is whole (SEO scores, a
        category name). This exists for the one place where the wait itself is
        the product: a reader watching a blog post being written.

        Retries work differently here, and have to. Once a chunk has been
        yielded the caller has already shown it, so a retry would restart the
        text from the beginning and duplicate what is on screen. A failure is
        therefore only retried while nothing has been emitted; after that it
        propagates and the caller keeps the partial text it already has.
        """
        self._require_configured()
        handle = self.get_model(
            model, generation_config=generation_config,
            system_instruction=system_instruction,
        )
        deadline = timeout or self._timeout
        attempts = (max_retries if max_retries is not None else self._max_retries) + 1

        last_error = None
        for attempt in range(attempts):
            started = time.perf_counter()
            emitted = 0
            try:
                kwargs = {}
                if _SUPPORTS_REQUEST_OPTIONS:
                    kwargs['request_options'] = {'timeout': deadline}
                stream = handle.generate_content(prompt, stream=True, **kwargs)

                for chunk in stream:
                    piece = self._chunk_text(chunk)
                    if piece:
                        emitted += len(piece)
                        yield piece

                # The terminal reasons only become readable once the iterator
                # is drained, so an empty stream is diagnosed here rather than
                # reported as a successful generation of nothing.
                if not emitted:
                    self._raise_for_empty_stream(stream)

                logger.info(
                    'Gemini %s ok', label,
                    extra={'label': label, 'model': model or self._default_model,
                           'attempt': attempt + 1, 'streamed': True,
                           'duration_ms': round((time.perf_counter() - started) * 1000, 1),
                           'chars': emitted},
                )
                return

            except (GeminiSafetyError, GeminiResponseError):
                raise

            except Exception as exc:
                retryable, error_class = _classify(exc)
                last_error = error_class(str(exc)[:300])
                logger.warning(
                    'Gemini %s failed (attempt %s/%s, %s chars in): %s',
                    label, attempt + 1, attempts, emitted, exc,
                    extra={'label': label, 'retryable': retryable,
                           'duration_ms': round((time.perf_counter() - started) * 1000, 1)},
                )
                # Mid-stream: the caller is holding text we cannot take back.
                if emitted:
                    raise last_error from exc
                if not retryable or attempt == attempts - 1:
                    break
                time.sleep(min(2 ** attempt, 8) * (0.5 + random.random()))

        raise last_error or GeminiError()

    @staticmethod
    def _chunk_text(chunk):
        """Text out of one stream chunk, or ``''``.

        A chunk carrying only a finish reason or a safety rating has no parts,
        and ``chunk.text`` raises on it. That is normal in a stream -- it is not
        a failure and must not end the iteration -- so it is swallowed here and
        the terminal state is inspected once at the end instead.
        """
        try:
            return chunk.text or ''
        except Exception:
            return ''

    @classmethod
    def _raise_for_empty_stream(cls, stream):
        """Explain a stream that produced no text at all.

        The terminal-state accessors on a streaming response are properties that
        can raise in their own right, and a diagnosis routine that throws its own
        exception would replace a real answer with a confusing one. So the
        inspection is guarded and falls back to the plainest true statement.
        """
        try:
            cls._diagnose_empty_stream(stream)
        except GeminiError:
            raise
        except Exception as exc:
            raise GeminiResponseError(
                'The AI returned no usable content.'
            ) from exc
        raise GeminiResponseError('The AI returned an empty response.')

    @staticmethod
    def _diagnose_empty_stream(stream):
        """Raise the precise error for an empty stream, or return."""
        feedback = getattr(stream, 'prompt_feedback', None)
        block_reason = getattr(feedback, 'block_reason', None) if feedback else None
        if block_reason:
            raise GeminiSafetyError(
                f'The prompt was blocked by a safety filter ({block_reason}).'
            )

        candidates = getattr(stream, 'candidates', None) or []
        finish_reason = str(
            getattr(candidates[0], 'finish_reason', '') or ''
        ).upper() if candidates else ''

        if 'SAFETY' in finish_reason:
            raise GeminiSafetyError()
        if 'RECITATION' in finish_reason:
            raise GeminiSafetyError(
                'The AI stopped because the output reproduced source material '
                'too closely. Try a different angle on the topic.'
            )
        # Nothing specific to say; the caller raises the general case.

    def stream_with_tools(self, contents, tools, *, model=None,
                          generation_config=None, system_instruction=None,
                          timeout=None, max_retries=None, tool_config=None,
                          label='tools'):
        """Run a tool-enabled turn, yielding text and tool calls as they arrive.

        Yields ``('text', str)`` for prose and ``('call', {'name', 'args'})``
        for each function call the model requests, in the order the model
        produced them. The caller runs the tools and calls this again with the
        results appended to ``contents`` -- the loop lives in
        :mod:`app.agent.loop`, not here, because the *policy* about when to stop
        calling tools is an application decision and this class is a transport.

        Streaming rather than :meth:`generate_text` for the same reason the blog
        writer streams: a model that says "let me look that up" before calling
        ``search_web`` should have said it on screen *before* the search runs,
        not after it finishes. With a non-streaming call the user watches
        nothing happen for the length of the whole turn.

        Function calls arrive as complete parts -- Gemini does not split a call's
        arguments across chunks the way some APIs split a delta -- so a call can
        be dispatched the moment its part is seen, with no accumulation buffer.

        Retries follow :meth:`stream_text`: only while nothing has been yielded.
        Once the caller has been handed a token it has shown it, and restarting
        would duplicate text on screen. Once it has been handed a *call* it may
        have run it, and restarting could run it twice -- which for a tool that
        writes is the difference between one draft and two.
        """
        self._require_configured()
        handle = self.get_model(
            model, generation_config=generation_config,
            system_instruction=system_instruction,
        )
        deadline = timeout or self._timeout
        attempts = (max_retries if max_retries is not None else self._max_retries) + 1

        last_error = None
        for attempt in range(attempts):
            started = time.perf_counter()
            emitted = 0
            try:
                kwargs = {'tools': tools}
                if tool_config is not None:
                    kwargs['tool_config'] = tool_config
                if _SUPPORTS_REQUEST_OPTIONS:
                    kwargs['request_options'] = {'timeout': deadline}

                stream = handle.generate_content(contents, stream=True, **kwargs)

                calls = 0
                for chunk in stream:
                    for kind, payload in self._chunk_parts(chunk):
                        emitted += 1
                        if kind == 'call':
                            calls += 1
                        yield kind, payload

                if not emitted:
                    # A turn that produced neither text nor a call is a failure
                    # with a reason attached; the reason is only readable once
                    # the iterator is drained.
                    self._raise_for_empty_stream(stream)

                logger.info(
                    'Gemini %s ok', label,
                    extra={'label': label, 'model': model or self._default_model,
                           'attempt': attempt + 1, 'streamed': True,
                           'tool_calls': calls, 'parts': emitted,
                           'duration_ms': round((time.perf_counter() - started) * 1000, 1)},
                )
                return

            except (GeminiSafetyError, GeminiResponseError):
                raise

            except Exception as exc:
                retryable, error_class = _classify(exc)
                last_error = error_class(str(exc)[:300])
                logger.warning(
                    'Gemini %s failed (attempt %s/%s, %s parts in): %s',
                    label, attempt + 1, attempts, emitted, exc,
                    extra={'label': label, 'retryable': retryable},
                )
                if emitted:
                    raise last_error from exc
                if not retryable or attempt == attempts - 1:
                    break
                time.sleep(min(2 ** attempt, 8) * (0.5 + random.random()))

        raise last_error or GeminiError()

    @staticmethod
    def _chunk_parts(chunk):
        """``(kind, payload)`` pairs out of one streaming chunk.

        Reads ``candidates[0].content.parts`` directly rather than going through
        ``chunk.text``, which raises whenever a chunk carries a function call
        instead of prose -- the normal case in a tool-enabled turn. Each accessor
        is guarded: a chunk holding only a finish reason or a safety rating has
        no parts at all, and that is routine mid-stream rather than a fault.
        """
        try:
            candidates = getattr(chunk, 'candidates', None) or []
            if not candidates:
                return
            parts = getattr(getattr(candidates[0], 'content', None), 'parts', None) or []
        except Exception:
            return

        for part in parts:
            call = getattr(part, 'function_call', None)
            # A proto message is falsy when empty, so `if call:` correctly
            # skips the default-constructed FunctionCall that sits on a
            # text-only part.
            if call and getattr(call, 'name', ''):
                try:
                    args = dict(call.args) if call.args else {}
                except Exception:
                    args = {}
                yield 'call', {'name': str(call.name), 'args': _plain(args)}
                continue

            text = getattr(part, 'text', '') or ''
            if text:
                yield 'text', text

    def generate_json(self, prompt, *, model=None, generation_config=None,
                      system_instruction=None, timeout=None, max_retries=None,
                      label='generate_json', default=None):
        """Run a prompt expected to return JSON, and parse it.

        Models wrap JSON in prose or a ```json fence more often than not, so the
        response is repaired before parsing. ``default`` is returned instead of
        raising when the caller can proceed without the structured result --
        used for optional enrichment (SEO scores, category suggestions) that
        should never fail a whole blog generation.
        """
        config = dict(generation_config or {})
        # Ask the API for JSON directly where supported; it removes the fence
        # and prose problem at the source rather than papering over it.
        config.setdefault('response_mime_type', 'application/json')

        try:
            raw = self.generate_text(
                prompt, model=model, generation_config=config,
                system_instruction=system_instruction, timeout=timeout,
                max_retries=max_retries, label=label,
            )
        except GeminiError:
            if default is not None:
                logger.warning('Gemini %s failed; using caller default', label)
                return default
            raise

        parsed = extract_json(raw)
        if parsed is None:
            if default is not None:
                logger.warning('Gemini %s returned unparsable JSON; using default', label)
                return default
            raise GeminiResponseError(
                'The AI response could not be parsed as JSON.'
            )
        return parsed

    def embed(self, text, *, model=None, task_type='retrieval_document',
              title=None, timeout=None):
        """Return the embedding vector for ``text``."""
        self._require_configured()
        name = model or self._embedding_model
        attempts = self._max_retries + 1

        last_error = None
        for attempt in range(attempts):
            try:
                kwargs = {'model': name, 'content': text, 'task_type': task_type}
                if title:
                    kwargs['title'] = title
                result = genai.embed_content(**kwargs)
                return result['embedding']
            except Exception as exc:
                retryable, error_class = _classify(exc)
                last_error = error_class(str(exc)[:300])
                logger.warning(
                    'Gemini embed failed (attempt %s/%s): %s',
                    attempt + 1, attempts, exc,
                )
                if not retryable or attempt == attempts - 1:
                    break
                time.sleep(min(2 ** attempt, 8) * (0.5 + random.random()))

        raise last_error or GeminiError()

    # --- Response handling ------------------------------------------------

    @staticmethod
    def _extract_text(response):
        """Pull usable text out of a response, or raise a precise error.

        ``response.text`` itself raises when the model returned no usable
        candidate, and the message it raises with does not say why. Inspecting
        the finish reason first turns "something went wrong" into either "the
        safety filter blocked this" or "the output hit the token ceiling",
        which are different problems for the caller.
        """
        feedback = getattr(response, 'prompt_feedback', None)
        block_reason = getattr(feedback, 'block_reason', None) if feedback else None
        if block_reason:
            raise GeminiSafetyError(
                f'The prompt was blocked by a safety filter ({block_reason}).'
            )

        candidates = getattr(response, 'candidates', None) or []
        if not candidates:
            raise GeminiResponseError('The AI returned no content.')

        finish_reason = str(getattr(candidates[0], 'finish_reason', '') or '')
        if 'SAFETY' in finish_reason.upper():
            raise GeminiSafetyError()
        if 'RECITATION' in finish_reason.upper():
            raise GeminiSafetyError(
                'The AI stopped because the output reproduced source material '
                'too closely. Try a different angle on the topic.'
            )

        try:
            text = response.text
        except Exception as exc:
            raise GeminiResponseError(
                f'The AI response could not be read ({exc}).'
            ) from exc

        if not text or not text.strip():
            if 'MAX_TOKENS' in finish_reason.upper():
                raise GeminiResponseError(
                    'The AI response hit its length limit before producing '
                    'any text. Try a shorter request.'
                )
            raise GeminiResponseError('The AI returned an empty response.')

        return text


def _plain(value):
    """Convert proto-backed containers into plain Python, recursively.

    Function-call arguments come back as ``MapComposite`` and
    ``RepeatedComposite`` -- dict-like and list-like, but neither
    JSON-serialisable nor safe to hold past the response. They are logged, stored
    in an audit trail and handed to tool functions, so they have to be real
    dicts, lists and scalars before they leave this module.

    The isinstance checks are deliberately duck-typed on ``items``/iterability
    rather than on the proto classes: those types are private to the marshalling
    layer and have moved between SDK versions.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, 'items'):
        try:
            return {str(k): _plain(v) for k, v in value.items()}
        except Exception:
            return str(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    # RepeatedComposite is iterable but is not a list or tuple.
    try:
        return [_plain(v) for v in value]
    except TypeError:
        return str(value)


_FENCE_RE = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.S | re.I)


def extract_json(text):
    """Best-effort JSON out of a model response, or ``None``.

    Four escalating strategies, because a model asked for JSON will variously
    return it clean, inside a fence, after a sentence of preamble, or with a
    trailing comma. Shared as a module function so agents that still call the
    SDK directly can use the same repair logic.
    """
    if not text:
        return None
    if isinstance(text, (dict, list)):
        return text

    candidate = text.strip()

    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        pass

    fenced = _FENCE_RE.search(candidate)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except (ValueError, TypeError):
            candidate = fenced.group(1)

    # Widest balanced object or array in the text.
    for opener, closer in (('{', '}'), ('[', ']')):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            slice_ = candidate[start:end + 1]
            try:
                return json.loads(slice_)
            except (ValueError, TypeError):
                # Trailing commas are the single most common malformation.
                repaired = re.sub(r',\s*([}\]])', r'\1', slice_)
                try:
                    return json.loads(repaired)
                except (ValueError, TypeError):
                    continue

    logger.warning('Could not extract JSON from AI response: %r', text[:200])
    return None


# Module-level singleton, mirroring the ``cache`` pattern: imported directly by
# agents, reconfigured (never replaced) by the app factory.
gemini = GeminiClient()
