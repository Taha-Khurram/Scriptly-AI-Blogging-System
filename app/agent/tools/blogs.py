"""Blog tools -- write, edit, delete, list and read posts.

The tools that touch real user data, which makes two things non-negotiable here
in a way they are not in :mod:`app.agent.tools.research`.

**Authority comes from the context, never from an argument.** None of these
tools takes a ``user_id``. There is no parameter a model could fill in wrongly
or maliciously, and :func:`_visible_blog` re-checks ownership on every single
read and write against ``ctx.user_id``. This is stricter than the existing
dashboard routes, which is deliberate -- see the note on ``_visible_blog``.

**Destruction is two-phase and the tool cannot complete it.** ``delete_blog``
records what *would* be deleted and returns a token. Nothing is removed. The
deletion happens in :func:`execute_confirmed_delete`, which is reachable only
from the confirm endpoint, only with a single-use token, and only from the user
whose click produced it. An agent that can delete a post because it decided the
user probably meant it is not a feature.

The write path reuses the existing pipeline
-------------------------------------------

``create_blog`` does not reimplement generation. It calls the same
:class:`~app.agents.content_agent.ContentAgent`, the same
:class:`~app.agents.formatting_agent.FormattingAgent`, the same
:class:`~app.agents.category_agent.CategoryAgent` and the same
``create_draft`` that ``/api/generate`` has always called, in the same order,
producing the same document shape. What changes is where the topic came from:
an approved outline instead of a raw prompt. Drafts, the approval queue, the
public site and the SEO tools therefore work on chat-written posts on day one,
with no migration.
"""
from __future__ import annotations

import time

from app.agents.category_agent import CategoryAgent
from app.agents.content_agent import ContentAgent
from app.agents.edit_agent import EditAgent, summarise_change
from app.agents.formatting_agent import FormattingAgent
from app.agents.outline_agent import resolve_length, resolve_tone
from app.core.logging import get_logger
from app.services.gemini_client import GeminiError, gemini

logger = get_logger(__name__)

# Rows one listing may return. The list is read by a model and rendered as a
# card; past a dozen it is a table, and a table belongs on the All Blogs page.
MAX_LIST_LIMIT = 25
DEFAULT_LIST_LIMIT = 10

# How much of a post is returned to the *model* by get_blog. The full body goes
# to the UI card; the model gets a working excerpt, because a 12 KB post in the
# tool result is re-sent on every subsequent turn of the loop and edit
# instructions do not need the whole text to be written.
MAX_MODEL_EXCERPT_CHARS = 3_000

STATUSES = ('DRAFT', 'UNDER_REVIEW', 'PUBLISHED', 'SCHEDULED')


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def _visible_blog(ctx, blog_id):
    """``(blog, error)`` -- the post if this user may act on it, else why not.

    The rule: an author may act on their own posts, and an ADMIN may act on any
    post belonging to their own site. ``site_owner_id`` is the tenant boundary
    the rest of the schema is built on, so an admin of one site cannot reach
    another site's drafts even with a valid id.

    **This is stricter than the dashboard.** ``/api/update_blog/<id>`` and
    ``/api/delete_blog/<id>`` in ``blog_routes.py`` perform no ownership check
    at all -- any signed-in user can edit or delete any post by id. That is a
    pre-existing gap in those routes and it is flagged in the handover notes
    rather than changed here, because tightening them touches the approval-queue
    flow where an admin edits another author's post. What matters for this
    module is that the agent does not inherit the gap: a natural-language
    interface that will happily act on any id it is given is a far easier gap to
    walk into than a REST endpoint.
    """
    if not blog_id:
        return None, {
            'ok': False,
            'error': 'no_blog',
            'message': (
                'No post is in focus and none was named. Ask which post they '
                'mean, or call list_blogs to show them their posts.'
            ),
        }

    blog = ctx.db.get_blog_by_id(str(blog_id))
    if not blog:
        return None, {
            'ok': False,
            'error': 'not_found',
            'message': f'No post with id {blog_id} exists. It may already be deleted.',
        }

    if blog.get('author_id') == ctx.user_id:
        return blog, None

    if ctx.user_role == 'ADMIN':
        try:
            site_owner = ctx.db.get_site_owner_for_user(ctx.user_id)
        except Exception:
            logger.exception('Could not resolve site owner for %s', ctx.user_id)
            site_owner = None
        if site_owner and blog.get('site_owner_id') == site_owner:
            return blog, None

    # Not-found and not-yours answer identically, so a model asked to "delete
    # blog abc123" cannot be used to enumerate other tenants' post ids.
    return None, {
        'ok': False,
        'error': 'not_found',
        'message': f'No post with id {blog_id} exists. It may already be deleted.',
    }


