"""
Built-in tools for Open WebUI.

These tools are automatically available when native function calling is enabled.

IMPORTANT: DO NOT IMPORT THIS MODULE DIRECTLY IN OTHER PARTS OF THE CODEBASE.
"""

import asyncio
import contextvars
import logging
import re
import time
from typing import Literal, Optional

from fastapi import HTTPException, Request

from open_webui.config import RAG_EMBEDDING_QUERY_PREFIX
from open_webui.env import (
    KNOWLEDGE_GREP_MAX_MATCHES,
    VIEW_FILE_DEFAULT_MAX_CHARS,
    VIEW_FILE_MAX_CHARS,
)
from open_webui.events import EVENTS, publish_event
from open_webui.models.chats import Chats, chat_search_content_query, chat_search_terms
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.users import UserModel
from open_webui.retrieval.utils import get_content_from_url
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.routers.images import (
    CreateImageForm,
    EditImageForm,
    image_edits,
    image_generations,
)
from open_webui.routers.retrieval import search_web as _search_web
from open_webui.socket.main import sio
from open_webui.tasks import stop_item_tasks
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.notifications import notify_target
from open_webui.utils.sanitize import sanitize_code

log = logging.getLogger(__name__)

MATCH_BUDGET_SECONDS = 2.0
MAX_REGEX_QUANTIFIER_COUNT = 2_000
MAX_REGEX_QUANTIFIER_EXPANSION = 100_000
_COUNTED_QUANTIFIER_RE = re.compile(r'(?<!\\)\{(\d+)(?:,\d*)?\}')


class _MatchBudgetExceeded(Exception):
    """A tool call spent its whole matching budget, so the caller reports it."""


class _MatchBudget:
    """Matching time remaining, counted only inside search() so awaits do not consume it."""

    def __init__(self):
        self.remaining = MATCH_BUDGET_SECONDS


_active_match_budget: contextvars.ContextVar[_MatchBudget | None] = contextvars.ContextVar('match_budget', default=None)


def _is_regex_pattern(pattern: str) -> bool:
    r"""Detect if a pattern looks like regex (|, .*, .+, \d, \w, \s, [...])."""
    return (
        '|' in pattern
        or '.*' in pattern
        or '.+' in pattern
        or '.?' in pattern
        or '\\d' in pattern
        or '\\w' in pattern
        or '\\s' in pattern
        or bool(re.search(r'\[.+\]', pattern))
    )


def _normalize_regex(pattern: str) -> str:
    r"""Normalize POSIX BRE patterns to Python regex (\| → |)."""
    return pattern.replace('\\|', '|').replace('\\|', '|')


def _validate_regex_quantifiers(pattern: str) -> str | None:
    """Reject counted quantifiers that make regex compilation expand too much."""
    quantifier_expansion = 1
    for quantifier in _COUNTED_QUANTIFIER_RE.finditer(pattern):
        count_text = quantifier.group(1)
        count = int(count_text) if len(count_text) <= 6 else MAX_REGEX_QUANTIFIER_COUNT + 1
        if count > MAX_REGEX_QUANTIFIER_COUNT:
            return f'Regex quantifier counts over {MAX_REGEX_QUANTIFIER_COUNT:g} are not supported'

        # ponytail: conservative expansion catches nested quantifier bombs without mirroring regex syntax.
        quantifier_expansion *= max(count, 1)
        if quantifier_expansion > MAX_REGEX_QUANTIFIER_EXPANSION:
            return 'Regex quantifiers expand too much, lower the counts'

    return None


def build_matcher(pattern: str, case_insensitive: bool = False, use_regex: bool = False) -> tuple:
    """Build a matcher function. Returns (match_fn, error_str_or_None)."""
    import regex

    if not use_regex and _is_regex_pattern(pattern):
        use_regex = True

    if use_regex:
        normalized = _normalize_regex(pattern)
        quantifier_error = _validate_regex_quantifiers(normalized)
        if quantifier_error:
            return None, quantifier_error
        try:
            re_flags = regex.IGNORECASE if case_insensitive else 0
            compiled = regex.compile(normalized, re_flags)
        except regex.error as e:
            return None, f'Invalid regex: {e}'

        budget = _active_match_budget.get() or _MatchBudget()

        def matches(line: str) -> bool:
            started = time.monotonic()
            try:
                # A negative timeout disables it, so an exhausted budget must not reach search().
                if budget.remaining <= 0:
                    raise TimeoutError
                return bool(compiled.search(line, timeout=budget.remaining))
            except TimeoutError:
                raise _MatchBudgetExceeded(f'Search exceeded {MATCH_BUDGET_SECONDS:g}s, narrow the pattern') from None
            finally:
                budget.remaining -= time.monotonic() - started

        return matches, None
    else:
        sp = pattern.lower() if case_insensitive else pattern
        return (lambda line: sp in (line.lower() if case_insensitive else line)), None


async def _has_read_access_to_file(
    file,
    user: dict,
) -> bool:
    """Check if a user can read a file via ownership, admin role, model attachment, or access grants."""
    user_id = user.get('id')
    user_role = user.get('role', 'user')
    if file.user_id == user_id or user_role == 'admin':
        return True
    from open_webui.utils.access_control.files import has_access_to_file

    return await has_access_to_file(
        file_id=file.id,
        access_type='read',
        user=UserModel(**user),
    )


