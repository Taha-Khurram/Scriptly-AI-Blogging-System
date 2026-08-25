import re

from app.agents.content_agent import ContentAgent
from app.agents.formatting_agent import FormattingAgent
from app.agents.seo_agent import SEOAgent
from app.services.gemini_client import gemini, GeminiError
from app.utils.parallel import TimedExecution
from app.core.logging import get_logger

logger = get_logger(__name__)


class BlogAgent:
    def __init__(self):
        self.content_agent = ContentAgent()
        self.formatting_agent = FormattingAgent()
        self.seo_agent = SEOAgent()

    def run_pipeline(self, user_prompt, enable_seo=False, region="PK",
                     on_thought=None, on_content=None, on_stage=None):
        """
        Optimized AI blog generation pipeline.

        The blog is generated in a single model call (no separate outline
        round-trip) for the fastest possible turnaround. Humanization is NOT
        run here — it is applied on-demand from the drafts page instead.
        SEO is disabled by default; use run_seo_analysis() for full optimization.

        Args:
            user_prompt: Topic/prompt for the blog
            enable_seo: Whether to run full SEO optimization (slower, default False)
            region: Target region for SEO keywords (default Pakistan)
            on_thought: fn(text, kind) — a line of the agent's reasoning, either
                the model's own plan ('plan') or something this pipeline
                observed ('note'). Optional.
            on_content: fn(chunk) — a piece of the draft as it is written.
                Passing this switches content generation to streaming.
            on_stage: fn(stage, progress) — called where the stage actually
                changes. Without it the caller has to guess the boundaries from
                outside, which is how "Writing the draft" ends up on screen
                during formatting.

        The three callbacks run on this thread and are expected to be cheap
        (append to a buffer). Nothing in the pipeline's own behaviour depends on
        them, so a caller that wants the old blocking behaviour passes none and
        gets exactly what it got before.
        """
        logger.info("Starting Optimized AI Pipeline ---")

        def stage(name, progress):
            if on_stage:
                on_stage(name, progress)

        def thought(text, kind='note'):
            if on_thought:
                on_thought(text, kind)

        try:
            # Step 1: Generate the full blog in a single call
            stage('content', 15)
            with TimedExecution("Content Generation"):
                if on_content:
                    content_data = self.content_agent.stream_blog(
                        user_prompt,
                        on_thought=lambda line: thought(line, 'plan'),
                        on_content=on_content,
                    )
                else:
                    content_data = self.content_agent.generate_blog(user_prompt)

            if not content_data or 'markdown' not in content_data:
                raise KeyError("Content agent failed to return 'markdown' data.")

            markdown_text = content_data['markdown']
            final_title = user_prompt.title()

            if content_data.get('partial'):
                thought(
                    'The stream cut out before the end — saving what was '
                    'written so you can finish it.'
                )

            # Derive the outline from the generated section headings (no LLM call)
            outline = re.findall(r'^##\s+(.+?)\s*$', markdown_text, re.MULTILINE)

            # Step 2: Format Content (run immediately, no SEO delay)
            stage('formatting', 70)
            if outline:
                thought(
                    'Structure: {} sections — {}.'.format(
                        len(outline), ', '.join(outline[:4])
                        + (', …' if len(outline) > 4 else '')
                    )
                )

            with TimedExecution("Formatting"):
                formatted_data = self.formatting_agent.format_blog(
                    content=markdown_text,
                    title=final_title
                )

            thought(
                '{} words, {} — table of contents built from {} '
                'heading{}.'.format(
                    formatted_data['statistics']['word_count'],
                    formatted_data['reading_time_text'],
                    len(formatted_data['toc']),
                    '' if len(formatted_data['toc']) == 1 else 's',
                )
            )

            # Step 4: Quick SEO analysis (optional, lightweight)
            seo_data = None
            if enable_seo:
                with TimedExecution("SEO Analysis"):
                    try:
                        # Use analyze_only for speed - no content rewriting
                        seo_data = self.seo_agent.analyze_only(
                            title=final_title,
                            content=markdown_text
                        )
                        seo_data['enabled'] = True
                    except Exception as seo_error:
                        logger.warning(f"SEO analysis skipped: {seo_error}")
                        seo_data = {"error": str(seo_error), "skipped": True}

            # Step 5: Package for Firestore
            word_count = formatted_data['statistics']['word_count']

            return {
                "title": final_title,
                "outline": outline,
                "content": {
                    "markdown": markdown_text,
                    "html": formatted_data['html'],
                    "original_markdown": content_data['markdown']
                },
                "formatting": {
                    "toc": formatted_data['toc'],
                    "toc_html": formatted_data['toc_html'],
                    "reading_time": formatted_data['reading_time_text'],
                    "reading_time_minutes": formatted_data['reading_time_minutes'],
                    "statistics": formatted_data['statistics'],
                    "has_code": formatted_data['has_code_blocks'],
                    "has_images": formatted_data['has_images'],
                    "has_tables": formatted_data['has_tables']
                },
                "seo": seo_data if seo_data else {"enabled": False},
                "metadata": {
                    "word_count": word_count,
                    # Read from the client so this metadata cannot drift
                    # out of date the next time the model changes.
                    "model_used": gemini.default_model,
                    "status": "success",
                    "seo_enabled": enable_seo,
                    "humanized": False,
                    "target_region": region if enable_seo else None,
                    "streamed": bool(content_data.get('streamed')),
                    "partial": bool(content_data.get('partial')),
                    # The model's own plan for the piece, kept with the draft:
                    # it is the only record of why the post takes the angle it
                    # does, and the reasoning panel is gone once the run ends.
                    "plan": content_data.get('plan') or []
                }
            }

        except GeminiError as e:
            # These carry a message written for the reader ("at its request
            # limit", "declined this topic"), which the generic handler below
            # would replace with "an unexpected system error".
            logger.warning("AI generation failed: %s", e)
            return {
                "error": getattr(e, 'message', None) or str(e),
                "error_code": getattr(e, 'code', None),
                "status": "failed",
                "partial_outline": outline if 'outline' in locals() else None
            }
        except (IndexError, KeyError, ValueError) as e:
            logger.exception("Pipeline Error")
            return {
                "error": str(e),
                "status": "failed",
                "partial_outline": outline if 'outline' in locals() else None
            }
        except Exception:
            logger.exception("Unexpected Error")
            return {"error": "An unexpected system error occurred.", "status": "failed"}

    def run_seo_analysis(self, title, content, region="PK"):
        """
        Run SEO analysis only (without full blog generation)
        Useful for analyzing existing content
        """
        try:
            return self.seo_agent.optimize_blog(title, content, region)
        except Exception as e:
            logger.exception("SEO Analysis Error")
            return {"error": str(e), "status": "failed"}

    def format_content(self, content, title=""):
        """
        Format content only (without generation)
        Useful for formatting existing/imported content
        """
        try:
            return self.formatting_agent.format_blog(content, title)
        except Exception as e:
            logger.exception("Formatting Error")
            return {"error": str(e), "status": "failed"}