def _scope_ids(ctx):
    """Author ids whose posts this user may list.

    Mirrors ``blogs_listing_routes._team_scope``: an admin sees their team, a
    writer sees themselves. Falls back to the user alone if the team lookup
    fails -- a listing that shows fewer posts is a degradation; one that shows
    somebody else's is a breach.
    """
    if ctx.user_role != 'ADMIN':
        return [ctx.user_id]
    try:
        sub_users = ctx.db.get_my_sub_users(ctx.user_id) or []
    except Exception:
        logger.exception('Team lookup failed for %s', ctx.user_id)
        return [ctx.user_id]
    ids = [ctx.user_id]
    ids.extend(u.get('uid') for u in sub_users if u.get('uid'))
    return list(dict.fromkeys(ids))


def _markdown_of(blog):
    """The post's markdown, whatever shape the document stores it in.

    Blog content has been three shapes across this schema's life: a bare
    string, a dict with ``body``, and a dict with ``markdown``/``html``/``toc``.
    All three exist in live data, so every reader has to handle all three --
    doing it here means the tools do not each get it slightly wrong.
    """
    content = blog.get('content')
    if isinstance(content, dict):
        return content.get('markdown') or content.get('body') or content.get('text') or ''
    return content or ''


# ---------------------------------------------------------------------------
# create_blog
# ---------------------------------------------------------------------------