# =============================================================================
# TIME UTILITIES
# =============================================================================


async def notify(
    message: str,
    target: str = '',
    title: str = '',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Send a notification to the user's configured notification target.

    :param message: Notification body.
    :param target: Optional target id or name. Empty uses the default target.
    :param title: Optional notification title.
    """
    user_id = (__user__ or {}).get('id')
    if not user_id:
        return 'Notification failed: user not found.'

    app_name = getattr(getattr(__request__, 'app', None), 'state', None)
    # LICENSE covers this Open WebUI notification identifier.
    # Do not alter, remove, obscure, or replace it except as LICENSE permits:
    # https://docs.openwebui.com/license.
    app_name = getattr(app_name, 'WEBUI_NAME', 'Open WebUI')
    try:
        result = await notify_target(user_id, message, target=target, title=title, app_name=app_name)
        return f'Notification sent to {result.get("target_id")}.'
    except Exception as e:
        return f'Notification failed: {e}'


async def get_current_timestamp(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current Unix timestamp in seconds.

    :return: JSON with current_timestamp (seconds), current_iso (UTC ISO format), and user_local_iso (user's local time)
    """
    try:
        import datetime
        from zoneinfo import ZoneInfo

        now = datetime.datetime.now(datetime.timezone.utc)
        result = {
            'current_timestamp': int(now.timestamp()),
            'current_iso': now.isoformat(),
        }

        # Include the user's local time if timezone is available
        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                user_tz = ZoneInfo(tz_name)
                user_now = now.astimezone(user_tz)
                result['user_local_iso'] = user_now.isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'get_current_timestamp error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def calculate_timestamp(
    days_ago: int = 0,
    weeks_ago: int = 0,
    months_ago: int = 0,
    years_ago: int = 0,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current Unix timestamp, optionally adjusted by days, weeks, months, or years.
    Use this to calculate timestamps for date filtering in search functions.
    Examples: "last week" = weeks_ago=1, "3 days ago" = days_ago=3, "a year ago" = years_ago=1

    :param days_ago: Number of days to subtract from current time (default: 0)
    :param weeks_ago: Number of weeks to subtract from current time (default: 0)
    :param months_ago: Number of months to subtract from current time (default: 0)
    :param years_ago: Number of years to subtract from current time (default: 0)
    :return: JSON with current_timestamp and calculated_timestamp (both in seconds)
    """
    try:
        import datetime

        from dateutil.relativedelta import relativedelta

        now = datetime.datetime.now(datetime.timezone.utc)
        current_ts = int(now.timestamp())

        # Calculate the adjusted time
        total_days = days_ago + (weeks_ago * 7)
        adjusted = now - datetime.timedelta(days=total_days)

        # Handle months and years separately (variable length)
        if months_ago > 0 or years_ago > 0:
            adjusted = adjusted - relativedelta(months=months_ago, years=years_ago)

        adjusted_ts = int(adjusted.timestamp())

        result = {
            'current_timestamp': current_ts,
            'current_iso': now.isoformat(),
            'calculated_timestamp': adjusted_ts,
            'calculated_iso': adjusted.isoformat(),
        }

        # Include the user's local time if timezone is available
        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                user_tz = ZoneInfo(tz_name)
                result['user_local_iso'] = now.astimezone(user_tz).isoformat()
                result['calculated_local_iso'] = adjusted.astimezone(user_tz).isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except ImportError:
        # Fallback without dateutil
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        current_ts = int(now.timestamp())
        total_days = days_ago + (weeks_ago * 7) + (months_ago * 30) + (years_ago * 365)
        adjusted = now - datetime.timedelta(days=total_days)
        adjusted_ts = int(adjusted.timestamp())
        result = {
            'current_timestamp': current_ts,
            'current_iso': now.isoformat(),
            'calculated_timestamp': adjusted_ts,
            'calculated_iso': adjusted.isoformat(),
        }

        tz_name = __user__.get('timezone') if __user__ else None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                user_tz = ZoneInfo(tz_name)
                result['user_local_iso'] = now.astimezone(user_tz).isoformat()
                result['calculated_local_iso'] = adjusted.astimezone(user_tz).isoformat()
                result['user_timezone'] = tz_name
            except Exception:
                pass

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'calculate_timestamp error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# WEB SEARCH TOOLS
# =============================================================================


async def search_web(
    query: str,
    count: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the public web for information. Best for current events, external references,
    or topics not covered in internal documents.

    :param query: The search query to look up
    :param count: Number of results to return (default: admin-configured value)
    :return: JSON with search results containing title, link, and snippet for each result
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        engine = await Config.get('web.search.engine')
        user = UserModel(**__user__) if __user__ else None

        configured = await Config.get('web.search.result_count')
        max_count = 5 if configured is None else configured
        count = max(1, min(count, max_count)) if count is not None else max_count

        results = await _search_web(__request__, engine, query, user)

        # Limit results
        results = results[:count] if results else []

        return JSONCodec.dumps(
            [{'title': r.title, 'link': r.link, 'snippet': r.snippet} for r in results],
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'search_web error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def fetch_url(
    url: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Fetch and extract the main text content from a web page URL.

    :param url: The URL to fetch content from
    :return: The extracted text content from the page
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        content, _ = await get_content_from_url(__request__, url)

        # Truncate if configured (WEB_FETCH_MAX_CONTENT_LENGTH)
        # Guard: content may be None if the web loader silently failed
        if content is not None:
            max_length = await Config.get('web.fetch.max_content_length')
            if max_length and max_length > 0 and len(content) > max_length:
                content = content[:max_length] + '\n\n[Content truncated...]'
        else:
            content = ''

        return content
    except Exception as e:
        log.warning(f'fetch_url error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# IMAGE GENERATION TOOLS
# =============================================================================


async def generate_image(
    prompt: str,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Generate an image based on a text prompt.

    :param prompt: A detailed description of the image to generate
    :return: Confirmation that the image was generated, or an error message
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        images = await image_generations(
            request=__request__,
            form_data=CreateImageForm(prompt=prompt),
            metadata=(
                {'channel_id': __chat_id__.removeprefix('channel:'), 'message_id': __message_id__}
                if isinstance(__chat_id__, str) and __chat_id__.startswith('channel:')
                else None
            ),
            user=user,
        )

        # Prepare file entries for the images
        image_files = [{'type': 'image', **img} for img in images]

        # Persist files to DB if chat context is available
        if is_saved_chat_id(__chat_id__) and __message_id__ and images:
            db_files = await Chats.add_message_files_by_id_and_message_id(
                __chat_id__,
                __message_id__,
                image_files,
            )
            if db_files is not None:
                image_files = db_files

        # Emit the images to the UI if event emitter is available
        if __event_emitter__ and image_files:
            await __event_emitter__(
                {
                    'type': 'chat:message:files',
                    'data': {
                        'files': image_files,
                    },
                }
            )
            # Return a message indicating the image is already displayed
            return JSONCodec.dumps(
                {
                    'status': 'success',
                    'message': 'The image has been successfully generated and is already visible to the user in the chat. You do not need to display or embed the image again - just acknowledge that it has been created.',
                    'images': images,
                },
                ensure_ascii=False,
            )

        return JSONCodec.dumps({'status': 'success', 'images': images}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'generate_image error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def edit_image(
    prompt: str,
    image_urls: list[str],
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Transform one or more existing images according to a text prompt.
    Supports targeted edits such as adding, removing, replacing, inpainting, extending, or compositing image content.

    :param prompt: A description of the transformation to apply to the provided images
    :param image_urls: Source image URLs to modify or use as composition inputs
    :return: Confirmation that the images were edited, or an error message
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        user = UserModel(**__user__) if __user__ else None

        images = await image_edits(
            request=__request__,
            form_data=EditImageForm(prompt=prompt, image=image_urls),
            metadata=(
                {'channel_id': __chat_id__.removeprefix('channel:'), 'message_id': __message_id__}
                if isinstance(__chat_id__, str) and __chat_id__.startswith('channel:')
                else None
            ),
            user=user,
        )

        # Prepare file entries for the images
        image_files = [{'type': 'image', **img} for img in images]

        # Persist files to DB if chat context is available
        if is_saved_chat_id(__chat_id__) and __message_id__ and images:
            db_files = await Chats.add_message_files_by_id_and_message_id(
                __chat_id__,
                __message_id__,
                image_files,
            )
            if db_files is not None:
                image_files = db_files

        # Emit the images to the UI if event emitter is available
        if __event_emitter__ and image_files:
            await __event_emitter__(
                {
                    'type': 'chat:message:files',
                    'data': {
                        'files': image_files,
                    },
                }
            )
            # Return a message indicating the image is already displayed
            return JSONCodec.dumps(
                {
                    'status': 'success',
                    'message': 'The edited image has been successfully generated and is already visible to the user in the chat. You do not need to display or embed the image again - just acknowledge that it has been created.',
                    'images': images,
                },
                ensure_ascii=False,
            )

        return JSONCodec.dumps({'status': 'success', 'images': images}, ensure_ascii=False)
    except Exception as e:
        log.exception(f'edit_image error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# USER INPUT TOOLS
# =============================================================================


async def ask_user(
    questions: list[dict],
    allow_other: bool = True,
    timeout_ms: int = 120_000,
    __event_call__: callable = None,
) -> str:
    """
    Ask the user clarifying questions before continuing.
    Use this when the next step depends on user intent, preference, or a tradeoff that cannot be inferred safely.

    :param questions: 1-3 question objects, each with id, header, question, and 2-3 options. Each option needs label and description.
    :param allow_other: Whether users may enter a free-form answer instead of choosing one of the options
    :param timeout_ms: How long the browser should keep the prompt open before cancelling it
    :return: JSON with status and answers keyed by question id
    """
    try:
        if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
            raise ValueError('ask_user requires 1-3 questions.')

        normalized_questions = []
        seen_ids = set()
        for index, question in enumerate(questions):
            if not isinstance(question, dict):
                raise ValueError('Each question must be an object.')

            question_id = str(question.get('id') or '').strip()[:64]
            if not question_id:
                raise ValueError('Each question requires a non-empty id.')
            if question_id in seen_ids:
                raise ValueError(f'Duplicate question id: {question_id}')
            seen_ids.add(question_id)

            options = question.get('options')
            if not isinstance(options, list) or not 2 <= len(options) <= 3:
                raise ValueError('Each question requires 2-3 options.')

            normalized_options = []
            for option in options:
                if not isinstance(option, dict):
                    raise ValueError('Each option must be an object.')

                label = str(option.get('label') or '').strip()[:80]
                description = str(option.get('description') or '').strip()[:240]
                if not label or not description:
                    raise ValueError('Each option requires a label and description.')

                normalized_options.append(
                    {
                        'label': label,
                        'description': description,
                    }
                )

            question_text = str(question.get('question') or '').strip()[:500]
            if not question_text:
                raise ValueError('Each question requires question text.')

            normalized_questions.append(
                {
                    'id': question_id,
                    'header': str(question.get('header') or '').strip()[:48] or f'Question {index + 1}',
                    'question': question_text,
                    'options': normalized_options,
                    'allow_other': bool(question.get('allow_other', allow_other)),
                }
            )

        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 60_000 <= timeout_ms <= 240_000:
            timeout_ms = 120_000

        if __event_call__ is None:
            return JSONCodec.dumps(
                {
                    'status': 'error',
                    'error': 'User input requires an active browser session with WebSocket connection.',
                },
                ensure_ascii=False,
            )

        output = await __event_call__(
            {
                'type': 'request:user_input',
                'data': {
                    'questions': normalized_questions,
                    'allow_other': allow_other,
                    'timeout_ms': timeout_ms,
                },
            }
        )

        if not isinstance(output, dict):
            return JSONCodec.dumps({'status': 'error', 'error': 'Invalid user input response.'}, ensure_ascii=False)
        if output.get('error'):
            return JSONCodec.dumps({'status': 'error', 'error': output.get('error')}, ensure_ascii=False)
        if output.get('status') == 'cancelled':
            return JSONCodec.dumps({'status': 'cancelled', 'answers': {}}, ensure_ascii=False)

        return JSONCodec.dumps(
            {
                'status': 'answered',
                'answers': output.get('answers', {}),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'ask_user error: {e}')
        return JSONCodec.dumps({'status': 'error', 'error': str(e)}, ensure_ascii=False)


# =============================================================================
# CODE INTERPRETER TOOLS
# =============================================================================


async def execute_code(
    code: str,
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __event_call__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
    __metadata__: dict = None,
) -> str:
    """
    Execute Python code in a sandboxed environment and return the output.
    Use this to perform calculations, data analysis, generate visualizations,
    or run any Python code that would help answer the user's question.

    :param code: The Python code to execute
    :return: JSON with stdout, stderr, and result from execution
    """
    from uuid import uuid4

    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    try:
        # Sanitize code (strips ANSI codes and markdown fences)
        code = sanitize_code(code)

        # Import blocked modules from config (same as middleware)
        from open_webui.config import CODE_INTERPRETER_BLOCKED_MODULES

        # Add import blocking code if there are blocked modules
        if CODE_INTERPRETER_BLOCKED_MODULES:
            import textwrap

            blocking_code = textwrap.dedent(
                f"""
                import builtins

                BLOCKED_MODULES = {CODE_INTERPRETER_BLOCKED_MODULES}

                _real_import = builtins.__import__
                def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                    if name.split('.')[0] in BLOCKED_MODULES:
                        importer_name = globals.get('__name__') if globals else None
                        if importer_name == '__main__':
                            raise ImportError(
                                f"Direct import of module {{name}} is restricted."
                            )
                    return _real_import(name, globals, locals, fromlist, level)

                builtins.__import__ = restricted_import
                """
            )
            code = blocking_code + '\n' + code

        engine = await Config.get('code_interpreter.engine', 'pyodide')
        if engine == 'pyodide':
            # Execute via frontend pyodide using bidirectional event call
            if __event_call__ is None:
                return JSONCodec.dumps(
                    {'error': 'Event call not available. WebSocket connection required for pyodide execution.'}
                )

            output = await __event_call__(
                {
                    'type': 'execute:python',
                    'data': {
                        'id': str(uuid4()),
                        'code': code,
                        'session_id': (__metadata__.get('session_id') if __metadata__ else None),
                        'files': (__metadata__.get('files', []) if __metadata__ else []),
                    },
                }
            )

            # Parse the output - pyodide returns dict with stdout, stderr, result
            if isinstance(output, dict):
                # Handle error responses from event_caller (e.g. session disconnected, timeout)
                if output.get('error') and not output.get('stdout') and not output.get('result'):
                    stderr = output['error']
                    stdout = ''
                    result = ''
                else:
                    stdout = output.get('stdout', '')
                    stderr = output.get('stderr', '')
                    result = output.get('result', '')
            else:
                stdout = ''
                stderr = ''
                result = str(output) if output else ''

        elif engine == 'jupyter':
            from open_webui.utils.code_interpreter import execute_code_jupyter

            jupyter_auth = await Config.get('code_interpreter.jupyter.auth')

            output = await execute_code_jupyter(
                await Config.get('code_interpreter.jupyter.url'),
                code,
                (await Config.get('code_interpreter.jupyter.auth_token') if jupyter_auth == 'token' else None),
                (await Config.get('code_interpreter.jupyter.auth_password') if jupyter_auth == 'password' else None),
                await Config.get('code_interpreter.jupyter.timeout'),
            )

            stdout = output.get('stdout', '')
            stderr = output.get('stderr', '')
            result = output.get('result', '')

        else:
            return JSONCodec.dumps({'error': f'Unknown code interpreter engine: {engine}'})

        # Handle image outputs (base64 encoded) - replace with uploaded URLs
        # Get actual user object for image upload (upload_image requires user.id attribute)
        if __user__ and __user__.get('id'):
            from open_webui.models.users import Users
            from open_webui.utils.files import get_image_url_from_base64

            user = await Users.get_user_by_id(__user__['id'])

            # Extract and upload images from stdout
            if stdout and isinstance(stdout, str):
                stdout_lines = stdout.split('\n')
                for idx, line in enumerate(stdout_lines):
                    if 'data:image/png;base64' in line:
                        image_url = await get_image_url_from_base64(
                            __request__,
                            line,
                            __metadata__ or {},
                            user,
                        )
                        if image_url:
                            stdout_lines[idx] = f'![Output Image]({image_url})'
                stdout = '\n'.join(stdout_lines)

            # Extract and upload images from result
            if result and isinstance(result, str):
                result_lines = result.split('\n')
                for idx, line in enumerate(result_lines):
                    if 'data:image/png;base64' in line:
                        image_url = await get_image_url_from_base64(
                            __request__,
                            line,
                            __metadata__ or {},
                            user,
                        )
                        if image_url:
                            result_lines[idx] = f'![Output Image]({image_url})'
                result = '\n'.join(result_lines)

        response = {
            'status': 'success',
            'stdout': stdout,
            'stderr': stderr,
            'result': result,
        }

        return JSONCodec.dumps(response, ensure_ascii=False)
    except Exception as e:
        log.exception(f'execute_code error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# CHATS TOOLS
# =============================================================================


async def search_chats(
    query: str,
    count: int = 5,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Search the user's previous chat conversations by title and message content,
    excluding the current chat. Helpful for finding details from earlier
    conversations when they are not already visible in the current context.
    Exact phrase matches are preferred, and descriptive keyword queries are
    supported.

    :param query: Exact phrase or descriptive keyword query to find matching previous chats
    :param count: Maximum number of results to return (default: 5)
    :param start_timestamp: Only include chats updated after this Unix timestamp (seconds)
    :param end_timestamp: Only include chats updated before this Unix timestamp (seconds)
    :return: JSON with matching chats containing id, title, updated_at, and content snippet
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chats = await Chats.get_chats_by_user_id_and_search_text(
            user_id=user_id,
            search_text=query,
            include_archived=False,
            skip=0,
            limit=count * 3,  # Fetch more for filtering
        )

        results = []
        for chat in chats:
            # Skip the current chat to avoid showing it in search results
            if __chat_id__ and chat.id == __chat_id__:
                continue

            # Apply date filters (updated_at is in seconds)
            if start_timestamp and chat.updated_at < start_timestamp:
                continue
            if end_timestamp and chat.updated_at > end_timestamp:
                continue

            # Find a matching message snippet
            snippet = ''
            messages = (getattr(chat, 'chat', None) or {}).get('history', {}).get('messages', {})
            if not messages:
                messages = (getattr(chat, 'chat', None) or {}).get('messages', {}) or {}
            if isinstance(messages, list):
                messages = {str(idx): message for idx, message in enumerate(messages)}

            lower_query = chat_search_content_query(query)
            needles = list(dict.fromkeys([lower_query, *chat_search_terms(lower_query)])) if lower_query else []

            for needle in needles:
                for msg_id, msg in messages.items():
                    content = msg.get('content', '') if isinstance(msg, dict) else ''
                    if isinstance(content, str) and needle in content.lower():
                        idx = content.lower().find(needle)
                        start = max(0, idx - 50)
                        end = min(len(content), idx + len(needle) + 100)
                        snippet = (
                            ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
                        )
                        break
                if snippet:
                    break

            title = chat.title or ''
            if not snippet and any(needle in title.lower() for needle in needles):
                snippet = f'Title match: {title}'

            results.append(
                {
                    'id': chat.id,
                    'title': chat.title,
                    'snippet': snippet,
                    'updated_at': chat.updated_at,
                }
            )

            if len(results) >= count:
                break

        return JSONCodec.dumps(results, ensure_ascii=False)
    except Exception as e:
        log.exception(f'search_chats error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_chat(
    chat_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the full conversation history of a chat by its ID after a relevant
    previous chat has been identified.

    :param chat_id: The ID of the chat to retrieve
    :return: JSON with the chat's id, title, and messages
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        user_id = __user__.get('id')

        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)

        if not chat:
            return JSONCodec.dumps({'error': 'Chat not found or access denied'})

        # Extract messages from history
        messages = []
        history = chat.chat.get('history', {})
        msg_dict = history.get('messages', {})

        # Build message chain from currentId
        current_id = history.get('currentId')
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            msg = msg_dict.get(current_id)
            if msg:
                messages.append(
                    {
                        'role': msg.get('role', ''),
                        'content': msg.get('content', ''),
                    }
                )
            current_id = msg.get('parentId') if msg else None

        # Reverse to get chronological order
        messages.reverse()

        return JSONCodec.dumps(
            {
                'id': chat.id,
                'title': chat.title,
                'messages': messages,
                'updated_at': chat.updated_at,
                'created_at': chat.created_at,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'view_chat error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# SUB-AGENT TOOL
# =============================================================================


async def delegate_task(
    task: str,
    context: str = '',
    file_ids: list[str] | None = None,
    background: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __metadata__: dict = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Delegate focused work to a parallel sub-agent using the current model and tools.

    :param task: The specific task for the sub-agent to complete
    :param context: Relevant context, decisions, or file paths for the task
    :param file_ids: Attached file IDs the sub-agent needs. Use this for images or files;
        do not put file IDs only in context.
    :param background: Return immediately and continue this chat when the sub-agent finishes
    :return: Foreground result text, or a JSON dispatch handle for background work
    """
    if __request__ is None:
        return 'Error: request context not available.'
    if getattr(__request__.state, 'internal', False) is True:
        return 'Error: sub-agents cannot delegate recursively.'

    from open_webui.utils.subagents import delegate

    return await delegate(
        task,
        context,
        background,
        file_ids=file_ids,
        request=__request__,
        user_data=__user__ or {},
        metadata=__metadata__ or {},
        parent_chat_id=__chat_id__ or '',
        parent_message_id=__message_id__,
    )


async def _get_accessible_chat_files(
    files: Optional[list[dict]],
    user: dict,
    file_id: Optional[str] = None,
) -> list[tuple[dict, object]]:
    from open_webui.models.files import Files

    accessible = []
    seen = set()

    for item in files or []:
        if not isinstance(item, dict) or item.get('type', 'file') != 'file':
            continue
        fid = item.get('id') or item.get('url') or ''
        if (
            not isinstance(fid, str)
            or not fid
            or fid in seen
            or fid.startswith(('http://', 'https://', 'data:'))
            or (file_id and fid != file_id)
        ):
            continue
        normalized = {**item, 'id': fid, 'type': 'file'}
        if 'name' not in normalized and item.get('filename'):
            normalized['name'] = item.get('filename')
        seen.add(fid)

        file = await Files.get_file_by_id(fid)
        if file and await _has_read_access_to_file(file, user):
            accessible.append((normalized, file))

    return accessible


def _grep_file_models(
    files_to_search: list,
    pattern: str,
    case_insensitive: bool = False,
    count_only: bool = False,
) -> str:
    matches, err = build_matcher(pattern, case_insensitive)
    if err:
        return JSONCodec.dumps({'error': err})

    results = []
    total_matches = 0
    counts = []

    for file in files_to_search:
        content = ''
        if file.data:
            content = file.data.get('content', '')
        if not content:
            continue

        lines = content.split('\n')
        file_matches = 0

        for i, line in enumerate(lines, 1):
            if matches(line):
                file_matches += 1
                total_matches += 1
                if not count_only and len(results) < KNOWLEDGE_GREP_MAX_MATCHES:
                    results.append(f'{file.id}  {file.filename}:{i}: {line}')

        if file_matches > 0 and count_only:
            counts.append(f'{file.id}  {file.filename}: {file_matches}')

    if count_only:
        if not counts:
            return f'No matches for "{pattern}"'
        return '\n'.join(counts) + f'\n[{total_matches} total matches]'

    if not results:
        return f'No matches for "{pattern}"'

    output = '\n'.join(results)
    if total_matches > KNOWLEDGE_GREP_MAX_MATCHES:
        output += f'\n[{KNOWLEDGE_GREP_MAX_MATCHES} of {total_matches} matches shown — use file_id to narrow]'
    return output


async def list_chat_files(
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
) -> str:
    """
    List files attached to the current chat.

    :return: JSON with attached chat files containing id, filename, content type, size, and updated time when available
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        files = []
        for item, file in await _get_accessible_chat_files(__files__, __user__):
            file_info = {
                'id': file.id,
                'filename': file.filename,
                'name': item.get('name') or file.filename,
                'type': item.get('type', 'file'),
                'updated_at': file.updated_at,
            }
            content_type = item.get('content_type') or (file.meta or {}).get('content_type')
            size = item.get('size') or (file.meta or {}).get('size')
            if content_type:
                file_info['content_type'] = content_type
            if size:
                file_info['size'] = size
            files.append(file_info)

        return JSONCodec.dumps(files, ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def grep_chat_files(
    pattern: str,
    file_id: Optional[str] = None,
    case_insensitive: bool = False,
    count_only: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
) -> str:
    """
    Search exact text across files attached to the current chat.
    Pass file_id from the attached_files block to search one file.

    :param pattern: The text pattern to search for
    :param file_id: Optional attached file ID to search within a single file
    :param case_insensitive: If true, ignore case when matching
    :param count_only: If true, return only match counts per file
    :return: Matching lines with file IDs, filenames, and line numbers
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if not pattern or not pattern.strip():
        return JSONCodec.dumps({'error': 'Pattern is required'})

    if isinstance(file_id, str) and file_id.lower() in ('none', 'null', ''):
        file_id = None

    try:
        attached_ids = set()
        for item in __files__ or []:
            if not isinstance(item, dict) or item.get('type', 'file') != 'file':
                continue
            fid = item.get('id') or item.get('url')
            if isinstance(fid, str) and fid and not fid.startswith(('http://', 'https://', 'data:')):
                attached_ids.add(fid)

        if not attached_ids:
            return JSONCodec.dumps({'error': 'No files are attached to this chat'})
        if file_id and file_id not in attached_ids:
            return JSONCodec.dumps({'error': 'File not found'})

        files_to_search = [file for _, file in await _get_accessible_chat_files(__files__, __user__, file_id)]
        if not files_to_search:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        return _grep_file_models(files_to_search, pattern, case_insensitive, count_only)
    except Exception as e:
        log.exception(f'grep_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def query_chat_files(
    query: str,
    file_id: Optional[str] = None,
    count: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
    __files__: list[dict] = None,
) -> str:
    """
    Search files attached to the current chat using semantic/vector search.
    Pass file_id from the attached_files block to search one file, or omit it to search all attached chat files.

    :param query: The search query to find semantically relevant content
    :param file_id: Optional attached file ID to search within a single file
    :param count: Maximum number of results to return, capped by the server RAG top k
    :return: JSON with relevant chunks containing content, source filename, and relevance score
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    if isinstance(file_id, str) and file_id.lower() in ('none', 'null', ''):
        file_id = None
    if isinstance(count, str):
        if count.lower() in ('none', 'null', ''):
            count = None
        else:
            try:
                count = int(count)
            except ValueError:
                count = None

    try:
        from open_webui.retrieval.utils import get_sources_from_items

        attached_ids = set()
        for item in __files__ or []:
            if not isinstance(item, dict) or item.get('type', 'file') != 'file':
                continue
            fid = item.get('id') or item.get('url')
            if isinstance(fid, str) and fid and not fid.startswith(('http://', 'https://', 'data:')):
                attached_ids.add(fid)

        if not attached_ids:
            return JSONCodec.dumps({'error': 'No files are attached to this chat'})
        if file_id and file_id not in attached_ids:
            return JSONCodec.dumps({'error': 'File not found'})

        accessible = await _get_accessible_chat_files(__files__, __user__, file_id)
        if not accessible:
            return JSONCodec.dumps({'error': 'No accessible files found'})

        file_items = [{**item} for item, _ in accessible]
        rag_config = await Config.get_many(
            'rag.top_k',
            'rag.top_k_reranker',
            'rag.relevance_threshold',
            'rag.hybrid_bm25_weight',
            'rag.enable_hybrid_search',
            'rag.full_context',
        )
        top_k = rag_config.get('rag.top_k') or 5
        count = top_k if count is None else max(1, min(count, top_k))
        full_context = all(item.get('context') == 'full' for item in file_items) or rag_config.get('rag.full_context')

        embedding_function = getattr(__request__.app.state, 'EMBEDDING_FUNCTION', None)
        if not embedding_function and not full_context:
            return JSONCodec.dumps({'error': 'Embedding function not configured'})

        user_model = UserModel(**__user__)
        sources = await get_sources_from_items(
            request=__request__,
            items=file_items,
            queries=[query],
            embedding_function=(
                lambda queries, prefix: (
                    embedding_function(queries, prefix=prefix, user=user_model) if embedding_function else None
                )
            ),
            k=count,
            reranking_function=(
                (lambda q, docs: __request__.app.state.RERANKING_FUNCTION(q, docs, user=user_model))
                if getattr(__request__.app.state, 'RERANKING_FUNCTION', None)
                else None
            ),
            k_reranker=rag_config.get('rag.top_k_reranker'),
            r=rag_config.get('rag.relevance_threshold'),
            hybrid_bm25_weight=rag_config.get('rag.hybrid_bm25_weight'),
            hybrid_search=rag_config.get('rag.enable_hybrid_search'),
            full_context=full_context,
            user=user_model,
        )

        chunks = []
        for source in sources or []:
            documents = source.get('document') or []
            metadatas = source.get('metadata') or []
            distances = source.get('distances') or []
            source_info = source.get('source') or {}

            for idx, doc in enumerate(documents):
                metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
                chunk = {
                    'content': doc,
                    'source': metadata.get('source', metadata.get('name', source_info.get('name', 'Unknown'))),
                    'file_id': metadata.get('file_id', source_info.get('id', '')),
                }
                if idx < len(distances):
                    chunk['distance'] = distances[idx]
                chunks.append(chunk)

        return JSONCodec.dumps(chunks[:count], ensure_ascii=False)
    except Exception as e:
        log.exception(f'query_chat_files error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def view_file(
    file_id: str,
    offset: int = 0,
    max_chars: int = VIEW_FILE_DEFAULT_MAX_CHARS,
    line_numbers: bool = False,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the content of a file by its ID. Supports pagination for large files.

    :param file_id: The ID of the file to retrieve
    :param offset: Character offset to start reading from (default: 0)
    :param max_chars: Maximum characters to return (a server-side hard cap applies)
    :param line_numbers: If true, prefix each line with its 1-indexed line number
    :param start_line: Optional 1-indexed start line (overrides offset/max_chars when set)
    :param end_line: Optional 1-indexed end line (inclusive)
    :return: JSON with the file's id, filename, content, and pagination metadata if truncated
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    # Coerce parameters from LLM tool calls (may come as strings)
    if isinstance(offset, str):
        try:
            offset = int(offset)
        except ValueError:
            offset = 0
    if isinstance(max_chars, str):
        try:
            max_chars = int(max_chars)
        except ValueError:
            max_chars = VIEW_FILE_DEFAULT_MAX_CHARS

    # Enforce hard cap
    max_chars = min(max(max_chars, 1), VIEW_FILE_MAX_CHARS)
    offset = max(offset, 0)

    try:
        from open_webui.models.files import Files

        file = await Files.get_file_by_id(file_id)
        if not file:
            return JSONCodec.dumps({'error': 'File not found'})

        if not await _has_read_access_to_file(file, __user__):
            return JSONCodec.dumps({'error': 'File not found'})

        content = ''
        if file.data:
            content = file.data.get('content', '')

        total_chars = len(content)

        # Line-based addressing (overrides char-based offset/max_chars)
        if start_line is not None:
            all_lines = content.split('\n')
            total_lines = len(all_lines)
            s = max(1, int(start_line)) - 1  # 1-indexed to 0-indexed
            e = min(total_lines, int(end_line) if end_line else s + 100)
            selected = all_lines[s:e]
            sliced = '\n'.join(f'{s + i + 1}: {line}' for i, line in enumerate(selected))
            is_truncated = e < total_lines
            result = {
                'id': file.id,
                'filename': file.filename,
                'content': sliced,
                'updated_at': file.updated_at,
                'created_at': file.created_at,
                'total_lines': total_lines,
                'showing_lines': f'{s + 1}-{e}',
            }
            if is_truncated:
                result['truncated'] = True
                result['next_start_line'] = e + 1
            return JSONCodec.dumps(result, ensure_ascii=False)

        sliced = content[offset : offset + max_chars]
        is_truncated = (offset + len(sliced)) < total_chars

        if line_numbers:
            start_ln = content[:offset].count('\n') + 1
            lines = sliced.split('\n')
            sliced = '\n'.join(f'{start_ln + i}: {line}' for i, line in enumerate(lines))

        result = {
            'id': file.id,
            'filename': file.filename,
            'content': sliced,
            'updated_at': file.updated_at,
            'created_at': file.created_at,
        }

        if is_truncated or offset > 0:
            result['truncated'] = is_truncated
            result['total_chars'] = total_chars
            result['returned_chars'] = len(sliced)
            result['offset'] = offset
            if is_truncated:
                result['next_offset'] = offset + len(sliced)

        return JSONCodec.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.exception(f'view_file error: {e}')
        return JSONCodec.dumps({'error': str(e)})


# =============================================================================
# TASK MANAGEMENT TOOLS
# =============================================================================

from typing import Literal

from pydantic import BaseModel, Field

VALID_TASK_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled'}


class TaskItem(BaseModel):
    id: Optional[str] = Field(None, description='Unique identifier for the task. Auto-generated if omitted.')
    content: str = Field(..., description='Task description.')
    status: Literal['pending', 'in_progress', 'completed', 'cancelled'] = Field('pending', description='Task status.')


def _task_summary(all_tasks: list[dict]) -> dict:
    """Build summary counts for a task list."""
    pending = sum(1 for t in all_tasks if t['status'] == 'pending')
    in_progress = sum(1 for t in all_tasks if t['status'] == 'in_progress')
    completed = sum(1 for t in all_tasks if t['status'] == 'completed')
    cancelled = sum(1 for t in all_tasks if t['status'] == 'cancelled')
    return {
        'total': len(all_tasks),
        'pending': pending,
        'in_progress': in_progress,
        'completed': completed,
        'cancelled': cancelled,
    }


async def _emit_tasks(event_emitter, all_tasks: list[dict]):
    """Persist task state to the UI."""
    if event_emitter:
        await event_emitter(
            {
                'type': 'chat:message:tasks',
                'data': {
                    'tasks': all_tasks,
                },
            }
        )


async def create_tasks(
    tasks: list[TaskItem],
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a visible task checklist for multi-step work so progress can be shown in chat.

    :param tasks: List of task items. Each item: content (string, required), status (pending|in_progress|completed|cancelled, default pending), id (optional, auto-generated).
    :return: JSON with the full task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        all_tasks = []
        for idx, task in enumerate(tasks):
            if hasattr(task, 'model_dump'):
                d = task.model_dump(exclude_none=True)
            elif isinstance(task, dict):
                d = task
            else:
                d = dict(task)

            content = str(d.get('content', '')).strip()
            if not content:
                continue

            item_id = str(d.get('id', '') or '').strip() or str(idx + 1)
            status = str(d.get('status', 'pending')).strip().lower()
            if status not in VALID_TASK_STATUSES:
                status = 'pending'

            all_tasks.append({'id': item_id, 'content': content, 'status': status})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'tasks error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_task(
    id: str,
    status: str = 'completed',
    __chat_id__: str = None,
    __message_id__: str = None,
    __event_emitter__: callable = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Mark a single visible task item as completed, in_progress, pending, or cancelled.

    :param id: The task ID to update
    :param status: New status: completed, in_progress, pending, or cancelled (default: completed)
    :return: JSON with the updated task list and summary counts
    """
    if not is_saved_chat_id(__chat_id__):
        return JSONCodec.dumps({'error': 'Saved chat context not available'})

    try:
        status = status.strip().lower()
        if status not in VALID_TASK_STATUSES:
            return JSONCodec.dumps(
                {'error': f'Invalid status: {status}. Must be one of: {", ".join(sorted(VALID_TASK_STATUSES))}'}
            )

        all_tasks = await Chats.get_chat_tasks_by_id(__chat_id__)

        found = False
        for task in all_tasks:
            if task['id'] == id:
                task['status'] = status
                found = True
                break

        if not found:
            return JSONCodec.dumps({'error': f'Task with id "{id}" not found'})

        await Chats.update_chat_tasks_by_id(__chat_id__, all_tasks)
        await _emit_tasks(__event_emitter__, all_tasks)

        return JSONCodec.dumps(
            {'tasks': all_tasks, 'summary': _task_summary(all_tasks)},
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_task_status error: {e}')
        return JSONCodec.dumps({'error': str(e)})