def create_blog(ctx, outline_id=None, tone=None, length=None, keywords=None,
                **_ignored):
    """Write the full post from an **approved** outline and save it as a draft.

    The approval check is the first thing that happens and there is no argument
    that bypasses it. A refusal comes back as a normal tool result, not an
    exception, so the agent can turn it into "I need you to approve the outline
    first" -- which is the sentence the user needs.
    """
    outline_id = (outline_id or ctx.focus_outline_id or '').strip()
    if not outline_id:
        return {
            'ok': False,
            'error': 'no_outline',
            'message': (
                'There is no outline for this post. Call create_outline first '
                'and wait for the user to approve it. You may not write a post '
                'without an approved outline.'
            ),
        }

    outline = ctx.db.get_outline(outline_id, ctx.user_id)
    if not outline:
        return {
            'ok': False,
            'error': 'outline_not_found',
            'message': 'That outline does not exist. Draft a new one for approval.',
        }

    status = outline.get('status')
    if status != 'approved':
        # The gate. Everything else in this flow is a convenience; this is the
        # requirement. It is checked against stored state, so no amount of
        # conversational pressure moves it.
        ctx.add_card('approval_required', {
            'outline_id': outline_id,
            'title': outline.get('title', ''),
            'status': status or 'unknown',
        })
        return {
            'ok': False,
            'error': 'outline_not_approved',
            'outline_status': status or 'unknown',
            'message': (
                'REFUSED: this outline is not approved '
                f'(status: {status or "unknown"}), so no post was written. Ask '
                'the user to approve it — they can use the Approve button on '
                'the outline card, or say so in the chat. Do not call this tool '
                'again until submit_outline_approval reports approved: true.'
                if status != 'superseded' else
                'REFUSED: that outline was replaced by a revision. Point the '
                'user at the current version and get that one approved.'
            ),
        }

    tone = tone or outline.get('tone') or 'professional'
    length = length or outline.get('length') or 'medium'
    keywords = keywords or outline.get('keywords') or []
    word_range, _, _ = resolve_length(length)

    ctx.emit('status', stage='writing', label='Writing the post')
    ctx.emit('thought', text=(
        f'Writing "{outline.get("title", "the post")}" — '
        f'{len(outline.get("sections") or [])} sections, {word_range}, '
        f'{tone} tone.'
    ))

    started = time.time()

    try:
        content_data = ContentAgent().stream_from_outline(
            {
                'title': outline.get('title', ''),
                'angle': outline.get('angle', ''),
                'audience': outline.get('audience', ''),
                'sections': outline.get('sections') or [],
                'sources': outline.get('sources') or [],
            },
            tone=resolve_tone(tone),
            keywords=keywords,
            word_range=word_range,
            # Straight into the turn log, so the user watches the post being
            # written instead of watching a spinner for ninety seconds.
            on_content=lambda chunk: ctx.emit('draft', text=chunk),
        )
    except GeminiError as exc:
        return {
            'ok': False,
            'error': getattr(exc, 'code', 'ai_error'),
            'message': (
                (getattr(exc, 'message', None) or str(exc))
                + ' The outline is still approved, so you can retry writing '
                'without asking for approval again.'
            ),
        }
    except Exception as exc:
        logger.exception('Writing from outline %s failed', outline_id)
        return {
            'ok': False,
            'error': 'write_failed',
            'message': f'Writing the post failed ({exc}). The outline is still approved.',
        }

    markdown_text = content_data.get('markdown') or ''
    title = outline.get('title') or (outline.get('topic') or 'Untitled')

    ctx.emit('status', stage='formatting', label='Formatting and filing')

    formatted = FormattingAgent().format_blog(content=markdown_text, title=title)

    # Category, from the user's existing taxonomy where one fits. `use_cache`
    # because this is the same read the create screen does and the categories
    # do not change mid-conversation.
    try:
        categories = ctx.db.get_all_categories(ctx.user_id, limit=50, use_cache=True)
        category = CategoryAgent().categorize_blog(title, markdown_text,
                                                   categories=categories)
    except Exception:
        logger.exception('Categorisation failed; filing without a category')
        category = ''

    stats = formatted.get('statistics') or {}

    # The same document shape `/api/generate` writes. Deliberately identical:
    # Drafts, the approval queue, the public site and the SEO tools all read
    # this shape, and a chat-written post that is subtly different from a
    # create-screen post is a bug in every one of those screens at once.
    document = {
        'title': title,
        'outline': [s.get('heading', '') for s in (outline.get('sections') or [])],
        'content': {
            'body': markdown_text,
            'html': formatted.get('html', ''),
            'markdown': markdown_text,
            'toc': formatted.get('toc', []),
            'toc_html': formatted.get('toc_html', ''),
        },
        'formatting': {
            'toc': formatted.get('toc', []),
            'toc_html': formatted.get('toc_html', ''),
            'reading_time': formatted.get('reading_time_text', ''),
            'reading_time_minutes': formatted.get('reading_time_minutes', 0),
            'statistics': stats,
            'has_code': formatted.get('has_code_blocks', False),
            'has_images': formatted.get('has_images', False),
            'has_tables': formatted.get('has_tables', False),
        },
        'seo': {'enabled': False},
        'category': category,
        # Always a draft. The agent writes; publishing stays a human action
        # taken on the Drafts or Approval screen, exactly as it is for the
        # create screen's default destination.
        'status': 'DRAFT',
        'author_id': ctx.user_id,
        'author': ctx.user_name,
        'metadata': {
            'word_count': stats.get('word_count') or len(markdown_text.split()),
            'model_used': gemini.default_model,
            'status': 'success',
            'seo_enabled': False,
            'humanized': False,
            'streamed': bool(content_data.get('streamed')),
            'partial': bool(content_data.get('partial')),
            # Provenance: which conversation and which approved plan produced
            # this post. The only durable answer to "why does this post exist".
            'source': 'chat_agent',
            'chat_session_id': ctx.session_id,
            'outline_id': outline_id,
            'approved_at': outline.get('approved_at', ''),
            'approved_via': outline.get('approved_via', ''),
        },
    }

    blog_id = ctx.db.create_draft(document, ctx.user_id)
    if not blog_id:
        return {
            'ok': False,
            'error': 'save_failed',
            'message': (
                'The post was written but could not be saved. Tell the user, '
                'and offer to try saving again.'
            ),
        }

    ctx.db.mark_outline_written(outline_id, ctx.user_id, blog_id)
    ctx.focus_blog(blog_id, title)
    ctx.created_blog_ids.append(blog_id)

    try:
        ctx.db.log_activity(
            user_id=ctx.user_id, user_name=ctx.user_name, type='generated',
            action_text='wrote a blog with the agent as DRAFT',
            blog_title=title,
        )
    except Exception:
        logger.exception('Activity log failed for chat-written blog %s', blog_id)

    # Also recorded in the generation history, so /history shows chat-written
    # posts beside create-screen ones. They are the same kind of event and
    # splitting them across two screens would make neither trustworthy.
    try:
        ctx.db.record_generation(
            ctx.user_id, outline.get('topic') or title,
            user_name=ctx.user_name,
            destination='draft',
            status='completed',
            title=title,
            category=category,
            blog_id=blog_id,
            blog_status='DRAFT',
            blog_slug=document.get('slug', ''),
            word_count=stats.get('word_count') or len(markdown_text.split()),
            section_count=len(formatted.get('toc') or []),
            reading_time=formatted.get('reading_time_text', ''),
            model_used=gemini.default_model,
            duration_seconds=time.time() - started,
            excerpt=markdown_text,
            thoughts=[{'text': f'Written from an approved outline '
                               f'({len(outline.get("sections") or [])} sections).',
                       'kind': 'note'}],
        )
    except Exception:
        logger.exception('Generation transcript failed for chat blog %s', blog_id)

    word_count = stats.get('word_count') or len(markdown_text.split())

    ctx.add_card('blog', {
        'blog_id': blog_id,
        'title': title,
        'status': 'DRAFT',
        'category': category,
        'word_count': word_count,
        'reading_time': formatted.get('reading_time_text', ''),
        'section_count': len(formatted.get('toc') or []),
        'excerpt': markdown_text[:600],
        'partial': bool(content_data.get('partial')),
    })

    return {
        'ok': True,
        'blog_id': blog_id,
        'title': title,
        'status': 'DRAFT',
        'category': category,
        'word_count': word_count,
        'section_count': len(formatted.get('toc') or []),
        'reading_time': formatted.get('reading_time_text', ''),
        'partial': bool(content_data.get('partial')),
        'message': (
            f'Saved as a draft ({word_count} words). '
            + ('The stream was cut short, so the post may be unfinished — say '
               'so. ' if content_data.get('partial') else '')
            + 'Tell the user what you wrote and that it is in their Drafts. Do '
              'not restate the whole post; they can see it.'
        ),
    }


# ---------------------------------------------------------------------------
# edit_blog
# ---------------------------------------------------------------------------

def edit_blog(ctx, instructions=None, blog_id=None, apply_title=False, **_ignored):
    """Apply one natural-language edit to an existing post.

    ``blog_id`` is optional: without it the edit lands on the post in focus,
    which is what makes "make the intro punchier" work as a follow-up.
    """
    instructions = (instructions or '').strip()
    if not instructions:
        return {
            'ok': False,
            'error': 'missing_instructions',
            'message': 'Say what to change about the post.',
        }

    blog, error = _visible_blog(ctx, ctx.resolve_blog_id(blog_id))
    if error:
        return error

    markdown_text = _markdown_of(blog)
    if not markdown_text.strip():
        return {
            'ok': False,
            'error': 'empty_post',
            'message': 'That post has no content to edit.',
        }

    title = blog.get('title', '')
    ctx.focus_blog(blog['id'], title)
    ctx.emit('status', stage='editing', label=f'Editing "{title[:50]}"')

    try:
        result = EditAgent().edit(markdown_text, instructions, title=title)
    except GeminiError as exc:
        return {
            'ok': False,
            'error': getattr(exc, 'code', 'ai_error'),
            'message': (getattr(exc, 'message', None) or str(exc))
                       + ' Nothing was changed.',
        }
    except (ValueError, TypeError) as exc:
        return {
            'ok': False,
            'error': 'edit_failed',
            'message': f'The edit could not be applied ({exc}). Nothing was changed.',
        }

    new_markdown = result['markdown']
    change = result['change']

    if change.get('unchanged'):
        # Worth its own answer. The model was told to return the post unchanged
        # rather than invent something when an instruction cannot be applied, so
        # "identical" is a meaningful signal and not a silent no-op.
        return {
            'ok': True,
            'changed': False,
            'blog_id': blog['id'],
            'message': (
                'The post came back identical, which means the edit could not '
                'be applied as asked. Tell the user nothing changed and ask '
                'them to be more specific about what to change.'
            ),
        }

    new_title = title
    if _as_bool(apply_title) and result.get('title_suggestion'):
        new_title = result['title_suggestion']

    # Re-format so the stored document keeps its shape: html, toc and toc_html
    # are read directly by the public site and the draft editor, and writing a
    # bare markdown string over a structured content dict is how a published
    # post loses its table of contents.
    formatted = FormattingAgent().format_blog(content=new_markdown, title=new_title)
    new_content = {
        'body': new_markdown,
        'html': formatted.get('html', ''),
        'markdown': new_markdown,
        'toc': formatted.get('toc', []),
        'toc_html': formatted.get('toc_html', ''),
    }

    saved = ctx.db.update_blog_content(blog['id'], new_title, new_content)
    if not saved:
        return {
            'ok': False,
            'error': 'save_failed',
            'message': (
                'The edit was made but could not be saved, so the post is '
                'unchanged. Offer to try again.'
            ),
        }

    try:
        ctx.db.log_activity(
            user_id=ctx.user_id, user_name=ctx.user_name, type='edited',
            action_text=f'edited with the agent: {instructions[:80]}',
            blog_title=new_title,
        )
    except Exception:
        logger.exception('Activity log failed for edit of %s', blog['id'])

    ctx.focus_blog(blog['id'], new_title)

    ctx.add_card('blog_edited', {
        'blog_id': blog['id'],
        'title': new_title,
        'status': blog.get('status', 'DRAFT'),
        'instruction': instructions[:200],
        'summary': summarise_change(change),
        'word_delta': change.get('word_delta', 0),
        'structure_unchanged': change.get('structure_unchanged', True),
        'excerpt': new_markdown[:600],
    })

    return {
        'ok': True,
        'changed': True,
        'blog_id': blog['id'],
        'title': new_title,
        'title_changed': new_title != title,
        'title_suggestion': result.get('title_suggestion') or '',
        'change': change,
        'message': (
            f'Edit applied and saved. {summarise_change(change)} '
            + ('The section structure changed, which a targeted edit usually '
               'should not do — mention it so the user can check. '
               if not change.get('structure_unchanged') else '')
            + ('There is a suggested new title '
               f'("{result["title_suggestion"]}") which was NOT applied; offer '
               'it to the user, noting that retitling changes the post URL. '
               if result.get('title_suggestion') and not apply_title else '')
            + 'Summarise what changed in one or two lines.'
        ),
    }


# ---------------------------------------------------------------------------
# delete_blog -- phase one only
# ---------------------------------------------------------------------------

def delete_blog(ctx, blog_id=None, **_ignored):
    """Ask for confirmation to delete a post. **Deletes nothing.**

    Returns a token the UI turns into a confirm dialog. The delete itself is
    :func:`execute_confirmed_delete`, called from the confirm endpoint.

    There is no ``force`` or ``confirmed`` parameter, and that absence is the
    design: a model that can pass ``confirmed=True`` will eventually pass it
    because the user said "yeah delete the old one" three messages ago about a
    different post. The second phase has to be a separate request from the
    browser, or the confirmation is decoration.
    """
    blog, error = _visible_blog(ctx, ctx.resolve_blog_id(blog_id))
    if error:
        return error

    title = blog.get('title', 'Untitled')
    status = (blog.get('status') or 'DRAFT').upper()

    token = ctx.db.create_confirmation(
        ctx.user_id,
        session_id=ctx.session_id,
        action='delete_blog',
        target_id=blog['id'],
        summary=f'Delete "{title}" ({status})',
        payload={'title': title, 'status': status},
    )

    if not token:
        # Refuse rather than proceed. A delete that could not be recorded as
        # pending must not happen unrecorded.
        return {
            'ok': False,
            'error': 'confirmation_failed',
            'message': (
                'The confirmation could not be set up, so nothing was deleted. '
                'Ask the user to delete it from the Drafts screen instead.'
            ),
        }

    ctx.focus_blog(blog['id'], title)

    ctx.add_card('confirm_delete', {
        'token': token,
        'blog_id': blog['id'],
        'title': title,
        'status': status,
        'word_count': (blog.get('metadata') or {}).get('word_count') or 0,
        # Named on the card because the consequence differs: a published post
        # disappearing from a live site is not the same event as a draft going.
        'published': status == 'PUBLISHED',
    })

    return {
        'ok': True,
        'pending_confirmation': True,
        'confirm_token': token,
        'blog_id': blog['id'],
        'title': title,
        'status': status,
        'message': (
            f'NOTHING HAS BEEN DELETED. A confirmation for "{title}" ({status}) '
            'is now shown to the user, and they must click Delete on it. Tell '
            'them what will be removed'
            + (' — this one is PUBLISHED, so it will disappear from their live '
               'site' if status == 'PUBLISHED' else '')
            + ' and that it cannot be undone. Then stop; do not call this tool '
              'again for the same post.'
        ),
    }


def execute_confirmed_delete(db, user_id, user_name, token):
    """Phase two: redeem a token and delete the post. Returns a result dict.

    Takes a repository rather than a :class:`ToolContext` because it runs on the
    request thread of the confirm endpoint, not inside a turn -- there may be no
    turn in flight at all by the time the user clicks.

    The token is consumed inside a transaction before anything is deleted, so a
    double-click cannot delete twice and a replayed request finds the token
    spent.
    """
    record = db.consume_confirmation(token, user_id, action='delete_blog')
    if not record:
        return {
            'ok': False,
            'error': 'invalid_confirmation',
            'message': (
                'That confirmation has expired or was already used. Ask the '
                'agent again if the post should still go.'
            ),
        }

    blog_id = record.get('target_id')
    title = (record.get('payload') or {}).get('title') or 'Untitled'

    # Re-read rather than trusting the payload: the post may have been deleted
    # or changed between the confirmation being issued and the click.
    blog = db.get_blog_by_id(blog_id) if blog_id else None
    if not blog:
        return {
            'ok': True,
            'already_gone': True,
            'blog_id': blog_id,
            'title': title,
            'message': f'"{title}" was already gone. Nothing to do.',
        }

    if blog.get('author_id') != user_id:
        # The token proves intent, not authority. Ownership is re-checked at the
        # moment of deletion, because the two are separated in time here and a
        # role can change in between.
        site_owner = None
        try:
            site_owner = db.get_site_owner_for_user(user_id)
        except Exception:
            logger.exception('Could not resolve site owner during delete')
        if not (site_owner and blog.get('site_owner_id') == site_owner):
            return {
                'ok': False,
                'error': 'not_found',
                'message': 'That post could not be deleted.',
            }

    deleted = db.delete_blog(blog_id)
    if not deleted:
        return {
            'ok': False,
            'error': 'delete_failed',
            'message': f'"{title}" could not be deleted. Nothing was removed.',
        }

    try:
        db.log_activity(
            user_id=user_id, user_name=user_name, type='deleted',
            action_text='permanently deleted (confirmed in chat)',
            blog_title=title,
        )
    except Exception:
        logger.exception('Activity log failed for confirmed delete of %s', blog_id)

    logger.info('Blog deleted via chat confirmation',
                extra={'blog_id': blog_id, 'user_id': user_id})

    return {
        'ok': True,
        'deleted': True,
        'blog_id': blog_id,
        'title': title,
        'message': f'"{title}" is deleted. That cannot be undone.',
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def list_blogs(ctx, status=None, search=None, category=None,
               limit=DEFAULT_LIST_LIMIT, **_ignored):
    """List the user's posts, optionally filtered by status, text or category."""
    status_filter = (status or 'all').strip().upper()
    if status_filter not in STATUSES:
        status_filter = 'all'

    limit = _as_int(limit, DEFAULT_LIST_LIMIT, 1, MAX_LIST_LIMIT)

    ctx.emit('status', stage='listing', label='Looking through your posts')

    try:
        page = ctx.db.get_all_blogs_filtered(
            user_ids=_scope_ids(ctx),
            status_filter=status_filter if status_filter != 'ALL' else 'all',
            category_filter=(category or 'all').strip() or 'all',
            search=(search or '').strip(),
            date_from='', date_to='',
            page=1, per_page=limit,
        )
    except Exception as exc:
        logger.exception('list_blogs failed')
        return {
            'ok': False,
            'error': 'list_failed',
            'message': f'Could not read the blog list ({exc}).',
        }

    rows = [
        {
            'blog_id': item.get('id', ''),
            'title': item.get('title', 'Untitled'),
            'status': item.get('status', ''),
            'category': item.get('category', ''),
            'author': item.get('author', ''),
            'updated': str(item.get('updated_at') or item.get('created_at') or '')[:10],
        }
        for item in (page.get('blogs') or page.get('items') or [])
    ]

    if rows:
        ctx.add_card('blog_list', {
            'items': rows,
            'total': page.get('total') or len(rows),
            'filters': {
                'status': status_filter,
                'search': (search or '').strip(),
                'category': (category or '').strip(),
            },
        })

    # A single result is almost always the post the user is about to talk
    # about, so focusing it here is what makes "shorten it" work straight after
    # "find my post about pricing".
    if len(rows) == 1 and rows[0]['blog_id']:
        ctx.focus_blog(rows[0]['blog_id'], rows[0]['title'])

    return {
        'ok': True,
        'count': len(rows),
        'total_matching': page.get('total') or len(rows),
        'blogs': rows,
        'message': (
            f'{len(rows)} post(s) shown to the user as a list. Refer to them by '
            'title, not id. Do not repeat the whole list in prose.'
            if rows else
            'No posts matched. Say so and offer to widen the search or write '
            'something new.'
        ),
    }


def get_blog(ctx, blog_id=None, **_ignored):
    """Read one post in full, for review or before editing it."""
    blog, error = _visible_blog(ctx, ctx.resolve_blog_id(blog_id))
    if error:
        return error

    markdown_text = _markdown_of(blog)
    title = blog.get('title', 'Untitled')
    ctx.focus_blog(blog['id'], title)

    formatting = blog.get('formatting') or {}
    metadata = blog.get('metadata') or {}
    word_count = metadata.get('word_count') or len(markdown_text.split())

    ctx.add_card('blog_preview', {
        'blog_id': blog['id'],
        'title': title,
        'status': blog.get('status', ''),
        'category': blog.get('category', ''),
        'word_count': word_count,
        'reading_time': formatting.get('reading_time', ''),
        # The whole post, for the expandable preview in the chat. The model gets
        # the excerpt below instead.
        'markdown': markdown_text,
    })

    truncated = len(markdown_text) > MAX_MODEL_EXCERPT_CHARS

    return {
        'ok': True,
        'blog_id': blog['id'],
        'title': title,
        'status': blog.get('status', ''),
        'category': blog.get('category', ''),
        'word_count': word_count,
        'section_count': len(formatting.get('toc') or []),
        'sections': [
            (item.get('text') or item.get('title') or '')
            if isinstance(item, dict) else str(item)
            for item in (formatting.get('toc') or [])
        ][:12],
        'content_excerpt': markdown_text[:MAX_MODEL_EXCERPT_CHARS],
        'content_truncated': truncated,
        'message': (
            'The full post is shown to the user as an expandable preview. '
            + ('You have the first part of it; the edit tool reads the whole '
               'post from storage, so you do not need the rest to edit it. '
               if truncated else '')
            + 'This post is now in focus, so edit_blog and delete_blog will '
              'act on it without an id.'
        ),
    }


def _as_int(value, default, low, high):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def _as_bool(value):
    """A model-supplied boolean, which arrives as a string often enough to matter.

    ``bool("false")`` is ``True``, and here that would silently retitle a post --
    changing its slug and breaking inbound links -- for a model that meant the
    opposite. So a string is read for its content, and anything unrecognised is
    False: the safe direction for a flag whose only job is to authorise a change
    the user did not explicitly ask for.
    """
    if isinstance(value, str):
        return value.strip().lower() in ('true', 'yes', '1', 'y', 'on')
    return bool(value)
